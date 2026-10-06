"""
Export one Edly tenant's forum (`cs_comments_service` Mongo) to JSONL under
`<out-dir>/forum/` -- `contents`, derived `users` (id->username map; non-member
emails blanked), derived thread-follow `subscriptions`. Part of the MIT
off-boarding export tooling (EDLYPRODUCT-8584 Phase 3); all logic lives in
`tenant_export/forum.py`, this is the Django/Mongo plumbing.

Mongo connection: Django setting `EXPORT_TENANT_FORUM_MONGO` (e.g. in
lms/envs/private.py: `{'URI': 'mongodb://ro:...@host/', 'DB': 'cs_comments_service'}`)
or env `EXPORT_TENANT_FORUM_MONGO_URI` / `_DB` -- see `tenant_export/mongo.py`.
Use a read-only Mongo user. Host/db are printed (credentials masked) and the
global `contents` collection must be non-empty before anything is read.

Needs `course_orgs` from scope.json (written by `export_tenant_scope`; older
scope files are handled by computing it from course_ids).

Usage:
    python manage.py lms export_tenant_forum MIT --scope scope.json --out-dir ./out
    python manage.py lms export_tenant_forum MIT --scope scope.json --out-dir ./out --dry-run
"""
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from openedx.features.edly.tenant_export import forum, manifest as manifest_mod, mongo, tables
from openedx.features.edly.tenant_export.scope import load_scope_file, scope_orgs
from openedx.features.edly.tenant_export.sqlutil import membership_subquery


class Command(BaseCommand):
    help = "Export one tenant's forum (Mongo) to JSONL: contents, users, subscriptions."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument('--scope', required=True, help='Path to scope.json (see export_tenant_scope).')
        parser.add_argument('--out-dir', required=True, help='Directory to write forum/*.jsonl + MANIFEST.json into.')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print counts (contents/threads/users/subscriptions) only; writes nothing.',
        )

    def handle(self, *args, **options):
        os.umask(0o077)  # forum posts + usernames are learner data

        scope_data = load_scope_file(options['scope'])
        if options['slug'] != scope_data['slug']:
            raise CommandError(f"slug {options['slug']!r} does not match scope.json's slug {scope_data['slug']!r}")
        orgs = scope_orgs(scope_data)

        try:
            params = mongo.forum_params(getattr(settings, 'EXPORT_TENANT_FORUM_MONGO', None), os.environ)
        except ValueError as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"forum mongo: {mongo.mask_uri(params['uri'])} db={params['db']} orgs={orgs}")

        with connection.cursor() as cursor:
            cursor.execute(membership_subquery(scope_data['sub_org_id']))
            member_ids = {str(row[0]) for row in cursor.fetchall()}

        out_dir = Path(options['out_dir'])
        mf = None
        if not options['dry_run']:
            out_dir.mkdir(parents=True, exist_ok=True)
            scope_sha = manifest_mod.sha256_of_text(Path(options['scope']).read_text())
            mf = manifest_mod.Manifest(out_dir / "MANIFEST.json", scope_data['slug'], scope_sha, tables.FORUM_KEYS)

        client, db = mongo.connect(params)
        try:
            stats = forum.export_forum(
                db, orgs, member_ids, out_dir, scope_data['slug'], mf, self.stdout.write, dry_run=options['dry_run'],
            )
        except forum.ForumError as exc:
            raise CommandError(str(exc))
        finally:
            client.close()

        prefix = "[dry-run] " if options['dry_run'] else ""
        self.stdout.write(prefix + ", ".join(f"{k}={v}" for k, v in stats.items()))
        if mf:
            self.stdout.write(f"manifest status: {mf.finalize()}")
