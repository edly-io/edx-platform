"""
Admin registration for Clearesult.
"""
import six
from config_models.admin import KeyedConfigurationModelAdmin
from completion.models import BlockCompletion
from django.contrib.auth.models import User
from django.contrib import admin
from django.contrib import messages
from django.contrib.sites.models import Site
from django.utils.translation import ugettext_lazy as _

from openedx.features.clearesult_features.forms import UserCreditsProfileAdminForm
from openedx.features.clearesult_features.models import (
    ClearesultCourseCredit,
    ClearesultCreditProvider,
    UserCreditsProfile,
    ClearesultUserProfile,
    ClearesultSiteConfiguration,
    ClearesultUserSiteProfile,
    ClearesultGroupLinkage,
    ClearesultCatalog,
    ClearesultCourse,
    ClearesultLocalAdmin,
    ClearesultGroupLinkedCatalogs,
    ClearesultCourseCompletion,
    ClearesultCourseConfig,
    ClearesultCourseEnrollment,
    ParticipationGroupCode,
    ClearesultCourseMetaTag
)


class ClearesultCreditProviderAdmin(admin.ModelAdmin):
    """
    Admin config clearesult credit providers.
    """
    list_display = ('name', 'short_code')


class ClearesultCourseCreditsAdmin(admin.ModelAdmin):
    """
    Admin config for clearesult credits offered by the courses.
    """
    list_display = ('course_id', 'credit_type', 'credit_value')


class UserCreditsProfileAdmin(admin.ModelAdmin):
    """
    Admin config for user credit ids.
    """
    form = UserCreditsProfileAdminForm
    list_display = ('user', 'credit_type', 'credit_id', 'courses', 'earned_credits', 'total_credits')


class ClearesultSiteConfigurationAdmin(KeyedConfigurationModelAdmin):
    """
    Admin config for `ClearesultSiteConfiguration`.
    """
    search_fields = ('site__id', 'site__name')


class ClearesultUserSiteProfileAdmin(admin.ModelAdmin):
    """
    Admin config for `ClearesultUserSiteProfile`.
    """
    list_display = ('user', 'site')

class ClearesultCourseAdmin(admin.ModelAdmin):
    """
    Admin config clearesult courses.
    """
    list_display = ('course_id', 'site', 'is_event')
    list_filter = ('is_event', 'site')
    search_fields = ('course_id',)

    # disable change functionality
    def has_change_permission(self, request, obj=None):
        return False


class ClearesultCatalogAdmin(admin.ModelAdmin):
    """
    Admin config clearesult credit providers.
    """
    list_display = ('name', 'site')

class ClearesultGroupLinkageAdmin(admin.ModelAdmin):
    """
    Admin config clearesult credit providers.
    """
    list_display = ('name', 'site')

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def delete_model(self, request, obj):
        """
        Don't delete group objects linked as a default group with some site
        """
        is_default = False

        sites = Site.objects.filter(name__icontains="LMS")

        for site in sites:
            config = site.clearesult_configuration.latest('change_date')
            if config.default_group == obj:
                is_default = True
                messages.error(request, "Group is set as a default group of some site. Remove the linkage first then try again.")
                break

        if not is_default:
            super().delete_model(request, obj)

class ClearesultLocalAdminInterface(admin.ModelAdmin):
    """
    Admin config clearesult credit providers.
    """
    list_display = ('site', 'user')
    search_fields = ('site', 'user')

    # disable change functionality
    def has_change_permission(self, request, obj=None):
        return False

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "site":
            kwargs["queryset"] = Site.objects.filter(name__icontains="LMS")
        elif db_field.name == "user":
            kwargs["queryset"] = User.objects.filter(is_superuser=False, is_staff=False, is_active=True)

        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ClearesultGroupLinkedCatalogsAdmin(admin.ModelAdmin):
    """
    Admin config clearesult credit providers.
    """
    list_display = ('id', 'group', 'catalog')


class ClearesultCourseCompletionAdmin(admin.ModelAdmin):
    list_display = ('user', 'course_id', 'completion_date', 'pass_date')


class ClearesultUserProfileAdmin(admin.ModelAdmin):
    readonly_fields=('get_user_email',)
    list_display = ('user', 'get_user_email', 'site_identifiers', 'extensions')

    def get_user_email(self, obj):
        return obj.user.email

    get_user_email.short_description = 'Email'


class BlockCompleteionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'context_key', 'block_key', 'block_type', 'completion', )

class ClearesultCourseConfigAdmin(admin.ModelAdmin):
    list_display = ('id', 'course_id', 'site', 'mandatory_courses_allotted_time', 'mandatory_courses_notification_period')

class ClearesultCourseEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'get_user_email', 'get_course_id', 'enrollment', 'updated_date')
    search_fields = ('enrollment__user__email',)
    readonly_fields=('get_user_email', 'get_course_id',)

    def get_user_email(self, obj):
        return obj.enrollment.user.email

    def get_course_id(self, obj):
        return six.text_type(obj.enrollment.course_id)

    get_user_email.short_description = 'Email'
    get_course_id.short_description = 'Course'


class ParticipationGroupCodeAdmin(admin.ModelAdmin):
    list_display = ('id', 'group', 'code')
    search_fields = ('code',)


class ClearesultCourseMetaTagAdmin(admin.ModelAdmin):
    list_display = ('id', 'title')
    search_fields = ('title',)


admin.site.register(ClearesultCourseCredit, ClearesultCourseCreditsAdmin)
admin.site.register(ClearesultCreditProvider, ClearesultCreditProviderAdmin)
admin.site.register(UserCreditsProfile, UserCreditsProfileAdmin)
admin.site.register(ClearesultUserProfile, ClearesultUserProfileAdmin)
admin.site.register(ClearesultSiteConfiguration, ClearesultSiteConfigurationAdmin)
admin.site.register(ClearesultUserSiteProfile, ClearesultUserSiteProfileAdmin)
admin.site.register(ClearesultCourse, ClearesultCourseAdmin)
admin.site.register(ClearesultCatalog, ClearesultCatalogAdmin)
admin.site.register(ClearesultGroupLinkage, ClearesultGroupLinkageAdmin)
admin.site.register(ClearesultLocalAdmin, ClearesultLocalAdminInterface)
admin.site.register(ClearesultGroupLinkedCatalogs, ClearesultGroupLinkedCatalogsAdmin)
admin.site.register(ClearesultCourseCompletion, ClearesultCourseCompletionAdmin)
admin.site.register(BlockCompletion, BlockCompleteionAdmin)
admin.site.register(ClearesultCourseConfig, ClearesultCourseConfigAdmin)
admin.site.register(ClearesultCourseEnrollment, ClearesultCourseEnrollmentAdmin)
admin.site.register(ParticipationGroupCode, ParticipationGroupCodeAdmin)
admin.site.register(ClearesultCourseMetaTag, ClearesultCourseMetaTagAdmin)
