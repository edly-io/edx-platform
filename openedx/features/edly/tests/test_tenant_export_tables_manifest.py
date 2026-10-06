"""Offline regression tests: journal WHERE leak fix, manifest status semantics, error stderr capture."""
import subprocess
import tempfile
import unittest

from openedx.features.edly.tenant_export.dbutil import describe_error
from openedx.features.edly.tenant_export import manifest as manifest_mod
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


class EdmParityTests(unittest.TestCase):
    """Every EDM edxapp table (tiers 2-10, checked-in fixture copy) must be dumped or documented as excluded."""

    def _edm_tables(self):
        from pathlib import Path
        path = Path(__file__).parent / "fixtures" / "edm_edxapp_tier_2_10.txt"
        return [line.split()[1] for line in path.read_text().splitlines() if line.strip()]

    def test_every_edm_table_is_dumped_or_excluded(self):
        from openedx.features.edly.tenant_export import tables
        accounted = set(tables.ALL_TIER_TABLES) | tables.excluded_keys()
        missing = [
            t for t in self._edm_tables()
            if tables.SOURCE_TABLE_NAME_OVERRIDES.get(t, t) not in accounted
        ]
        self.assertEqual(missing, [])

    def test_excluded_never_dumped_and_have_reasons(self):
        from openedx.features.edly.tenant_export import tables
        self.assertFalse(set(tables.EXCLUDED) & set(tables.ALL_TIER_TABLES))
        self.assertTrue(all(tables.EXCLUDED.values()))

    def test_every_dumped_table_has_a_where_builder(self):
        from openedx.features.edly.tenant_export import tables
        for t in tables.OTHER_TIER_TABLES:
            clause = tables.tier_other_where(t, 7, ["MITx", "my_org"])
            self.assertTrue(clause)
        # LIKE escaping applied to underscores in org names
        self.assertIn("my\\_org", tables.tier_other_where("teams_courseteam", 7, ["my_org"]))

    def test_secret_bearing_tables_not_dumped(self):
        from openedx.features.edly.tenant_export import secrets, tables
        for t in ("third_party_auth_oauth2providerconfig", "third_party_auth_samlconfiguration",
                  "third_party_auth_samlproviderconfig", "oauth_dispatch_applicationaccess"):
            self.assertNotIn(t, tables.ALL_TIER_TABLES)
            self.assertIn(t, tables.excluded_keys())
        self.assertIn("lti_consumer_lticonfiguration", secrets.SECRET_COLUMNS)

    def test_koa_names_not_ulmo_names(self):
        from openedx.features.edly.tenant_export import tables
        self.assertIn("certificates_certificatewhitelist", tables.ALL_TIER_TABLES)
        self.assertNotIn("certificates_certificateallowlist", tables.ALL_TIER_TABLES)


class ManifestExcludedHonestyTests(unittest.TestCase):
    def test_complete_impossible_until_excluded_recorded(self):
        d = tempfile.mkdtemp()
        mf = Manifest(f"{d}/MANIFEST.json", "t", "sha", ["a"], expected_excluded=["x"])
        mf.update_table("a", status="complete")
        self.assertEqual(mf.finalize(), "incomplete")
        mf.update_table("x", status="excluded_by_design", reason="why")
        self.assertEqual(mf.finalize(), "complete")

    def test_seed_records_all_excluded_with_reason(self):
        from openedx.features.edly.tenant_export import tables
        d = tempfile.mkdtemp()
        mf = Manifest(f"{d}/MANIFEST.json", "t", "sha", [], expected_excluded=tables.excluded_keys())
        manifest_mod.seed_excluded_entries(mf)
        self.assertEqual(mf.finalize(), "complete")
        self.assertTrue(mf.data["tables"]["third_party_auth_samlconfiguration"]["reason"])
