"""
Clearesult Models.
"""
import collections
import logging

from config_models.models import ConfigurationModel
from fernet_fields import EncryptedField
from django.db import models
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from opaque_keys.edx.django.models import CourseKeyField
from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from student.models import CourseEnrollment
from jsonfield.fields import JSONField

logger = logging.getLogger(__name__)

APP_LABEL = 'clearesult_features'


class EncryptedTextField(EncryptedField, models.CharField):
    description = "Encrypted Char Field"


class ClearesultCreditProvider(models.Model):
    class Meta:
        app_label = APP_LABEL

    name = models.CharField(max_length=255, unique=True)
    short_code = models.CharField(max_length=25, unique=True)

    def __str__(self):
        return self.name


class ClearesultCourseCredit(models.Model):
    class Meta:
        app_label = APP_LABEL
        unique_together = (
            ('credit_type', 'course_id')
        )

    credit_type = models.ForeignKey(ClearesultCreditProvider, on_delete=models.CASCADE)
    credit_value = models.DecimalField(decimal_places=2, max_digits=4,
                                       validators=[MinValueValidator(0.0), MaxValueValidator(10.0)])
    course_id = CourseKeyField(max_length=255, db_index=True)

    def __str__(self):
        return str(self.course_id) + ' ' + str(self.credit_type.short_code) + ' ' + str(self.credit_value)


class UserCreditsProfile(models.Model):
    class Meta:
        app_label = APP_LABEL
        unique_together = (
            ('user', 'credit_type')
        )

    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE, related_name='user_credit_profile')
    credit_type = models.ForeignKey(ClearesultCreditProvider, on_delete=models.CASCADE)
    credit_id = models.CharField(max_length=255)
    earned_course_credits = models.ManyToManyField(ClearesultCourseCredit, related_name='earned_credits', blank=True)

    def __str__(self):
        return str(self.user.username) + ' ' + str(self.credit_type.short_code) + ' ' + str(self.credit_id)

    def courses(self):
        return [credit.course_id for credit in self.earned_course_credits.all()]

    def earned_credits(self):
        return [credit.credit_value for credit in self.earned_course_credits.all()]

    def total_credits(self):
        total = 0.0
        for credit in self.earned_course_credits.all():
            total = total + float(credit.credit_value)
        return total


class ClearesultUserProfile(models.Model):
    class Meta:
        app_label = APP_LABEL
        verbose_name_plural = 'Clearesult user profiles'

    user = models.OneToOneField(User, unique=True, db_index=True,
                                related_name='clearesult_profile', on_delete=models.CASCADE)
    site_identifiers = models.CharField(max_length=255, blank=True)
    # * Drupal is sending affiliation ids of users in "jobTitle" field through azure ad b2c
    # we're saving that in the site_identifiers field. The data is received in this format:
    # "clearesult,bayren,clearesultpowerandlight"
    # BUT we save it in this format, adding an extra comma at the end
    # to avoid any error in search query
    # "clearesult,bayren,clearesult,"
    #
    # We are doing so because if we perform icontains query on "clearsultpowerandlight,bayren"
    # it would return data BUT in actual we are looking specifically for "clearesult,bayren"
    #
    # Adding "," we will perform search using "clearesult," as a key
    # To achieve this, we need to make sure that the site_identifiers always have an extra comma at the end
    # to do so we have overridden the save method
    # ans some extra methods including
    # def has_identifier():
    # &
    # def get_site_related_profiles():
    company = models.CharField(max_length=255, blank=True)
    state_or_province = models.CharField(max_length=255, blank=True)
    postal_code = models.CharField(max_length=50, blank=True)
    extensions = JSONField(
        null=False,
        blank=True,
        default=dict,
        load_kwargs={'object_pairs_hook': collections.OrderedDict}
    )

    def __str__(self):
        return 'Clearesult user profile for {}.'.format(self.user.username)

    def get_associated_sites(self):
        # To avoid circular import,
        from  openedx.features.clearesult_features.utils import get_affiliation_information
        sites = []
        site_identifiers = self.get_identifiers()
        for site_identifier in site_identifiers:
            affiliation_information = get_affiliation_information(site_identifier)
            site = Site.objects.get(id=affiliation_information.get('site_id'))
            sites.append(site)

        return sites


    @staticmethod
    def get_site_related_profiles(site_name, select_related_users=True):
        site_name = '{},'.format(site_name)
        if select_related_users:
            return ClearesultUserProfile.objects.filter(site_identifiers__icontains=site_name).select_related("user")

        return ClearesultUserProfile.objects.filter(site_identifiers__icontains=site_name)

    def save(self, *args, **kwargs):
        # modify site_identifiers
        site_identifiers = self.site_identifiers.strip()
        if site_identifiers and not site_identifiers.endswith(','):
            self.site_identifiers = site_identifiers + ','

        if not self.extensions:
            self.extensions = collections.OrderedDict()

        super(ClearesultUserProfile, self).save(*args, **kwargs)

    def has_identifier(self, identifier):
        if '{},'.format(identifier) in self.site_identifiers:
            return True
        return False

    def get_identifiers(self):
        identifiers = self.site_identifiers.split(',')
        return list(filter(lambda a: a != '', identifiers))

    def get_extension_value(self, name, default=None):
        try:
            return self.extensions.get(name, default)
        except AttributeError as error:
            logger.exception(u'Invalid JSON data. \n [%s]', error)

    def set_extension_value(self, name, value=None):
        try:
            self.extensions[name] = value
            self.save()
        except AttributeError as error:
            logger.exception(u'Invalid JSON data. \n [%s]', error)


