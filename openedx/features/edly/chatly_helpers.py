import json
import logging
import pytz
from datetime import datetime
import requests

from django.utils.html import escapejs
from cms.djangoapps.contentstore.utils import reverse_course_url
from common.djangoapps.util.json_request import JsonResponse
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from django.conf import settings
from rest_framework import status

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.edly.constants import CHATLY_COOKIE_NAME, CHATLY_IMPORT_ERROR_MESSAGES, COURSE_SIZE_LIMIT, DEFAULT_QA_PROMPT
from openedx.features.edly.models import ChatlyWidget
from  openedx.features.edly.api.serializers import ChatlyWidgetSerializer
from openedx.features.edly.utils import (
    obtain_token_from_chatly, get_edly_sub_org_from_request, get_integration_status_from_chatly
)

logger = logging.getLogger(__name__)


def get_chatly_token_from_cookie(request):
    """
    Method to retrieve chatly token from cookie if it exists else fetch from api.

    Method will return chatly token, and a string that can be evaluated using eval()
    to set the token if it was obtained from calling the api.

    Returns:
        eval_string: string
        chatly_token: string

    """
    eval_string = "None"
    if CHATLY_COOKIE_NAME in request.COOKIES:
        chatly_token = request.COOKIES[CHATLY_COOKIE_NAME]
    else:
        chatly_token = obtain_token_from_chatly()
        if chatly_token:
            eval_string = (
                f'response.set_cookie("{CHATLY_COOKIE_NAME}", chatly_token, max_age=604600)'
            )

    return eval_string, chatly_token


def send_chatly_request(request, endpoint, method, payload={}, post_files=None):
    """Method for send a request to the chatly endpoints."""

    org = get_edly_sub_org_from_request(request).slug
    payload['org'] = org
    reqUrl = "{}/api/{}".format(settings.CHATLY_BACKEND_ORIGIN, endpoint)

    token = ''
    if CHATLY_COOKIE_NAME in request.COOKIES:
        token = request.COOKIES[CHATLY_COOKIE_NAME]
    else:
        token = obtain_token_from_chatly()

    headersList = {
        "Authorization": "Bearer {}".format(token),
    }

    if method in ['GET', 'DELETE']:
        reqUrl = '{}/?org={}'.format(reqUrl, org)
        return requests.request(method, reqUrl , headers=headersList)

    if post_files:
        return requests.request(method, reqUrl, data=payload, files=post_files,headers=headersList)

    return requests.request(method, reqUrl, json=payload, headers=headersList)


def compare_date(edited_date, last_sync_date):
    """Comparison of date for delta calculation."""
    if not edited_date:
        return True

    edited_date = pytz.utc.localize(datetime.strptime(edited_date, "%b %d, %Y at %H:%M %Z"))
    return bool(edited_date > last_sync_date)


def get_course_delta(course_outline, chatly_widget):
    """
    Only get the required imformation from course outline to caculate course delta
    """
    key_to_extract = ['id', 'display_name', 'edited_on']
    chatly_data = {key: course_outline[key] for key in course_outline if key in key_to_extract}
    sections = course_outline.get("child_info", {}).get("children", [])
    chatly_data["delta_count"] = len(sections)
    chatly_data["last_sync_date"] = ""
    chatly_data["is_enable"] = False
    chatly_data["bot_key"] = ""
    chatly_data["section"] = [
        {
            "id":item.get("id"),
            "name":item.get("display_name"),
            "modified_or_not_exist":True
        }
        for item in sections
    ]
    if not chatly_widget.exists():
        return chatly_data

    chatly_widget = chatly_widget.first()
    chatly_data["is_enable"] = chatly_widget.is_enable
    chatly_data["bot_key"] = chatly_widget.bot_key

    if not chatly_widget.last_sync_date:
        chatly_data["last_sync_date"] = ""
        return chatly_data

    chatly_data["delta_count"] = 0
    chatly_data["last_sync_date"] = chatly_widget.last_sync_date.isoformat()
    for index,section in enumerate(sections):
        if compare_date(section.get("edited_on"), chatly_widget.last_sync_date):
            chatly_data["delta_count"] +=1
        else:
            chatly_data["section"][index]['modified_or_not_exist']= False

    return chatly_data


