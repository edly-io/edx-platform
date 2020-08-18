"""
Serializers for colaraz API.
"""
from django.conf import settings
from django.contrib.auth.models import User

from future.moves.urllib.parse import urlparse
from organizations.models import Organization
from rest_framework import serializers

from openedx.features.colaraz_features.api.fields import (
    DomainField,
    SiteNameField,
)
from openedx.features.colaraz_features.helpers import (
    create_oauth2_client_for_ecommerce_site,
    create_site_on_ecommerce,
    get_existing_site_domains,
    get_or_create_organization,
    get_or_create_sites,
    update_or_create_site_configurations,
    update_or_create_site_themes,
)

class SiteOrgSerializer(serializers.Serializer):
    auth_token = serializers.CharField(required=True)
    site_domain = DomainField(max_length=20, required=True)
    site_name = SiteNameField(max_length=20, required=True)
    site_theme = serializers.CharField(max_length=255, required=False, default=settings.DEFAULT_SITE_THEME)
    platform_name = serializers.CharField(max_length=255)
    company_type = serializers.CharField(max_length=255, required=False, default='')
    university_name = serializers.CharField(max_length=255, required=False, default='')
    organizations = serializers.ListField(
        child=serializers.SlugField(max_length=255),
        allow_empty=True,
        required=False,
    )
    site_partner = serializers.CharField(max_length=255, required=False, default='edx')
    payment_processors = serializers.CharField(max_length=255, required=False, default='')
    client_side_payment_processor = serializers.CharField(max_length=255, required=False, default='')
    ecommerce_from_email = serializers.EmailField(max_length=255, required=False, default=None)
    payment_support_email = serializers.EmailField(max_length=255, required=False, default=None)
    allow_update = serializers.BooleanField(default=False)

    def validate(self, fields):
        """
        Validate all the required fields.
        """
        if not fields.get('allow_update'):
            self.sites_validation(fields.get('site_name'), fields.get('site_domain'))
            self.org_validation(fields.get('site_name'))
        return fields

    @staticmethod
    def validate_auth_token(value):
        """
        Validate authorization token.
        """
        if value != getattr(settings, 'COLARAZ_SITE_CREATION_API_TOKEN', ''):
            raise serializers.ValidationError('Authentication Token is not valid')
        return value

    @staticmethod
    def sites_validation(name, domain):
        """
        Validate that site domains do not already exist.
        """
        site_domains = get_existing_site_domains(name=name, domain=domain)
        if site_domains:
            raise serializers.ValidationError(
                'Sites with domains "{}" already exist.'.format([str(i) for i in site_domains])
            )

    @staticmethod
    def org_validation(value):
        """
        Validate that organization does not already exist.
        """
        if Organization.objects.filter(short_name=value).exists():
            raise serializers.ValidationError('An organization with name "{}" already exists.'.format(value))

    def create(self, validated_data):
        """
        Create instances of site, site theme, organization and site configuration on edx-platform and ecommerce.
        """
        site_name = validated_data['site_name']
        ecommerce_site_url = '{uri.scheme}://{site_name}.{uri.netloc}/'.format(
            site_name=site_name,
            uri=urlparse(settings.ECOMMERCE_PUBLIC_URL_ROOT),
        )
        ecommerce_worker = User.objects.get(username=settings.ECOMMERCE_SERVICE_WORKER_USERNAME)
        client = create_oauth2_client_for_ecommerce_site(
            service_user=ecommerce_worker,
            site_name=site_name,
            url=ecommerce_site_url,
        )

        sites = get_or_create_sites(name=site_name, domain=validated_data['site_domain'])
        organization = get_or_create_organization(name=validated_data['site_name'])
        update_or_create_site_themes(sites=sites, theme=validated_data['site_theme'])
        update_or_create_site_configurations(
            sites=sites,
            organization=organization,
            university_name=validated_data['university_name'],
            platform_name=validated_data['platform_name'],
            organizations=validated_data.get('organizations', []),
            company_type=validated_data['company_type'],
            client_secret=client.client_secret,
            ecommerce_url=ecommerce_site_url,
        )
        ecommerce_data = create_site_on_ecommerce(
            ecommerce_worker=ecommerce_worker,
            lms_site_url='https://{}'.format(sites.lms.domain),
            ecommerce_site_url=ecommerce_site_url,
            client=client,
            validated_data=validated_data,
        )
        data = validated_data.copy()
        data.update(ecommerce_data)
        return data

    def update(self, instance, validated_data):
        raise NotImplementedError('Update is not supported.')
