"""credentials DB (Koa). Scope S = core_siteconfiguration.site_id.

Table list: EDM tables/credentials/tier_{2,4,5,6,7}.txt (read-only reference).
Those files are KNOWN INCOMPLETE (e.g. credentials_sitebadgeprovider-style
tables are not listed) -- run `export_tenant_audit --db credentials` on the demo site and
extend TABLES / EXCLUDED until the coverage scan is clean.

Tier 0+1 (auth_permission, auth_group_permissions, django_content_type) are
global lookups -> excluded_global.
"""
from openedx.features.edly.tenant_export.services import EXCLUDED_GLOBAL, ServiceSpec, one_id

TABLES = [
    "django_site", "core_siteconfiguration",
    "catalog_organization", "catalog_course", "catalog_program",
    "credentials_coursecertificate", "credentials_programcertificate",
    "credentials_signatory", "catalog_courserun",
    "credentials_usercredential", "records_usergrade",
    "catalog_course_owners", "catalog_program_authoring_organizations",
    "catalog_program_course_runs",
    "credentials_coursecertificate_signatories", "credentials_programcertificate_signatories",
    "credentials_usercredentialattribute",
    "core_user", "core_user_groups", "social_auth_usersocialauth",
]

SECRET_COLUMNS = {
    "core_user": {"password": "'!'"},
    "social_auth_usersocialauth": {"extra_data": "'{}'"},
    "core_siteconfiguration": {"segment_key": "''"},
}

EXCLUDED = {
    t: (EXCLUDED_GLOBAL, "global lookup (EDM tier 0+1), no tenant data")
    for t in ("auth_permission", "auth_group_permissions", "django_content_type")
}


def resolve(cursor, slug, scope):
    # UNVERIFIED: edx_org_short_name == slug is an assumption (column and
    # value format UNVERIFIED on Koa) -- confirm on the demo site.
    sid = one_id(cursor, "SELECT DISTINCT site_id FROM core_siteconfiguration WHERE edx_org_short_name = %s",
                 (slug,), f"credentials site for {slug!r}")
    return {"site_id": sid}


def where(table: str, ctx: dict) -> str:
    s = int(ctx["site_id"])
    courses = f"SELECT id FROM catalog_course WHERE site_id = {s}"
    programs = f"SELECT id FROM catalog_program WHERE site_id = {s}"
    ccerts = f"SELECT id FROM credentials_coursecertificate WHERE site_id = {s}"
    pcerts = f"SELECT id FROM credentials_programcertificate WHERE site_id = {s}"
    runs = f"SELECT id FROM catalog_courserun WHERE course_id IN ({courses})"
    # UserCredential has no site column and `username` is a plain CharField:
    # scope by content-type + certificate-id chain (as EDM does); username
    # is only used downstream to pick users.
    uc = (
        "(credential_content_type_id IN (SELECT id FROM django_content_type "
        "WHERE app_label = 'credentials' AND model = 'coursecertificate') "
        f"AND credential_id IN ({ccerts})) OR "
        "(credential_content_type_id IN (SELECT id FROM django_content_type "
        "WHERE app_label = 'credentials' AND model = 'programcertificate') "
        f"AND credential_id IN ({pcerts}))"
    )
    users = f"SELECT id FROM core_user WHERE username IN (SELECT username FROM credentials_usercredential WHERE {uc})"
    return {
        "django_site": f"id = {s}",
        "core_siteconfiguration": f"site_id = {s}",
        "catalog_organization": f"site_id = {s}",
        "catalog_course": f"site_id = {s}",
        "catalog_program": f"site_id = {s}",
        "credentials_coursecertificate": f"site_id = {s}",
        "credentials_programcertificate": f"site_id = {s}",
        "credentials_signatory": (
            "id IN (SELECT signatory_id FROM credentials_coursecertificate_signatories "
            f"WHERE coursecertificate_id IN ({ccerts})) OR "
            "id IN (SELECT signatory_id FROM credentials_programcertificate_signatories "
            f"WHERE programcertificate_id IN ({pcerts}))"
        ),
        "catalog_courserun": f"course_id IN ({courses})",
        "credentials_usercredential": uc,
        "records_usergrade": f"course_run_id IN ({runs})",
        "catalog_course_owners": f"course_id IN ({courses})",
        "catalog_program_authoring_organizations": f"program_id IN ({programs})",
        "catalog_program_course_runs": f"program_id IN ({programs})",
        "credentials_coursecertificate_signatories": f"coursecertificate_id IN ({ccerts})",
        "credentials_programcertificate_signatories": f"programcertificate_id IN ({pcerts})",
        "credentials_usercredentialattribute":
            f"user_credential_id IN (SELECT id FROM credentials_usercredential WHERE {uc})",
        "core_user": f"id IN ({users})",
        "core_user_groups": f"user_id IN ({users})",
        "social_auth_usersocialauth": f"user_id IN ({users})",
    }[table]


SPEC = ServiceSpec("credentials", TABLES, where, resolve, SECRET_COLUMNS, EXCLUDED)
