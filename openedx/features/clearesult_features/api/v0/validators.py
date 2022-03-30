import logging

from django.contrib.sites.models import Site
from django.db.models import Q
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.response import Response
from rest_framework import status
from opaque_keys import InvalidKeyError

from openedx.features.clearesult_features.models import (
	ClearesultCourse, ClearesultCatalog, ClearesultLocalAdmin,
	ClearesultUserProfile
)


log = logging.getLogger(__name__)



def validate_data_for_catalog_creation(data, user, allowed_sites):
    field_mapping = {
        'name': {
            'data_type': str,
            'verbose_data_type': 'String',
            'required_message': 'You must have to provide a catalog name.',
        },
        'site': {
            'data_type': dict,
            'verbose_data_type': 'Dictionery',
            'required_message': 'You can pass "{}" if you do not want to set its value.',
        },
        'clearesult_courses': {
            'data_type': list,
            'verbose_data_type': 'List',
            'required_message': 'You can pass "[]" if you do not want to set its value.',
        }
    }
    validated_data = {}

    error = validate_fields_quantity_and_data_types(data, field_mapping)
    if error:
        return error, None

    name = data.get('name')
    site = data.get('site')
    clearesult_courses = data.get('clearesult_courses')

    error, validated_catalog_name = validate_catalog_name(name)
    if error:
        return error, None
    else:
        validated_data['name'] = validated_catalog_name

    error, validated_clearesult_courses = validate_clearesult_courses(clearesult_courses, user, allowed_sites)
    if error:
        return error, None
    else:
        validated_data['clearesult_courses'] = validated_clearesult_courses

    error, validated_site = validate_site(site)
    if error:
        return error, None
    else:
        validated_data['site'] = validated_site

    # All is well !!!
    return None, validated_data


def validate_data_for_catalog_updation(data, user, allowed_sites):
    field_mapping = {
        'name': {
            'data_type': str,
            'verbose_data_type': 'String',
            'required_message': 'You must have to provide a catalog name.'
        },
        'clearesult_courses': {
            'data_type': list,
            'verbose_data_type': 'List',
            'required_message': 'You can pass "[]" if you do not want to set its value.'
        }
    }
    validated_data = {}

    error = validate_fields_quantity_and_data_types(data, field_mapping)
    if error:
        return error, None

    pk = data.get('pk')
    name = data.get('name')
    clearesult_courses = data.get('clearesult_courses')

    error, validated_clearesult_catalog = validate_clearesult_catalog_pk(pk)
    if error:
        return error, None
    else:
        validated_data['clearesult_catalog'] = validated_clearesult_catalog

    error, validated_catalog_name = validate_catalog_name(name)
    if error:
        return error, None
    else:
        validated_data['name'] = validated_catalog_name

    error, validated_clearesult_courses = validate_clearesult_courses(clearesult_courses, user, allowed_sites)
    if error:
        return error, None
    else:
        validated_data['clearesult_courses'] = validated_clearesult_courses

    # All is well !!!
    return None, validated_data


