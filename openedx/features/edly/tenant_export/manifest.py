"""MANIFEST.json writer -- incremental, per-table, with a trustworthy
top-level `status` computed against the FULL expected table set.

"Complete if nothing errored and the tables dict is non-empty" would let a
run killed after 10 of ~50 tables read as "complete" -- exactly the field the
plan says is "the one field to check before trusting a run". So `status` is
instead computed against `expected_tables`: every table we actually intend
to produce for this run must reach a terminal status before the manifest can
read "complete".

Ported unchanged (stdlib only, no Django import) from the standalone
reference implementation (`mit-tenant-export/export_mit/manifest.py`,
EDLYPRODUCT-8584 Phase 1), plus one addition: `seed_excluded_entries`,
previously a private helper in that tool's single `cli.py` -- now shared
between the `export_tenant_mysql` and `export_tenant_csmh` management
commands, which both write into the same on-disk manifest.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Statuses that count as "this table is done, one way or another" for the
# purpose of deciding whether the overall run is complete.
_TERMINAL_STATUSES = {"complete", "skipped_not_in_source"}


class Manifest:
    def __init__(self, path, tenant_slug: str, scope_sha256: str, expected_tables):
        self.path = Path(path)
        self.expected_tables = set(expected_tables)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {
                "tenant_slug": tenant_slug,
                "scope_sha256": scope_sha256,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "tables": {},
                "status": "incomplete",
            }

    def update_table(self, name: str, **fields) -> None:
        entry = self.data["tables"].get(name, {})
        entry.update(fields)
        self.data["tables"][name] = entry
        self._recompute_status()
        self._write()

    def _recompute_status(self) -> None:
        tables = self.data["tables"]
        if any(t.get("status") == "error" for t in tables.values()):
            self.data["status"] = "complete_with_errors"
            return
        missing = [t for t in self.expected_tables if tables.get(t, {}).get("status") not in _TERMINAL_STATUSES]
        self.data["status"] = "incomplete" if missing else "complete"

    def _write(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".partial")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)

    def finalize(self) -> str:
        self._recompute_status()
        self._write()
        return self.data["status"]


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def seed_excluded_entries(mf: Manifest) -> None:
    """Record `secrets.DENYLIST` and `tables.EXCLUDED_CROSS_TENANT_LEAK`
    tables with an explicit status, so their absence from a completed export
    is documented rather than silently unexplained. Purely informational --
    neither set is a member of `tables.EXPECTED_TABLES`, so this has no
    effect on `_recompute_status`'s "is this run complete" calculation.
    Idempotent -- safe to call from more than one subcommand against the
    same on-disk manifest (`export_tenant_mysql` and `export_tenant_csmh`
    both do, since either one might run first).

    Local imports (not at module level): keeps this module itself generic/
    dependency-light -- it only needs to know about the denylist/exclusion
    tables when this specific helper is actually called.
    """
    from openedx.features.edly.tenant_export import secrets as secrets_mod
    from openedx.features.edly.tenant_export import tables as tables_mod

    for t in sorted(secrets_mod.DENYLIST):
        if t not in mf.data["tables"]:
            mf.update_table(t, status="excluded_denylist")
    for t, reason in tables_mod.EXCLUDED_CROSS_TENANT_LEAK.items():
        if t not in mf.data["tables"]:
            mf.update_table(t, status="excluded_cross_tenant_leak", reason=reason)
