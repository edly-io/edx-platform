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
# EDM-listed tables deliberately not dumped must be recorded with an `excluded_*` status (and a reason).
_EXCLUDED_PREFIX = "excluded"


class Manifest:
    def __init__(self, path, tenant_slug: str, scope_sha256: str, expected_tables, expected_excluded=()):
        self.path = Path(path)
        # Phase 2: one shared manifest across dbs -- expected tables (stems,
        # see services.stem) are persisted and unioned, so a run covering
        # only one db can't make the whole export read "complete", and
        # export_tenant_package needs no db list.
        self.expected_tables = set(expected_tables)
        # Tables that must be recorded `excluded_*`; unioned/persisted like expected_tables so a run can
        # never read "complete" while an EDM-listed table is neither dumped nor documented as excluded.
        self.expected_excluded = set(expected_excluded)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            self.expected_tables |= set(self.data.get("expected_tables", []))
            self.expected_excluded |= set(self.data.get("expected_excluded", []))
        else:
            self.data = {
                "tenant_slug": tenant_slug,
                "scope_sha256": scope_sha256,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "tables": {},
                "status": "incomplete",
            }
        self.data["expected_tables"] = sorted(self.expected_tables)
        self.data["expected_excluded"] = sorted(self.expected_excluded)

    def update_table(self, name: str, **fields) -> None:
        entry = self.data["tables"].get(name, {})
        entry.update(fields)
        self.data["tables"][name] = entry
        self._recompute_status()
        self._write()

    def _recompute_status(self) -> None:
        tables = self.data["tables"]
        # Never attempted (no terminal status, not errored): an aborted run, not a finished one.
        missing = [
            t for t in self.expected_tables
            if tables.get(t, {}).get("status") not in _TERMINAL_STATUSES | {"error"}
        ]
        undocumented = [
            t for t in self.expected_excluded
            if not str(tables.get(t, {}).get("status", "")).startswith(_EXCLUDED_PREFIX)
        ]
        if missing or undocumented:
            self.data["status"] = "incomplete"
        elif any(t.get("status") == "error" for t in tables.values()):
            # Every expected table was attempted; some failed.
            self.data["status"] = "complete_with_errors"
        else:
            self.data["status"] = "complete"

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
    is documented rather than silently unexplained. Neither set is in
    `tables.EXPECTED_TABLES`, but both are in `Manifest.expected_excluded`,
    so a run is not "complete" until they have all been recorded.
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
            mf.update_table(t, status="excluded_denylist", reason=tables_mod.EXCLUDED.get(t, "hard-denylisted"))
    for t, reason in tables_mod.EXCLUDED.items():
        if t in mf.data["tables"]:
            continue
        leak = t in tables_mod.EXCLUDED_CROSS_TENANT_LEAK
        mf.update_table(t, status="excluded_cross_tenant_leak" if leak else "excluded_by_design", reason=reason)


def tree_sha256(root) -> str:
    """Deterministic digest of a directory tree: sha256 over sorted `relpath\0filesha\n` lines."""
    root = Path(root)
    outer = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        outer.update(f"{path.relative_to(root).as_posix()}\0{digest.hexdigest()}\n".encode())
    return outer.hexdigest()


def verify_entry_files(data: dict, out_dir, skip_keys=()) -> list:
    """Re-verify on-disk checksums for every manifest entry claimed "complete",
    flipping failures to `status: error` in `data` (mutated). The file an entry
    points at is `entry["file"]` (Phase 3: `forum/*.jsonl`, `s3/*.index.jsonl`),
    defaulting to the Phase 1/2 `<key>.sql`. Returns the problem keys.

    Lives here (not in export_tenant_package) so it is unit-testable offline.
    """
    problems = []
    for key, entry in list(data["tables"].items()):
        if entry.get("status") != "complete":
            continue
        if "tree_sha256" in entry:  # directory entry (OLX): `dir` is relative to the manifest's directory
            tree = Path(out_dir) / entry["dir"]
            if not tree.is_dir() or tree_sha256(tree) != entry["tree_sha256"]:
                problems.append(key)
                entry.update(status="error", error="directory missing or tree sha256 mismatch at package time")
            continue
        if key in skip_keys:
            continue
        path = Path(out_dir) / entry.get("file", f"{key}.sql")
        if not path.exists():
            problems.append(key)
            entry.update(status="error", error="file missing at package time")
            continue
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != entry.get("sha256"):
            problems.append(key)
            entry.update(status="error", error="sha256 mismatch at package time")
    return problems
