"""
Django Admin pages for Edly.
"""

from django.contrib import admin

from openedx.features.edly.models import EdlyOrganization, EdlySubOrganization, EdlyUserProfile


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


class EdlyUserProfileAdmin(admin.ModelAdmin):
    """
    Admin interface for the "EdlyUserProfile" object.
    """
    search_fields = ['edly_sub_organizations__name', 'user__username', 'user__email']
    list_display = ['username', 'email', 'edly_sub_organizations_slugs']

    def username(self, obj):
        return obj.user.username

    def email(self, obj):
        return obj.user.email

    def edly_sub_organizations_slugs(self, obj):
        return ', '.join(obj.edly_sub_organizations.values_list('slug', flat=True))


admin.site.register(EdlyOrganization, EdlyOrganizationAdmin)
admin.site.register(EdlySubOrganization, EdlySubOrganizationAdmin)
admin.site.register(EdlyUserProfile, EdlyUserProfileAdmin)
