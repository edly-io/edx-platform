"""
Offline regression test for the mogrify -> sql_literal escaping fix
(`tenant_export/sqlutil.py`'s `sql_literal`, used by
`tenant_export/secrets.py`'s `redacted_table_dump`) -- see
`tenant_export/sqlutil.py`'s module docstring and
`management/commands/export_tenant_mysql.py`'s docstring for why PyMySQL's
`cursor.mogrify()` (used by the original standalone-tool reference
implementation) could not be ported as-is: Koa's actual DB driver is
mysqlclient (MySQLdb), whose cursor has no `mogrify` method.

No DB/Django import needed -- a bare-bones fake connection/cursor is enough
to exercise the real escaping + redaction logic end to end.
"""
import os
import tempfile
import unittest

from openedx.features.edly.tenant_export.secrets import SECRET_COLUMNS, redacted_table_dump
from openedx.features.edly.tenant_export.sqlutil import sql_literal


class _FakeConnection:
    """Mimics the one method both MySQLdb.Connection and pymysql.Connection
    actually provide: `.literal(value)` -> bytes, the real escaped SQL
    literal for that single value. A quote-aware stand-in, not a full
    driver -- just enough to prove sql_literal() round-trips correctly.
    """

    def literal(self, value):
        if value is None:
            return b"NULL"
        if isinstance(value, int):
            return str(value).encode("utf-8")
        escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
        return ("'%s'" % escaped).encode("utf-8")


class SqlLiteralTests(unittest.TestCase):
    def test_string_value_is_quoted_and_escaped(self):
        self.assertEqual(sql_literal(_FakeConnection(), "o'brien"), "'o\\'brien'")

    def test_int_value_is_unquoted(self):
        self.assertEqual(sql_literal(_FakeConnection(), 42), "42")

    def test_none_value_is_sql_null(self):
        self.assertEqual(sql_literal(_FakeConnection(), None), "NULL")

    def test_binary_value_uses_hex_literal_not_connection_literal(self):
        # Must never reach _FakeConnection.literal() at all for bytes --
        # rendered directly as 0x<hex>, matching mysqldump's --hex-blob.
        self.assertEqual(sql_literal(_FakeConnection(), b"\xff\x00"), "0xff00")

    def test_empty_binary_value(self):
        self.assertEqual(sql_literal(_FakeConnection(), b""), "''")


def _unquote_sql_literal(sql_text):
    """Turn a simple quoted SQL literal (e.g. "'!'", "''") back into the
    Python value a real MySQL server would hand back for it. Only needs to
    handle the literals actually used in SECRET_COLUMNS (plain quoted
    strings, no escapes) -- this is a test double, not a SQL parser.
    """
    sql_text = sql_text.strip()
    if sql_text.startswith("'") and sql_text.endswith("'"):
        return sql_text[1:-1]
    return sql_text


class _FakeCursor:
    """Duck-typed DB-API cursor backing `redacted_table_dump` -- enough to
    drive one keyset-paginated pass over a fake `auth_user`-shaped table.
    Deliberately has NO `mogrify` method -- a regression back to calling
    `cursor.mogrify()` would raise AttributeError, failing these tests
    loudly instead of silently passing.

    Honors column-level literal substitution in the SELECT list (e.g.
    "'!' AS `password`"), the same way a real MySQL server would -- without
    this, the fake cursor would just echo back the raw stored row regardless
    of what the SQL actually asked for, which would let a redaction bug
    pass this test silently (the bug this exists to catch).
    """

    def __init__(self, columns, rows):
        self.connection = _FakeConnection()
        self._columns = columns
        self._rows = rows
        self._result = []

    def execute(self, sql, params=None):
        if sql.startswith("SELECT COLUMN_NAME, COLUMN_KEY FROM INFORMATION_SCHEMA.COLUMNS"):
            self._result = [(c, "PRI" if c == "id" else "") for c in self._columns]
        elif sql.startswith("SELECT"):
            last_id = int(sql.rsplit("`id` > ", 1)[1].split(" ", 1)[0])
            id_index = self._columns.index("id")

            select_clause = sql[len("SELECT "):sql.index(" FROM ")]
            overrides = {}  # column name -> literal SQL text replacing it
            for part in select_clause.split(", "):
                if " AS `" in part:
                    literal, colname = part.rsplit(" AS `", 1)
                    overrides[colname.rstrip("`")] = literal

            matching = [r for r in self._rows if r[id_index] > last_id]
            projected = []
            for row in matching:
                new_row = list(row)
                for colname, literal in overrides.items():
                    new_row[self._columns.index(colname)] = _unquote_sql_literal(literal)
                projected.append(tuple(new_row))
            self._result = projected
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchall(self):
        return self._result


class RedactedTableDumpTests(unittest.TestCase):
    """Locks in the one real behavior change from the reference
    implementation: the secret column's sentinel literal reaches the output
    `INSERT INTO` statement, rendered via sql_literal/.literal(), never via
    cursor.mogrify().
    """

    def test_password_column_is_redacted_in_output(self):
        self.assertIn("password", SECRET_COLUMNS["auth_user"])
        columns = ["id", "username", "password"]
        rows = [(1, "alice", "$2b$supersecrethash")]
        cursor = _FakeCursor(columns, rows)

        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "auth_user.sql")
            open(out_path, "w").close()  # schema already written, as the real caller does
            row_count = redacted_table_dump(cursor, "auth_user", "id IN (1)", out_path)
            with open(out_path) as f:
                contents = f.read()

        self.assertEqual(row_count, 1)
        self.assertIn("INSERT INTO `auth_user`", contents)
        self.assertIn("'alice'", contents)
        self.assertNotIn("supersecrethash", contents)
        self.assertIn("'!'", contents)  # the auth_user.password sentinel


if __name__ == "__main__":
    unittest.main()
