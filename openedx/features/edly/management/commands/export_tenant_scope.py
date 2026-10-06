"""
Resolve one Edly tenant's export scope (sub_org_id, course_org_filter,
course_ids) from Koa's own `edxapp` database and write it to `scope.json` --
the single source of truth every other `export_tenant_*` command reads,
never re-resolved independently.

Part of the MIT off-boarding export tooling (EDLYPRODUCT-8584 Phase 1).
Ported from a standalone, independently-audited reference implementation
(`mit-tenant-export/`, read-only reference, never imported) into this app's
own `tenant_export` package -- see
`openedx/features/edly/tenant_export/scope.py` for the resolution logic
itself, its EDM-citation notes, and the `[Audited 2026-10-02]` fixes.

Usage:
    python manage.py lms export_tenant_scope MIT --out scope.json
    python manage.py lms export_tenant_scope MIT --dry-run
"""
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from openedx.features.edly.tenant_export.scope import ScopeError, resolve_scope


class Command(BaseCommand):
    help = (
        "Resolve an Edly tenant's export scope (sub_org_id, course_org_filter, "
        "course_ids) and write it to scope.json for the other export_tenant_* "
        "commands to read."
    )

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument('--out', default='scope.json', help='Path to write scope.json to.')
        parser.add_argument(
            '--dry-run', action='store_true', help='Resolve and print scope only; writes nothing.',
        )

    def handle(self, *args, **options):
        os.umask(0o077)  # scope.json names the tenant's org/courses; keep consistent with the other export commands

        with connection.cursor() as cursor:
            try:
                resolved = resolve_scope(cursor, options['slug'])
            except ScopeError as exc:
                raise CommandError(str(exc))

        self.stdout.write(
            "slug={slug} sub_org_id={sub_org_id} course_org_filter={course_org_filter} "
            "tenant_user_count={tenant_user_count} course_count={course_count}".format(
                course_count=len(resolved['course_ids']), **resolved
            )
        )

        if options['dry_run']:
            return

        out_path = Path(options['out'])
        out_path.write_text(json.dumps(resolved, indent=2, sort_keys=True))
        self.stdout.write(self.style.SUCCESS(f"wrote {out_path}"))
