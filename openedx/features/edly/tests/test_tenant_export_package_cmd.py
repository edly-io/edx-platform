"""export_tenant_package exit codes (EDLYPRODUCT-8584): complete_with_errors fails
unless --allow-errors. Handler invoked directly (no Django settings needed)."""
import hashlib
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from django.core.management.base import CommandError

from openedx.features.edly.management.commands.export_tenant_package import Command


def _run(status_tables, **opts):
    out = Path(tempfile.mkdtemp())
    for name, entry in status_tables.items():  # real file + sha so verify_entry_files is satisfied
        if entry["status"] == "complete":
            (out / f"{name}.sql").write_text("x")
            entry["sha256"] = hashlib.sha256(b"x").hexdigest()
    (out / "MANIFEST.json").write_text(json.dumps({"tenant_slug": "mit", "scope_sha256": "s", "tables": status_tables}))
    cmd = Command(stdout=StringIO(), stderr=StringIO())
    options = dict(slug="mit", out_dir=str(out), skip_db=["credentials", "discovery", "ecommerce", "notes"], skip_forum=True,
                   skip_s3=True, allow_errors=False)
    options.update(opts)
    cmd.handle(**options)
    return cmd


class PackageExitCodeTests(unittest.TestCase):
    def _tables(self, **extra):
        from openedx.features.edly.tenant_export import tables
        data = {t: {"status": "complete"} for t in tables.EXPECTED_TABLES}
        data.update(extra)
        return data

    def test_notes_required_unless_skipped(self):
        with self.assertRaises(CommandError):
            _run(self._tables(), skip_db=["credentials", "discovery", "ecommerce"])

    def test_complete_exits_zero(self):
        _run(self._tables())

    def test_complete_with_errors_fails_by_default(self):
        with self.assertRaises(CommandError) as cm:
            _run(self._tables(extra_step={"status": "error"}))
        self.assertIn("--allow-errors", str(cm.exception))

    def test_allow_errors_accepts_complete_with_errors(self):
        _run(self._tables(extra_step={"status": "error"}), allow_errors=True)

    def test_incomplete_still_fails_with_allow_errors(self):
        with self.assertRaises(CommandError):
            _run({}, allow_errors=True)


if __name__ == "__main__":
    unittest.main()
