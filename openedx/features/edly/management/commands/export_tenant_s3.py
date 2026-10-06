"""
Copy one Edly tenant's S3 assets into a delivery bucket under
`<dest-prefix>s3/<logical bucket>/<source key>`. Part of the MIT off-boarding
export tooling (EDLYPRODUCT-8584 Phase 3); key scoping is in
`tenant_export/s3_resolvers.py`, the copy/verify engine in `s3_copy.py`, bucket
config in `s3_sources.py` (Django setting `EXPORT_TENANT_S3_SOURCES`; LMS
defaults for edx-storage/grades). Logical buckets: discovery, credentials,
grades, edx-storage, video-meta, ora-submissions, profile-images (the default 7), plus the OPT-IN
`cert-template-assets` (PLATFORM-WIDE `certificate_template_assets/`, includes other tenants' assets;
only copied when named in --buckets, never part of the manifest's expected keys).

Per bucket it writes `<out-dir>/s3/<logical>.index.jsonl` (every candidate key
+ outcome) and a manifest entry `s3__<logical>`; `.done` marker on success.
Default copy mode is server-side (`--copy-mode stream` as fallback). No ACLs.
The delivery bucket must differ from every source bucket and the prefix must be
empty unless `--resume`. Credentials: per-source `access_key`/`secret_key` in
the setting, else the default boto3 chain; the delivery client uses the
optional `EXPORT_TENANT_S3_DEST` setting (same keys) else the default chain.
Profile images need the real `settings.PROFILE_IMAGE_HASH_SEED` (never recorded).

Usage:
    python manage.py cms export_tenant_s3 MIT --scope scope.json --out-dir ./out --dest-bucket mit-handoff
    python manage.py cms export_tenant_s3 MIT --scope scope.json --out-dir ./out --dry-run --buckets grades,discovery
(`cms` or `lms`: video-meta reads the Mongo modulestore config, present in both.)
"""
import dataclasses
import os
from pathlib import Path

import boto3
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from openedx.features.edly.tenant_export import manifest as manifest_mod, mongo, resume, services, tables
from openedx.features.edly.tenant_export import s3_copy, s3_resolvers, s3_sources
from openedx.features.edly.tenant_export.scope import load_scope_file, scope_orgs

_MAX_UNRESOLVED_IN_MANIFEST = 200


def _client(cfg):
    kwargs = {"region_name": cfg.get("region"), "endpoint_url": cfg.get("endpoint_url")}
    if cfg.get("access_key"):
        kwargs.update(aws_access_key_id=cfg["access_key"], aws_secret_access_key=cfg["secret_key"])
    return boto3.client("s3", **{k: v for k, v in kwargs.items() if v})


