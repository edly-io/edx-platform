"""
Serializers for colaraz API.
"""
from django.conf import settings

from rest_framework import serializers
from organizations.models import Organization

from openedx.features.colaraz_features.api.fields import DomainField
from openedx.features.colaraz_features.helpers import (
    create_sites, create_site_themes, create_organization, create_site_configurations, do_sites_exists
)


class SiteOrgSerializer(serializers.Serializer):
    site_domain = DomainField(max_length=90)
    site_name = serializers.CharField(max_length=40, required=False, default='')
    site_theme = serializers.CharField(max_length=255, default=settings.DEFAULT_SITE_THEME)
    org_name = serializers.CharField(max_length=255)
    org_short_name = serializers.SlugField(max_length=255)
    platform_name = serializers.CharField(max_length=255)
    university_name = serializers.CharField(max_length=255, required=False, default='')
    organizations = serializers.ListField(
        child=serializers.SlugField(max_length=255)
    )

    @staticmethod
    def validate_site_domain(value):
        """
        Validate that site domain does not already exists.
        """
        if do_sites_exists(domain=value):
            raise serializers.ValidationError('An LMS or studio site with domain "{}" already exists.'.format(value))
        return value

    @staticmethod
    def validate_org_name(value):
        """
        Validate that organization name does not already exists.
        """
        if Organization.objects.filter(name=value).exists():
            raise serializers.ValidationError('An organization with name "{}" already exists.'.format(value))
        return value

    @staticmethod
    def validate_org_short_name(value):
        """
        Validate that organization short name does not already exists.
        """
        if Organization.objects.filter(short_name=value).exists():
            raise serializers.ValidationError('An organization with short name "{}" already exists.'.format(value))
        return value

    def create(self, validated_data):
        """
        Create instances of site, site theme, organization and site configuration.
        """
        sites = create_sites(domain=validated_data['site_domain'], name=validated_data['site_name'])
        organization = create_organization(name=validated_data['org_name'], short_name=validated_data['org_short_name'])
        create_site_themes(sites=sites, theme=validated_data['site_theme'])
        create_site_configurations(
            sites=sites,
            organization=organization,
            university_name=validated_data['university_name'],
            platform_name=validated_data['platform_name'],
            organizations=validated_data['organizations'],
        )

    def update(self, instance, validated_data):
        raise NotImplementedError('Update is not supported.')
