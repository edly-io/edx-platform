"""Offline regression tests: journal WHERE leak fix, manifest status semantics, error stderr capture."""
import subprocess
import tempfile
import unittest

from openedx.features.edly.tenant_export.dbutil import describe_error
from openedx.features.edly.tenant_export.manifest import Manifest
from openedx.features.edly.tenant_export.sqlutil import membership_subquery, org_like_clause
from openedx.features.edly.tenant_export.tables import tier7_where


class JournalWhereTests(unittest.TestCase):
    def test_requires_membership_and_org_no_unfiltered_branch(self):
        clause = tier7_where("journal_djangoapp_journalmodel", 7, "MITx")
        self.assertEqual(
            clause, f"user IN ({membership_subquery(7)}) AND ({org_like_clause('course_id', 'MITx')})",
        )
        self.assertNotIn(" OR ", clause.replace(membership_subquery(7), "").replace(
            org_like_clause('course_id', 'MITx'), ""))
        self.assertNotIn("IS NULL", clause)


class ManifestStatusTests(unittest.TestCase):
    def _mf(self):
        d = tempfile.mkdtemp()
        return Manifest(f"{d}/MANIFEST.json", "t", "sha", ["a", "b", "c"])

    def test_error_with_unattempted_tables_is_incomplete(self):
        mf = self._mf()
        mf.update_table("a", status="error", error="boom")
        self.assertEqual(mf.finalize(), "incomplete")

    def test_all_attempted_some_errors(self):
        mf = self._mf()
        mf.update_table("a", status="error")
        mf.update_table("b", status="complete")
        mf.update_table("c", status="complete")
        self.assertEqual(mf.finalize(), "complete_with_errors")

    def test_all_complete(self):
        mf = self._mf()
        for t in "abc":
            mf.update_table(t, status="complete")
        self.assertEqual(mf.finalize(), "complete")


class DescribeErrorTests(unittest.TestCase):
    def test_includes_stderr(self):
        exc = subprocess.CalledProcessError(2, ["mysqldump"], stderr=b"Access denied")
        self.assertIn("Access denied", describe_error(exc))