class ClearesultUserSiteProfile(models.Model):
    """
    This model saves data for a user that is only relevant to a specific
    site.
    """
    user = models.ForeignKey(User, related_name='clearesult_site_profile', on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)

    saved_security_code = EncryptedTextField(max_length=20, verbose_name='Saved site security code')

    class Meta:
        unique_together = ('user', 'site')

    def __str__(self):
        return '{} - {}'.format(self.site, self.user)


class ClearesultCourse(models.Model):
    """
    This model saves clearesult course type.
    Clearesult courses can have following types:
    - Public (site value will be null)
    - Private to specific site
    """

    course_id = CourseKeyField(max_length=255, db_index=True, unique=True)
    site = models.ForeignKey(Site, on_delete=models.CASCADE, null=True, blank=True)
    is_event = models.BooleanField(default=False, verbose_name='Is Event')
    meta_tags = models.CharField(max_length=255, default='')

    class Meta:
        app_label = APP_LABEL
        verbose_name_plural = 'Clearesult Courses'

    def __str__(self):
        return '{} - {}'.format( self.course_id, self.site)

    def get_meta_tags(self):
        if self.meta_tags.strip() == "":
            return []
        return self.meta_tags.split(',')


class ClearesultCatalog(models.Model):
    """
    This model saves clearesult catalogs.
    Clearesult Catalogs has following types types:
    - Public (site value will be null, public catalog can only contain public courses)
    - Private to specific site (can only contain local/private courses linked to that site)
    """

    name = models.CharField(max_length=255)
    site = models.ForeignKey(Site, on_delete=models.CASCADE, null=True, blank=True)
    clearesult_courses = models.ManyToManyField(ClearesultCourse, related_name='courses', blank=True)

    class Meta:
        app_label = APP_LABEL
        verbose_name_plural = 'Clearesult Catalogs'
        unique_together = (
            ('name', 'site')
        )

    def __str__(self):
        return '{} - {}'.format( self.site, self.name)


class ClearesultGroupLinkage(models.Model):
    """
    This model saves clearesult user groups and catalogs assigned to that group.

    Each user group will be linked to specific site and can be linked to any public catalogs
    or local catalogs linked to it's site e.g. site_b user_groups can not be linked with site_a catalogs
    """

    name = models.CharField(max_length=255)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    users =  models.ManyToManyField(User, blank=True)
    catalogs = models.ManyToManyField(
        ClearesultCatalog, related_name='linked_catalogs', blank=True, through='ClearesultGroupLinkedCatalogs')

    class Meta:
        app_label = APP_LABEL
        verbose_name_plural = 'Clearesult User Groups'
        unique_together = (
            ('name', 'site')
        )

    def __str__(self):
        return '{} - {}'.format( self.site, self.name)