def save_course_outline(course_key_string, course_outline):
    """Save a snapshot of the course outline for later delta calculation processing."""
    key_to_extract = ['id', 'edited_on']
    snap_json = {key: course_outline[key] for key in course_outline if key in key_to_extract}
    snap_json['chapter'] = [
        {
            'id':item.get('id'),
            'edited_on':item.get('edited_on'),
        }
        for item in course_outline.get('child_info', {}).get('children', [])
    ]
    ChatlyWidget.objects.filter(course_key=course_key_string).update(
        sync_date = timezone.now(),
        sync_json = snap_json,
        sync_status = 0
    )


def send_chatly_export_request_and_return_response(request, course_key_string, artifact, tarball):
    """This method sends the course export request and returns respective response."""
    if tarball.size > COURSE_SIZE_LIMIT:
        artifact.file.close()
        return JsonResponse({'error': 'Please make sure the course size is less than 50MB'}, status=400)

    send_chatly_request(
        request,
        'import/course/', 'POST',
        {
            "course_id": course_key_string,
            "prev_directory_id":  request.POST.get('directory_id'),
            "bot_id":  request.POST.get('bot_id'),
            "callback_url": request.build_absolute_uri().replace(request.get_full_path(), '')
        },
        post_files = { "zip_file": tarball }
    )
    artifact.file.close()

    return JsonResponse({"ExportStatus": 1})


def get_chatly_integrate_status(request, chatly_token):
    """Check whether chatly is integrated with studio or not."""
    site_config = configuration_helpers.get_current_site_configuration()
    try:
        chatly_integrated = True
        if not "CHATLY" in site_config.site_values["DJANGO_SETTINGS_OVERRIDE"]:
            site_config.site_values["DJANGO_SETTINGS_OVERRIDE"]["CHATLY"] = {
                "chatly_integrated": False
            }

        if not site_config.site_values["DJANGO_SETTINGS_OVERRIDE"]["CHATLY"][
            "chatly_integrated"
        ]:
            chatly_integrated = get_integration_status_from_chatly(
                request.user.email,
                get_edly_sub_org_from_request(request).slug,
                chatly_token,
            )
            site_config.site_values["DJANGO_SETTINGS_OVERRIDE"]["CHATLY"] = {
                "chatly_integrated": chatly_integrated
            }
            site_config.save()

    except Exception:
        chatly_integrated = False

    return chatly_integrated


def get_context_data(course_key, course_structure):
    """Get the context data for chatly widget."""
    chatly_widget = ChatlyWidget.objects.filter(course_key=course_key)
    chatly_data = get_course_delta(course_structure, chatly_widget)
    widget_data = {
        'prompt':escapejs(DEFAULT_QA_PROMPT),
        'temperature':'1.0',
    }
    if chatly_widget.exists():
        widget_data = chatly_widget.values('is_enable', 'unable_to_answer_response', 'temperature', 'ai_output_focus', 'advanced_mode', 'prompt', 'bot_id', 'allowed_directories').first()
        widget_data['prompt'] = escapejs(widget_data['prompt'])
        widget_data['ai_output_focus'] = escapejs(widget_data['ai_output_focus'])

    return widget_data, chatly_data


def creation_directory(request, title):
    """create a course directory, required for creating a bot."""

    response = send_chatly_request(request, 'directory/', 'POST', {'title':title})
    if response.status_code != 201:
        logger.info('Error Creating the directory')
        return None

    return response.json().get('data', {}).get('id')


def create_bot(request, directory_id):
    """A helper method for creating chatly bot based on user input"""

    payload = {
        'bot_name': request.POST.get('title', ''),
        'bot_description':request.POST.get('title', ''),
        'temperature': 1,
        'unable_to_answer_response':'nothing',
        'prompt_type': 'keywords',
        'ai_output_focus':['Answering Questions', 'Brainstorming Ideas', 'Innovative Solutions'],
        'allowed_directories':[directory_id],
    }

    response = send_chatly_request(
        request,
        'botconfig/create/',
        'POST',
        payload
    )
    if response.status_code !=201:
        logger.info('Error Creating Bot: {}'.format(response.json().get('details')))
        return (None, None)

    return (response.json().get('id'), response.json().get('bot_key'))


