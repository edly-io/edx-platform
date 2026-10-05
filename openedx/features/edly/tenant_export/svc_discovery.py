"""discovery DB (Koa). Scope P = core_partner.id where short_code == slug.

Table list: EDM tables/discovery/tier_{2,3,4}.txt (read-only reference).
Known incomplete (courseentitlement, curriculum, degree, publisher,
salesforce... not listed) -- run `export_tenant_audit --db discovery` on the demo site.

Deliberately NOT copied from EDM:
  * program_excluded_course_runs: EDM compares program ids to course ids;
    here it is scoped by program_id (and the run must be this partner's).
  * *_authoring_organizations: EDM scopes by the course, not the
    organization; here BOTH ends must belong to P (no foreign org ids).
  * course_metadata_video: scoped from the owning course/run/program rows.
Not dumped: core_user(+groups) and tier 0+1 lookups (global),
core_salesforceconfiguration (global + secrets).
"""
from openedx.features.edly.tenant_export.services import EXCLUDED_GLOBAL, ServiceSpec, one_id

TABLES = [
    "core_partner", "core_historicalpartner", "django_site",
    "course_metadata_organization", "course_metadata_historicalorganization",
    "course_metadata_person", "course_metadata_personsocialnetwork",
    "course_metadata_subject", "course_metadata_subjecttranslation",
    "course_metadata_video",
    "course_metadata_course", "course_metadata_course_authoring_organizations",
    "course_metadata_course_subjects", "course_metadata_historicalcourse",
    "course_metadata_program", "course_metadata_program_authoring_organizations",
    "course_metadata_program_courses", "course_metadata_program_credit_backing_organizations",
    "course_metadata_historicalprogram",
    "course_metadata_courserun", "course_metadata_courserun_staff",
    "course_metadata_seat", "course_metadata_program_excluded_course_runs",
]

# core_historicalpartner copies the same columns as core_partner.
_PARTNER_SECRETS = {"marketing_site_api_password": "''", "analytics_token": "''"}
SECRET_COLUMNS = {"core_partner": dict(_PARTNER_SECRETS), "core_historicalpartner": dict(_PARTNER_SECRETS)}

EXCLUDED = {
    "core_salesforceconfiguration": (EXCLUDED_GLOBAL, "global config holding Salesforce credentials"),
    "core_user": (EXCLUDED_GLOBAL, "discovery service accounts, not tenant data"),
    "core_user_groups": (EXCLUDED_GLOBAL, "discovery service accounts, not tenant data"),
}


def resolve(cursor, slug, scope):
    pid = one_id(cursor, "SELECT id FROM core_partner WHERE short_code = %s", (slug,),
                 f"discovery partner for {slug!r}")
    return {"partner_id": pid}


def where(table: str, ctx: dict) -> str:
    p = int(ctx["partner_id"])
    orgs = f"SELECT id FROM course_metadata_organization WHERE partner_id = {p}"
    persons = f"SELECT id FROM course_metadata_person WHERE partner_id = {p}"
    subjects = f"SELECT id FROM course_metadata_subject WHERE partner_id = {p}"
    courses = f"SELECT id FROM course_metadata_course WHERE partner_id = {p}"
    programs = f"SELECT id FROM course_metadata_program WHERE partner_id = {p}"
    runs = f"SELECT id FROM course_metadata_courserun WHERE course_id IN ({courses})"
    return {
        "core_partner": f"id = {p}",
        "core_historicalpartner": f"id = {p}",
        "django_site": f"id IN (SELECT site_id FROM core_partner WHERE id = {p})",
        "course_metadata_organization": f"partner_id = {p}",
        "course_metadata_historicalorganization": f"partner_id = {p}",
        "course_metadata_person": f"partner_id = {p}",
        "course_metadata_personsocialnetwork": f"person_id IN ({persons})",
        "course_metadata_subject": f"partner_id = {p}",
        "course_metadata_subjecttranslation": f"master_id IN ({subjects})",
        # UNVERIFIED: video FK columns on course/courserun/program UNVERIFIED.
        "course_metadata_video": (
            f"id IN (SELECT video_id FROM course_metadata_course WHERE partner_id = {p} AND video_id IS NOT NULL) "
            f"OR id IN (SELECT video_id FROM course_metadata_courserun WHERE course_id IN ({courses}) AND video_id IS NOT NULL) "
            f"OR id IN (SELECT video_id FROM course_metadata_program WHERE partner_id = {p} AND video_id IS NOT NULL)"
        ),
        "course_metadata_course": f"partner_id = {p}",
        "course_metadata_course_authoring_organizations":
            f"course_id IN ({courses}) AND organization_id IN ({orgs})",
        "course_metadata_course_subjects": f"course_id IN ({courses}) AND subject_id IN ({subjects})",
        "course_metadata_historicalcourse": f"partner_id = {p}",
        "course_metadata_program": f"partner_id = {p}",
        "course_metadata_program_authoring_organizations":
            f"program_id IN ({programs}) AND organization_id IN ({orgs})",
        "course_metadata_program_courses": f"program_id IN ({programs}) AND course_id IN ({courses})",
        "course_metadata_program_credit_backing_organizations":
            f"program_id IN ({programs}) AND organization_id IN ({orgs})",
        "course_metadata_historicalprogram": f"partner_id = {p}",
        "course_metadata_courserun": f"course_id IN ({courses})",
        "course_metadata_courserun_staff": f"courserun_id IN ({runs}) AND person_id IN ({persons})",
        "course_metadata_seat": f"course_run_id IN ({runs})",
        "course_metadata_program_excluded_course_runs":
            f"program_id IN ({programs}) AND courserun_id IN ({runs})",
    }[table]


SPEC = ServiceSpec("discovery", TABLES, where, resolve, SECRET_COLUMNS, EXCLUDED)