class ClearesultSiteConfiguration(ConfigurationModel):
    KEY_FIELDS = ('site', )

    site = models.ForeignKey(Site, related_name='clearesult_configuration', on_delete=models.CASCADE)

    security_code_required = models.BooleanField(default=True)
    security_code = EncryptedTextField(max_length=20, verbose_name="Site security code", null=True, blank=True)
    participation_code_required = models.BooleanField(default=False)
    default_group = models.ForeignKey(ClearesultGroupLinkage, null=True, blank=True, on_delete=models.SET_NULL, default=None)
    mandatory_courses_allotted_time = models.IntegerField(blank=True, null=True, default=20)
    mandatory_courses_notification_period = models.IntegerField(blank=True, null=True, default=2)
    courses_notification_period = models.IntegerField(blank=True, null=True, default=2)
    events_notification_period = models.IntegerField(blank=True, null=True, default=2)

    class Meta:
        app_label = APP_LABEL
        verbose_name_plural = 'Clearesult Site Configurations'

    def __str__(self):
        return '"{}" configurations'.format(self.site)


class ClearesultGroupLinkedCatalogs(models.Model):
    """
    This model saves mandatory courses list of catalogs assigned to groups.
    """
    catalog = models.ForeignKey(ClearesultCatalog, on_delete=models.CASCADE)
    group = models.ForeignKey(ClearesultGroupLinkage, on_delete=models.CASCADE)
    mandatory_courses = models.ManyToManyField(ClearesultCourse, related_name='mandatory_courses', blank=True)

    class Meta:
        app_label = APP_LABEL
        verbose_name_plural = 'Clearesult Group Catalogs Mandatory Courses'

class ClearesultLocalAdmin(models.Model):
    """
    This model saves clearesult local admin.
    """

    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    user =  models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        app_label = APP_LABEL
        verbose_name_plural = 'Clearesult Local Admin'
        unique_together = (
            ('site', 'user')
        )

    def __str__(self):
        return '{} - {}'.format( self.site, self.user)


class ClearesultUserSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    session_key = models.CharField(max_length=255, blank=True)

    class Meta:
        app_label = APP_LABEL
        verbose_name_plural = 'Clearesult User Session'
        unique_together = (
            ('user', 'session_key')
        )

    def __str__(self):
        return '{} - ({})'.format( self.user.email, self.session_key)


class ClearesultCourseCompletion(models.Model):
    """
    This model saves the course completion information of user.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    course_id = CourseKeyField(max_length=255, db_index=True)
    completion_date = models.DateTimeField(blank=True, null=True)
    pass_date = models.DateTimeField(blank=True, null=True)

    class Meta:
        app_label = APP_LABEL
        unique_together = (
            ('course_id', 'user')
        )


class ClearesultCourseConfig(models.Model):
    """
    This model saves the course configs on different sites.

    Mandatory Courses due dates can be managed as follows
    - site default configs in ClearesultSiteConfigurations
    - course specific configs in ClearesultCourseConfig

    Priority has been given to course specific configs but if course specific configs is not there for the mandatory
    course then site defaults will be used.
    """
    course_id = CourseKeyField(max_length=255, db_index=True)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    mandatory_courses_allotted_time = models.IntegerField(blank=True, null=True)
    mandatory_courses_notification_period = models.IntegerField(blank=True, null=True)

    class Meta:
        app_label = APP_LABEL
        unique_together = (
            ('course_id', 'site')
        )


class ClearesultCourseEnrollment(models.Model):
    """
    This model will save the enrollment date.
    """
    enrollment = models.OneToOneField(CourseEnrollment, on_delete=models.CASCADE)
    updated_date = models.DateTimeField()

    class Meta:
        app_label = APP_LABEL


class ParticipationGroupCode(models.Model):
    """
    This model will save the participation group codes.
    """
    group = models.OneToOneField(ClearesultGroupLinkage, on_delete=models.CASCADE)
    code = models.CharField(max_length=255, unique=True)

    class Meta:
        app_label = APP_LABEL


class ClearesultCourseMetaTag(models.Model):
    title = models.CharField(max_length=255, unique=True)

    def save(self, *args, **kwargs):
        self.title = self.title.strip().upper()
        super(ClearesultCourseMetaTag, self).save(*args, **kwargs)
