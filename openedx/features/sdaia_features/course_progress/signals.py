"""
Signal handlers for the course progress emails
"""
import logging

from completion.models import BlockCompletion
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.contrib.sites.models import Site

from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.features.sdaia_features.course_progress.models import CourseCompletionEmailHistory
from openedx.features.sdaia_features.course_progress.tasks import send_user_course_progress_email
from openedx.features.sdaia_features.course_progress.utils import get_user_course_progress
from xmodule.modulestore.django import modulestore

logger = logging.getLogger(__name__)


@receiver(post_save, sender=BlockCompletion)
def evaluate_subsection_completion_milestones(**kwargs):
    """
    Receives the BlockCompletion signal and sends the email to 
    the user if he completes a specific course progress threshold.
    """
    instance = kwargs['instance']
    if not instance.context_key.is_course:
        return  # Content in a library or some other thing that doesn't support milestones
    
    course_key = instance.context_key

    units = modulestore().get_items(course_key, qualifiers={'category': 'vertical'})
    for unit in units:
        if instance.block_key in unit.children:
            completed_units = BlockCompletion.objects.filter(block_key__in=unit.children, completion=1.0).count()
            if not(completed_units == len(unit.children)):
                return
            break


    course = modulestore().get_course(course_key)
    course_completion_percentages_for_emails = course.course_completion_percentages_for_emails
    if not course.allow_course_completion_emails or not course_completion_percentages_for_emails:
        return

    course_completion_percentages_for_emails = course_completion_percentages_for_emails.split(",")
    try:
        course_completion_percentages_for_emails = [int(entry.strip()) for entry in course_completion_percentages_for_emails]
    except Exception as e:
        log.info(f"invalid course_completion_percentages_for_emails for course {str(course_key)}")
        return

    user_id = instance.user_id
    user = User.objects.get(id=user_id)
    user_completion_progress_email_history, _ = CourseCompletionEmailHistory.objects.get_or_create(user=user, course_key=course_key)
    progress_last_email_sent_at = user_completion_progress_email_history and user_completion_progress_email_history.last_progress_email_sent
    if progress_last_email_sent_at == course_completion_percentages_for_emails[-1]:
        return

    site = Site.objects.first() or Site.objects.get_current()
    with emulate_http_request(site, user):
        user_completion_percentage = get_user_course_progress(user, course_key)

    if user_completion_percentage > progress_last_email_sent_at:
        for course_completion_percentages_for_email in course_completion_percentages_for_emails:
            if user_completion_percentage >= course_completion_percentages_for_email > progress_last_email_sent_at:
                send_user_course_progress_email.delay(user_completion_percentage, progress_last_email_sent_at, course_completion_percentages_for_email, str(course_key), user_id)
