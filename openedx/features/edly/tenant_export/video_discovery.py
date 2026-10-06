"""Video-id discovery for the video-meta S3 resolver (EDLYPRODUCT-8584 Phase 3):
SQL (`edxval_coursevideo`) UNION Mongo modulestore video xblocks. Logic copied
from edlysaas_data_migrations/utils/video_discovery.py (read-only reference,
never imported). Most tenants' videos exist ONLY via the Mongo path (Studio
writes `edx_video_id` inline, no coursevideo row), so SQL-only would badly
undercount. Stdlib only; takes `edx_rows(sql)` + a pymongo-like db.

Difference from EDM: EDM binds `edx_video_id` values as query PARAMETERS, so any string works.
We hand-build the IN list (the export's `edx_rows` takes no params because the SQL carries literal
`%`), so ids with any character outside [A-Za-z0-9-_.] are REJECTED (never escaped), reported in
`unresolved_edx_video_ids`, and their transcripts/images are NOT copied. Real ids are UUID-like or
`external-video-*`, so this should be empty; a non-empty list is the signal to look at them by hand.
"""
from openedx.features.edly.tenant_export.sqlutil import chunk_list, org_like_clause

_SAFE_EVID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def _quoted(values):
    """SQL-quote values; anything outside [A-Za-z0-9-_.] is returned as `rejected`
    rather than escaped (video ids are UUID-like; never hand-escape)."""
    ok, rejected = [], []
    for v in values:
        (ok if v and set(v) <= _SAFE_EVID_CHARS else rejected).append(v)
    return ok, rejected


def mongo_edx_video_ids(modulestore_db, orgs) -> set:
    """edx_video_id strings of video xblocks in the orgs' published/draft structures."""
    structure_ids = set()
    for course in modulestore_db["modulestore.active_versions"].find({"org": {"$in": list(orgs)}}):
        for branch in ("published-branch", "draft-branch"):
            if (course.get("versions") or {}).get(branch):
                structure_ids.add(course["versions"][branch])

    evids, defs_missing = set(), set()
    for chunk in chunk_list(list(structure_ids), 100):
        for structure in modulestore_db["modulestore.structures"].find({"_id": {"$in": chunk}}):
            for block in structure.get("blocks", []):
                if not isinstance(block, dict) or block.get("block_type") != "video":
                    continue
                fields = block.get("fields") if isinstance(block.get("fields"), dict) else {}
                if fields.get("edx_video_id"):
                    evids.add(fields["edx_video_id"])
                elif block.get("definition"):
                    defs_missing.add(block["definition"])
    for chunk in chunk_list(list(defs_missing), 500):  # fallback: definition.fields.edx_video_id
        for definition in modulestore_db["modulestore.definitions"].find({"_id": {"$in": chunk}}):
            fields = definition.get("fields")
            if isinstance(fields, dict) and fields.get("edx_video_id"):
                evids.add(fields["edx_video_id"])
    return evids


def discover_video_ids(edx_rows, modulestore_db, orgs, log=print) -> tuple:
    """-> (edxval_video.id set, stats). `modulestore_db` is required: a silent
    SQL-only fallback would undercount (hard error instead, caller decides)."""
    if modulestore_db is None:
        raise ValueError("Mongo modulestore connection required for video discovery (DOC_STORE_CONFIG)")
    orgs = list(orgs)
    sql_ids = {int(r[0]) for r in edx_rows(
        f"SELECT DISTINCT video_id FROM edxval_coursevideo WHERE ({org_like_clause('course_id', orgs)})"
    )}

    evids = mongo_edx_video_ids(modulestore_db, orgs)
    ok, rejected = _quoted(sorted(evids))
    mongo_ids, resolved = set(), set()
    for chunk in chunk_list(ok, 500):
        quoted = ",".join(f"'{v}'" for v in chunk)
        for pk, evid in edx_rows(f"SELECT id, edx_video_id FROM edxval_video WHERE edx_video_id IN ({quoted})"):
            mongo_ids.add(int(pk))
            resolved.add(evid)
    unresolved = sorted(set(ok) - resolved) + rejected
    if unresolved:
        log(f"video discovery: {len(unresolved)} edx_video_id(s) have no edxval_video row, e.g. {unresolved[:5]}")
    stats = {"coursevideo_ids": len(sql_ids), "mongo_edx_video_ids": len(evids),
             "unresolved_edx_video_ids": unresolved, "total": len(sql_ids | mongo_ids)}
    return sql_ids | mongo_ids, stats
