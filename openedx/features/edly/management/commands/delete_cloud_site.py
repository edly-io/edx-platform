"""
A management command to update formus sites
"""
# pylint: disable=broad-except

import logging

from django.db import transaction
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.contrib.sites.models import Site

from cms.djangoapps.contentstore.management.commands.delete_course import (
    Command as DeleteCourseCommand
)
from openedx.features.edly.models import EdlySubOrganization
from figures.sites import get_course_keys_for_site
from openedx.features.edly.api.v1.helper import get_users_for_site

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Custom management command for the deletion of a multisite. 
    """
    help = 'Update Forums Sites'

    def add_arguments(self, parser):
        """
        Sending --apply argument with management command will also update the database,
        Otherwise It's generate report only.
        """
        parser.add_argument(
            '--site',
            '-a',
            default='',
            help='Name of the site.',
        )

    def _delete_users(self, user_obj):
        """
        Delete users using the existing edx-platform command.
        """
        for user in user_obj:
            if user.get('site_count', 1) < 2: 
                username = user.get('user__username', '')
                email = user.get('user__email', '')
                try:
                    call_command(
                        'manage_user', 
                        username,
                        email,
                        '--remove',
                    )
                    logger.info(f"Successfully deleted user: {user.get('user__id', '')}, {email}")
                except Exception as e:
                    logger.exception(f"Failed to delete user {username}: {str(e)}")

    def _delete_courses(self, site):
        """
        Delete courses using the imported delete_course command.
        """
        course_ids = get_course_keys_for_site(site)
        delete_course_cmd = DeleteCourseCommand()
        delete_course_cmd.stdout = self.stdout
        
        for course_id in course_ids:
            try:
                delete_course_cmd.handle(
                    course_key=str(course_id),
                    force=True,
                    remove_assets=True,
                    keep_instructors=False
                )
                logger.info(f"Successfully deleted course: {course_id}")
            except Exception as e:
                logger.info(f"Failed to delete course {course_id}: {str(e)}")

    def delete_django_sub_org(self, sub_org):
        """
        Delete the Django sub organization.
        """
        count = EdlySubOrganization.objects.filter(edly_organization=sub_org.edly_organization).count()
        if count == 1:
            sub_org.edly_organization.delete()

        sub_org.lms_site.delete()
        sub_org.studio_site.delete()
        sub_org.preview_site.delete()    
        # sub_org.edx_organization.delete()
        sub_org.delete()

    def _delete_site_data(self, site, edly_sub_org):
        """
        Delete all the site data for a give site configuration. 
        """
        with transaction.atomic():
            self._delete_courses(site)
            user_obj = get_users_for_site(edly_sub_org)
            self._delete_users(user_obj)
            self.delete_django_sub_org(edly_sub_org)

    def handle(self, *args, **options):
        """Deletion of site, that are passed from the site"""
        try:
            site = options.get('site')
            logger.info(f"Deleting current site: {site}")
            site = Site.objects.get(domain=site)
            sub_org = EdlySubOrganization.objects.get(lms_site_id=site.id)
            if not sub_org:
                raise CommandError(f"sub-organzation not found: {site}")
            
            self._delete_site_data(site, sub_org)
            
        except Exception as e:
            raise CommandError(f"Failed to delete site: {str(e)}")
