from django.contrib import admin

from openedx.features.lums.models import UserProfileExtension


class UserProfileExtensionAdmin(admin.ModelAdmin):
    """ Admin interface for the UserAttribute model. """
    list_display = ('user', 'province',)
    list_filter = ('province',)
    search_fields = ('user__username', 'province',)

    class Meta(object):
        model = UserProfileExtension


admin.site.register(UserProfileExtension, UserProfileExtensionAdmin)
