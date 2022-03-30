"""
Serializers for Clearesult v0 APIs.
"""
import six
import json
import logging

from django.conf import settings
from django.contrib.sites.models import Site
from django.contrib.auth.models import User
from django.db.models import fields, Q
from rest_framework.exceptions import NotFound
from rest_framework import serializers

from openedx.features.clearesult_features.models import (
    UserCreditsProfile, ClearesultCreditProvider, ClearesultCatalog,
    ClearesultCourse, ClearesultGroupLinkage, ClearesultGroupLinkedCatalogs,
    ClearesultSiteConfiguration, ClearesultCourseConfig, ParticipationGroupCode
)
from openedx.features.clearesult_features.api.v0.validators import validate_user_for_site
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.clearesult_features.tasks import (
    enroll_students_to_mandatory_courses,
    check_and_enroll_group_users_to_mandatory_courses
)


log = logging.getLogger(__name__)

class UserCreditsProfileSerializer(serializers.ModelSerializer):
    credit_type_details = serializers.SerializerMethodField()

    def get_credit_type_details(self, obj):
        return {
            'name': obj.credit_type.name,
            'short_code': obj.credit_type.short_code,
        }

    class Meta:
        model = UserCreditsProfile
        fields = ('id', 'credit_type', 'credit_id', 'credit_type_details')
        read_only_fields = ('credit_type_details', 'id')
        write_only_fields = ('user',)


class ClearesultCreditProviderSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClearesultCreditProvider
        fields = ('short_code', 'name', 'id')
        read_only_fields = ('id', )


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('username', 'email', 'id')


class SiteSerializer(serializers.ModelSerializer):
    default_group = serializers.SerializerMethodField()
    class Meta:
        model = Site
        fields = ('id', 'domain', 'default_group')

    def get_default_group(self, obj):
        clearesult_configuration = ClearesultSiteConfiguration.current(obj)
        if clearesult_configuration and clearesult_configuration.default_group:
            return clearesult_configuration.default_group.id


class ClearesultGroupsSerializer(serializers.ModelSerializer):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        fields = kwargs.get('context', {}).get('fields',  ['id', 'name', 'site', 'users'])

        for item in set(self.fields.keys()) - set(fields):
            self.fields.pop(item, None)

    def validate_site(self, site):
        allowed_sites = self.context.get('allowed_sites')

        # for super user allowed sites will be null - no need to validate site
        if allowed_sites:
            if site not in allowed_sites:
                raise serializers.ValidationError(
                    u'You are not authorized to perform action on this site.'
                )
        return site

    def validate(self, validated_data):
        users = validated_data.get('users')
        if users:
            if self.instance:
                site = self.instance.site
            else:
                site = validated_data.get('site')

            if not site:
                raise serializers.ValidationError("Invalid action - for users, site is a required field")

            for user in users:
                if not (validate_user_for_site(user, site)):
                    raise serializers.ValidationError("Invalid action - users must belong to the site")
        return validated_data

    def update(self, instance, validated_data):
        # if group is already linked with some catalogs and have some existing mandatory courses
        # then enroll newly added users to the mandatory courses (if not already enrolled)
        newly_added_group_users = set(validated_data.get('users', [])) - set(instance.users.all())
        if len(newly_added_group_users):
            request = self.context.get('request')
            if request:
                log.info("call Task to check and enroll newly added group users to mandatory courses.")
                check_and_enroll_group_users_to_mandatory_courses.delay(
                    instance.id, [user.id for user in newly_added_group_users], instance.site.id, request.user.id)
            else:
                log.info("Mandatory courses TASK can not be called without request.")
        return super().update(instance, validated_data)

    def to_representation(self, instance):
        response = super().to_representation(instance)

        fields = self.context.get('fields', ['id', 'name', 'site', 'users'])
        if "catalogs" in fields:
            response['catalogs'] = ClearesultMandatoryCoursesSerializer(
                ClearesultGroupLinkedCatalogs.objects.filter(group=instance), many=True).data
        if "users" in fields:
            response['users'] = UserSerializer(instance.users.all(), many=True).data

        if "site" in fields:
            response['site'] = SiteSerializer(instance.site).data

        return response

    class Meta:
        model = ClearesultGroupLinkage
        fields = '__all__'
        read_only_fields = ('id', )


class ClearesultCourseSerializer(serializers.ModelSerializer):
    course_name = serializers.SerializerMethodField()
    site = SiteSerializer()
    class Meta:
        model = ClearesultCourse
        fields = '__all__'
        extra_kwargs = {'course_name': {'read_only': True}}

    def get_course_name(self, obj):
        try:
            display_name = CourseOverview.objects.get(id=obj.course_id).display_name
        except CourseOverview.DoesNotExist:
            raise NotFound('The course id: {} does not exist.'.format(obj.course_id))
        except AssertionError as err:
            raise NotFound(err)

        return display_name


class ClearesultCatalogSerializer(serializers.ModelSerializer):
    clearesult_courses = ClearesultCourseSerializer(many=True)
    site = SiteSerializer()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        fields = kwargs.get('context', {}).get('fields')

        if fields:
            for item in set(self.fields.keys()) - set(fields):
                self.fields.pop(item, None)

    class Meta:
        model = ClearesultCatalog
        fields = '__all__'
        read_only_fields = ('id', 'site',)


class ClearesultMandatoryCoursesSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClearesultGroupLinkedCatalogs
        fields = ('id', 'catalog', 'mandatory_courses')
        read_only_fields = ('id', 'catalog')

    def to_representation(self, instance):
        response = super().to_representation(instance)
        response['catalog'] = ClearesultCatalogSerializer(instance.catalog).data
        return response

    def validate_mandatory_courses(self, mandatory_courses):
        # right now we are just using this serializer for update.
        # Below check is for future when we add post method.
        if self.instance:
            mandatory_courses_ids = self.initial_data.get('mandatory_courses')
            if len(self.instance.catalog.clearesult_courses.filter(id__in=mandatory_courses_ids)) != len(mandatory_courses):
                raise serializers.ValidationError(
                    u'courses can not be make mandatory until it is not already linked with the catalog.')
        return mandatory_courses

    def validate(self, validated_data):
        # right now we are just using this serializer for update.
        # Below check is for future when we add post method.
        if self.instance:
            allowed_sites = self.context.get('allowed_sites')

            # for super user allowed sites will be null - no need to validate site
            if allowed_sites:
                if self.instance.group.site not in allowed_sites:
                    raise serializers.ValidationError(
                        u'You are not authorized to perform action on this site.'
                    )

        return validated_data

    def update(self, instance, validated_data):
        action = self.context.get('action')

        if not action or action not in ["add", "remove"]:
            raise serializers.ValidationError(u'action field is not valid, available options are "add", "remove"')

        if action=="add":
            request = self.context.get('request')
            if request:
                log.info("Enroll existing group users to the newly added mandatory course.")
                enroll_students_to_mandatory_courses.delay(
                    [user.id for user in instance.group.users.all()],
                    [six.text_type(course.course_id) for course in validated_data.get('mandatory_courses',[])],
                    instance.group.site.id, request.user.id)
            else:
                log.info("Mandatory courses TASK can not be called without request.")
            for course in validated_data.get('mandatory_courses'):
                instance.mandatory_courses.add(course)
        else:
            for course in validated_data.get('mandatory_courses'):
                instance.mandatory_courses.remove(course)

            # delete existing mandatory courses configs.
            ClearesultCourseConfig.objects.filter(
                site=instance.group.site,
                course_id__in=[course.course_id for course in validated_data.get('mandatory_courses')]
            ).delete()

        return instance


class MandatoryCoursesConfigSerializer(serializers.ModelSerializer):
    course_name = serializers.SerializerMethodField()
    course_config = serializers.SerializerMethodField()

    class Meta:
        model = ClearesultCourse
        fields = ('id', 'course_id', 'course_name', 'course_config')

    def get_course_name(self, obj):
        try:
            display_name = CourseOverview.objects.get(id=obj.course_id).display_name
        except CourseOverview.DoesNotExist:
            raise NotFound('The course id: {} does not exist.'.format(obj.course_id))
        except AssertionError as err:
            raise NotFound(err)

        return display_name

    def get_course_config(self, obj, **kwargs):
        try:
            site_id = self.context.get('site_id')
            courseConfig = ClearesultCourseConfig.objects.get(course_id=obj.course_id, site__id=site_id)
            return {
                "id": courseConfig.id,
                "mandatory_courses_allotted_time": courseConfig.mandatory_courses_allotted_time,
                "mandatory_courses_notification_period": courseConfig.mandatory_courses_notification_period
            }
        except ClearesultCourseConfig.DoesNotExist:
            return None


class ClearesultCourseConfigSerializer(serializers.ModelSerializer):
    course_name = serializers.SerializerMethodField()

    class Meta:
        model = ClearesultCourseConfig
        fields = ('id', 'course_id', 'course_name', 'site', 'mandatory_courses_allotted_time', 'mandatory_courses_notification_period')
        read_only_fields = ('id', 'course_name')
        extra_kwargs = {
            'site': {'write_only': True},
        }

    def get_course_name(self, obj):
        try:
            display_name = CourseOverview.objects.get(id=obj.course_id).display_name
        except CourseOverview.DoesNotExist:
            raise NotFound('The course id: {} does not exist.'.format(obj.course_id))
        except AssertionError as err:
            raise NotFound(err)

        return display_name


class ParticipationGroupCodeSerializer(serializers.Serializer):
    code = serializers.CharField()

    def validate_code(self, code):
        try:
            participation = ParticipationGroupCode.objects.get(code=code)
            request = self.context.get("request")
            if not request:
                raise serializers.ValidationError(
                    u'Invalid site - missing site info in request'
                )
            if participation.group.site != request.site:
                raise serializers.ValidationError(
                    u'Invalid code - code does not exist for the given site'
                )
            return code

        except ParticipationGroupCode.DoesNotExist:
            raise serializers.ValidationError(
                u'Invalid code - participation code does not exist'
            )

    def create(self, validated_data):
        return ParticipationGroupCode.objects.get(code=validated_data.get("code"))
