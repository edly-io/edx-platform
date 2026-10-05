"""
Tenant-filtered OLX course export. Part of the MIT off-boarding export
tooling (EDLYPRODUCT-8584 Phase 1). A plain BaseCommand, matching
`cms/djangoapps/contentstore/management/commands/export_all_courses.py`'s
own pattern exactly (same imports, same `export_course_to_xml` call, same
"..." course-directory-name convention) -- the only difference is the
course list comes from `scope.json["course_ids"]` (already resolved by
`export_tenant_scope`, never recomputed here) instead of an unscoped
`modulestore().get_courses()`.

This never touches `export_all_courses.py` itself and never edits
`edlysaas-data-migrations` -- it calls the same underlying
`export_course_to_xml()` (`xmodule/modulestore/xml_exporter.py`) that
command already uses.

Must run via `manage.py cms` (needs the Studio/contentstore app stack that
owns course export) -- `openedx.features.edly` is installed in both LMS and
CMS (confirmed in `lms/envs/common.py` and `cms/envs/common.py`), so this
command is discoverable either way, but `manage.py lms export_tenant_olx`
will fail at the `contentstore()`/`modulestore()` calls, which need the CMS
process's modulestore configuration.

Usage:
    python manage.py cms export_tenant_olx MIT --scope scope.json --out-dir ./out/olx
"""
import json
import os

from django.core.management.base import BaseCommand, CommandError
from opaque_keys.edx.keys import CourseKey

from xmodule.contentstore.django import contentstore
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.xml_exporter import export_course_to_xml

from openedx.features.edly.tenant_export.scope import load_scope_file


class Command(BaseCommand):
    help = "Export one Edly tenant's courses (per scope.json) to OLX."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument('--scope', required=True, help='Path to scope.json (see export_tenant_scope).')
        parser.add_argument('--out-dir', required=True, help='Directory to write OLX course directories into.')

    def handle(self, *args, **options):
        os.umask(0o077)  # course content isn't PII, but keep the whole export tree consistently private

        scope_data = load_scope_file(options['scope'])
        if options['slug'] != scope_data['slug']:
            raise CommandError(
                f"slug {options['slug']!r} does not match scope.json's slug {scope_data['slug']!r}"
            )

        out_dir = options['out_dir']
        os.makedirs(out_dir, exist_ok=True)

        content_store = contentstore()
        module_store = modulestore()

        exported = []
        failed = []
        for course_id_str in scope_data['course_ids']:
            try:
                course_key = CourseKey.from_string(course_id_str)
                # Same "..." separator convention export_all_courses.py uses
                # for a filesystem-safe course directory name.
                course_dir = course_id_str.replace('/', '...')
                export_course_to_xml(module_store, content_store, course_key, out_dir, course_dir)
                exported.append(course_id_str)
                self.stdout.write(f"OLX exported: {course_id_str}")
            except Exception as exc:  # pylint: disable=broad-except -- one bad course must not abort the rest
                failed.append({'course_id': course_id_str, 'error': str(exc)})
                self.stderr.write(self.style.ERROR(f"OLX export FAILED for {course_id_str}: {exc}"))

        self.stdout.write(f"OLX export done: {len(exported)} ok, {len(failed)} failed")
        if failed:
            # One level up from out_dir (e.g. out/olx -> out/olx_export_failures.json)
            # -- sibling to the main package's MANIFEST.json, not buried inside the
            # OLX course-directory tree itself.
            failures_path = os.path.join(out_dir, "..", "olx_export_failures.json")
            with open(failures_path, 'w') as f:
                json.dump(failed, f, indent=2)
            self.stdout.write(f"failure details written to {failures_path}")
            raise CommandError(f"{len(failed)} course(s) failed OLX export -- see {failures_path}")
