"""export_tenant_csmh logic: CSMH lives in a genuinely separate database
(`edxapp_csmh`, confirmed via
lms/djangoapps/coursewarehistoryextended/models.py +
openedx/core/lib/django_courseware_routers.py), linked to the main edxapp
DB's `courseware_studentmodule` only by `student_module_id` -- a non-DB-
constrained FK (`db_constraint=False`) crossing databases, which mysqldump
cannot express as a cross-database subquery. So: resolve the id set on the
edxapp side first (keyset-paginated -- this table can be millions of rows),
then dump edxapp_csmh in <=1000-id chunks against that id set.

Ported unchanged from the standalone reference implementation
(`mit-tenant-export/export_mit/csmh.py`, EDLYPRODUCT-8584 Phase 1) --
`dump_csmh` now takes a plain `csmh_conn_params` dict (built by the
`export_tenant_csmh` command from Django's `connections['student_module_history']
.settings_dict` via `dbutil.conn_params_from_settings_dict`) instead of the
standalone tool's pymysql `DBConfig`, matching `dbutil.dump_table_chunked`'s
own signature.
"""
from openedx.features.edly.tenant_export import dbutil
from openedx.features.edly.tenant_export.sqlutil import org_like_clause, where_clauses_for_ids
from openedx.features.edly.tenant_export.tables import CSMH_TABLE

_STUDENTMODULE_BATCH = 5000
_CSMH_CHUNK_SIZE = 1000


def fetch_studentmodule_ids(cursor, course_org_filter) -> list:
    """Keyset-paginated id collection (id > last_id), not OFFSET -- needed
    for a table that can be millions of rows with KB-sized `state` blobs.
    Reuses the same course_id WHERE already used for this table in tier 6.
    """
    where = org_like_clause("course_id", course_org_filter)
    ids = []
    last_id = 0
    while True:
        cursor.execute(
            "SELECT id FROM courseware_studentmodule "
            f"WHERE ({where}) AND id > {int(last_id)} ORDER BY id LIMIT {_STUDENTMODULE_BATCH}"
        )
        batch = [r[0] for r in cursor.fetchall()]
        if not batch:
            break
        ids.extend(batch)
        last_id = batch[-1]
        if len(batch) < _STUDENTMODULE_BATCH:
            break
    return ids


def dump_csmh(edxapp_cursor, csmh_conn_params, course_org_filter, out_path, dry_run: bool = False) -> int:
    """Returns the row count. In dry-run mode, that IS the exact count (we
    already collected the real id set -- no separate COUNT query needed) and
    nothing is written to `out_path`.
    """
    student_module_ids = fetch_studentmodule_ids(edxapp_cursor, course_org_filter)
    if not dry_run:
        clauses = (
            where_clauses_for_ids("student_module_id IN ({ids})", student_module_ids, chunk_size=_CSMH_CHUNK_SIZE)
            if student_module_ids
            else []
        )
        dbutil.dump_table_chunked(csmh_conn_params, CSMH_TABLE, out_path, clauses)
    return len(student_module_ids)
