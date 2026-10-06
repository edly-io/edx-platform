"""export_tenant_scope: slug -> {sub_org_id, course_org_filter, course_ids}.

`scope.json` is the single source of truth every other `export_tenant_*`
command reads -- nothing else re-resolves scope independently. Ported
unchanged (besides the import path and the addition of `load_scope_file`,
used by every other command) from the standalone reference implementation
(`mit-tenant-export/export_mit/scope.py`, EDLYPRODUCT-8584 Phase 1). No
Django import here on purpose -- `resolve_scope` takes a plain DB-API
cursor, so `management/commands/export_tenant_scope.py` (which does import
Django, to get `connection.cursor()`) stays a thin wrapper around this.

Reference only (read, never imported -- EDM is never touched):
  edlysaas_data_migrations/management/commands/migrate_users_and_courses.py:184-220  -- EdlySubOrganization + course_org_filter
  edlysaas_data_migrations/management/commands/migrate_users_and_courses.py:263-277  -- _get_tenant_user_ids (source-only, confirmed)
  edlysaas_data_migrations/management/commands/migrate_tenant_tier_data.py:2399-2410 -- course-id LIKE resolution

[Audited 2026-10-02 -- citation correction] Do NOT reference
migrate_users_and_courses.py:222-260 as a safe "source-only" block -- despite
living in the function the plan's own notes once called "entirely
source-side," those lines also query Ulmo's TARGET_OPENEDX and return None
if MIT has no Ulmo tenant configured there (it doesn't; MIT runs its own
vanilla Tutor install with no Edly Ulmo tenant at all). Everything below
queries the Koa source only, matching migrate_tenant_tier_data.py's own
source-only tenant-context resolution instead.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from openedx.features.edly.tenant_export.sqlutil import org_like_clause, validate_org_token


class ScopeError(Exception):
    """Raised for any condition that should hard-stop an export_tenant_scope run."""


_COURSE_ORG_RE = re.compile(r"^course-v1:([^+]+)\+")


def merge_orgs(course_org_filter, course_ids) -> list:
    """FALLBACK only (see `derive_orgs`): course_org_filter UNION the orgs actually present (in their real case)
    in `course_ids`. MySQL LIKE is case-insensitive, so a site-config filter
    of `mit` matches `course-v1:MIT+...`; but S3 keys and Mongo prefixes are
    case-SENSITIVE (Phase 3), so they need the real-case spelling too."""
    orgs = set(course_org_filter)
    for course_id in course_ids:
        match = _COURSE_ORG_RE.match(course_id)
        if match:
            orgs.add(match.group(1))
    return sorted(orgs)


def derive_orgs(edx_orgs_m2m, course_org_filter, course_ids) -> list:
    """The org set used for S3 / Mongo prefixes and org LIKE scoping outside Phase 1.

    BASE = EDM's source: the sub-org's `edly_edlysuborganization_edx_organizations` M2M ->
    `organizations_organization.short_name` (EDM utils/s3_resolvers.py `_get_tenant_orgs`).
    ADDITION (not in EDM, deliberate): real-case spellings found in `course_ids` whose
    case-folded org equals a base org (e.g. base `MITx`, course `course-v1:mitx+...`), because
    S3 keys and Mongo prefixes are case-SENSITIVE while MySQL LIKE is not. Orgs in `course_ids`
    that are NOT in the M2M (case-insensitively) are NOT added -- EDM would not copy them.
    FALLBACK: an empty M2M (query failed / unconfigured sub-org) falls back to the old
    `merge_orgs(course_org_filter, course_ids)` superset rather than exporting nothing."""
    base = {o for o in edx_orgs_m2m or [] if o}
    if not base:
        return merge_orgs(course_org_filter, course_ids)
    folded = {o.lower() for o in base}
    for course_id in course_ids:
        match = _COURSE_ORG_RE.match(course_id)
        if match and match.group(1).lower() in folded:
            base.add(match.group(1))
    return sorted(base)


def scope_orgs(scope_data: dict) -> list:
    """`course_orgs` of a loaded scope.json; computed on the fly for a scope
    file written before Phase 3 (no re-resolve needed)."""
    orgs = scope_data.get("course_orgs") or derive_orgs(
        scope_data.get("edx_orgs_m2m"), scope_data["course_org_filter"], scope_data["course_ids"],
    )
    for org in orgs:
        validate_org_token(org)
    return orgs


def resolve_scope(cursor, slug: str) -> dict:
    cursor.execute(
        "SELECT id, name, lms_site_id FROM edly_edlysuborganization WHERE slug = %s",
        (slug,),
    )
    row = cursor.fetchone()
    if not row:
        raise ScopeError(f"suborg with slug={slug!r} not found in source edxapp DB")
    sub_org_id, _name, lms_site_id = row

    cursor.execute(
        "SELECT COUNT(DISTINCT user_id) FROM edly_edlymultisiteaccess WHERE sub_org_id = %s",
        (sub_org_id,),
    )
    tenant_user_count = cursor.fetchone()[0]

    if not lms_site_id:
        raise ScopeError(f"suborg {slug!r} has no lms_site_id -- cannot resolve course_org_filter")

    cursor.execute(
        """
        SELECT site_values FROM site_configuration_siteconfiguration
        WHERE site_id = %s ORDER BY id DESC LIMIT 1
        """,
        (lms_site_id,),
    )
    config_row = cursor.fetchone()
    if not config_row or not config_row[0]:
        raise ScopeError(f"no site_configuration row for site_id={lms_site_id} (suborg {slug!r})")

    # This is a raw DB-API cursor (same as the reference implementation's
    # pymysql cursor), not the Django ORM's JSONField descriptor -- MySQL
    # always hands back the column's raw text here, so this always needs
    # json.loads, unconditionally.
    site_values = json.loads(config_row[0])
    org_filter = site_values.get("course_org_filter")
    if isinstance(org_filter, str):
        course_org_filter = [org_filter]
    elif isinstance(org_filter, list):
        course_org_filter = list(org_filter)
    else:
        course_org_filter = []

    # Fail loudly -- do NOT fall back to "1=0" the way EDM does (plan: "Fail
    # loudly on empty course_org_filter (hard error) -- do not fall back to
    # 1=0 the way EDM does"). A silent 1=0 here would make every downstream
    # subcommand "succeed" with zero rows, which is worse than a hard error.
    if not course_org_filter:
        raise ScopeError(
            f"empty course_org_filter for suborg {slug!r} (site_id={lms_site_id}) -- refusing to export"
        )
    for org in course_org_filter:
        validate_org_token(org)

    cursor.execute(
        f"SELECT id FROM course_overviews_courseoverview WHERE ({org_like_clause('id', course_org_filter)})"
    )
    course_ids = sorted(r[0] for r in cursor.fetchall())

    # Phase 3 org base (EDM's source of truth for S3/forum prefixes) -- see `derive_orgs`.
    # Mismatch vs course_org_filter is warned about by export_tenant_scope.
    try:
        cursor.execute(
            "SELECT o.short_name FROM edly_edlysuborganization_edx_organizations esoo "
            "JOIN organizations_organization o ON o.id = esoo.organization_id "
            "WHERE esoo.edlysuborganization_id = %s",
            (sub_org_id,),
        )
        edx_orgs_m2m = sorted(r[0] for r in cursor.fetchall())
    except Exception:  # pylint: disable=broad-except -- informational; must never break Phase 1 scope resolution
        edx_orgs_m2m = []

    return {
        "slug": slug,
        "sub_org_id": sub_org_id,
        "course_org_filter": course_org_filter,
        "course_ids": course_ids,
        "course_orgs": derive_orgs(edx_orgs_m2m, course_org_filter, course_ids),
        "course_orgs_source": "edx_orgs_m2m" if edx_orgs_m2m else "fallback:course_org_filter+course_ids",
        "edx_orgs_m2m": edx_orgs_m2m,
        "tenant_user_count": tenant_user_count,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }


def load_scope_file(path) -> dict:
    """Load + validate a `scope.json` written by `export_tenant_scope` --
    every other `export_tenant_*` command reads scope this way, never
    re-resolving it independently.
    """
    data = json.loads(Path(path).read_text())
    for org in data["course_org_filter"]:
        validate_org_token(org)
    for org in data.get("course_orgs", []):
        validate_org_token(org)
    return data
