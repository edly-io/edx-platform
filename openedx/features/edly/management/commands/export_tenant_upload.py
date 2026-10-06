"""
Push a finished tenant export bundle (the --out-dir produced by the
export_tenant_* commands) to the delivery S3 bucket. Part of the MIT
off-boarding export tooling (EDLYPRODUCT-8584 Phase 4).

Gates, all hard stops:
  * `export_tenant_package` has been run (it alone writes the full expected
    set + `skipped_*` markers; a dump command's own "complete" can describe a
    partial bundle),
  * MANIFEST.json status is "complete" and every on-disk checksum (files and
    the OLX tree) still matches.
Only manifest-listed files plus the OLX tree are uploaded (allowlist);
anything else lying in --out-dir is reported and NOT uploaded. MANIFEST.json
goes up LAST: its presence means the bundle is whole. A different MANIFEST.json
already at the destination is refused unless --overwrite (which deletes it
first, so a half-replaced prefix never looks complete); stale objects of a
previous run are never pruned.

Objects are written with SSE (default AES256) and the
bucket-owner-full-control ACL so a cross-account recipient owns them; pass
--sse none / --no-acl for buckets that reject either.
Integrity: size + sha256 object metadata, plus the S3 ETag when it is a
plain MD5 (single-part, non-KMS). A PutObject-only bucket (no HeadObject)
uploads fine but is reported "unverified" and cannot resume.

Default prefix is "<slug>/" -- the same default as export_tenant_s3, so the
bundle lands next to the copied `s3/<bucket>/...` assets.

Usage:
    python manage.py lms export_tenant_upload MIT --out-dir ./out --dest-bucket my-bucket [--dry-run]
"""
import hashlib
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


def on_disk_files(out_dir: Path):
    """Every regular file under out_dir except MANIFEST.json, .state/ and *.partial (relative posix paths)."""
    files = []
    for path in sorted(out_dir.rglob("*")):
        rel = path.relative_to(out_dir)
        if (not path.is_file() or rel.parts[0] in _SKIP_DIRS or path.name.endswith(".partial")
                or rel.as_posix() == "MANIFEST.json"):
            continue
        files.append(rel.as_posix())
    return files


def allowed_files(out_dir: Path, data: dict):
    """The upload allowlist: files the manifest lists as complete + the OLX tree."""
    allowed = set()
    for key, entry in data["tables"].items():
        if entry.get("status") != "complete":
            continue
        if "tree_sha256" in entry:
            allowed.update(
                p.relative_to(out_dir).as_posix() for p in (out_dir / entry["dir"]).rglob("*") if p.is_file()
            )
        elif key == tables.OLX_KEY:
            raise CommandError("OLX manifest entry has no tree_sha256 -- re-run export_tenant_olx, then export_tenant_package")
        else:
            allowed.add(entry.get("file", f"{key}.sql"))
    return sorted(allowed)


def _md5(path: Path) -> str:
    digest = hashlib.md5()  # nosec - compared with S3's own ETag, not a security control
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def upload_one(client, bucket, key, path: Path, extra=None):
    """Upload `path` -> `key`. Returns 'uploaded' | 'skipped' (same sha256 already there) | 'unverified'
    (uploaded, but HeadObject is denied so it could not be checked)."""
    digest = dbutil.sha256_file(path)
    try:
        if client.head_object(Bucket=bucket, Key=key).get("Metadata", {}).get("sha256") == digest:
            return "skipped"
    except Exception:  # pylint: disable=broad-except  # absent (or HEAD denied) -> upload
        pass
    args = dict(extra or {})
    args["Metadata"] = {"sha256": digest}
    client.upload_file(str(path), bucket, key, ExtraArgs=args)
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception:  # pylint: disable=broad-except  # write-only drop bucket
        return "unverified"
    if head["ContentLength"] != path.stat().st_size or head.get("Metadata", {}).get("sha256") != digest:
        raise CommandError(f"post-upload verification failed for s3://{bucket}/{key}")
    etag = (head.get("ETag") or "").strip('"')
    if etag and "-" not in etag and args.get("ServerSideEncryption") != "aws:kms" and etag != _md5(path):
        raise CommandError(f"post-upload ETag mismatch for s3://{bucket}/{key}")
    return "uploaded"


