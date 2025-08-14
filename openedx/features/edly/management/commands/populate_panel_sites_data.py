"""
Edly's management command to populate dummy data for provided sites on given date.
"""
import logging
from datetime import datetime, timedelta
from random import choice, randint, sample, shuffle

from django.db import connection
from django.db.models import Q
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.core.management import BaseCommand, CommandError
from django.utils import timezone

from edly_panel_app.api.v1.constants import REGISTRATION_FIELDS_VALUES  # pylint: disable=no-name-in-module
from edly_panel_app.api.v1.helpers import _register_user  # pylint: disable=no-name-in-module
from edly_panel_app.models import EdlyUserActivity
from figures.models import CourseDailyMetrics, LearnerCourseGradeMetrics, SiteDailyMetrics, SiteMonthlyMetrics
from figures.sites import site_course_ids, get_organizations_for_site
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.grades.models import PersistentCourseGrade
from openedx.core.djangoapps.django_comment_common.models import assign_default_role
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview 
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from openedx.features.edly.models import EdlyMultiSiteAccess
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from student import auth
from student.helpers import AccountValidationError
from student.models import CourseAccessRole, CourseEnrollment
from student.roles import CourseInstructorRole, CourseStaffRole

logger = logging.getLogger(__name__)


class DummyDataConstants:
    """Constants for dummy data generation."""
    
    DEFAULT_PASSWORD = 'arbisoft@123.aA'
    MIN_USERS = 50
    MAX_USERS = 70
    MIN_ENROLLMENT = 20
    MAX_ENROLLMENT = 30
    MAX_USERS_PER_SITE = 220
    MAX_STAFF_USERS = 15
    MAX_COURSE_CREATER = 12
    USERS_TO_SAMPLE = 10
    STAFF_USERS_TO_CREATE = 5
    DUMMY_DATES_COUNT = 15
    RANDOM_COURSES_LIMIT = 20
    COURSE_LIMIT = 8
    
    # Activity generation constants
    HISTORICAL_YEARS = 2
    MIN_MONTHLY_ACTIVITIES = 115
    MAX_MONTHLY_ACTIVITIES = 200
    ACTIVITY_RETENTION_YEARS = 2
    
    FIRST_NAMES = [
        'john', 'emily', 'michael', 'sarah', 'david', 'lisa', 'james', 'emma', 'robert', 'olivia',
        'william', 'sophia', 'joseph', 'ava', 'daniel', 'isabella', 'matthew', 'mia', 'andrew',
        'charlotte', 'christopher', 'amelia', 'joshua', 'harper', 'ryan', 'evelyn', 'ethan', 'abigail',
        'tyler', 'madison', 'brandon', 'ella', 'nicholas', 'grace', 'nathan', 'chloe', 'logan', 'zoey',
        'gabriel', 'lily', 'justin', 'hannah', 'lucas', 'addison', 'jack', 'riley', 'aaron', 'layla',
        'christian', 'elena', 'sam', 'aubrey', 'connor', 'stella', 'hunter', 'aurora', 'ian', 'penelope',
        'carter', 'skylar', 'jordan', 'elena', 'mason', 'nova', 'luke', 'zoe', 'dylan', 'scarlett',
        'cameron', 'aria', 'xavier', 'madison', 'isaac', 'brooklyn', 'adam', 'claire', 'jason', 'nora',
        'owen', 'lucy', 'julian', 'aurora', 'leo', 'savannah', 'miles', 'hazel', 'oscar', 'violet',
        'ezra', 'aurora', 'jose', 'stella', 'calvin', 'luna', 'roman'
    ]
    
    LAST_NAMES = [
        'smith', 'jones', 'brown', 'davis', 'wilson', 'taylor', 'anderson', 'thomas', 'jackson',
        'white', 'harris', 'martin', 'thompson', 'garcia', 'martinez', 'robinson', 'clark', 'rodriguez',
        'lewis', 'lee', 'walker', 'hall', 'allen', 'young', 'hernandez', 'king', 'wright', 'lopez',
        'hill', 'scott', 'green', 'adams', 'baker', 'gonzalez', 'nelson', 'carter', 'mitchell', 'perez',
        'roberts', 'turner', 'phillips', 'campbell', 'parker', 'evans', 'edwards', 'collins', 'stewart',
        'sanchez', 'morris', 'rogers', 'reed', 'cook', 'morgan', 'bell', 'murphy', 'bailey', 'rivera',
        'cooper', 'richardson', 'cox', 'howard', 'ward', 'torres', 'peterson', 'gray', 'ramirez', 'james',
        'watson', 'brooks', 'kelly', 'sanders', 'price', 'bennett', 'wood', 'barnes', 'ross', 'henderson',
        'coleman', 'jenkins', 'perry', 'powell', 'long', 'patterson', 'hughes', 'flores', 'washington',
        'butler', 'simmons', 'foster', 'gonzales', 'bryant', 'alexander', 'russell', 'griffin', 'diaz'
    ]


