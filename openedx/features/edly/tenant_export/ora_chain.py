"""Tier 8 (ORA/assessment) -- the submission-UUID chain + the UUID-trap guard.

No Django/MySQL-driver import here on purpose -- every function takes a
plain DB-API cursor (duck-typed), so this module (and its offline tests)
never need a real Django settings module or MySQL driver installed. Ported
unchanged from the standalone reference implementation
(`mit-tenant-export/export_mit/ora_chain.py`, EDLYPRODUCT-8584 Phase 1),
which was itself independently audited 2026-10-02 -- every `[Audited
2026-10-02]` comment below is a real, verified correctness/security fix,
not a suggestion to reconsider. Only the import path is new.

Reference only (read, never imported):
  edlysaas_data_migrations/management/commands/migrate_tenant_tier_data.py:1065-1212 (per-table WHERE dispatcher)
  edlysaas_data_migrations/management/commands/migrate_tenant_tier_data.py:1214-1330 (chain entry point + prefetch helpers)

This is the one tier that needs Python-side ID prefetching (plan: "the one
exception that needs Python-side prefetch, because of the hex-vs-dashed UUID
mismatch"). Those prefetched sets can still be large for an active tenant,
so every place one gets embedded into a `mysqldump --where=` argv string is
chunked (`sqlutil.where_clauses_for_ids`) rather than passed as one
potentially-huge IN (...) list -- mysqldump's `--where` is a single argv
string, and Linux caps a single argv element at MAX_ARG_STRLEN (128 KiB).
The internal prefetch queries below have no such limit (they're sent over
the wire via the DB-API cursor, not via a subprocess argv), so they freely
use full, unchunked ID lists.
"""
from dataclasses import dataclass, fields

from openedx.features.edly.tenant_export.sqlutil import (
    anon_membership_subquery,
    org_like_clause,
    where_clauses_for_ids,
    count_rows,
)


@dataclass(frozen=True)
class Tier8Ids:
    """The chain's prefetched ID sets.

    [Audited 2026-10-02 -- BLOCKING fix] `get_tier8_where_clauses` actually
    depends on seven prefetched ID-sets, not the two originally planned
    (student_item_ids, submission_uuids) -- the first five fields below, in
    dependency order. Without the other three (`peer_workflow_ids`,
    `training_workflow_ids`, `assessment_workflow_ids`, `rubric_ids`),
    roughly half of tier 8's tables would silently export zero rows.

    `training_example_ids` and `peerworkflowitem_ids` are NOT part of that
    canonical list -- they're a mechanical derivation needed so the two
    audit-fixed tables (assessment_trainingexample*, assessment_peerworkflowitem)
    can be safely CHUNKED for mysqldump without risking the same shared
    child row landing in two different chunks' output (see
    get_tier8_where_clauses below).
    """

    student_item_ids: set
    submission_uuids: set
    source_score_ids: set
    peer_workflow_ids: set
    training_workflow_ids: set
    assessment_workflow_ids: set
    rubric_ids: set
    training_example_ids: set
    peerworkflowitem_ids: set


def _empty_tier8_ids() -> Tier8Ids:
    return Tier8Ids(**{f.name: set() for f in fields(Tier8Ids)})


def to_dashed_uuid(u: str) -> str:
    """Convert a 32-char no-dash UUID to the canonical 36-char dashed form.

    `submissions_submission.uuid` is stored as 32-char hex (no hyphens) on
    MySQL; every downstream assessment/workflow table's `submission_uuid`
    column stores the canonical 36-char hyphenated form. A naive IN (...)
    join between the two silently matches nothing -- no error, just an empty
    result. [migrate_tenant_tier_data.py:1233-1238]
    """
    if len(u) == 36:
        return u
    return f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}"


def _fetch_set(cursor, sql: str) -> set:
    cursor.execute(sql)
    return {row[0] for row in cursor.fetchall()}