class Command(BaseCommand):
    help = "Copy one tenant's S3 assets (7 default logical buckets + opt-in cert-template-assets) into a delivery bucket + per-bucket JSONL index."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument('--scope', required=True, help='Path to scope.json (see export_tenant_scope).')
        parser.add_argument('--out-dir', required=True, help='Directory for s3/*.index.jsonl + MANIFEST.json.')
        parser.add_argument('--dest-bucket', help='Delivery bucket (required unless --dry-run).')
        parser.add_argument('--dest-prefix', help='Key prefix in the delivery bucket (default: "<slug>/").')
        parser.add_argument(
            '--buckets',
            help=f"Comma-separated subset of: {', '.join(s3_sources.ALL_BUCKETS)} (default: "
                 f"{', '.join(s3_sources.LOGICAL_BUCKETS)}). 'cert-template-assets' is opt-in only and "
                 "PLATFORM-WIDE (not tenant-scoped: copies every tenant's certificate template assets).",
        )
        parser.add_argument('--copy-mode', choices=('server', 'stream'), default='server')
        parser.add_argument('--workers', type=int, default=8)
        parser.add_argument('--resume', action='store_true', help='Continue into a non-empty delivery prefix.')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List/resolve candidate keys per bucket (read-only); no copy, no files, no manifest.',
        )

    def handle(self, *args, **options):
        os.umask(0o077)

        scope_data = load_scope_file(options['scope'])
        slug = scope_data['slug']
        if options['slug'] != slug:
            raise CommandError(f"slug {options['slug']!r} does not match scope.json's slug {slug!r}")
        dry_run = options['dry_run']
        if not dry_run and not options['dest_bucket']:
            raise CommandError("--dest-bucket is required unless --dry-run")

        wanted = [b.strip() for b in options['buckets'].split(',') if b.strip()] if options['buckets'] \
            else list(s3_sources.LOGICAL_BUCKETS)
        unknown = set(wanted) - set(s3_sources.ALL_BUCKETS)
        if unknown:
            raise CommandError(f"unknown bucket(s): {sorted(unknown)}")
        try:
            s3_resolvers.require_service_blocks(wanted, scope_data.get('services'))
        except s3_resolvers.ResolverError as exc:
            raise CommandError(str(exc))

        cfgs, unconfigured = s3_sources.resolve_sources(
            getattr(settings, 'EXPORT_TENANT_S3_SOURCES', None), lambda n, d=None: getattr(settings, n, d), wanted,
        )
        if unconfigured:
            raise CommandError(
                f"no source bucket configured for {unconfigured} -- set EXPORT_TENANT_S3_SOURCES (see s3_sources.py)"
            )
        dest_prefix = s3_copy.normalize_prefix(options['dest_prefix'] if options['dest_prefix'] is not None else slug)
        if not dry_run:
            try:
                s3_sources.validate_dest(options['dest_bucket'], cfgs)
            except ValueError as exc:
                raise CommandError(str(exc))

        # Source clients + preflight: fail before any work if a bucket is unreachable.
        src_clients = {}
        for logical, cfg in cfgs.items():
            src_clients[logical] = _client(dataclasses.asdict(cfg))
            try:
                src_clients[logical].head_bucket(Bucket=cfg.bucket)
            except Exception as exc:  # pylint: disable=broad-except
                raise CommandError(f"{logical}: cannot access source bucket {cfg.bucket!r}: {type(exc).__name__}")
            self.stdout.write(f"source {logical}: s3://{cfg.bucket} (region={cfg.region or 'default'})")

        dst = None
        if not dry_run:
            dst = _client(getattr(settings, 'EXPORT_TENANT_S3_DEST', None) or {})
            try:
                dst.head_bucket(Bucket=options['dest_bucket'])
                s3_copy.check_dest_empty(dst, options['dest_bucket'], dest_prefix, options['resume'])
            except s3_copy.CopyError as exc:
                raise CommandError(str(exc))
            except Exception as exc:  # pylint: disable=broad-except
                raise CommandError(f"cannot access delivery bucket {options['dest_bucket']!r}: {type(exc).__name__}")

        def edx_rows(sql):
            with connection.cursor() as cursor:
                cursor.execute(sql)  # no params: SQL contains literal % (LIKE)
                return cursor.fetchall()

        def svc_rows(db, sql):
            with services.service_connection(db).cursor() as cursor:
                cursor.execute(sql)
                return cursor.fetchall()

        modulestore_client = modulestore_db = None
        if 'video-meta' in wanted:
            try:
                modulestore_client, modulestore_db = mongo.connect(mongo.modulestore_params(settings.DOC_STORE_CONFIG))
            except Exception as exc:  # pylint: disable=broad-except
                raise CommandError(f"video-meta needs the Mongo modulestore (DOC_STORE_CONFIG): {type(exc).__name__}")

        base_ctx = s3_resolvers.ResolverContext(
            slug=slug, sub_org_id=scope_data['sub_org_id'], course_orgs=scope_orgs(scope_data),
            course_ids=scope_data['course_ids'], services=scope_data.get('services', {}),
            edx_rows=edx_rows, svc_rows=svc_rows, modulestore_db=modulestore_db,
            profile_seed=getattr(settings, 'PROFILE_IMAGE_HASH_SEED', '') or '', log=self.stdout.write,
        )

        out_dir = Path(options['out_dir'])
        mf = None
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            scope_sha = manifest_mod.sha256_of_text(Path(options['scope']).read_text())
            mf = manifest_mod.Manifest(out_dir / "MANIFEST.json", slug, scope_sha, tables.S3_KEYS)

        try:
            for logical in wanted:
                self._run_bucket(logical, cfgs[logical], src_clients[logical], dst, base_ctx, options, dest_prefix,
                                 out_dir, mf, slug)
        finally:
            if modulestore_client:
                modulestore_client.close()

        if mf:
            self.stdout.write(
                f"manifest status for this command's keys only (overall status: run export_tenant_package): {mf.finalize()}"
            )

    def _run_bucket(self, logical, cfg, src, dst, base_ctx, options, dest_prefix, out_dir, mf, slug):
        key = f"s3__{logical}"
        dry_run = options['dry_run']
        if not dry_run and resume.is_done(out_dir, slug, key):
            self.stdout.write(f"skip (done): {key}")
            return
        ctx = dataclasses.replace(
            base_ctx, list_keys=s3_copy.make_lister(src, cfg.bucket), root_path=cfg.root_path, stats={},
        )
        try:
            result = s3_copy.copy_bucket(
                logical, s3_resolvers.RESOLVERS[logical](ctx), src, cfg.bucket, dst, options['dest_bucket'],
                dest_prefix, out_dir, mode=options['copy_mode'], workers=options['workers'], dry_run=dry_run,
                log=self.stdout.write, coverage=lambda counts: s3_resolvers.coverage(logical, ctx, counts),
            )
        except Exception as exc:  # pylint: disable=broad-except -- one bucket must not abort the others
            self.stderr.write(self.style.ERROR(f"ERROR {key}: {exc}"))
            if mf:
                mf.update_table(key, status="error", error=str(exc))
            return

        extras = dict(ctx.stats.get(logical, {}))
        unresolved = extras.pop("unresolved_edx_video_ids", None)
        if unresolved:
            extras["unresolved_edx_video_ids"] = unresolved[:_MAX_UNRESOLVED_IN_MANIFEST]
            extras["unresolved_edx_video_ids_total"] = len(unresolved)
        if dry_run:
            self.stdout.write(f"[dry-run] {key}: {result['candidates']} candidate keys {extras or ''}")
            return
        mf.update_table(key, source=cfg.public(), **extras, **result)
        if result['status'] == 'complete':
            resume.mark_done(out_dir, slug, key)
        summary = {k: result[k] for k in ('candidates', 'copied', 'skipped', 'missing', 'errors', 'bytes')}
        self.stdout.write(f"{result['status']} {key}: {summary}")
        for warning in result.get('warnings', []):
            self.stdout.write(self.style.WARNING(f"WARNING {key}: {warning}"))
        if result.get('error'):
            self.stderr.write(self.style.ERROR(f"ERROR {key}: {result['error']}"))
