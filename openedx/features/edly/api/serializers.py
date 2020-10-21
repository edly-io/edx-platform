from rest_framework import serializers

from openedx.core.djangoapps.site_configuration.helpers import get_value_for_org


class SiteSerialzier(serializers.Serializer):
    domain = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()

    def get_domain(self, obj):
        return self.context['edly_sub_org_of_user'].lms_site.domain

    def get_name(self, obj):
        return self.context['edly_sub_org_of_user'].lms_site.name


class UserSiteSerializer(serializers.Serializer):
    site = serializers.SerializerMethodField()
    app_config = serializers.SerializerMethodField()
    branding = serializers.SerializerMethodField()

    def get_site(self, obj):
        serializer = SiteSerialzier({}, context=self.context)
        return serializer.data

    def get_app_config(self, obj):
        mobile_app_config = get_value_for_org(
            self.context['edly_sub_org_of_user'].edx_organization.short_name,
            'MOBILE_APP_CONFIG'
        )
        return mobile_app_config

    def get_branding(self, obj):
        branding = get_value_for_org(
            self.context['edly_sub_org_of_user'].edx_organization.short_name,
            'BRANDING'
        )
        colors = get_value_for_org(
            self.context['edly_sub_org_of_user'].edx_organization.short_name,
            'COLORS'
        )
        branding.update(colors)
        return branding
