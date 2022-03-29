"""
Django admin command to send mandatory courses due date emails.
"""
import six
from completion.models import BlockCompletion
from datetime import datetime, timedelta
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.test import RequestFactory
from logging import getLogger
from student.models import CourseEnrollment

from lms.djangoapps.courseware.courses import get_course_by_id
from openedx.features.course_experience.utils import get_course_outline_block_tree
from openedx.features.clearesult_features.models import ClearesultGroupLinkedCatalogs, ClearesultCourse
from openedx.features.clearesult_features.utils import (
    get_mandatory_courses_due_date_config, send_course_due_date_approching_email,
    send_due_date_passed_email_to_admins
)

logger = getLogger(__name__)


class Command(BaseCommand):
    """
    This command will check and send emails to students for approaching due dates of mandatory courses notification.
    Example usage:
        $ ./manage.py lms check_and_send_due_dates_emails
    """
    help = 'Command to check and send mandatory courses due-dates emails'

    def _is_course_completed(self, request, enrollment):
        course_outline_blocks = get_course_outline_block_tree(
            request, six.text_type(enrollment.course_id), request.user
        )

        if course_outline_blocks:
            return course_outline_blocks.get('complete')
        else:
            return True

    def _get_mandatory_courses_enrollments(self):
        all_mandatory_courses = ClearesultCourse.objects.none()
        linkage_with_mandatory_courses = ClearesultGroupLinkedCatalogs.objects.exclude(mandatory_courses=None)
        for linkage in linkage_with_mandatory_courses:
            all_mandatory_courses |= linkage.mandatory_courses.filter(is_event=False)

        all_mandatory_courses = all_mandatory_courses.distinct()

        # extract all mandatory courses enrollments:
        mandatory_courses_enrollments = CourseEnrollment.objects.filter(
            is_active=True, course_id__in =[course.course_id for course in all_mandatory_courses])

        return mandatory_courses_enrollments

    def _update_passed_due_dates_site_users_data(self, passed_due_dates_site_users, site, enrollment, due_date):
        """
        The passed_due_dates_site_users is a dict which will be used to send compiled email to local admins and superusers
        containing users data who haven't completed the mandatory courses in the allotted time.

        Sample data is as follows:
        passed_due_dates_site_users = {
            site_domain_1: {
                courses_id1: {
                    "course_name": "sample course 1"
                    "users": [
                        {
                            "username": username1,
                            "email: username1@example.com
                            "due_date": 22-1-2021
                        }
                    ]
                },
                courses_id2: {
                    "course_name": "sample course 1"
                    "users": [
                        {
                            "username": username1,
                            "email: username1@example.com
                            "due_date": 22-1-2021
                        },
                        {
                            "username": username2,
                            "email: username2@example.com
                            "due_date": 22-1-2021
                        }
                    ]
                },
            },
            site_domain_2: {
                courses_id1: {
                    "course_name": "sample course 1"
                    "users": [
                        {
                            "username": username1,
                            "email: username1@example.com
                            "due_date": 22-1-2021
                        }
                    ]
                },
            }
        }
        """
        course_id_str = six.text_type(enrollment.course_id)
        course = get_course_by_id(enrollment.course_id)
        if passed_due_dates_site_users.get(site.domain):
            site_enrollments = passed_due_dates_site_users.get(site.domain)
            user_dict = {
                "username": enrollment.username,
                "full_name": enrollment.user.first_name + " " + enrollment.user.last_name,
                "email": enrollment.user.email,
                "due_date": due_date.strftime("%m/%d/%Y")
            }
            if site_enrollments.get(course_id_str):
                course_users = site_enrollments.get(course_id_str).get("users")
                course_users.append(user_dict)
            else:
                course_users = [user_dict]

            site_enrollments.update({
                course_id_str: {
                    "course_name": course.display_name_with_default,
                    "users": course_users
                }
            })
            passed_due_dates_site_users.update({
                site.domain: site_enrollments
            })
        else:
            new_site_enrollments = {
                course_id_str: {
                    "course_name": course.display_name_with_default,
                    "users": [
                        {
                            "username": enrollment.username,
                            "full_name": enrollment.user.first_name + " " + enrollment.user.last_name,
                            "email": enrollment.user.email,
                            "due_date": due_date.strftime("%m/%d/%Y")
                        }
                    ]
                }
            }
            passed_due_dates_site_users.update({
                site.domain: new_site_enrollments
            })

    def _log_final_report(self, total_enrollments, incomplete_enrollments, near_due_dates_users_count, student_failed_notifications_emails):
        logger.info('\n\n\n')
        logger.info("--------------------- DUE DATES EMAILS STATS - {} ---------------------".format(
            datetime.now().date().strftime("%m-%d-%Y")
        ))
        logger.info('Total number of mandatory courses active enrollments: {}'.format(total_enrollments))
        logger.info('Total number of incomplete enrollments: {}'.format(incomplete_enrollments))
        logger.info('Total number of users who need to be informed about approaching due dates: {}'.format(near_due_dates_users_count))
        logger.info(
            'Total number of failures which were encountered while sending emails to students: {}'.format(
                len(student_failed_notifications_emails)
            )
        )
        logger.info('Emails of students who did not receive emails due to system exception: {}'.format(student_failed_notifications_emails))


    def handle(self, *args, **options):
        incomplete_enrollments = 0
        near_due_dates_users_count = 0
        student_failed_notifications_emails = []
        passed_due_dates_site_users = {}
        mandatory_courses_enrollments = self._get_mandatory_courses_enrollments()

        # mock request factory to send emails
        request = RequestFactory().get(u'/')

        for enrollment in mandatory_courses_enrollments:
            logger.info("------> {} Checking for user: {}, course: {}".format(
                enrollment.id, enrollment.user.email, six.text_type(enrollment.course_id))
            )
            try:
                request.user = enrollment.user

                # check if course completed
                if(not self._is_course_completed(request, enrollment)):
                    incomplete_enrollments += 1
                    logger.info("=> Result - course not completed")

                    config = get_mandatory_courses_due_date_config(request, enrollment)
                    allotted_time = config.get("mandatory_courses_allotted_time")
                    notification_period = config.get("mandatory_courses_notification_period")
                    site = config.get("site")

                    if allotted_time and notification_period and site:
                        # calculate estimated due date for the enrollment.
                        enrollment_date = enrollment.clearesultcourseenrollment.updated_date.date()
                        due_date = enrollment_date + timedelta(days=int(allotted_time))
                        logger.info("enrollment date: {}, calculated due date: {} using site: {} config".format(
                            enrollment_date, due_date, site.domain))

                        if due_date - timedelta(days=int(notification_period)) == datetime.now().date():
                            # notification_period days are remaining in due date
                            # send due date approching emails to students
                            near_due_dates_users_count += 1
                            if not send_course_due_date_approching_email(request, config, enrollment):
                                student_failed_notifications_emails.append("{}: {}".format(
                                    enrollment.user.email, six.text_type(enrollment.course_id))
                                )

                        elif due_date + timedelta(days=1) == datetime.now().date():
                            # user hasn't completed courses in estimated due date.

                            # update passed_due_dates_site_users dict which will be used to send compiled email to
                            # local admins and superusers.
                            self._update_passed_due_dates_site_users_data(passed_due_dates_site_users, site, enrollment, due_date)
                    else:
                        logger.error("=> Mandatory courses config values are not properly set.")

                else:
                    logger.info("=> Result - course is completed")
            except Exception as e:
                logger.error("=> Error")
                logger.error(str(e))

        send_due_date_passed_email_to_admins(passed_due_dates_site_users)
        self._log_final_report(
            len(mandatory_courses_enrollments), incomplete_enrollments,
            near_due_dates_users_count, student_failed_notifications_emails
        )
