"""
Django Admin pages for Edly.
"""

from django.contrib import admin

from openedx.features.edly.models import (
    EdlyMultiSiteAccess,
    EdlyOrganization,
    EdlySubOrganization,
    EdlyUserProfile,
    StudentCourseProgress,
    PasswordChange,
    PasswordHistory,
    TwoFactorBypass,
    OTPSession
)


class EdlySubOrganizationAdmin(admin.ModelAdmin):
    """
    Admin interface for the "EdlySubOrganization" object.
    """
    search_fields = ['name', 'slug']

    list_display = [
        'edly_organization_name',
        'edly_organization_slug',
        'name',
        'slug',
        'edx_organizations_names',
        'edx_organizations_short_names',
        'is_active',
        'created',
        'modified'
    ]

    def edly_organization_name(self, obj):
        return obj.edly_organization.name

    def edly_organization_slug(self, obj):
        return obj.edly_organization.slug

    def edx_organizations_names(self, obj):
        return ', '.join(obj.edx_organizations.all().values_list('name', flat=True))

    def edx_organizations_short_names(self, obj):
        return ', '.join(obj.get_edx_organizations)


class EdlySubOrganizationInlineAdmin(admin.StackedInline):
    """
    Admin inline interface for the "EdlySubOrganization" object.
    """
    model = EdlySubOrganization
    extra = 0


class EdlyOrganizationAdmin(admin.ModelAdmin):
    """
    Admin interface for the "EdlyOrganization" object.
    """
    search_fields = ['name', 'slug']
    list_display = ['name', 'slug', 'enable_all_edly_sub_org_login', 'created', 'modified']
    inlines = [EdlySubOrganizationInlineAdmin]

class StudentCourseProgressAdmin(admin.ModelAdmin):
    """
    Admin interface for the "StudentCourseProgress" object.
    """
    list_display = ['student', 'course_id', 'completed_block', 'completion_date']
    search_fields = ['course_id', 'student__username', 'student__email']


class EdlyMultisiteAccessAdmin(admin.ModelAdmin):
    """
    Admin interface for the "EdlyMultiSiteAccess" object.
    """
    list_display = ["user", "user_email", "sub_org"]
    list_filter = ["sub_org__name"]
    search_fields = ["user__username", "user__email", "sub_org__name"]
    autocomplete_fields = ["user", "sub_org"]

    def user_email(self, obj):
        return obj.user.email

class EdlyUserProfileAdmin(admin.ModelAdmin):
    """
    Admin interface for the "EdlyMultiSiteAccess" object.
    """
    list_display = ["user", "is_blocked", "is_social_user"]
    list_filter = ["is_blocked", "is_social_user"]
    search_fields = ["user__username", "user__email"]


class PasswordChangeAdmin(admin.ModelAdmin):
    list_display = ("last_changed", "user", )
    list_filter = ("last_changed", "user", )


class PasswordHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "created", )
    list_filter = ("user", "created")


class TwoFactorBypassAdmin(admin.ModelAdmin):
    list_display = ['user', 'reason', 'created_at', 'created_by']
    list_filter = ['created_at']
    search_fields = ['user__username', 'user__email', 'reason']
    readonly_fields = ['created_at']
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class OTPSessionAdmin(admin.ModelAdmin):
    list_display = ['user', 'created_at', 'expires_at', 'is_verified', 'attempts']
    list_filter = ['is_verified', 'created_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at', 'otp_code']
    
    def has_add_permission(self, request):
        return False

admin.site.register(TwoFactorBypass, TwoFactorBypassAdmin)
admin.site.register(OTPSession, OTPSessionAdmin)
admin.site.register(PasswordChange, PasswordChangeAdmin)
admin.site.register(PasswordHistory, PasswordHistoryAdmin)
admin.site.register(StudentCourseProgress, StudentCourseProgressAdmin)
admin.site.register(EdlyOrganization, EdlyOrganizationAdmin)
admin.site.register(EdlySubOrganization, EdlySubOrganizationAdmin)
admin.site.register(EdlyMultiSiteAccess, EdlyMultisiteAccessAdmin)
admin.site.register(EdlyUserProfile, EdlyUserProfileAdmin)
