"""edx-notes-api DB (Koa). Single table `v1_note`.

`v1_note.user_id` holds the course-independent anonymous id
(`student_anonymoususerid.anonymous_user_id`, course-less row; edxapp
`edxnotes/helpers.py` posts `anonymous_id_for_user(user, None)`), and the
table has no tenant column. A note is exported only if BOTH hold:
  * its author is a tenant member (`edly_edlymultisiteaccess`), read through
    a cross-schema subquery into the edxapp DB, and
  * its `course_id` belongs to one of the tenant's course orgs.
Membership alone would ship a multi-tenant member's highlights of ANOTHER
tenant's course text (same leak the journal tier-7 table is guarded against);
the org clause alone (EDM's `course_id LIKE`) would be the reverse. Known
limit: notes by non-members on tenant courses are not exported (as for
every other member-scoped table).

Preconditions (checked in `resolve`): the notes DB is on the same MySQL
server as edxapp, the notes DB user can SELECT on the edxapp schema, and
`EXPORT_TENANT_DATABASES['notes']['NAME']` is the real schema (default
`edx_notes_api`, see `services.SCHEMA_NAMES`).
UNVERIFIED on the demo site: that `student_anonymoususerid.course_id` of the
course-independent row is NULL or '' (checked in the WHERE, not against data).
The cross-schema SELECT grant above is a deployment requirement, not tested.
ponytail: cross-schema subquery only; if notes ever lives on another server,
resolve anon ids in Python and chunk them.
"""
import re

from openedx.features.edly.tenant_export.scope import scope_orgs
from openedx.features.edly.tenant_export.services import ServiceSpec
from openedx.features.edly.tenant_export.sqlutil import membership_subquery, org_like_clause

TABLES = ["v1_note"]

_SCHEMA_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _check_schema(schema: str) -> None:
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"unsafe edxapp schema name {schema!r}")


def resolve(cursor, slug, scope):  # pylint: disable=unused-argument
    from django.db import connection

    from openedx.features.edly.tenant_export import services
    from openedx.features.edly.tenant_export.scope import ScopeError

    notes_cfg = services.service_connection("notes").settings_dict
    for key in ("HOST", "PORT"):
        if str(notes_cfg.get(key) or "") != str(connection.settings_dict.get(key) or ""):
            raise ScopeError(
                f"notes DB {key} differs from edxapp's: the cross-schema notes dump needs both schemas on one "
                "MySQL server (EXPORT_TENANT_DATABASES['notes'] must not override HOST/PORT)"
            )
    schema = connection.settings_dict["NAME"]
    _check_schema(schema)
    return {"sub_org_id": int(scope["sub_org_id"]), "edxapp_db": schema, "course_orgs": scope_orgs(scope)}


def where(table: str, ctx: dict) -> str:
    schema = ctx["edxapp_db"]
    _check_schema(schema)
    orgs = ctx["course_orgs"]
    if not orgs:
        raise ValueError("notes scope needs at least one course org")
    members = membership_subquery(int(ctx["sub_org_id"])).replace(
        "edly_edlymultisiteaccess", f"{schema}.edly_edlymultisiteaccess"
    )
    return {
        # course-independent anon id: course_id is '' (empty CourseKey) or NULL
        "v1_note": (
            f"user_id IN (SELECT anonymous_user_id FROM {schema}.student_anonymoususerid "
            f"WHERE (course_id IS NULL OR course_id = '') AND user_id IN ({members})) "
            f"AND ({org_like_clause('course_id', orgs)})"
        ),
    }[table]


SPEC = ServiceSpec("notes", TABLES, where, resolve)
