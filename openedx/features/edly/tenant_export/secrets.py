"""Hard denylist (whole-run refusal) + column-level redaction.

No Django/MySQL-driver import -- `redacted_table_dump` takes a plain DB-API
cursor. Ported from the standalone reference implementation
(`mit-tenant-export/export_mit/secrets.py`, EDLYPRODUCT-8584 Phase 1) with
one real change: `redacted_table_dump` now renders each row via
`sqlutil.sql_literal` instead of `cursor.mogrify()` -- see that function's
docstring for why.
"""
from openedx.features.edly.tenant_export.sqlutil import sql_literal

# Tables with zero archival value (ephemeral login state) and no
# tenant-scoping precedent anywhere in EDM. `export_tenant_mysql` refuses to
# run entirely if any of these appear in a `--tables` override -- don't
# attempt to scope/redact them, just never touch them.
DENYLIST = {
    "oauth2_provider_accesstoken",
    "oauth2_provider_refreshtoken",
    "oauth2_provider_grant",
    "oauth2_provider_idtoken",
    "oauth2_accesstoken",
    "oauth2_grant",
    "oauthtoken",
    "oauth2_client",
    "django_session",
    "social_auth_partial",
    "social_auth_code",
    "social_auth_nonce",
    "social_auth_association",
    # Secret-bearing EDM tier-4 tables (OAuth `secret`, SAML `private_key`): never exported. Reasons live in
    # tables.EXCLUDED; they are listed here too so a `--tables` override cannot request them.
    "third_party_auth_oauth2providerconfig",
    "third_party_auth_samlconfiguration",
    "third_party_auth_samlproviderconfig",
}

# table -> {secret column: SQL literal sentinel to SELECT in its place}.
# The first 3 are the "obvious" ones; the rest were found during design review / the EDM parity audit.
SECRET_COLUMNS = {
    "auth_user": {"password": "'!'"},
    "auth_registration": {"activation_key": "''"},
    "social_auth_usersocialauth": {"extra_data": "'{}'"},
    "student_pendingemailchange": {"activation_key": "''"},
    "verify_student_softwaresecurephotoverification": {"photo_id_key": "''"},
    # lti_consumer_lticonfiguration (EDM `_migrate_lti_configurations` copies these verbatim; we blank them).
    # UNVERIFIED against a real Koa schema: if a column is absent the table errors loudly (never ships a secret).
    "lti_consumer_lticonfiguration": {"lti_1p1_client_secret": "''", "lti_1p3_private_key": "''"},
}


class DenylistedTableError(Exception):
    pass


def check_denylist(tables) -> None:
    hit = DENYLIST & set(tables)
    if hit:
        raise DenylistedTableError(
            "refusing to run: these tables are hard-denylisted (ephemeral/secret, "
            f"no archival value, no tenant-scoping precedent): {sorted(hit)}"
        )


def redacted_table_dump(cursor, table: str, where_clause: str, out_path, batch_size: int = 2000) -> int:
    """Dump `table` without the real value of its secret column(s) ever
    leaving the database: the SELECT itself substitutes each secret column
    for its sentinel literal, so redaction happens before a row ever reaches
    this process (column-level SELECT-rewrite, not regex post-processing --
    mysqldump output has no safe way to scrub free-text/blob fields after
    the fact).

    Schema is expected to already be in `out_path` (via a separate
    `mysqldump --no-data` call -- see dbutil.dump_redacted_table); this only
    appends data as `INSERT INTO` statements. Keyset-paginated on `id` --
    every one of the 5 SECRET_COLUMNS tables has a plain auto-increment `id`
    PK.

    [Resolved -- flagged in the plan as "needs resolving during
    implementation, not a blocker"] The reference implementation rendered
    each row via PyMySQL's `cursor.mogrify()`. Koa's actual DB driver is
    mysqlclient (MySQLdb) -- confirmed via requirements/edx/base.txt -- and
    its cursor has no `mogrify` method at all (confirmed by reading
    `MySQLdb/cursors.py` at the pinned `mysqlclient==2.0.1` tag). Each value
    is now escaped individually via `sqlutil.sql_literal(conn, value)`,
    using the raw DB-API connection's own `.literal()` -- present, with
    identical semantics, on both MySQLdb and PyMySQL connections. Never
    hand-rolled escaping.
    """
    secret_cols = SECRET_COLUMNS[table]

    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
        (table,),
    )
    columns = [row[0] for row in cursor.fetchall()]
    id_index = columns.index("id")

    select_list = ", ".join(
        f"{secret_cols[c]} AS `{c}`" if c in secret_cols else f"`{c}`" for c in columns
    )
    col_list_sql = ", ".join(f"`{c}`" for c in columns)
    # The raw DB-API connection (MySQLdb.Connection / pymysql.Connection) --
    # both expose this as `cursor.connection` (confirmed by reading
    # MySQLdb/cursors.py's BaseCursor.__init__ at the pinned tag; Django's
    # own CursorWrapper layers forward unknown attributes straight through
    # to it, so this also works on the cursor `connection.cursor()` hands
    # back).
    conn = cursor.connection

    row_count = 0
    last_id = 0
    with open(out_path, "a", encoding="utf-8") as out:
        while True:
            # `id > {int(last_id)}` inlined directly (not a %s bind param) --
            # avoids mixing param substitution with a WHERE string that may
            # itself contain literal '%' (SQL LIKE wildcards), which trips
            # the "only format when a second execute() argument is given"
            # behavior shared by PyMySQL and Django's MySQL cursor alike.
            sql = (
                f"SELECT {select_list} FROM `{table}` "
                f"WHERE ({where_clause}) AND id > {int(last_id)} "
                f"ORDER BY id LIMIT {int(batch_size)}"
            )
            cursor.execute(sql)
            rows = cursor.fetchall()
            if not rows:
                break
            for row in rows:
                values_sql = ", ".join(sql_literal(conn, v) for v in row)
                out.write(f"INSERT INTO `{table}` ({col_list_sql}) VALUES ({values_sql});\n")
            row_count += len(rows)
            last_id = rows[-1][id_index]
            if len(rows) < batch_size:
                break

    return row_count