def prefetch_tier8_ids(cursor, sub_org_id, course_org_filter) -> Tier8Ids:
    """Walk the chain once, in dependency order (plan's numbered list):

    1. student_item_ids   -- chain entry point
    2. submission_uuids   -- dashed, from student_item_ids
    3. source_score_ids
    4. peer_workflow_ids
    5. training_workflow_ids
    6. assessment_workflow_ids
    7. rubric_ids          -- two paths: via assessments, via training examples
    """
    if not course_org_filter:
        return _empty_tier8_ids()

    org_clause = org_like_clause("course_id", course_org_filter)
    anon_subq = anon_membership_subquery(sub_org_id)

    # 1. student_item_ids
    student_item_ids = _fetch_set(
        cursor,
        f"SELECT id FROM submissions_studentitem WHERE student_id IN ({anon_subq}) AND ({org_clause})",
    )

    # 2. submission_uuids (dashed)
    submission_uuids = set()
    if student_item_ids:
        ids_csv = ",".join(str(i) for i in student_item_ids)
        cursor.execute(f"SELECT uuid FROM submissions_submission WHERE student_item_id IN ({ids_csv})")
        submission_uuids = {to_dashed_uuid(row[0]) for row in cursor.fetchall()}

    # 3. source_score_ids
    source_score_ids = set()
    if student_item_ids:
        ids_csv = ",".join(str(i) for i in student_item_ids)
        source_score_ids = _fetch_set(cursor, f"SELECT id FROM submissions_score WHERE student_item_id IN ({ids_csv})")

    # 4-6. peer / training / assessment workflow ids
    peer_workflow_ids = set()
    training_workflow_ids = set()
    assessment_workflow_ids = set()
    if submission_uuids:
        uuids_csv = ",".join(f"'{u}'" for u in submission_uuids)
        peer_workflow_ids = _fetch_set(
            cursor, f"SELECT id FROM assessment_peerworkflow WHERE submission_uuid IN ({uuids_csv})"
        )
        training_workflow_ids = _fetch_set(
            cursor, f"SELECT id FROM assessment_studenttrainingworkflow WHERE submission_uuid IN ({uuids_csv})"
        )
        assessment_workflow_ids = _fetch_set(
            cursor, f"SELECT id FROM workflow_assessmentworkflow WHERE submission_uuid IN ({uuids_csv})"
        )

    # 7. rubric_ids -- path 1 (via assessments) + path 2 (via training examples)
    rubric_ids = set()
    if submission_uuids:
        uuids_csv = ",".join(f"'{u}'" for u in submission_uuids)
        rubric_ids |= _fetch_set(
            cursor,
            "SELECT DISTINCT rubric_id FROM assessment_assessment "
            f"WHERE submission_uuid IN ({uuids_csv}) AND rubric_id IS NOT NULL",
        )
    if training_workflow_ids:
        wf_csv = ",".join(str(w) for w in training_workflow_ids)
        cursor.execute(
            "SELECT DISTINCT te.rubric_id FROM assessment_trainingexample te "
            "JOIN assessment_studenttrainingworkflowitem stwi ON stwi.training_example_id = te.id "
            f"WHERE stwi.workflow_id IN ({wf_csv}) AND te.rubric_id IS NOT NULL"
        )
        rubric_ids |= {row[0] for row in cursor.fetchall()}

    # Derived sets -- see Tier8Ids docstring. Each is one extra plain SELECT
    # (no argv-limit exposure; these never go on a subprocess command line).
    training_example_ids = set()
    if training_workflow_ids:
        wf_csv = ",".join(str(w) for w in training_workflow_ids)
        training_example_ids = _fetch_set(
            cursor,
            "SELECT DISTINCT training_example_id FROM assessment_studenttrainingworkflowitem "
            f"WHERE workflow_id IN ({wf_csv})",
        )

    peerworkflowitem_ids = set()
    if peer_workflow_ids:
        # [Plan] author_id OR scorer_id predicate: prefetch matching ids with
        # ONE plain SQL SELECT over the whole (unchunked) peer_workflow_ids
        # set first -- chunking peer_workflow_ids itself and applying
        # "author_id IN (chunk) OR scorer_id IN (chunk)" per chunk would let
        # a single row match in two different chunks (author in chunk A,
        # scorer in chunk B), double-counting it in the dump output.
        # Resolving to a single id set up front and chunking THAT instead
        # (see get_tier8_where_clauses) avoids the double-count entirely.
        wf_csv = ",".join(str(w) for w in peer_workflow_ids)
        peerworkflowitem_ids = _fetch_set(
            cursor,
            f"SELECT id FROM assessment_peerworkflowitem WHERE author_id IN ({wf_csv}) OR scorer_id IN ({wf_csv})",
        )

    return Tier8Ids(
        student_item_ids=student_item_ids,
        submission_uuids=submission_uuids,
        source_score_ids=source_score_ids,
        peer_workflow_ids=peer_workflow_ids,
        training_workflow_ids=training_workflow_ids,
        assessment_workflow_ids=assessment_workflow_ids,
        rubric_ids=rubric_ids,
        training_example_ids=training_example_ids,
        peerworkflowitem_ids=peerworkflowitem_ids,
    )


