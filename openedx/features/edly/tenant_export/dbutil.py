"""mysqldump subprocess wrapper + connection-param helpers shared by the
`export_tenant_mysql` and `export_tenant_csmh` management commands.

Takes a plain `dict` (`host`/`port`/`user`/`password`/`database`) rather
than importing `django.db` or a MySQL driver directly, so it stays testable
without either installed -- the caller (a management command) is the only
place that touches `django.db.connection`/`connections` and passes the
result in via `conn_params_from_settings_dict`.

This replaces the standalone reference implementation's `export_mit/db.py`
+ `export_mit/config.py` (EDLYPRODUCT-8584 Phase 1): no separate `.env` /
pymysql connection needed at all now -- host/user/password/port/database all
already live in Django's own `connection.settings_dict`.
"""
import hashlib
import os
import stat
import subprocess
import tempfile

# [Plan] "Mandatory mysqldump flags, one wrapper function every tier uses" --
# --single-transaction is mandatory, not cosmetic: without it, a
# subquery-based --where referencing a second table fails with "table was
# not locked".
_MYSQLDUMP_FLAGS = [
    "--single-transaction",
    "--no-tablespaces",
    "--set-gtid-purged=OFF",
    "--hex-blob",
    "--default-character-set=utf8mb4",
    "--column-statistics=0",
]


def conn_params_from_settings_dict(settings_dict: dict) -> dict:
    """Build the plain dict `dump_table_chunked`/`dump_redacted_table` need
    from Django's own parsed DB config (`connection.settings_dict`, or
    `connections['student_module_history'].settings_dict` for CSMH) -- no
    separate .env/credentials file to manage at all.
    """
    return {
        "host": settings_dict.get("HOST") or "localhost",
        "port": settings_dict.get("PORT") or 3306,
        "user": settings_dict["USER"],
        "password": settings_dict.get("PASSWORD") or "",
        "database": settings_dict["NAME"],
    }


def table_exists(cursor, table: str) -> bool:
    """Scoped to the cursor's OWN current database via `DATABASE()`, not a
    separately-passed db-name string -- avoids any risk of that string
    drifting from whatever schema this cursor is actually connected to.
    """
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return cursor.fetchone()[0] > 0


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _escape_cnf_value(value: str) -> str:
    """Escape a value for a double-quoted entry in a MySQL option file.
    Quoting is mandatory here, not just tidy: an UNQUOTED '#' starts a
    comment in an option file and would silently truncate the password at
    that character.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_defaults_file(conn_params: dict) -> str:
    """mode-600 ini with a [client] section -- never pass the password on
    argv (visible in `ps`/process listings otherwise).
    """
    fd, path = tempfile.mkstemp(prefix="edly_tenant_export_", suffix=".cnf")
    lines = ["[client]"]
    host = conn_params.get("host")
    if host:
        lines.append(f'host="{_escape_cnf_value(str(host))}"')
    port = conn_params.get("port")
    if port:
        lines.append(f"port={int(port)}")
    lines.append(f'user="{_escape_cnf_value(conn_params["user"])}"')
    lines.append(f'password="{_escape_cnf_value(conn_params.get("password") or "")}"')
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def describe_error(exc) -> str:
    """str(exc) plus mysqldump's captured stderr (CalledProcessError only), capped."""
    msg = str(exc)
    stderr = getattr(exc, "stderr", None)
    if stderr:
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        msg += f" | stderr: {stderr.strip()[:2000]}"
    return msg


def _run_mysqldump(conn_params: dict, table: str, where_clause: str, out_path, *, append: bool, no_data: bool) -> None:
    assert no_data or where_clause not in ("", "1=1"), f"refusing unscoped dump for {table}"
    defaults_path = _write_defaults_file(conn_params)
    try:
        args = ["mysqldump", f"--defaults-extra-file={defaults_path}"] + _MYSQLDUMP_FLAGS
        if no_data:
            args.append("--no-data")
        else:
            if append:
                args.append("--no-create-info")
            args.append(f"--where={where_clause}")
        args += [conn_params["database"], table]

        mode = "ab" if append else "wb"
        with open(out_path, mode) as out:
            subprocess.run(args, stdout=out, stderr=subprocess.PIPE, check=True)
    finally:
        os.remove(defaults_path)


def dump_table_chunked(conn_params: dict, table: str, out_path, where_clauses: list) -> None:
    """Dump a table whose scope is expressed as N independent WHERE clauses
    (one per id chunk -- see sqlutil.where_clauses_for_ids / ora_chain.py),
    each guaranteed to match disjoint rows. The first call carries the
    schema; later calls are data-only appends (`--no-create-info`).

    If `where_clauses` is empty, dump schema only (zero matching rows) so
    the per-table .sql file still exists and is structurally valid.
    """
    if not where_clauses:
        _run_mysqldump(conn_params, table, "", out_path, append=False, no_data=True)
        return
    for i, clause in enumerate(where_clauses):
        _run_mysqldump(conn_params, table, clause, out_path, append=(i > 0), no_data=False)


def dump_redacted_table(conn_params: dict, table: str, where_clause: str, out_path, cursor, secret_cols=None) -> int:
    """Schema via `mysqldump --no-data`, then data via a column-redacting
    SELECT run on the CALLER's own already-open Django cursor
    (secrets.redacted_table_dump) -- see plan's "Secrets" section. Unlike
    the standalone reference implementation, no second DB connection is
    opened here at all: Django already owns the one connection this whole
    management command runs under, so the same cursor doubles as both the
    scope-resolution cursor and the redacted-data-SELECT cursor.
    """
    from openedx.features.edly.tenant_export import secrets as secrets_mod

    _run_mysqldump(conn_params, table, "", out_path, append=False, no_data=True)
    return secrets_mod.redacted_table_dump(cursor, table, where_clause, out_path, secret_cols=secret_cols)
