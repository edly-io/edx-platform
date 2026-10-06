"""edx-notes-api DB (Koa). Single table `v1_note`.

Scoped by TENANT MEMBERSHIP, not by course org (unlike EDM's
migrate_notes_tenant_data, which uses `course_id LIKE` and so leaks/misses
across tenants). `v1_note.user_id` holds the course-independent ORA-style
anonymous id (`student_anonymoususerid.anonymous_user_id`, course-less row),
so the scope is the anon ids of `edly_edlymultisiteaccess` members, read
through a cross-schema subquery into the edxapp DB (same MySQL server; the
notes DB user needs SELECT on edxapp.student_anonymoususerid).
ponytail: cross-schema subquery only; if notes lives on another server, resolve anon ids in Python and chunk them.
"""
import re

from openedx.features.edly.tenant_export.services import ServiceSpec
from openedx.features.edly.tenant_export.sqlutil import membership_subquery

TABLES = ["v1_note"]

_SCHEMA_RE = re.compile(r"^[A-Za-z0-9_]+$")


def resolve(cursor, slug, scope):  # pylint: disable=unused-argument
    from django.db import connection

    schema = connection.settings_dict["NAME"]
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"unsafe edxapp schema name {schema!r}")
    return {"sub_org_id": int(scope["sub_org_id"]), "edxapp_db": schema}


def where(table: str, ctx: dict) -> str:
    schema = ctx["edxapp_db"]
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"unsafe edxapp schema name {schema!r}")
    members = membership_subquery(int(ctx["sub_org_id"])).replace(
        "edly_edlymultisiteaccess", f"{schema}.edly_edlymultisiteaccess"
    )
    return {
        # course-independent anon id: course_id is '' (empty CourseKey) or NULL
        "v1_note": (
            f"user_id IN (SELECT anonymous_user_id FROM {schema}.student_anonymoususerid "
            f"WHERE (course_id IS NULL OR course_id = '') AND user_id IN ({members}))"
        ),
    }[table]


SPEC = ServiceSpec("notes", TABLES, where, resolve)