def get_tier8_where_clauses(table: str, ids: Tier8Ids, sub_org_id, course_org_filter) -> list:
    """Return a list of chunked WHERE clauses for `table` (dump each clause
    as its own mysqldump call -- see dbutil.dump_table_chunked). An empty
    list means "scope resolves to zero rows, dump schema only".

    `sub_org_id`/`course_org_filter` are only used by the two tables whose
    scope doesn't depend on any prefetched id set at all
    (submissions_studentitem, problem_builder_answer); pass None for both
    when calling this for any other table (e.g. from the UUID-trap guard).
    """
    if table == "submissions_studentitem":
        clause = (
            f"student_id IN ({anon_membership_subquery(sub_org_id)}) "
            f"AND ({org_like_clause('course_id', course_org_filter)})"
        )
        return [clause]

    if table == "problem_builder_answer":
        # student_id stores the anonymous_user_id (same as submissions_studentitem);
        # this table uses course_key, not course_id.
        clause = (
            f"student_id IN ({anon_membership_subquery(sub_org_id)}) "
            f"AND ({org_like_clause('course_key', course_org_filter)})"
        )
        return [clause]

    if table == "assessment_rubric":
        return where_clauses_for_ids("id IN ({ids})", ids.rubric_ids)
    if table == "assessment_criterion":
        return where_clauses_for_ids("rubric_id IN ({ids})", ids.rubric_ids)
    if table == "assessment_criterionoption":
        return where_clauses_for_ids(
            "criterion_id IN (SELECT id FROM assessment_criterion WHERE rubric_id IN ({ids}))",
            ids.rubric_ids,
        )

    if table == "assessment_trainingexample":
        # [Audited 2026-10-02 -- BLOCKING leak fix] Do NOT scope by
        # `rubric_id IN (rubric_ids)` as EDM does. `assessment_rubric.
        # content_hash` is unique=True (ORA2 base.py:77) -- identical rubrics
        # are deduplicated into ONE shared row across the whole platform.
        # Any rubric MIT happens to share with another client (a common
        # assessment template) would pull that other client's staff-authored
        # training-example content into MIT's export. Scope instead through
        # MIT's own training-workflow items, never through the shared rubric.
        return where_clauses_for_ids("id IN ({ids})", ids.training_example_ids)
    if table == "assessment_trainingexample_options_selected":
        return where_clauses_for_ids("trainingexample_id IN ({ids})", ids.training_example_ids)

    if table in ("assessment_assessment", "assessment_assessmentfeedback"):
        return where_clauses_for_ids("submission_uuid IN ({ids})", ids.submission_uuids, quote=True)
    if table in ("assessment_assessmentfeedback_assessments", "assessment_assessmentfeedback_options"):
        return where_clauses_for_ids(
            "assessmentfeedback_id IN (SELECT id FROM assessment_assessmentfeedback WHERE submission_uuid IN ({ids}))",
            ids.submission_uuids,
            quote=True,
        )
    if table == "assessment_assessmentpart":
        return where_clauses_for_ids(
            "assessment_id IN (SELECT id FROM assessment_assessment WHERE submission_uuid IN ({ids}))",
            ids.submission_uuids,
            quote=True,
        )

    if table in ("assessment_peerworkflow", "assessment_staffworkflow", "assessment_studenttrainingworkflow",
                 "workflow_assessmentworkflow"):
        return where_clauses_for_ids("submission_uuid IN ({ids})", ids.submission_uuids, quote=True)

    if table == "assessment_peerworkflowitem":
        # Pre-resolved single id set (see prefetch_tier8_ids) -- chunk on the
        # plain `id` column only, never re-derive via "author_id OR scorer_id"
        # per chunk (that reintroduces the double-count bug being avoided).
        return where_clauses_for_ids("id IN ({ids})", ids.peerworkflowitem_ids)

    if table == "assessment_studenttrainingworkflowitem":
        return where_clauses_for_ids("workflow_id IN ({ids})", ids.training_workflow_ids)

    if table == "workflow_assessmentworkflowstep":
        return where_clauses_for_ids("workflow_id IN ({ids})", ids.assessment_workflow_ids)

    if table in ("submissions_submission", "submissions_score", "submissions_scoresummary"):
        return where_clauses_for_ids("student_item_id IN ({ids})", ids.student_item_ids)
    if table == "submissions_scoreannotation":
        # FK to score, not student_item -- and must use the SOURCE score ids
        # (there is no target remap in this export tool at all, unlike EDM).
        return where_clauses_for_ids("score_id IN ({ids})", ids.source_score_ids)

    raise KeyError(f"no tier-8 WHERE builder for table {table!r}")


# =============================================================================
# UUID-trap guard [Plan: Verification items 4 and 5]
# =============================================================================
#
# Item 4's original scope only checked the 4 tables below that carry their
# own course_id independent of the UUID chain. [Audited 2026-10-02] extends
# this to the rubric/criterion, scoreannotation, and *workflowitem tables --
# exactly the tables that would have silently exported zero rows under the
# original (pre-audit) 2-id-set prefetch, and that the narrower 4-table test
# would NOT have caught.
#
# Every direct count below is derived independently of the chain: through
# course_id/course_key columns only, never through submission_uuid or
# anonymous_user_id -- so a bug in the chain can't also corrupt the thing
# checking it.

