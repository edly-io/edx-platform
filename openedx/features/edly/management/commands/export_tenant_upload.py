"""
Push a finished tenant export bundle (the --out-dir produced by the
export_tenant_* commands) to the delivery S3 bucket. Part of the MIT
off-boarding export tooling (EDLYPRODUCT-8584 Phase 4).

Refuses unless MANIFEST.json status is "complete" and every on-disk checksum
still matches. MANIFEST.json is uploaded LAST: its presence in the bucket
means the bundle is whole. Re-runnable: objects already there with the same
sha256 (stored as object metadata) are skipped.

Default prefix is "<slug>/" -- the same default as export_tenant_s3, so the
bundle lands next to the copied `s3/<bucket>/...` assets.

Usage:
    python manage.py lms export_tenant_upload MIT --out-dir ./out --dest-bucket my-bucket [--dry-run]
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from openedx.features.edly.tenant_export import dbutil, manifest as manifest_mod, tables

_SKIP_DIRS = {".state"}


def _client():
    """Delivery client, built exactly like export_tenant_s3's (lazy: boto3/settings only when really uploading)."""
    from django.conf import settings

    from openedx.features.edly.management.commands.export_tenant_s3 import _client as build

    return build(getattr(settings, 'EXPORT_TENANT_S3_DEST', None) or {})


def bundle_files(out_dir: Path):
    """Relative posix paths to upload, MANIFEST.json excluded (it goes last)."""
    files = []
    for path in sorted(out_dir.rglob("*")):
        rel = path.relative_to(out_dir)
        if (not path.is_file() or rel.parts[0] in _SKIP_DIRS or path.name.endswith(".partial")
                or rel.as_posix() == "MANIFEST.json"):
            continue
        files.append(rel.as_posix())
    return files


def upload_one(client, bucket, key, path: Path):
    """Upload `path` -> `key`; skip if dest already has the same sha256. Returns 'uploaded'|'skipped'."""
    digest = dbutil.sha256_file(path)
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        if head.get("Metadata", {}).get("sha256") == digest:
            return "skipped"
    except Exception:  # pylint: disable=broad-except  # 404 -> upload; real errors resurface on upload
        pass
    client.upload_file(str(path), bucket, key, ExtraArgs={"Metadata": {"sha256": digest}})
    head = client.head_object(Bucket=bucket, Key=key)
    if head["ContentLength"] != path.stat().st_size or head.get("Metadata", {}).get("sha256") != digest:
        raise CommandError(f"post-upload verification failed for s3://{bucket}/{key}")
    return "uploaded"


class Command(BaseCommand):
    help = "Upload a finalized tenant export bundle to the delivery S3 bucket (MANIFEST.json last)."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant.')
        parser.add_argument('--out-dir', required=True, help='Finalized bundle directory.')
        parser.add_argument('--dest-bucket', required=True, help='Delivery bucket.')
        parser.add_argument('--dest-prefix', help='Key prefix in the delivery bucket (default: "<slug>/").')
        parser.add_argument('--workers', type=int, default=4)
        parser.add_argument('--dry-run', action='store_true', help='List what would be uploaded; write nothing.')
        parser.add_argument(
            '--allow-errors', action='store_true',
            help='Accept status "complete_with_errors" (default: only "complete" uploads).',
        )

    def handle(self, *args, **options):
        os.umask(0o077)
        out_dir = Path(options['out_dir'])
        manifest_path = out_dir / "MANIFEST.json"
        if not manifest_path.exists():
            raise CommandError(f"no MANIFEST.json in {out_dir} -- run export_tenant_package first")
        data = json.loads(manifest_path.read_text())
        if options['slug'] != data['tenant_slug']:
            raise CommandError(f"slug {options['slug']!r} does not match manifest's tenant_slug {data['tenant_slug']!r}")

        allowed = ('complete', 'complete_with_errors') if options['allow_errors'] else ('complete',)
        if data.get('status') not in allowed:
            raise CommandError(f"manifest status is {data.get('status')!r}; run export_tenant_package until it is {allowed}")
        problems = manifest_mod.verify_entry_files(data, out_dir, skip_keys=(tables.OLX_KEY,))
        if problems:
            raise CommandError(f"on-disk files no longer match the manifest: {problems}")

        prefix = options['dest_prefix'] if options['dest_prefix'] is not None else f"{options['slug']}/"
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        files = bundle_files(out_dir)

        if options['dry_run']:
            for rel in files + ["MANIFEST.json"]:
                self.stdout.write(f"[dry-run] s3://{options['dest_bucket']}/{prefix}{rel}")
            return

        client = _client()
        client.head_bucket(Bucket=options['dest_bucket'])
        with ThreadPoolExecutor(max_workers=options['workers']) as pool:
            results = list(pool.map(
                lambda rel: upload_one(client, options['dest_bucket'], prefix + rel, out_dir / rel), files,
            ))
        # Manifest last: its presence marks a whole bundle.
        results.append(upload_one(client, options['dest_bucket'], prefix + "MANIFEST.json", manifest_path))
        self.stdout.write(
            f"uploaded {results.count('uploaded')}, skipped {results.count('skipped')} "
            f"to s3://{options['dest_bucket']}/{prefix}"
        )
