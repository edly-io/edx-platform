"""
Helper functions for colaraz app.
"""
from collections import namedtuple

from django.contrib.sites.models import Site

from organizations.models import Organization

from openedx.core.djangoapps.theming.models import SiteTheme
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration


# Tuple containing information for lms and studio
# This named tuple can be used for sites, site themes, organizations etc.
Pair = namedtuple('Pair', 'lms studio')
LMS_AND_STUDIO_SITE_PREFIXES = ('courses', 'studio')


def do_sites_exists(domain):
    """
    Check if either lms or studio site for the given domain name exist.

    Arguments:
        domain (str): Domain to identify the site. e.g. example.com, test.example.com etc.

    Returns:
        (bool): True if lms and studio sites do not exist for the given domain, False otherwise.
    """
    return Site.objects.filter(
        domain__in=['{}.{}'.format(prefix, domain) for prefix in LMS_AND_STUDIO_SITE_PREFIXES]
    ).exists()


def remove_duplicates(items):
    """
    Remove duplicate items from the argument list.

    The function will not work if the items in the provided list are not hashable.

    Arguments:
        (list): List of hashable items from which to remove duplicates.

    Returns:
        (list): List of items containing only unique items.
    """
    return list(set(items))


def create_sites(domain, name):
    """
    Create sites for lms and studio with domain and name provided in the arguments.

    LMS Domain will be formed by prepending `courses` to the passed in domain. And Studio domain will be
    formed by prepending `studio` to the passed in domain.

    Arguments:
        domain (str): Domain to identify the site. e.g. example.com, test.example.com etc.
        name (str): Human readable name for the site.

    Returns:
        (Pair<Site, Site>): named tuple containing lms, studio sites.
    """
    sites = (
        Site.objects.create(
            domain='{}.{}'.format(prefix, domain),
            name='{}.{}'.format(prefix, name),
        ) for prefix in LMS_AND_STUDIO_SITE_PREFIXES
    )
    return Pair(*sites)


def create_site_themes(sites, theme):
    """
    Create site themes for the site provided in the arguments.

    Arguments:
        sites (Pair<Site, Site>): Named tuple containing lms and studio sites.
        theme (str): Theme name to be added for the sites.
    Returns:
        (Pair<SiteTheme, SiteTheme>): named tuple containing lms, studio sites themes.
    """
    site_themes = (
        SiteTheme.objects.create(
            site=site,
            theme_dir_name=theme
        ) for site in sites
    )
    return Pair(*site_themes)


def create_organization(name, short_name):
    """
    Create an organization with the given name.

    Short name for the organization will be extracted from the name by slugify-ing the name.

    Arguments:
        name (str): Name of the organization to create.
        short_name (str): Short name of the organization to create.

    Returns:
        (organization.Organization): Instance of the newly created organization.
    """
    return Organization.objects.create(
        name=name,
        short_name=short_name,
    )


def create_site_configurations(sites, organization, university_name, platform_name, organizations):
    """
    Create site configurations for the given sites.

    Following fields will be set in the configuration
        1. SESSION_COOKIE_DOMAIN: site.domain
        2. PLATFORM_NAME: platform_name
        3. site_domain: site.domain
        4. site_name: university_name
        5. course_org_filters: [ organization.short_name ] + organizations

    Arguments:
        sites (Pair<Site, Site>): named tuple containing lms and studio sites for which to create the configuration.
        organization (organization.Organization): Organization to associate with the configuration.
        university_name (str): Name of the university
        platform_name (str): Platform name that appears on the dashboard and several other places.
        organizations ([str]): List of organizations to add in the configuration.

    Returns:
        (Pair<SiteConfiguration, SiteConfiguration>): named tuple containing lms, studio sites configurations.
    """
    site_configurations = (
        SiteConfiguration.objects.create(
            site=site,
            enabled=True,
            values=dict(
                SESSION_COOKIE_DOMAIN=site.domain,
                PLATFORM_NAME=platform_name,
                platform_name=platform_name,
                site_domain=site.domain,
                SITE_NAME=site.domain,
                university=university_name,
                course_org_filters=remove_duplicates(organizations + [organization.short_name]),
                **(
                    {
                        'AUTH_PROVIDER_FALLBACK_URL': 'https://{}'.format(sites.lms.domain)
                    } if site == sites.studio else {}
                )
            )
        ) for site in sites
    )
    return Pair(*site_configurations)


def get_request_schema(request):
    """
    Returns schema of request
    """
    environ = getattr(request, "environ", {})
    return environ.get("wsgi.url_scheme", "http")


def get_site_base_url(request):
    """
    Returns current request's complete url
    """
    schema = get_request_schema(request)
    domain = get_request_site_domain(request)

    return '{}://{}'.format(schema, domain)


def get_request_site_domain(request):
    """
    Returns domain of site being requested by the User.
    """
    site = getattr(request, 'site', None)
    domain = getattr(site, 'domain', None)
    return domain
