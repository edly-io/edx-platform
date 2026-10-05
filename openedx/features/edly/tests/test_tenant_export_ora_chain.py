"""
UUID-trap guard test [Plan Verification items 4 and 5]. Ported unchanged
(besides the import path) from the standalone reference implementation's
`tests/test_ora_chain.py` (EDLYPRODUCT-8584 Phase 1) -- it was already
duck-typed against a plain cursor, not driver-specific, so it ports as-is.

Two parts:

* Offline (always runs, no DB needed): `to_dashed_uuid`, the raise/no-raise
  behavior of `check_counts`, and a regression lock on the two
  [Audited 2026-10-02] leak fixes -- the rubric-dedup leak
  (assessment_trainingexample must never reference rubric_id) and the
  peerworkflowitem double-count fix (must use the pre-resolved id set, never
  "author_id OR scorer_id" per chunk).

* Live (skipped unless EDLY_TENANT_EXPORT_TEST_DB_HOST/USER/PASSWORD are
  set): would run the real tier-8 prefetch + UUID-trap guard against a live
  Koa source DB -- not runnable in this environment (no live DB connection
  available), kept here as the hook for the demo-site test
  (MIT-EXPORT-EDLYPRODUCT-8584-PLAN.md Sec 11.5).
"""
import os
import unittest

from openedx.features.edly.tenant_export import ora_chain
from openedx.features.edly.tenant_export.ora_chain import (
    Tier8Ids,
    UUIDChainError,
    check_counts,
    get_tier8_where_clauses,
    to_dashed_uuid,
)


class ToDashedUUIDTests(unittest.TestCase):
    def test_already_dashed_is_unchanged(self):
        u = "12345678-1234-1234-1234-123456789012"
        self.assertEqual(to_dashed_uuid(u), u)

    def test_hex_converts_to_dashed(self):
        hex_uuid = "12345678123412341234123456789012"
        self.assertEqual(to_dashed_uuid(hex_uuid), "12345678-1234-1234-1234-123456789012")


class CheckCountsTests(unittest.TestCase):
    """The guard only cares about the 0-vs->0 case -- that's the exact
    silent-empty-chain symptom tier 8 is built to avoid.
    """

    def test_raises_on_silent_chain_failure(self):
        with self.assertRaises(UUIDChainError):
            check_counts("workflow_assessmentworkflow", chain_count=0, direct_count=5)

    def test_ok_when_both_zero(self):
        check_counts("workflow_assessmentworkflow", chain_count=0, direct_count=0)  # must not raise

    def test_ok_when_chain_has_rows(self):
        check_counts("workflow_assessmentworkflow", chain_count=5, direct_count=5)  # must not raise

    def test_ok_when_chain_exceeds_direct(self):
        # Not expected in practice (direct counts are a superset), but the
        # guard is only a tripwire for chain==0 while direct>0.
        check_counts("workflow_assessmentworkflow", chain_count=7, direct_count=5)  # must not raise


def _fake_tier8_ids(**overrides) -> Tier8Ids:
    base = dict(
        student_item_ids=set(),
        submission_uuids=set(),
        source_score_ids=set(),
        peer_workflow_ids=set(),
        training_workflow_ids={1, 2},
        assessment_workflow_ids=set(),
        rubric_ids={99},
        training_example_ids={10, 11},
        peerworkflowitem_ids={5, 6},
    )
    base.update(overrides)
    return Tier8Ids(**base)


