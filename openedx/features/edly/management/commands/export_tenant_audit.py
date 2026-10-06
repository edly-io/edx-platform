"""
Read-only coverage + secret-column scan of one service database against its
Phase 2 spec (EDLYPRODUCT-8584): flags tables present in the DB that are
neither dumped nor consciously excluded (EDM's tier lists are known
incomplete, and the ecommerce list is unverified), spec tables missing from
the DB, and secret-looking columns in dumped tables that are not redacted.
Exits non-zero if anything is flagged. Run on the demo site before trusting
an export. Connection: see `tenant_export/services.py`.

Usage:
    python manage.py lms export_tenant_audit --db ecommerce
"""
import json

from django.core.management.base import BaseCommand, CommandError

from openedx.features.edly.tenant_export import audit, services


class Command(BaseCommand):
    help = "Coverage + secret-column scan of a credentials/discovery/ecommerce DB vs its export spec (read-only)."

    def add_arguments(self, parser):
        parser.add_argument('--db', required=True, choices=services.SERVICE_DBS)

    def handle(self, *args, **options):
        spec = services.get_spec(options['db'])
        with services.service_connection(spec.name).cursor() as cursor:
            cursor.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE()")
            source_tables = [r[0] for r in cursor.fetchall()]
            cursor.execute("SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE()")
            columns = cursor.fetchall()

        report = audit.coverage_report(source_tables, spec)
        report["suspect_secret_columns"] = audit.secret_scan(columns, spec)
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if report["unlisted"] or report["missing_in_source"] or report["suspect_secret_columns"]:
            raise CommandError("audit found gaps -- see report above")