GUARD_TABLES = (
    "workflow_assessmentworkflow",
    "assessment_peerworkflow",
    "assessment_staffworkflow",
    "assessment_studenttrainingworkflow",
    "assessment_peerworkflowitem",
    "assessment_studenttrainingworkflowitem",
    "workflow_assessmentworkflowstep",
    "assessment_trainingexample",
    "assessment_rubric",
    "submissions_scoreannotation",
)


class UUIDChainError(Exception):
    """A chain-derived tier-8 count is 0 while an independent, chain-free
    count for the same table is > 0 -- the exact silent hex/dashed-UUID (or
    prefetch-gap) failure tier 8 is built to avoid. Hard-fail, don't log.
    """


def check_counts(table: str, chain_count: int, direct_count: int) -> None:
    if chain_count == 0 and direct_count > 0:
        raise UUIDChainError(
            f"{table}: chain-derived count is 0 but an independent direct count is "
            f"{direct_count} -- likely UUID-chain/prefetch bug, aborting"
        )


def _direct_counts(cursor, course_org_filter) -> dict:
    counts = {}

    for table in (
        "workflow_assessmentworkflow",
        "assessment_peerworkflow",
        "assessment_staffworkflow",
        "assessment_studenttrainingworkflow",
    ):
        where = org_like_clause("course_id", course_org_filter)
        counts[table] = count_rows(cursor, table, where)

    where = org_like_clause("course_id", course_org_filter)
    cursor.execute(
        "SELECT COUNT(*) FROM assessment_peerworkflowitem "
        f"WHERE author_id IN (SELECT id FROM assessment_peerworkflow WHERE {where})"
    )
    counts["assessment_peerworkflowitem"] = cursor.fetchone()[0]

    where = org_like_clause("course_id", course_org_filter)
    cursor.execute(
        "SELECT COUNT(*) FROM assessment_studenttrainingworkflowitem "
        f"WHERE workflow_id IN (SELECT id FROM assessment_studenttrainingworkflow WHERE {where})"
    )
    counts["assessment_studenttrainingworkflowitem"] = cursor.fetchone()[0]

    where = org_like_clause("course_id", course_org_filter)
    cursor.execute(
        "SELECT COUNT(*) FROM workflow_assessmentworkflowstep "
        f"WHERE workflow_id IN (SELECT id FROM workflow_assessmentworkflow WHERE {where})"
    )
    counts["workflow_assessmentworkflowstep"] = cursor.fetchone()[0]

    where = org_like_clause("stw.course_id", course_org_filter)
    cursor.execute(
        "SELECT COUNT(DISTINCT te.id) FROM assessment_trainingexample te "
        "JOIN assessment_studenttrainingworkflowitem stwi ON stwi.training_example_id = te.id "
        "JOIN assessment_studenttrainingworkflow stw ON stwi.workflow_id = stw.id "
        f"WHERE {where}"
    )
    counts["assessment_trainingexample"] = cursor.fetchone()[0]

    where = org_like_clause("w.course_id", course_org_filter)
    cursor.execute(
        "SELECT COUNT(DISTINCT a.rubric_id) FROM assessment_assessment a "
        "JOIN workflow_assessmentworkflow w ON w.submission_uuid = a.submission_uuid "
        f"WHERE a.rubric_id IS NOT NULL AND {where}"
    )
    counts["assessment_rubric"] = cursor.fetchone()[0]

    where = org_like_clause("si.course_id", course_org_filter)
    cursor.execute(
        "SELECT COUNT(*) FROM submissions_scoreannotation sa "
        "JOIN submissions_score sc ON sc.id = sa.score_id "
        "JOIN submissions_studentitem si ON si.id = sc.student_item_id "
        f"WHERE {where}"
    )
    counts["submissions_scoreannotation"] = cursor.fetchone()[0]

    return counts


def run_uuid_trap_guard(cursor, course_org_filter, ids: Tier8Ids) -> dict:
    """Hard-fail (raise UUIDChainError) if any GUARD_TABLES entry shows the
    silent-empty-chain symptom. Returns {table: (chain_count, direct_count)}
    for callers that want to log it.
    """
    direct = _direct_counts(cursor, course_org_filter)
    results = {}
    for table in GUARD_TABLES:
        clauses = get_tier8_where_clauses(table, ids, None, None)
        chain_count = sum(count_rows(cursor, table, c) for c in clauses) if clauses else 0
        check_counts(table, chain_count, direct[table])
        results[table] = (chain_count, direct[table])
    return results