class RubricLeakRegressionTests(unittest.TestCase):
    """[Audited 2026-10-02 -- BLOCKING leak fix] assessment_rubric.content_hash
    is deduplicated platform-wide (unique=True); scoping trainingexample
    tables by rubric_id would pull another tenant's shared-rubric training
    content into this export. Locks that fix in place so it can't silently
    regress back to EDM's original (unsafe-for-this-tool) approach.
    """

    def test_trainingexample_where_never_mentions_rubric_id(self):
        ids = _fake_tier8_ids()
        for table in ("assessment_trainingexample", "assessment_trainingexample_options_selected"):
            for clause in get_tier8_where_clauses(table, ids, None, None):
                self.assertNotIn("rubric_id", clause, f"{table} must not be scoped via rubric_id")

    def test_trainingexample_scoped_via_training_workflow_items(self):
        ids = _fake_tier8_ids()
        clauses = get_tier8_where_clauses("assessment_trainingexample", ids, None, None)
        self.assertEqual(clauses, ["id IN (10,11)"])

    def test_trainingexample_options_selected_scoped_via_same_set(self):
        ids = _fake_tier8_ids()
        clauses = get_tier8_where_clauses("assessment_trainingexample_options_selected", ids, None, None)
        self.assertEqual(clauses, ["trainingexample_id IN (10,11)"])


class PeerWorkflowItemChunkingTests(unittest.TestCase):
    """[Plan] chunking "author_id IN (chunk) OR scorer_id IN (chunk)" per
    chunk can double-count a row (author in one chunk, scorer in another).
    Must use the pre-resolved, already-deduplicated id set instead.
    """

    def test_uses_preresolved_ids_not_author_or_scorer(self):
        ids = _fake_tier8_ids()
        clauses = get_tier8_where_clauses("assessment_peerworkflowitem", ids, None, None)
        self.assertEqual(clauses, ["id IN (5,6)"])
        for clause in clauses:
            self.assertNotIn(" OR ", clause)


class TrainingExampleEmptySetTests(unittest.TestCase):
    def test_empty_training_example_ids_yields_no_clauses(self):
        ids = _fake_tier8_ids(training_example_ids=set())
        self.assertEqual(get_tier8_where_clauses("assessment_trainingexample", ids, None, None), [])


_LIVE_ENV_VARS = (
    "EDLY_TENANT_EXPORT_TEST_DB_HOST",
    "EDLY_TENANT_EXPORT_TEST_DB_USER",
    "EDLY_TENANT_EXPORT_TEST_DB_PASSWORD",
)


@unittest.skipUnless(
    all(os.environ.get(v) for v in _LIVE_ENV_VARS),
    "live Koa DB not configured (set EDLY_TENANT_EXPORT_TEST_DB_HOST/USER/PASSWORD) -- "
    "this is the check meant to run on the demo site, not in an offline/CI run",
)
class LiveUUIDTrapGuardTests(unittest.TestCase):
    """Runs the real tier-8 prefetch + UUID-trap guard against a live Koa
    source DB for a real tenant slug (EDLY_TENANT_EXPORT_TEST_SLUG, default
    "MIT"). Deliberately connects directly (not via Django's `connection`)
    so this test can run even outside a configured Django settings module --
    mirrors how an operator would do a one-off live check on the demo site.
    """

    @classmethod
    def setUpClass(cls):
        import MySQLdb  # local import: only this live path needs a driver

        cls.conn = MySQLdb.connect(
            host=os.environ["EDLY_TENANT_EXPORT_TEST_DB_HOST"],
            port=int(os.environ.get("EDLY_TENANT_EXPORT_TEST_DB_PORT", "3306")),
            user=os.environ["EDLY_TENANT_EXPORT_TEST_DB_USER"],
            passwd=os.environ["EDLY_TENANT_EXPORT_TEST_DB_PASSWORD"],
            db=os.environ.get("EDLY_TENANT_EXPORT_TEST_DB_NAME", "edxapp"),
        )
        cls.slug = os.environ.get("EDLY_TENANT_EXPORT_TEST_SLUG", "MIT")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_prefetch_and_guard_do_not_raise(self):
        from openedx.features.edly.tenant_export import scope as scope_mod

        with self.conn.cursor() as cursor:
            resolved = scope_mod.resolve_scope(cursor, self.slug)
            ids = ora_chain.prefetch_tier8_ids(cursor, resolved["sub_org_id"], resolved["course_org_filter"])
            ora_chain.run_uuid_trap_guard(cursor, resolved["course_org_filter"], ids)


if __name__ == "__main__":
    unittest.main()