def validate_fields_quantity_and_data_types(data, fields_mapping):
    for required_field in fields_mapping.keys():
        if not required_field in data:
            return Response(
                {
                    'detail': 'You did not provide the {}.\n{}.'.format(
                              required_field, fields_mapping.get(required_field).get('required_message'))
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        if not isinstance(data.get(required_field), fields_mapping.get(required_field).get('data_type')):
            return Response(
                {
                    'detail': 'The {} field should be of {} type.'.format(
                              required_field, fields_mapping.get(required_field).get('verbose_data_type'))
                },
                status=status.HTTP_400_BAD_REQUEST
            )

    return None


def validate_catalog_name(name):
    name = name.strip()
    if name == '':
        return Response(
            {'detail': 'Please provide a valid catalog name'},
            status=status.HTTP_400_BAD_REQUEST
        ), None

    return None, name


def validate_site(site):
    if site == {}:
        return None, None

    domain = site.get('domain', '')
    try:
        site = Site.objects.get(domain=domain)
        return None, site
    except Site.DoesNotExist:
        return Response(
            {'detail': 'The site for this domain {} does not exist.'.format(domain)},
            status=status.HTTP_400_BAD_REQUEST
        ), None


def validate_sites_for_local_admin(user):
    if user.is_superuser:
        return None, None

    queryset = ClearesultLocalAdmin.objects.filter(user=user).select_related('site')
    if not queryset:
        return Response(
            {'detail': 'You are not authenticated to perform this action'},
            status=status.HTTP_403_FORBIDDEN
        ), None
    allowed_sites = []

    for item in queryset:
        allowed_sites.append(item.site)

    return None, allowed_sites


def validate_clearesult_catalog_pk(pk):
    if pk is None:
        return Response(
            {'detail': 'Please provide id of catalog in the URL'},
            status=status.HTTP_400_BAD_REQUEST
        ), None
    else:
        try:
            clearesult_catalog = ClearesultCatalog.objects.get(id=pk)
        except ClearesultCatalog.DoesNotExist:
            return Response(
                {'detail': 'The Clearesult catalog with this {} id does not exist.'.format(pk)},
                status=status.HTTP_400_BAD_REQUEST
            ), None
        except ValueError:
            return Response(
                {'detail': 'Please provide valid id for clearesult catalog.'}
            ), None

    return None, clearesult_catalog


def validate_clearesult_courses(clearesult_courses, user, allowed_sites):
    """
    Return "None, courses" if all goes well,
    otherwise return "error, None"
    """
    courses = []
    for clearesult_course in clearesult_courses:
        if not isinstance(clearesult_course, str):
            return Response(
                {'detail': 'The clearesult_course should be the list of course ids (strings).'},
                status=status.HTTP_400_BAD_REQUEST
            ), None

        try:
            if user.is_superuser:
                courses.append(ClearesultCourse.objects.get(course_id=clearesult_course))
            else:
                courses.append(
                    ClearesultCourse.objects.get(
                        Q(course_id=clearesult_course) & (Q(site__in=allowed_sites) | Q(site=None))
                    )
                )
        except ClearesultCourse.DoesNotExist:
            return Response(
                {'detail': 'Course with this id {} does not exist.'.format(clearesult_course)},
                status=status.HTTP_400_BAD_REQUEST
            ), None
        except AssertionError:
            return Response(
                {'detail': 'Please correct the format of course_id: {}.'.format(clearesult_course)},
                status=status.HTTP_400_BAD_REQUEST
            ), None
        except InvalidKeyError:
            return Response(
                {'detail': '{} is not a valid course key'.format(clearesult_course)},
                status=status.HTTP_400_BAD_REQUEST
            ), None

    return None, courses


def validate_catalog_update_deletion(user, catalog):
    if not catalog.site:
        return Response(
            {'detail': 'You are not allowed to perform this action.'},
            status=status.HTTP_403_FORBIDDEN
        )
    try:
        _ = ClearesultLocalAdmin.objects.get(user=user, site=catalog.site)
    except ClearesultLocalAdmin.DoesNotExist:
        return Response(
            {'detail': 'You are not allowed to perform this action.'},
            status=status.HTTP_403_FORBIDDEN
        )
    return None


def validate_user_for_site(user, site):
    try:
        # convert "blackhills - LMS" to "blackhills"
        site_name = "-".join(site.name.split('-')[:-1]).rstrip()

        # ! Note: site name must contain "-" otherwise it will return empty string.
        if not site_name:
            log.info("Site name ({}) is not in a correct format.".format(site.name))
            log.info("Correct format is <site_name> - <site_type> i.e. 'blackhills - LMS'.")
            return False

        if user.clearesult_profile.has_identifier(site_name):
            return True
        log.info("user {} does not belong to site {}. ".format(user.email, site_name))
        return False
    except User.clearesult_profile.RelatedObjectDoesNotExist:
        log.info("user clearesult profile does not exist for user {}.".format(user.email))
        return False
