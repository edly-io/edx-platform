"""Small, dependency-free SQL string helpers shared by scope/tables/ora_chain.

Deliberately has no import of Django or any MySQL driver package -- it only
ever operates on a DB-API cursor/connection passed in by the caller (duck-
typed), so modules that just need WHERE-clause logic (notably ora_chain, and
its offline tests) never need a real Django settings module or MySQL driver
installed. This module is ported essentially unchanged from the standalone
reference implementation (`mit-tenant-export/export_mit/sqlutil.py`,
EDLYPRODUCT-8584 Phase 1) -- only the module-path references below and the
addition of `sql_literal` are new.

No table/column name here is ever built from unvalidated input: org short
names are run through `validate_org_token()` before any WHERE clause touches
them (see scope.py, which validates once at export_tenant_scope time), and
numeric ids are always cast through `int()` before interpolation. We never
pass `%s` style bind params together with a hand-built WHERE string here --
Django's MySQL cursor only attempts `%`-substitution when a second
`execute()` argument is given, and these WHERE strings often contain literal
`%` (SQL LIKE wildcards), so mixing the two is a trap (see dbutil.py / the
`export_tenant_*` management commands, which always call
`cursor.execute(sql)` with no second argument for composed SQL).
"""
import re

# Matches Organization.short_name's own validator in edx-organizations
# (organizations/models.py: `^[a-zA-Z0-9._-]*$`) -- confirmed by direct read
# of the installed package, not assumed.
_SAFE_ORG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_org_token(org: str) -> None:
    """Reject anything that isn't a safe SQL string literal for an org
    short_name. This is a SQL-injection safety net (course_org_filter comes
    from an operator-editable JSON config blob, not directly from an
    external user, but it's cheap to be strict) -- it intentionally still
    allows '.', '-' and '_', all of which are valid real short_name
    characters; see `_escape_like_value` for how '_' is handled safely in a
    LIKE pattern instead of being banned outright.
    """
    if not org or not _SAFE_ORG_RE.match(org):
        raise ValueError(
            f"unsafe course_org_filter entry {org!r} -- expected only letters, "
            "digits, '.', '-' and '_'"
        )


def _escape_like_value(value: str) -> str:
    """Escape '_' and '%' (SQL LIKE single-char / multi-char wildcards) so a
    literal underscore in a real org short_name (a valid character there,
    per edx-organizations) can't over-match. MySQL's default LIKE escape
    character is backslash with no ESCAPE clause needed (ANSI mode off,
    the default).

    [Plan: "A course_org_filter value containing a literal underscore would
    act as a SQL LIKE wildcard and over-match" -- this is that fix.]
    """
    return value.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")


def membership_subquery(sub_org_id) -> str:
    """Live SQL subquery resolving tenant membership -- never a materialized
    Python ID list. Keeps every tier-3/5/7 WHERE clause short and constant
    size regardless of tenant size, avoiding mysqldump's ~128KiB argv limit
    entirely (MAX_ARG_STRLEN) rather than working around it.

    [Plan: "Use a live SQL subquery in --where=, not a Python-collected ID
    list, for tiers 3/5/6/7".]
    """
    return f"SELECT user_id FROM edly_edlymultisiteaccess WHERE sub_org_id = {int(sub_org_id)}"


def anon_membership_subquery(sub_org_id) -> str:
    """Tenant members' ORA anonymous_user_id values, as a live subquery."""
    return (
        "SELECT anonymous_user_id FROM student_anonymoususerid WHERE user_id IN "
        f"({membership_subquery(sub_org_id)})"
    )


def org_like_clause(column: str, orgs, prefix: str = "course-v1") -> str:
    """Build `column LIKE 'prefix:org+%' OR ...` for every org, safely."""
    parts = []
    for org in orgs:
        validate_org_token(org)
        parts.append(f"{column} LIKE '{prefix}:{_escape_like_value(org)}+%'")
    return " OR ".join(parts)


def chunk_list(values, size=1000):
    """Yield successive `size`-element chunks of `values` (order-stable)."""
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i : i + size]


def where_clauses_for_ids(template: str, ids, quote: bool = False, chunk_size: int = 1000) -> list:
    """Build one WHERE clause per chunk of `ids`, substituted into the
    `{ids}` placeholder in `template` (e.g. "id IN ({ids})").

    Only safe when every row of the target table is associated with exactly
    one value in `ids` -- i.e. chunks partition the matches with no overlap.
    That's true for every ordinary FK-scoped tier-8 table, but NOT for a
    table matched by an OR across two columns (assessment_peerworkflowitem)
    or a table reachable via a many-to-many-shaped join (the rubric-leak-
    fixed assessment_trainingexample tables) -- both of those are handled by
    pre-resolving a single, already-deduplicated id set in ora_chain.py
    before this function ever sees it, so the general case here stays safe.
    """
    clauses = []
    for chunk in chunk_list(ids, chunk_size):
        literal = ",".join(f"'{v}'" if quote else str(int(v)) for v in chunk)
        clauses.append(template.format(ids=literal))
    return clauses


def count_rows(cursor, table: str, where_clause: str) -> int:
    """`SELECT COUNT(*)` using the exact same WHERE text a dump call would
    use, so a dry-run count and a post-dump row count are directly
    comparable (the plan's "count parity" check).
    """
    assert where_clause not in ("", "1=1"), f"refusing unscoped COUNT for {table}"
    cursor.execute(f"SELECT COUNT(*) FROM `{table}` WHERE {where_clause}")
    return cursor.fetchone()[0]


def sql_literal(conn, value) -> str:
    """Render one Python value as a safe SQL literal for hand-built
    `INSERT INTO` text (`secrets.py`'s column-redacting dump) -- via the raw
    DB-API connection's own `.literal()`, never hand-rolled escaping and
    never `cursor.mogrify()`.

    [Resolved -- flagged in the plan as "needs resolving during
    implementation, not a blocker"] The reference implementation this was
    ported from used PyMySQL's `cursor.mogrify()`. Confirmed via
    `requirements/edx/base.txt` ("mysqlclient==2.0.1") that Koa's actual DB
    driver is mysqlclient (MySQLdb), and confirmed by reading
    `MySQLdb/cursors.py` at that exact pinned tag that its cursor has no
    `mogrify` method at all. `.literal()` is the portable replacement: it is
    present, with identical "render this one Python value as an SQL
    literal" semantics, on BOTH `MySQLdb.connections.Connection` (confirmed
    by reading that same pinned source -- always returns `bytes`) and
    `pymysql.connections.Connection` (`.literal()` is a documented alias for
    `.escape()`) -- so this also still works unchanged if this module is
    ever exercised against a pymysql connection (e.g. local/offline
    testing).

    Binary values are rendered as a `0x<hex>` literal directly (matching
    mysqldump's own `--hex-blob` convention used elsewhere in this tool)
    rather than trusting `conn.literal()`'s own bytes-literal quoting plus a
    utf-8 decode of its result, which can raise for genuinely non-UTF-8
    binary data. None of the 5 `secrets.SECRET_COLUMNS` tables carry a
    binary column today, but a plain `SELECT *`-shaped row here means any
    future column addition hits this path automatically -- this guards that
    case rather than assuming it away.
    """
    if isinstance(value, (bytes, bytearray)):
        return "0x" + value.hex() if value else "''"
    literal = conn.literal(value)
    return literal.decode("utf-8") if isinstance(literal, (bytes, bytearray)) else str(literal)
