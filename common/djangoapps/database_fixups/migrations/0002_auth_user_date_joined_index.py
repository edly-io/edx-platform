"""
Add an index on auth_user.date_joined.

auth_user belongs to django.contrib.auth, so there is no model in this repo on
which to declare the index — the only route is raw DDL, the same approach
0001_initial takes for the email uniqueness constraint and
django_comment_common/0008_role_user_index.py takes for an implicit M2M table.

Unindexed, any ORDER BY or range scan on date_joined costs a full scan of
auth_user plus a filesort. Admin user listing sorted by registration date and
registration-over-time reporting both do exactly that; measured on a 2.5M-row
table, one page of a date_joined-sorted list took ~12s, and ~2ms once this
index existed.

The introspection guard is deliberate: auth_user is shared by many apps, so
the index may already exist, and CREATE INDEX is not idempotent — a bare
RunSQL would fail the whole migration on such a database. 0001_initial guards
for the same reason.

On MySQL 5.6+ this builds online (ALGORITHM=INPLACE, LOCK=NONE), so reads and
writes continue, but it is not instant: expect minutes on a multi-million-row
table, plus a permanent cost on every auth_user write and the index's disk.
"""

from django.db import migrations

INDEX_NAME = "rwaq_auth_user_date_joined_idx"


def add_index(apps, schema_editor):
    """Create the index unless date_joined is already the leading column of one."""
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(cursor, "auth_user")
    for details in constraints.values():
        # Only a *leading* date_joined serves an ORDER BY or range scan on it,
        # so a composite that mentions the column later does not count.
        if details.get("index") and (details.get("columns") or [None])[0] == "date_joined":
            return
    schema_editor.execute(f"CREATE INDEX {INDEX_NAME} ON auth_user (date_joined)")


def drop_index(apps, schema_editor):
    """Drop only the index this migration created, never a pre-existing one."""
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(cursor, "auth_user")
    if INDEX_NAME in constraints:
        schema_editor.execute(f"DROP INDEX {INDEX_NAME} ON auth_user")


class Migration(migrations.Migration):

    dependencies = [
        ("database_fixups", "0001_initial"),
    ]

    operations = [
        # atomic=False mirrors 0001_initial: MySQL cannot roll DDL back, and a
        # long index build should not sit inside an open transaction.
        migrations.RunPython(add_index, drop_index, atomic=False),
    ]