class DummyUserGenerator:
    """Handles dummy user creation and management."""
    
    @staticmethod
    def generate_dummy_users():
        """Generate random number of dummy users data."""
        dummy_users = []
        users_count = randint(DummyDataConstants.MIN_USERS, DummyDataConstants.MAX_USERS)
        
        # Prepare registration fields
        registration_fields = REGISTRATION_FIELDS_VALUES.copy()
        for field in ['username', 'name', 'password', 'email', 'confirm_email']:
            registration_fields.pop(field, None)
        
        for _ in range(1, users_count):
            username = f"{choice(DummyDataConstants.FIRST_NAMES)}_{choice(DummyDataConstants.LAST_NAMES)}"
            dummy_users.append({
                'username': username,
                'email': f'{username}@example.com',
                'name': username,
                'password': DummyDataConstants.DEFAULT_PASSWORD,
                **registration_fields,
            })
        
        return dummy_users
        
    @staticmethod
    def generate_random_date(reference_date, max_months_ago=7):
        """
        Generate a random date that is between 0 and max_months_ago months before the reference date.
        
        Args:
            reference_date (datetime): The reference date to generate a date before
            max_months_ago (int): Maximum number of months to go back
            
        Returns:
            datetime: A randomly generated date
        """
        # Calculate a random month offset (0 to max_months_ago-1)
        month_offset = randint(0, max_months_ago-1)
        
        # Calculate the year and month for this date
        if reference_date.month - month_offset <= 0:
            # Handle wraparound to previous year
            year_offset = ((reference_date.month - month_offset - 1) // 12) - 1
            month = reference_date.month - month_offset + (abs(year_offset) * 12)
            year = reference_date.year + year_offset
        else:
            month = reference_date.month - month_offset
            year = reference_date.year
            
        # Calculate days in month
        if month == 2:
            # Simple leap year check
            if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
                days_in_month = 29
            else:
                days_in_month = 28
        elif month in [4, 6, 9, 11]:
            days_in_month = 30
        else:
            days_in_month = 31
            
        # Generate random day in month
        day = randint(1, days_in_month)
        
        # Generate random time
        hour = randint(0, 23)
        minute = randint(0, 59)
        second = randint(0, 59)
        
        # Create the date
        return datetime(year=year, month=month, day=day, hour=hour, minute=minute, second=second)
    
    @staticmethod
    def generate_random_login_date(join_date, reference_date=None):
        """
        Generate a random login date that occurs after the join date but before the reference date.
        
        Args:
            join_date (datetime): The user's join date
            reference_date (datetime, optional): The reference date (defaults to current time)
            
        Returns:
            datetime: A random login date after join_date but before reference_date
        """
        if reference_date is None:
            reference_date = datetime.now()  # Use current time instead of today to include time component
            
        # Ensure reference_date is after join_date
        if reference_date <= join_date:
            return join_date  # Can't login before joining
            
        # Calculate seconds difference between join date and reference date
        time_diff_seconds = int((reference_date - join_date).total_seconds())
        
        if time_diff_seconds <= 0:
            return join_date
        
        # Generate a random number of seconds to add to join_date
        # This ensures login is always after join date but before reference date
        random_seconds = randint(1, time_diff_seconds)
        login_date = join_date + timedelta(seconds=random_seconds)
        
        return login_date
    
    @staticmethod
    def distribute_user_dates_randomly(edly_sub_org, months=7):
        """
        Distribute all users of a site randomly over the last N months by changing their date_joined field.
        Also adds a random last_login date that occurs after the join date.
        
        Args:
            edly_sub_org: The edly sub organization to get users from
            months: Number of months to distribute over (default: 7)
            
        Returns:
            int: Number of users whose dates were updated
        """
        # Get all users associated with this edly sub organization
        user_ids = EdlyMultiSiteAccess.objects.filter(
            sub_org=edly_sub_org
        ).values_list('user_id', flat=True)
        
        if not user_ids:
            logger.warning(f'No existing users found for edly_sub_org: {edly_sub_org}')
            return 0
        
        # Get all users
        users = get_user_model().objects.filter(id__in=user_ids)
        total_users = users.count()
        
        if total_users == 0:
            logger.warning('No users found to distribute dates')
            return 0
            
        logger.info(f'Distributing {total_users} users over the last {months} months')
        
        now = datetime.now()  # Use current time to ensure last_login is always in the past
        updated_count = 0
        
        for user in users:
            # Generate random join date
            join_date = DummyUserGenerator.generate_random_date(now, months)
            
            # Generate random login date (after join date but before current time)
            login_date = DummyUserGenerator.generate_random_login_date(join_date, now)
            
            try:
                # Update user's date_joined and last_login
                user.date_joined = join_date
                user.last_login = login_date
                user.save(update_fields=['date_joined', 'last_login'])
                updated_count += 1
            except Exception as err:
                logger.error(f'Failed to update dates for user {user.username}: {err}')
        
        logger.info(f'Successfully updated join and login dates for {updated_count}/{total_users} users')
        return updated_count
    
    @staticmethod
    def register_users(site, dummy_users):
        """Register dummy users for a specific site."""
        extra_fields = site.configuration.get_value(
            'DJANGO_SETTINGS_OVERRIDE', {}
        ).get('REGISTRATION_EXTRA_FIELDS', {})
        
        for user in dummy_users:
            try:
                logger.info(f'Registering user: {user["username"]}')
                _register_user(
                    params=user,
                    site=site,
                    site_configuration={'extra_fields': extra_fields},
                    message_context={},
                    tos_required=False,
                    skip_email=True,
                )
            except (AccountValidationError, ValidationError) as err:
                logger.info(f'Failure registering user: {user["username"]} - {err}')
            except Exception:
                logger.exception(f'Failure registering user: {user["username"]}')
    
    @staticmethod
    def get_random_existing_users(edly_sub_org, count=None):
        """
        Get random existing users from the platform for a specific edly sub organization.
        
        Args:
            edly_sub_org: The edly sub organization
            count: Number of users to return (defaults to MIN_USERS to MAX_USERS range)
        
        Returns:
            QuerySet: Random existing users
        """
        if count is None:
            count = randint(DummyDataConstants.MIN_MONTHLY_ACTIVITIES, DummyDataConstants.MAX_MONTHLY_ACTIVITIES)
        
        # Get users associated with this edly sub organization
        existing_user_ids = EdlyMultiSiteAccess.objects.filter(
            sub_org=edly_sub_org
        ).exclude(
            user__courseaccessrole__role__in=['global_course_creator', 'course_creator_group']
        ).values_list('user_id', flat=True)
        
        if not existing_user_ids:
            logger.warning(f'No existing users found for edly_sub_org: {edly_sub_org}')
            return get_user_model().objects.none()
        
        # Convert to list and sample randomly
        available_users = list(existing_user_ids)
        sample_size = min(count, len(available_users))
        selected_user_ids = sample(available_users, sample_size)
        
        logger.info(f'Selected {sample_size} random existing users for analytics processing')
        return get_user_model().objects.filter(id__in=selected_user_ids)


class MetricsGenerator:
    """Handles metrics data generation."""
    
    @staticmethod
    def get_course_id(site):
        org = list(get_organizations_for_site(site).values_list('name', flat=True))
        return list(CourseOverview.objects.filter(org__in=org).values_list('id'))


    @staticmethod
    def get_site_active_learner_counts(site, date_for):
        """
        Calculate actual site-level active learner counts based on StudentModule data.
        
        Args:
            site: Site object
            date_for: Date to calculate metrics for
            
        Returns:
            dict: Contains site-level active learner counts
        """
        try:

            # Get all course IDs for this site
            course_ids = MetricsGenerator.get_course_id(site)            
            if not course_ids:
                return {
                    'todays_active_learners_count': 0,
                    'this_month_active_learners_count': 0
                }

            # Get active learners for today across all site courses
            active_today = StudentModule.objects.filter(
                ~Q(student__courseaccessrole__role='course_creator_group'),
                student__is_staff=False,
                student__is_superuser=False,
                course_id__in=course_ids,
                modified__date=date_for.date()
            ).values_list('student__id', flat=True).distinct().count()

            # Get active learners for this month across all site courses
            active_this_month = StudentModule.objects.filter(
                ~Q(student__courseaccessrole__role='course_creator_group'),
                student__is_staff=False,
                student__is_superuser=False,
                course_id__in=course_ids,
                modified__year=date_for.year,
                modified__month=date_for.month,
            ).values_list('student__id', flat=True).distinct().count()

            return {
                'todays_active_learners_count': active_today,
                'this_month_active_learners_count': active_this_month
            }
            
        except Exception as err:
            logger.error(f'Error calculating site active learner counts for {site.domain}: {err}')
            # Fallback to reasonable defaults
            return {
                'todays_active_learners_count': 0,
                'this_month_active_learners_count': 0
            }
    
    @staticmethod
    def generate_dummy_metrics(date):
        """Generate random dates within month with dummy metrics data."""
        start_date = date
        end_date = date + timedelta(days=30)
        
        # Generate all dates in the month
        dummy_dates = [start_date]
        current_date = start_date
        while current_date != end_date:
            current_date += timedelta(days=1)
            dummy_dates.append(current_date)
        
        # Sample random dates
        sampled_dates = sample(dummy_dates, min(DummyDataConstants.DUMMY_DATES_COUNT, len(dummy_dates)))
        sampled_dates.append(end_date)
        
        metrics = []
        for date_item in sampled_dates:
            # Generate total counts first to ensure logical consistency
            total_user_count = randint(30, 100)
            course_count = randint(5, 20)
            total_enrollment_count = randint(total_user_count, total_user_count * 3)  # Users can enroll in multiple courses
            
            # Active users can't exceed total users
            todays_active_user_count = randint(5, total_user_count)
            todays_active_learners_count = randint(5, min(todays_active_user_count, total_enrollment_count))
            
            metrics.append({
                'date_for': date_item,
                'todays_active_user_count': todays_active_user_count,
                'todays_active_learners_count': todays_active_learners_count,
                'total_user_count': total_user_count,
                'course_count': course_count,
                'total_enrollment_count': total_enrollment_count,
            })
        
        return metrics
    
    @staticmethod
    def create_site_monthly_metrics(site, populate_date):
        """Create site monthly metrics."""
        logger.info('Saving Site Monthly Metrics')
        # Get a realistic active user count based on the total user count from daily metrics
        # Use the most recent daily metrics if available
        try:
            latest_daily = SiteDailyMetrics.objects.filter(site=site).order_by('-date_for').first()
            if latest_daily:
                total_users = latest_daily.total_user_count
                active_users = randint(total_users // 4, total_users // 2)  # 25-50% of users are active monthly
            else:
                active_users = randint(10, 30)
        except:
            active_users = randint(10, 30)
            
        smm, created = SiteMonthlyMetrics.objects.update_or_create(
            month_for=populate_date,
            site=site,
            defaults={'active_user_count': active_users},
        )
        # No need to call save() after update_or_create
        if created:
            logger.info(f'Created new monthly metrics for site: {site.domain}')
        else:
            logger.info(f'Updated existing monthly metrics for site: {site.domain}')
    
    @staticmethod
    def create_site_daily_metrics(site, dates, use_real_active_counts=True):
        """
        Create site daily metrics.
        
        Args:
            site: Site object
            dates: List of date dictionaries with metrics
            use_real_active_counts: If True, calculate real active learner counts from StudentModule data
        """
        for date in dates:
            logger.info('Saving Site Daily Metrics')
            
            # Optionally replace with real active learner counts
            if use_real_active_counts:
                try:
                    real_counts = MetricsGenerator.get_site_active_learner_counts(site, date['date_for'])
                    date['todays_active_learners_count'] = real_counts['todays_active_learners_count']
                    logger.info(f'Using real active learner count: {real_counts["todays_active_learners_count"]} for {date["date_for"]}')
                except Exception as err:
                    logger.warning(f'Failed to get real active counts, using dummy data: {err}')
            
            sdm, created = SiteDailyMetrics.objects.update_or_create(
                date_for=date['date_for'],
                site_id=site.id,
                defaults=date,
            )
            # No need to call save() after update_or_create
            if created:
                logger.info(f'Created new daily metrics for {date["date_for"]}')
            else:
                logger.info(f'Updated existing daily metrics for {date["date_for"]}')


class CourseMetricsGenerator:
    """Handles course-specific metrics and enrollment."""
    
    @staticmethod 
    def get_letter_grade(point):
        """give a grade, return letter grade based on absolute grading."""
        letter_grade = 'A'
        if point<50:
            letter_grade = 'F'
        elif point <65:
            letter_grade = 'D'
        elif point < 80:
            letter_grade = 'C'
        elif point< 90:
            letter_grade = 'B'
        
        return letter_grade

    @staticmethod
    def create_student_activity_entries(user, course_id, enrollment_date, current_date, completion_date=None):
        """
        Create StudentModule entries to simulate student activity for analytics.
        
        This creates entries that will be picked up by the get_active_learner_ids_this_month
        function which looks for StudentModule records with modified timestamps in the current month.
        
        Args:
            user: The user to create activity for
            course_id: The course ID 
            enrollment_date: When the user enrolled
            current_date: Current processing date
            completion_date: When the user completed the course (optional)
        """
        try:
            from opaque_keys.edx.locator import BlockUsageLocator
            
            # Create some common module types that students interact with

            module_types = ['sequential', 'course']
            # Determine activity end date - use completion date if available, otherwise current date
            activity_end = completion_date if completion_date else current_date
            activity_start = enrollment_date

            # Create 1-4 StudentModule entries per user to simulate realistic activity
            num_activities = randint(1, 3)

            for i in range(num_activities):
                # Generate a random activity date between enrollment and activity_end
                if activity_start < activity_end:
                    time_diff_seconds = int((activity_end - activity_start).total_seconds())
                    if time_diff_seconds > 0:
                        random_seconds = randint(1, time_diff_seconds)
                        activity_timestamp = activity_start + timedelta(seconds=random_seconds)
                    else:
                        activity_timestamp = activity_start
                else:
                    activity_timestamp = activity_start
                
                # Choose a random module type
                module_type = choice(module_types)
                
                # Create a fake module_state_key (usage_id)
                # This simulates a block within the course
                fake_block_id = f"block-v1:{course_id.org}+{course_id.course}+{course_id.run}+type@{module_type}+block@{randint(1000, 9999)}"
                
                try:
                    module_state_key = BlockUsageLocator.from_string(fake_block_id)
                except:
                    # Fallback if there's an issue with the locator
                    continue
                
                defaults={
                        'module_type': module_type,
                        'state': '{"progress": 1}',  # Simple state indicating completion
                        'grade': randint(70, 100) if module_type == 'problem' else None,
                        'max_grade': 100 if module_type == 'problem' else None,
                        'done': choice(['f', 'i', 'na'])
                }
                if defaults['grade']:
                    defaults['done'] = 'f'
                
                # Create or update StudentModule entry
                student_module, created = StudentModule.objects.update_or_create(
                    student=user,
                    course_id=course_id,
                    module_state_key=module_state_key,
                    defaults=defaults
                )
                
                # Update the modified timestamp to the activity timestamp using raw SQL
                # since modified field has auto_now=True
                if created or student_module.modified < activity_timestamp:
                    cursor = connection.cursor()
                    cursor.execute(
                        "UPDATE courseware_studentmodule SET modified = %s WHERE id = %s",
                        [activity_timestamp, student_module.id]
                    )
                    
                    # Also ensure recent activity for "this month" analytics
                    # Create some activity in the current month for active learner tracking
                    if i < 2:  # Create 1-2 activities in the current month
                        current_month_start = current_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                        current_month_activity = current_month_start + timedelta(
                            days=randint(0, current_date.day - 1),
                            hours=randint(0, 23),
                            minutes=randint(0, 59)
                        )
                        
                        cursor.execute(
                            "UPDATE courseware_studentmodule SET modified = %s WHERE id = %s",
                            [current_month_activity, student_module.id]
                        )
            
            logger.info(f'Created {num_activities} StudentModule activity entries for {user.username} in {course_id}')
            
        except Exception as err:
            logger.error(f'Error creating StudentModule entries for {user.username}: {err}')

    @staticmethod
    def get_active_learner_counts(course_id, date_for):
        """
        Calculate actual active learner counts based on StudentModule data.
        This mimics the logic used in the actual analytics functions.
        
        Args:
            course_id: Course ID to check
            date_for: Date to calculate metrics for
            
        Returns:
            dict: Contains active_learners_today and active_learners_this_month counts
        """
        try:
            
            # Get active learners for today (StudentModule modified today)
            active_today = StudentModule.objects.filter(
                ~Q(student__courseaccessrole__role='course_creator_group'),
                ~Q(student__courseaccessrole__role='global_course_creator'),
                student__is_staff=False,
                student__is_superuser=False,
                course_id=course_id,
                modified__date=date_for.date()
            ).values_list('student__id', flat=True).distinct().count()
            
            # Get active learners for this month (StudentModule modified this month)
            active_this_month = StudentModule.objects.filter(
                ~Q(student__courseaccessrole__role='course_creator_group'),
                ~Q(student__courseaccessrole__role='global_course_creator'),
                student__is_staff=False,
                student__is_superuser=False,
                course_id=course_id,
                modified__year=date_for.year,
                modified__month=date_for.month,
            ).values_list('student__id', flat=True).distinct().count()
            
            return {
                'active_learners_today': active_today,
                'active_learners_this_month': active_this_month
            }
            
        except Exception as err:
            logger.error(f'Error calculating active learner counts for {course_id}: {err}')
            # Fallback to reasonable defaults
            return {
                'active_learners_today': 0,
                'active_learners_this_month': 0
            }

    @staticmethod
    def create_activity_for_all_enrolled_users(course_id, site):
        """
        Create StudentModule activity entries for all enrolled users in a course.
        This ensures analytics data is consistent regardless of when users were enrolled.
        
        Args:
            course_id: Course ID to process
            site: Site object
        """
        try:
            today = timezone.now()
            
            # Get all active enrollments for this course
            enrollments = CourseEnrollment.objects.filter(
                course_id=course_id,
                is_active=True
            ).select_related('user')
            
            if not enrollments.exists():
                logger.info(f'No enrollments found for course {course_id}')
                return
            
            logger.info(f'Creating StudentModule activity entries for {enrollments.count()} enrolled users in course {course_id}')
            
            # pick random enrolment number of enrolments
            random_enrollment_count = randint(1, enrollments.count())
            random_enrollments = sample(list(enrollments), random_enrollment_count)
            for enrollment in random_enrollments:
                try:
                    # Check if user already has StudentModule entries for this course
                    existing_modules = StudentModule.objects.filter(
                        student=enrollment.user,
                        course_id=course_id
                    ).count()
                    
                    # Only create entries if user doesn't have sufficient activity data
                    if existing_modules < 3:  # Ensure minimum 3 activity entries per user
                        # Get completion date if user has completed the course
                        completion_date = None
                        try:
                            grade = PersistentCourseGrade.objects.filter(
                                user_id=enrollment.user.id,
                                course_id=course_id,
                                passed_timestamp__isnull=False
                            ).first()
                            if not grade:
                                completion_date = grade.passed_timestamp
                                continue
                        except:
                            pass
                        
                        CourseMetricsGenerator.create_student_activity_entries(
                            enrollment.user, 
                            course_id, 
                            enrollment.created, 
                            today,
                            completion_date
                        )
                    else:
                        logger.debug(f'User {enrollment.user.username} already has {existing_modules} StudentModule entries for course {course_id}')
                        
                except Exception as err:
                    logger.error(f'Failed to create StudentModule entries for user {enrollment.user.username} in course {course_id}: {err}')
                    
        except Exception as err:
            logger.error(f'Error creating activity for enrolled users in course {course_id}: {err}')

    @staticmethod
    def enroll_users_in_courses(users, site, course_ids):
        """
        Enroll users in the provided courses.
        
        Args:
            users: The users to enroll
            site: The site to associate with
            course_ids: The course IDs to enroll in
        """
        if not course_ids:
            logger.info('No course IDs provided, skipping user enrollment')
            return
        
        # Initialize metrics dictionary
        course_metrics_dict = {}
            
        # Convert users to list for sampling
        users_list = list(users)
        courses = [{'id': course_id} for course_id in course_ids]
        
        for course in courses:
            course_id_str = course['id']
            course_id = CourseKey.from_string(course_id_str)
            
            # Get metrics for this course if available
            course_metric = course_metrics_dict.get(course_id_str, {})
            
            # Get the target enrollment count from metrics
            target_enrollment_count = course_metric.get('enrollment_count', randint(DummyDataConstants.USERS_TO_SAMPLE, DummyDataConstants.MAX_ENROLLMENT))
            
            # Check existing enrollments
            existing_enrollments = CourseEnrollment.objects.filter(
                course_id=course_id,
                is_active=True
            ).count()

            if existing_enrollments >= DummyDataConstants.MIN_ENROLLMENT or existing_enrollments > target_enrollment_count:
                logger.info(f'Course {course_id}: Already has {existing_enrollments} enrollments, which meets or exceeds minimum {DummyDataConstants.MIN_ENROLLMENT}. Skipping new enrollments.')
                continue

            logger.info(f'Course {course_id}: Target enrollments: {target_enrollment_count}, Existing enrollments: {existing_enrollments}')
            
            # Calculate how many new enrollments we need
            additional_enrollments_needed = randint(0, target_enrollment_count - existing_enrollments)
            
            if additional_enrollments_needed <= 0:
                logger.info(f'Course {course_id}: Already has {existing_enrollments} enrollments, which meets or exceeds target {target_enrollment_count}. Skipping new enrollments.')
                continue
            
            logger.info(f'Course {course_id}: Creating {additional_enrollments_needed} new enrollments to meet target of {target_enrollment_count}')
            
            # Sample users for new enrollments, avoiding users already enrolled
            # First, get existing enrolled users
            enrolled_user_ids = set(CourseEnrollment.objects.filter(
                course_id=course_id,
                is_active=True
            ).values_list('user_id', flat=True))
            
            # Filter out already enrolled users
            available_users = [user for user in users_list if user.id not in enrolled_user_ids]
            
            if not available_users:
                logger.warning(f'Course {course_id}: No available users for enrollment. All users may already be enrolled.')
                continue
            
            # Sample from available users
            users_to_enroll = sample(
                available_users,
                min(additional_enrollments_needed, len(available_users))
            )
            
            # If we have metrics, use the completion_percentage to determine how many users should pass
            completion_percentage = course_metric.get('completion_percentage', randint(50, 90))
            
            # Get the target number of completions directly from the metrics
            target_completions = course_metric.get('num_learners_completed', 
                                                   int((completion_percentage / 100) * target_enrollment_count))
            
            # Check how many users already have passing grades (passed_timestamp is not null)
            existing_passed = PersistentCourseGrade.objects.filter(
                course_id=course_id,
                passed_timestamp__isnull=False
            ).count()
            
            # Calculate how many more users need to pass to meet the target
            additional_passing_needed = max(0, target_completions - existing_passed)
            logger.info(f'Course {course_id}: Target completions: {target_completions}, Existing passed: {existing_passed}, Additional needed: {additional_passing_needed}')
            
            # Make sure we don't try to pass more users than we're enrolling
            users_to_complete = min(additional_passing_needed, len(users_to_enroll))
            
            # Create a list where the first users_to_complete will pass
            passing_status = [True] * users_to_complete + [False] * (len(users_to_enroll) - users_to_complete)
            # Shuffle to randomize which users pass
            shuffle(passing_status)
            
            # Track actual completions for verification
            actual_completions = 0
            
            for i, user in enumerate(users_to_enroll):
                try:
                    logger.info(f'Enrolling user {user.username} in course {str(course_id)}')
                    
                    # Enroll user
                    enrollment = CourseEnrollment.enroll(user, course_id)
                    
                    # Generate a random enrollment date (between user's join date and today)
                    # Get user's join date to ensure enrollment is after user registration
                    user_join_date = user.date_joined
                    today = timezone.now()
                    
                    # Calculate random enrollment date between user join date and today
                    if user_join_date < today:
                        time_diff_seconds = int((today - user_join_date).total_seconds())
                        if time_diff_seconds > 0:
                            random_seconds = randint(1, time_diff_seconds)
                            random_enrollment_date = user_join_date + timedelta(seconds=random_seconds)
                        else:
                            random_enrollment_date = user_join_date
                    else:
                        random_enrollment_date = user_join_date

                    # Update enrollment created date using raw SQL to bypass auto_now_add
                    cursor = connection.cursor()
                    cursor.execute(
                        "UPDATE student_courseenrollment SET created = %s WHERE id = %s",
                        [random_enrollment_date, enrollment.id]
                    )
                    
                    # Refresh enrollment object to get updated created date
                    enrollment.refresh_from_db()
                    
                    # Use the predefined passing status for this user
                    is_passed = passing_status[i]
                    if is_passed:
                        actual_completions += 1
                    
                    # Generate a grade consistent with the passing status
                    if is_passed:
                        percent_grade = randint(50, 100)
                    else:
                        percent_grade = randint(30, 49)

                    # Generate realistic passed_timestamp that's after enrollment date
                    passed_timestamp = None
                    if is_passed:
                        # Add random days (1-30) after enrollment for completion
                        days_to_complete = randint(1, 30)
                        passed_timestamp = enrollment.created + timedelta(days=days_to_complete)
                        if passed_timestamp > today:
                            passed_timestamp = today
                    
                    grade_params = {
                        'user_id': user.id,
                        'course_id': course_id,
                        'percent_grade': percent_grade,
                        'passed': is_passed,
                        'letter_grade': CourseMetricsGenerator.get_letter_grade(percent_grade),
                        'passed_timestamp': passed_timestamp,
                    }
                    
                    # The update_or_create method only returns the object, not a tuple
                    grade = PersistentCourseGrade.update_or_create(
                        **grade_params,
                    )
                    # Create LearnerCourseGradeMetrics entry
                    try:
                        # Generate realistic metrics data
                        points_possible = randint(700, 1000)
                        points_earned = int(points_possible * (percent_grade / 100))
                        sections_possible = randint(8, 15)
                        sections_worked = int(sections_possible * (percent_grade / 100))
                        
                        # Create LearnerCourseGradeMetrics record
                        metrics_data = {
                            'site': site,
                            'date_for': datetime.today().date(),
                            'user': user,
                            'course_id': str(course_id),
                            'points_possible': points_possible,
                            'points_earned': points_earned,
                            'sections_worked': sections_worked,
                            'sections_possible': sections_possible,
                            'letter_grade': CourseMetricsGenerator.get_letter_grade(percent_grade),
                            'percent_grade': round(percent_grade/100, 2),
                            'total_progress_percent': round(points_earned/points_possible , 2)
                        }

                        # Add passed_timestamp if the user passed (use the same timestamp as grade)
                        if percent_grade > 50 and passed_timestamp:
                            metrics_data['passed_timestamp'] = passed_timestamp
                        
                        lcgm, created = LearnerCourseGradeMetrics.objects.update_or_create(
                            site=site,
                            user=user,
                            course_id=str(course_id),
                            date_for=datetime.today().date(),
                            defaults=metrics_data
                        )
                        
                        logger.info(f'{"Created" if created else "Updated"} LearnerCourseGradeMetrics for {user.username} in {str(course_id)}')
                    except Exception as err:
                        logger.error(f'Failed to create LearnerCourseGradeMetrics for {user.username} in {str(course_id)}: {err}')
                    
                except Exception as err:
                    logger.error(f'Failed to enroll user {user.username} in course {course_id}: {err}')

    @staticmethod
    def create_course_daily_metrics(site, course_ids):
        """Generate course daily metrics for all courses in the site or provided course IDs."""

        if not course_ids:
            logger.info('No courses found for the site, skipping course metrics generation')
            return
            
        logger.info(f'Generating metrics for {len(course_ids)} courses in site {site.domain}')
        
        today = datetime.today()
        courses = [{'id': course_id} for course_id in course_ids]
        
        for course_data in courses:
            try:
                # Use a fixed seed for consistent randomization per course
                course_id_str = str(course_data['id'])
                course_id = CourseKey.from_string(course_id_str)
                
                # Create StudentModule activity entries for all enrolled users in this course
                # This ensures analytics data is consistent regardless of enrollment timing
                # and works even when courses already have sufficient enrollments
                CourseMetricsGenerator.create_activity_for_all_enrolled_users(course_id, site)
                
                # Check for existing enrollments
                existing_enrollments = CourseEnrollment.objects.filter(
                    course_id=course_id,
                    is_active=True
                ).count()
                
                # Check for existing passed learners
                existing_passed = PersistentCourseGrade.objects.filter(
                    course_id=course_id,
                    passed_timestamp__isnull=False
                ).count()
                
                # Calculate actual active learner counts based on StudentModule data
                active_counts = CourseMetricsGenerator.get_active_learner_counts(course_id, today)

                metrics = {
                    'site': site,
                    'date_for': today,
                    'enrollment_count': existing_enrollments,
                    'active_learners_today': active_counts['active_learners_today'],
                    'active_learners_this_month': active_counts['active_learners_this_month'],
                    'average_progress': round(randint(1, 100) / 100, 2),
                    'average_days_to_complete': randint(1, 30),
                    'num_learners_completed': existing_passed
                }
                
                logger.info(f'Populating Course Daily Metrics for {course_data["id"]} with metrics: {metrics}')
                cdm, created = CourseDailyMetrics.objects.update_or_create(
                    date_for=metrics['date_for'],
                    site_id=metrics['site'].id,
                    course_id=str(course_data['id']),
                    defaults=metrics,
                )
                # No need to call save() after update_or_create as it already saves the object
                if created:
                    logger.info(f'Created new course metrics for: {course_data["id"]}')
                else:
                    logger.info(f'Updated existing course metrics for: {course_data["id"]}')
                
            except Exception as err:
                logger.error(f'Error populating Course Daily Metrics for {course_data["id"]}: {err}')


class EdlyActivityManager:
    """Manages Edly-specific activities and user management."""
    
    @staticmethod
    def get_site_user_count(sub_org):
        """Get the number of users for a given site."""
        return EdlyMultiSiteAccess.objects.filter(sub_org=sub_org).count()
    
    @staticmethod
    def cleanup_old_activities(edly_sub_org):
        """
        Delete Edly user activities older than ACTIVITY_RETENTION_YEARS.
        """
        if not edly_sub_org:
            logger.info('No Edly sub-organization provided for activity cleanup.')
            return

        cutoff_date = datetime.now().date() - timedelta(days=365 * DummyDataConstants.ACTIVITY_RETENTION_YEARS)

        old_activities = EdlyUserActivity.objects.filter(
            activity_date__lt=cutoff_date,
            edly_sub_organization=edly_sub_org
        )
        old_count = old_activities.count()
        
        if old_count > 0:
            logger.info(f'Deleting {old_count} user activities older than {cutoff_date}')
            old_activities.delete()
        else:
            logger.info(f'No activities found older than {cutoff_date}')
    
    @staticmethod
    def generate_monthly_dates(start_date, end_date):
        """
        Generate a list of dates representing each month between start_date and end_date.
        
        Args:
            start_date (datetime): Start date
            end_date (datetime): End date
            
        Returns:
            list: List of dates (one per month)
        """
        dates = []
        current_date = start_date.replace(day=1)  # Start from first day of month
        
        while current_date <= end_date:
            # Generate a random day within the month
            if current_date.month == 12:
                next_month = current_date.replace(year=current_date.year + 1, month=1)
            else:
                next_month = current_date.replace(month=current_date.month + 1)
            
            # Random day between 1st and last day of month
            last_day = (next_month - timedelta(days=1)).day
            random_day = randint(1, last_day)
            activity_date = current_date.replace(day=random_day)
            
            dates.append(activity_date)  # Keep as datetime object
            current_date = next_month
        
        return dates
    
    @staticmethod
    def add_edly_activities(users, edly_sub_org, current_date):
        """
        Add users to edly sub organization and create user activities for the last 2 years.
        
        This method:
        1. Cleans up activities older than 2 years
        2. Generates activities for each month in the last 2 years
        3. Ensures 100-200 unique users per month for the organization
        4. Skips months that already have sufficient activities
        
        Args:
            users (QuerySet): Users to create activities for
            edly_sub_org: Edly sub organization to create activities for
            current_date (datetime): The current processing date
        """
        # Clean up old activities first
        EdlyActivityManager.cleanup_old_activities(edly_sub_org)
        
        # Calculate date range for last 2 years from the provided end date
        # Make sure we don't generate future activities by capping at today's date
        today = datetime.now().date()
        end_date_as_date = current_date.date()
        if end_date_as_date > today:
            logger.warning(f'Provided end date {end_date_as_date} is in the future. Capping at today: {today}')
            end_date = datetime.combine(today, datetime.min.time())

        start_date = end_date - timedelta(days=365 * DummyDataConstants.HISTORICAL_YEARS)
        
        logger.info(f'Generating Edly activities from {start_date.strftime("%Y-%m-%d")} to {end_date.strftime("%Y-%m-%d")}')
        logger.info(f'Historical years: {DummyDataConstants.HISTORICAL_YEARS}, Activity retention: {DummyDataConstants.ACTIVITY_RETENTION_YEARS}')
        
        # Generate monthly dates
        monthly_dates = EdlyActivityManager.generate_monthly_dates(start_date, end_date)
        
        logger.info(f'Generated {len(monthly_dates)} monthly dates for processing')
        
        # Convert users QuerySet to list for sampling
        users_list = list(users)
        total_users = len(users_list)
        
        if total_users == 0:
            logger.warning('No users provided for activity generation')
            return
        
        logger.info(f'Creating activities for {len(monthly_dates)} months with {total_users} available users')
        
        logger.info(f'Processing activities for organization: {edly_sub_org}')
        
        for activity_date in monthly_dates:
            # Get the first and last day of the month for this activity_date
            first_day = activity_date.replace(day=1)
            if activity_date.month == 12:
                last_day = activity_date.replace(year=activity_date.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                last_day = activity_date.replace(month=activity_date.month + 1, day=1) - timedelta(days=1)
            
            # Check existing activities for this month and organization
            existing_count = EdlyUserActivity.objects.filter(
                edly_sub_organization=edly_sub_org,
                activity_date__gte=first_day,
                activity_date__lte=last_day
            ).count()
            
            # Check if we already have sufficient activities for this month
            if existing_count >= DummyDataConstants.MIN_MONTHLY_ACTIVITIES:
                logger.info(
                    f'Month {activity_date.strftime("%Y-%m")} already has {existing_count} activities '
                    f'(>= {DummyDataConstants.MIN_MONTHLY_ACTIVITIES}). Skipping.'
                )
                continue
            
            # Calculate how many activities we need to create
            activities_needed =  DummyDataConstants.MAX_MONTHLY_ACTIVITIES - existing_count
            
            # Don't create more activities than we have users
            activities_to_create = min(activities_needed, total_users)
            activities_to_create = randint(min(20, activities_to_create),max(20, activities_to_create))
            
            logger.info(
                f'Creating {activities_to_create} activities for {activity_date.strftime("%Y-%m")} '
                f'(existing: {existing_count}, MAX-ACITIVTY: {activities_needed})'
            )
            
            # Sample unique users for this month
            sampled_users = sample(users_list, activities_to_create)
            
            # Generate unique dates for each user to avoid duplicates
            available_days = list(range(1, last_day.day + 1))
            
            created_count = 0
            for i, user in enumerate(sampled_users):
                # Ensure user has access to the organization
                edly_access_user, _ = EdlyMultiSiteAccess.objects.get_or_create(
                    user=user, sub_org=edly_sub_org
                )
                
                try:
                    # Use different approaches based on available days vs users
                    if len(available_days) >= len(sampled_users):
                        # Enough days - assign unique days
                        random_day = available_days[i % len(available_days)]
                    else:
                        # More users than days - allow some overlap but try to spread
                        random_day = randint(1, last_day.day)
                    
                    # Create actual date from activity_date's year and month + random day
                    month_year = activity_date.strftime('%Y-%m')
                    user_activity_date = datetime.strptime(f'{month_year}-{random_day:02d}', '%Y-%m-%d').date()
                    
                    # Check if this exact user+date+org combination already exists
                    existing = EdlyUserActivity.objects.filter(
                        user=edly_access_user.user,
                        activity_date=user_activity_date,
                        edly_sub_organization=edly_sub_org
                    ).exists()
                    
                    if existing:
                        logger.debug(f'Activity already exists for user {user.username} on {user_activity_date}')
                        continue
                        
                    # Use raw SQL to bypass auto_now_add constraint only for the creation part
                    cursor = connection.cursor()
                    table_name = EdlyUserActivity._meta.db_table
                    formatted_date = user_activity_date.strftime('%Y-%m-%d')
                    
                    # Execute raw SQL INSERT to bypass auto_now_add
                    cursor.execute(
                        f"INSERT INTO {table_name} (user_id, activity_date, edly_sub_organization_id) VALUES (%s, %s, %s)",
                        [edly_access_user.user.id, formatted_date, edly_sub_org.id]
                    )
                    created_count += 1
                    logger.debug(f'Created activity for user {user.username} on {user_activity_date}')
                    
                except Exception as err:
                    logger.exception(f'Unable to add edly_user_activity for user {user.username}: {err}')
            
            logger.info(f'Successfully created {created_count} new activities for {activity_date.strftime("%Y-%m")}')
        
        logger.info('Completed generating historical Edly activities')
    
    @staticmethod
    def create_staff_users(org, users):
        """Create staff users for an organization."""
        staff_users_count = CourseAccessRole.objects.filter(
            org=org, role='global_course_creator'
        ).count()
        
        if staff_users_count > DummyDataConstants.MAX_STAFF_USERS:
            logger.info(f'Organization {org} already has enough staff users')
            return
        
        to_pick = min(DummyDataConstants.STAFF_USERS_TO_CREATE, len(users))
        indices = sample(range(len(users)), to_pick)
        
        for index in indices:
            CourseAccessRole.objects.get_or_create(
                user=users[index],
                org=org,
                role='global_course_creator',
            )
        
        logger.info(f'Created {to_pick} staff users for organization {org}')
    
    def create_course_creator(org, users):
        """Create a course creator for an organization."""
        user_id = EdlyMultiSiteAccess.objects.filter(
            sub_org__slug=org,
        ).values_list('user_id', flat=True)
        
        course_creator_count = CourseAccessRole.objects.filter(
            user__id__in=user_id,
            role='course_creator_group'
        ).count()
        
        if course_creator_count > DummyDataConstants.MAX_COURSE_CREATER:
            logger.info(f'Organization {org} already has enough course creator users')
            return

        to_pick = min(DummyDataConstants.MAX_COURSE_CREATER, len(users))
        indices = sample(range(len(users)), to_pick)
        
        for index in indices:
            CourseAccessRole.objects.get_or_create(
                user=users[index],
                role='course_creator_group',
            )

        logger.info(f'Created {to_pick} course creator users for organization {org}')


class Command(BaseCommand):
    """
    Management command to populate dummy analytics data for a single site and courses.
    
    This command creates dummy users, enrollments, metrics data, and historical user activities
    for testing and development purposes in the Edly panel system.
    
    Features:
    - Processes one site per job execution
    - Creates dummy users or uses existing users
    - Generates site and course metrics
    - Creates historical user activities for the last 2 years (100-200 users per month)
    - Automatically cleans up activities older than 2 years
    - Skips months that already have sufficient activities (100+)
    - Validates course IDs in proper OpenEdX format
    """
    
    help = (
        'Populate panel site data in edly insights for a single site and courses. '
        'Creates historical user activities for the last 2 years (100-200 per month) and cleans up old data. '
        'Safe to run multiple times - skips months with sufficient data. '
        'Processes one site per job for better resource management.'
    )

    def add_arguments(self, parser):
        """Add command line arguments."""
        parser.add_argument(
            '--site',
            default='',
            help='Single LMS site domain (required)',
            required=True,
        )
        parser.add_argument(
            '--courses',
            default='',
            help='Comma separated list of course IDs in OpenEdX course key format (e.g., course-v1:Org+Course+Run). '
                 'Course IDs will be validated for proper format.',
        )
        parser.add_argument(
            '--date',
            default=datetime.today().strftime('%m/%Y'),
            help='The month and year of the data to populate (format: mm/yyyy)',
        )

    def validate_course_keys(self, courses_list):
        """
        Validate that course IDs are in proper OpenEdX course key format.
        
        Args:
            courses_list (list): List of course ID strings
            
        Returns:
            list: List of validated CourseKey objects
            
        Raises:
            CommandError: If any course ID is invalid
        """
        validated_course_keys = []
        invalid_courses = []
        
        for course_id in courses_list:
            try:
                # Attempt to create a CourseKey from the string
                course_key = CourseKey.from_string(course_id)
                validated_course_keys.append(course_key)
            except InvalidKeyError:
                invalid_courses.append(course_id)
        
        if invalid_courses:
            raise CommandError(
                f'Invalid course key format(s): {", ".join(invalid_courses)}. '
                f'Course IDs must be in OpenEdX format (e.g., course-v1:Org+Course+Run)'
            )
        
        return validated_course_keys

    def validate_inputs(self, options):
        """Validate command inputs."""
        site_domain = options['site'].strip()
        courses_list = [course.strip() for course in options['courses'].split(',') if course.strip()] if options['courses'] else []
        
        if not site_domain:
            raise CommandError('Site domain must be provided.')
        
        # Validate course keys if provided
        validated_course_keys = []
        if courses_list:
            validated_course_keys = self.validate_course_keys(courses_list)
        
        try:
            # Parse as mm/yyyy and set day to the last day of that month to include the entire month
            date_obj = datetime.strptime(options['date'], '%m/%Y')
            if date_obj.month == 12:
                last_day = date_obj.replace(year=date_obj.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                last_day = date_obj.replace(month=date_obj.month + 1, day=1) - timedelta(days=1)
            populate_date = last_day
        except ValueError:
            raise CommandError(
                f'Invalid date format: {options["date"]}. Please provide date in mm/yyyy format.'
            )
        
        return site_domain, validated_course_keys, populate_date

    def get_site_and_organization(self, site_domain):
        """Get site object and its corresponding organization."""
        try:
            site = Site.objects.get(domain=site_domain)
        except Site.DoesNotExist:
            raise CommandError(f'Site not found in database: {site_domain}')
        
        edly_sub_org = getattr(site, 'edly_sub_org_for_lms', None)
        if not edly_sub_org:
            raise CommandError(f'No edly_sub_org found for site: {site.domain}')
        
        return site, edly_sub_org

    def get_edx_organization(self, edly_sub_org):
        """Extract EDX organization name from Edly sub organization."""
        edx_org = edly_sub_org.edx_organizations.all().first()
        if not edx_org:
            raise CommandError(f'No EDX organization found for edly_sub_org: {edly_sub_org}')
        
        return edx_org.short_name

    def process_site_data(self, site, edly_sub_org, populate_date, dummy_users):
        """
        Process site-specific data including users and metrics.
        
        Returns:
            QuerySet: User objects that can be used for course processing
        """
        dates = MetricsGenerator.generate_dummy_metrics(populate_date)
        logger.info(f'Processing site: {site.domain}')
        
        # Register users if needed, or get existing users
        current_user_count = EdlyActivityManager.get_site_user_count(edly_sub_org)
        user_objects = get_user_model().objects.none()
        
        if current_user_count < DummyDataConstants.MAX_USERS_PER_SITE:
            logger.info(f'Registering dummy users for site: {site.domain}')
            DummyUserGenerator.register_users(site, dummy_users)
            # Get newly registered users
            registered_usernames = [user['username'] for user in dummy_users]
            user_objects = get_user_model().objects.filter(username__in=registered_usernames)
        else:
            logger.info(f'Site {site.domain} already has sufficient users ({current_user_count})')
            logger.info(f'Selecting random existing users for site: {site.domain}')
            user_objects = DummyUserGenerator.get_random_existing_users(edly_sub_org)
        
        # Create metrics (site daily metrics will use real active learner counts from StudentModule data)
        MetricsGenerator.create_site_monthly_metrics(site, populate_date)
        MetricsGenerator.create_site_daily_metrics(site, dates)
        
        logger.info(f'Total users available for processing: {user_objects.count()}')
        return user_objects

    def process_course_data(self, site, course_ids, user_objects):
        """Process course-specific data including metrics and enrollments."""
        if not course_ids:
            logger.info('No course IDs provided, skipping course-specific processing')
            return
        
        logger.info(f'Processing {len(course_ids)} courses for site: {site.domain}')
        
        # First enroll users in courses
        logger.info(f'Enrolling users in {len(course_ids)} courses for site: {site.domain}')
        CourseMetricsGenerator.enroll_users_in_courses(user_objects, site, course_ids)
        
        # After enrollment, generate course metrics based on actual enrollment data
        logger.info(f'Generating metrics for courses after enrollment for site: {site.domain}')
        CourseMetricsGenerator.create_course_daily_metrics(site, course_ids)

    def process_staff_users(self, edx_organization, user_objects):
        """Create staff users for organization."""
        EdlyActivityManager.create_staff_users(edx_organization, user_objects)
    
    def process_course_creator(self, edx_organization, user_objects):
        """Create course creator for the organization."""
        EdlyActivityManager.create_course_creator(edx_organization, user_objects)


    def handle(self, *args, **options):
        """
        Main command handler.
        
        This method orchestrates the entire dummy data population process:
        1. Validates inputs
        2. Generates dummy users
        3. Processes site-specific data (users, metrics)
        4. Distributes user join dates randomly over the last 7 months
        5. Processes course-specific data (enrollments, course metrics)
        6. Creates staff users
        """
        logger.info('Starting dummy data population process')
        
        # Validate inputs
        site_domain, validated_course_keys, populate_date = self.validate_inputs(options)
        logger.info(f'Processing site: {site_domain} with {len(validated_course_keys)} courses for {populate_date.strftime("%m/%Y")}')
        
        # Convert CourseKey objects to strings for processing
        courses_list = [str(course_key) for course_key in validated_course_keys]
        
        # Get site and organization
        site, edly_sub_org = self.get_site_and_organization(site_domain)
        edx_organization = self.get_edx_organization(edly_sub_org)
        
        # Generate dummy data
        logger.info('Generating dummy users')
        dummy_users = DummyUserGenerator.generate_dummy_users()
        
        # Process site data and get user objects
        logger.info('Processing site-specific data')
        user_objects = self.process_site_data(site, edly_sub_org, populate_date, dummy_users)
        
        if not user_objects.exists():
            logger.warning('No users available for processing. Cannot proceed with course data and activities.')
            return
            
        # Distribute all users of the site randomly over the last 7 months
        logger.info('Distributing user join dates randomly over the last 7 months')
        DummyUserGenerator.distribute_user_dates_randomly(edly_sub_org)
        
        # Add Edly activities
        logger.info('Adding Edly user activities')
        EdlyActivityManager.add_edly_activities(user_objects, edly_sub_org, populate_date)
        
        # Create staff users
        logger.info('Creating staff users')
        self.process_staff_users(edx_organization, user_objects)
        
        # Creating course creator 
        logger.info('Creating course creator for the site')
        self.process_course_creator(edx_organization, user_objects)

        # Process course data
        logger.info('Processing course data')
        self.process_course_data(site, courses_list, user_objects)
        logger.info('Dummy data population completed successfully')
