"""Coverage scan + secret-column scan helpers (pure functions; cli.audit feeds
them INFORMATION_SCHEMA rows).

Why: EDM's tier files are incomplete, and the ecommerce table list is
unverified, so "every table in the source DB is either dumped or consciously
excluded" must be checked mechanically on the demo site, not assumed.
"""
import re

from openedx.features.edly.tenant_export import secrets as secrets_mod

# Heuristic: column names that smell like secrets. Over-matches on purpose.
SECRET_NAME_RE = re.compile(
    r"pass(word|wd)?|secret|token|api_?key|_key$|^key$|credential|private|salt|signature|oauth|\bhash\b",
    re.I,
)
# Names that match the regex but are known-benign structural columns.
_BENIGN = re.compile(r"(^|_)(content_type|course_key|usage_key|session_key|primary_key|foreign_key|credential_id|credential_content_type_id)", re.I)


def coverage_report(source_tables, spec) -> dict:
    src = set(source_tables)
    handled = set(spec.tables) | set(spec.excluded) | set(secrets_mod.DENYLIST)
    return {
        "db": spec.name,
        # in the source DB but neither dumped nor consciously excluded
        "unlisted": sorted(src - handled),
        # in the spec but absent from the source DB (typo / wrong schema guess)
        "missing_in_source": sorted(set(spec.tables) - src),
    }


def secret_scan(columns, spec) -> list:
    """`columns`: iterable of (table, column). Returns "table.column" for every
    dumped table's secret-looking column that is not already redacted."""
    out = []
    for table, col in columns:
        if table not in spec.tables or col in spec.secret_columns.get(table, {}):
            continue
        if SECRET_NAME_RE.search(col) and not _BENIGN.search(col):
            out.append(f"{table}.{col}")
    return sorted(out)