def _guard_remote_manifest(client, bucket, key, local_sha, overwrite):
    """Refuse to mix a new bundle into a prefix holding a different MANIFEST.json (unless --overwrite)."""
    try:
        remote = client.head_object(Bucket=bucket, Key=key).get("Metadata", {}).get("sha256")
    except Exception:  # pylint: disable=broad-except  # none there (or HEAD denied)
        return
    if remote == local_sha:
        return
    if not overwrite:
        raise CommandError(
            f"s3://{bucket}/{key} already holds a different MANIFEST.json (a previous export?) -- "
            "pass --overwrite to replace it, or use another --dest-prefix"
        )
    client.delete_object(Bucket=bucket, Key=key)  # no manifest while the new files land


class Command(BaseCommand):
    help = "Upload a finalized tenant export bundle to the delivery S3 bucket (allowlist, MANIFEST.json last)."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant.')
        parser.add_argument('--out-dir', required=True, help='Finalized bundle directory (after export_tenant_package).')
        parser.add_argument('--dest-bucket', required=True, help='Delivery bucket.')
        parser.add_argument('--dest-prefix', help='Key prefix in the delivery bucket (default: "<slug>/").')
        parser.add_argument('--workers', type=int, default=4)
        parser.add_argument('--dry-run', action='store_true', help='List what would be uploaded; write nothing.')
        parser.add_argument(
            '--allow-errors', action='store_true',
            help='Accept status "complete_with_errors" (default: only "complete" uploads).',
        )
        parser.add_argument('--overwrite', action='store_true', help='Replace a different MANIFEST.json at the destination.')
        parser.add_argument('--sse', choices=('AES256', 'aws:kms', 'none'), default='AES256')
        parser.add_argument('--no-acl', action='store_true', help='Do not set ACL bucket-owner-full-control.')

    def handle(self, *args, **options):
        os.umask(0o077)
        out_dir = Path(options['out_dir'])
        manifest_path = out_dir / "MANIFEST.json"
        if not manifest_path.exists():
            raise CommandError(f"no MANIFEST.json in {out_dir} -- run export_tenant_package first")
        data = json.loads(manifest_path.read_text())
        if options['slug'] != data.get('tenant_slug'):
            raise CommandError(f"slug {options['slug']!r} does not match manifest's tenant_slug {data.get('tenant_slug')!r}")
        if 'skipped_dbs' not in data or 'skipped_phase3' not in data:
            raise CommandError(
                "manifest was never finalized by export_tenant_package -- its status may describe only part "
                "of the bundle; run export_tenant_package first"
            )

        allowed = ('complete', 'complete_with_errors') if options['allow_errors'] else ('complete',)
        if data.get('status') not in allowed:
            raise CommandError(f"manifest status is {data.get('status')!r}; run export_tenant_package until it is {allowed}")
        problems = manifest_mod.verify_entry_files(data, out_dir, skip_keys=(tables.OLX_KEY,))
        if problems:
            raise CommandError(f"on-disk files no longer match the manifest: {problems}")

        prefix = options['dest_prefix'] if options['dest_prefix'] is not None else f"{options['slug']}/"
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        files = allowed_files(out_dir, data)
        unlisted = sorted(set(on_disk_files(out_dir)) - set(files))
        if unlisted:
            self.stderr.write(self.style.WARNING(f"NOT uploading files the manifest does not list: {unlisted}"))

        if options['dry_run']:
            for rel in files + ["MANIFEST.json"]:
                self.stdout.write(f"[dry-run] s3://{options['dest_bucket']}/{prefix}{rel}")
            return

        extra = {}
        if options['sse'] != 'none':
            extra['ServerSideEncryption'] = options['sse']
        if not options['no_acl']:
            extra['ACL'] = 'bucket-owner-full-control'

        client = _client()
        try:
            client.head_bucket(Bucket=options['dest_bucket'])
        except Exception:  # pylint: disable=broad-except  # write-only bucket: first PutObject will say if it is unusable
            self.stderr.write(self.style.WARNING("head_bucket denied -- continuing; uploads may not be verifiable"))
        _guard_remote_manifest(
            client, options['dest_bucket'], prefix + "MANIFEST.json",
            dbutil.sha256_file(manifest_path), options['overwrite'],
        )
        with ThreadPoolExecutor(max_workers=options['workers']) as pool:
            results = list(pool.map(
                lambda rel: upload_one(client, options['dest_bucket'], prefix + rel, out_dir / rel, extra), files,
            ))
        # Manifest last: its presence marks a whole bundle.
        results.append(upload_one(client, options['dest_bucket'], prefix + "MANIFEST.json", manifest_path, extra))
        self.stdout.write(
            f"uploaded {results.count('uploaded')}, skipped {results.count('skipped')}, "
            f"unverified {results.count('unverified')} to s3://{options['dest_bucket']}/{prefix}"
        )