def handle_enable_bot(request, widget_obj):
    """Handling the enable disable of the bot"""

    is_bot_enable = request.POST.get('is_bot_enable', '').lower() == "true"
    course_key = request.POST.get('course_key', '')
    course_title = request.POST.get('title', '')

    if widget_obj.exists():
        widget_obj = widget_obj.first()
        widget_obj.is_enable = is_bot_enable
        widget_obj.save()
        return {'message':'Bot Cofigs Update Successful', 'status':status.HTTP_200_OK}

    directory_id = creation_directory(request, course_title)
    if not directory_id:
        return {'message':'fail to create the directory for Bot', 'status':status.HTTP_400_BAD_REQUEST}

    bot_id, bot_key = create_bot(request, directory_id)
    if not bot_id:
        return {'message':'fail to create the bot', 'status':status.HTTP_400_BAD_REQUEST}

    ChatlyWidget.objects.create(
        is_enable=is_bot_enable,
        course_key=course_key,
        bot_key=bot_key,
        bot_id=bot_id,
        allowed_directories=directory_id,
        temperature= request.POST.get('temperature', 1),
        unable_to_answer_response= request.POST.get('querry_answer','nothing'),
        ai_output_focus=('_,_').join(request.POST.getlist('tag[]', ["Answering Questions"])),
    )
    return {'message':'Bot Created Successfully', 'status':status.HTTP_200_OK}


def update_bot_config(request, widget_obj):
    """Handling of the updating the configuration of the bot Settings."""
    serializer = ChatlyWidgetSerializer(
        widget_obj, data=request.data, partial=True
    )

    if not serializer.is_valid():
        return {'message': 'Unable to update bot settings', status:status.HTTP_400_BAD_REQUEST}

    serializer.save()
    payload = {
        'bot_name': request.POST.get('title'),
        'temperature': serializer.data.get('temperature'),
        'unable_to_answer_response': serializer.data.get('unable_to_answer_response'),
        'prompt_type': 'raw_edited' if serializer.data.get('advanced_mode') else 'keywords',
        'ai_output_focus':  [] if not serializer.data.get('ai_output_focus') else serializer.data.get('ai_output_focus').split('_,_'),
        'prompt': serializer.data.get('prompt'),
    }
    response = send_chatly_request(
        request,
        'botconfig/{}/'.format(widget_obj.bot_id),
        'PUT',
        payload
    )

    if response.status_code !=200:
        logger.info('Error updating the Bot settings')
        return {'message': 'Unable to update the bot at the moment', 'status':status.HTTP_400_BAD_REQUEST}

    return {'message': 'Bot config update was successful', 'status':status.HTTP_200_OK}


def get_updated_message(message):
    """Update the error message to a better viewable error for end user"""

    logger.info("Error With chatly: {}".format(message))
    if not message:
        return "something went wrong with syncing course data, please contact support"

    return CHATLY_IMPORT_ERROR_MESSAGES[message]


def get_chatly_payload(request, course_key, course_structure):
    """
    Returns the chatly payload for the given course.
    """
    site_config = configuration_helpers.get_current_site_configuration()
    show_chatly_integration = (
        site_config.site_values["DJANGO_SETTINGS_OVERRIDE"]
        .get("CHATLY", {})
        .get("show_chatly_integration", True)
    )
    widget_data, chatly_data = get_context_data(course_key, course_structure)
    _, chatly_token = get_chatly_token_from_cookie(request)
    chatly_integrated = get_chatly_integrate_status(request, chatly_token)

    payload = {
        "show_chatly_integration": show_chatly_integration,
        "courselike_home_url": reverse_course_url("course_handler", course_key),
        "status_url": reverse_course_url("export_status_handler", course_key),
        "library": False,
        "delta_count": chatly_data.get("delta_count", 0),
        "section_length": len(chatly_data.get("section", [])),
        "chatly_token": chatly_token,
        "user": request.user,
        "sub_org": get_edly_sub_org_from_request(request).slug,
        "is_chatly_integrated": chatly_integrated,
        "is_enable": chatly_data.get("is_enable", False),
        "bot_key": chatly_data.get("bot_key", ""),
        "widget_data": widget_data,
        "chatly_data": json.dumps(chatly_data),
        "logo": site_config.site_values.get("BRANDING", {}).get("logo", ""),
        "course_key": course_key,
    }

    return payload
