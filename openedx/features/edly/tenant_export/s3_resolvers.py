"""The 7 default (+1 opt-in) per-bucket key resolvers for the Phase 3 S3 asset copy (EDLYPRODUCT-8584).

Each is a GENERATOR of source keys (verbatim -- the delivery layout keeps the
source key; no EDM-style target transform), taking a `ResolverContext`. The
key-scoping logic is copied from edlysaas_data_migrations/utils/s3_resolvers.py
(read-only reference, never imported). Stdlib only: SQL and S3 listing come in
as callables (`edx_rows`, `svc_rows`, `list_keys`), so the offline tests run
them over sqlite + FakeS3.

Differences from EDM, deliberately: ACLs dropped (we copy bytes, not serving
config); `SHA1(module_id)` and md5 hashing done in Python; discovery/
credentials scoped through our scope.json service ids / Phase 2 WHERE builders;
ORA2 additionally membership-filtered (EDM filters only by course org, so a non-member's
attachment in a tenant course is NOT copied here -- narrower than EDM, on purpose: PII scoping);
org prefixes use `course_orgs` = EDM's sub-org M2M orgs + real-case variants of those same orgs
found in course ids (scope.derive_orgs; S3 is case-sensitive, MySQL LIKE is not); credentials
signatory images are restricted to PROGRAM-certificate signatories like EDM (course-certificate-only
signatories are not copied; their DB rows are still in the Phase 2 dump). Org LIKE clauses escape
`_`/`%` in the org token (sqlutil.org_like_clause), so `My_Org` does not match `MyXOrg`; EDM binds
the org as a parameter without escaping `_`, i.e. EDM can over-match -- we are exact. No key
remapping anywhere: source keys are delivered verbatim.

`cert-template-assets` is OPT-IN (not in tables.S3_LOGICAL_BUCKETS / the default --buckets / the
manifest's expected keys): see `cert_template_assets`.
"""
import hashlib
from dataclasses import dataclass, field
from typing import Callable, Optional

from openedx.features.edly.tenant_export import svc_credentials, video_discovery
from openedx.features.edly.tenant_export.sqlutil import anon_membership_subquery, membership_subquery, org_like_clause


class ResolverError(Exception):
    """Hard stop for one bucket (wrong seed, missing prerequisite...)."""


@dataclass
class ResolverContext:
    slug: str
    sub_org_id: int
    course_orgs: list
    course_ids: list
    services: dict                                   # scope.json "services" block
    edx_rows: Callable                               # sql -> list of tuples (edxapp)
    svc_rows: Callable                               # (db, sql) -> list of tuples
    list_keys: Callable = None                       # prefix -> iterator of source keys (bound per bucket)
    modulestore_db: Optional[object] = None          # pymongo-like, for video discovery
    profile_seed: str = field(default="", repr=False)  # NEVER logged / put in a manifest
    root_path: str = ""                              # grades ROOT_PATH
    log: Callable = print
    stats: dict = field(default_factory=dict)        # resolver -> manifest-safe extras


def _dedup(keys):
    seen = set()
    for key in keys:
        if key not in seen:
            seen.add(key)
            yield key


def _ids(values) -> str:
    return ",".join(str(int(v)) for v in values)


_SERVICE_BUCKETS = ("discovery", "credentials")


def _missing_block_msg(db):
    return f"scope.json has no services.{db} block -- re-run export_tenant_scope --services {db}"


def require_service_blocks(wanted, services) -> None:
    """Fail fast (before any copy) if a requested discovery/credentials bucket has no scope.json
    `services.<db>` block; the operator excludes the bucket via --buckets or re-runs the scope."""
    missing = [b for b in _SERVICE_BUCKETS if b in wanted and not (services or {}).get(b)]
    if missing:
        raise ResolverError("; ".join(_missing_block_msg(b) for b in missing))


def _service_ctx(ctx, db):
    svc = ctx.services.get(db)
    if not svc:
        raise ResolverError(_missing_block_msg(db))
    return svc


# ---- 1. discovery / credentials / grades (simple joins) ---------------------

_DISCOVERY_IMAGES = {
    "course_metadata_organization": (("logo_image", "certificate_logo_image", "banner_image"), {}),
    "course_metadata_program": (
        ("banner_image", "card_image"),
        {"banner_image": ("large", "medium", "small", "x-small"), "card_image": ("card",)},
    ),
}


def discovery(ctx):
    """Org logos/banners + program banner/card images (+ StdImage size variations).
    `course_metadata_person` images are CloudFront/WordPress-hosted, not in S3."""
    partner_id = int(_service_ctx(ctx, "discovery")["partner_id"])

    def keys():
        for table, (fields, variations) in _DISCOVERY_IMAGES.items():
            rows = ctx.svc_rows("discovery", f"SELECT {', '.join(fields)} FROM {table} WHERE partner_id = {partner_id}")
            for row in rows:
                for column, path in zip(fields, row):
                    if not path:
                        continue
                    yield path
                    base, dot, ext = path.rpartition(".")
                    if dot:
                        for var in variations.get(column, ()):
                            yield f"{base}.{var}.{ext}"
    return _dedup(keys())


def credentials(ctx):
    """Signatory images of PROGRAM-certificate signatories of the site -- EDM parity
    (s3_resolvers.py CredentialsResolver joins credentials_programcertificate_signatories ->
    credentials_programcertificate). Course-certificate-only signatories are deliberately NOT
    copied (EDM would not); EDM scopes by core_siteconfiguration.edx_org_short_name, we use the
    same site via scope.json's resolved `site_id`."""
    site_id = int(_service_ctx(ctx, "credentials")["site_id"])
    rows = ctx.svc_rows(
        "credentials",
        "SELECT DISTINCT s.image FROM credentials_signatory s "
        "JOIN credentials_programcertificate_signatories pcs ON s.id = pcs.signatory_id "
        "JOIN credentials_programcertificate pc ON pcs.programcertificate_id = pc.id "
        f"WHERE pc.site_id = {site_id} AND s.image IS NOT NULL AND s.image != ''",
    )
    return _dedup(r[0] for r in rows)


def grades(ctx):
    """Grade/report CSVs: `[ROOT_PATH/]sha1(course_id)/...` per tenant course.

    ROOT_PATH comes from `GRADES_DOWNLOAD['ROOT_PATH']` (or the per-bucket `root_path` in
    EXPORT_TENANT_S3_SOURCES) and is prepended to the listing prefix. EDM lists `sha1(course_id)/`
    with NO root, so our SOURCE KEYS (which keep ROOT_PATH, delivered verbatim) equal EDM's only when
    ROOT_PATH is empty; a non-empty one means EDM would have found nothing. The effective value and
    that flag are recorded in the manifest entry (`root_path`, `keys_match_edm`)."""
    root = f"{ctx.root_path.strip('/')}/" if ctx.root_path.strip("/") else ""

    def keys():
        found_dirs = 0
        for course_id in ctx.course_ids:
            hit = False
            for key in ctx.list_keys(f"{root}{hashlib.sha1(str(course_id).encode('utf-8')).hexdigest()}/"):
                hit = True
                yield key
            found_dirs += hit
        ctx.stats["grades"] = {
            "course_dirs_expected": len(ctx.course_ids), "course_dirs_found": found_dirs,
            "root_path": root.rstrip("/"), "keys_match_edm": not root,
        }
    return _dedup(keys())


# ---- 2. edx-storage / video-meta --------------------------------------------

def edx_storage(ctx):
    """block-v1:{org}+, h5pxblockmedia/{org}/, {org}/ (SGA etc.), scormxblockmedia/{org}/, and
    scorm/{sha1(module_id)}/ (SCORM-new -- EDM parity: only blocks with learner state rows;
    a SCORM block nobody has opened is not found. Known gap, same as EDM)."""
    def keys():
        for org in ctx.course_orgs:
            for prefix in (f"block-v1:{org}+", f"h5pxblockmedia/{org}/", f"{org}/", f"scormxblockmedia/{org}/"):
                yield from ctx.list_keys(prefix)
        rows = ctx.edx_rows(
            "SELECT DISTINCT module_id FROM courseware_studentmodule "
            f"WHERE module_type = 'scorm' AND ({org_like_clause('course_id', ctx.course_orgs)})"
        )
        for (module_id,) in rows:
            yield from ctx.list_keys(f"scorm/{hashlib.sha1(str(module_id).encode('utf-8')).hexdigest()}/")
    return _dedup(keys())


def video_meta(ctx):
    """Transcripts + video images as `media/<path>` in the video-meta bucket (explicit keys)."""
    video_ids, stats = video_discovery.discover_video_ids(ctx.edx_rows, ctx.modulestore_db, ctx.course_orgs, ctx.log)
    ctx.stats["video-meta"] = {k: v for k, v in stats.items() if k != "unresolved_edx_video_ids"}
    ctx.stats["video-meta"]["unresolved_edx_video_ids"] = stats["unresolved_edx_video_ids"]
    ids = sorted(video_ids)

    def keys():
        for i in range(0, len(ids), 500):
            chunk = _ids(ids[i:i + 500])
            for (path,) in ctx.edx_rows(
                "SELECT DISTINCT transcript FROM edxval_videotranscript "
                f"WHERE video_id IN ({chunk}) AND transcript IS NOT NULL AND transcript != ''"
            ):
                yield f"media/{path}"
            # images only exist via the coursevideo join (tenants with none legitimately have 0)
            for (path,) in ctx.edx_rows(
                "SELECT DISTINCT vi.image FROM edxval_videoimage vi JOIN edxval_coursevideo cv ON vi.course_video_id = cv.id "
                f"WHERE cv.video_id IN ({chunk}) AND vi.image IS NOT NULL AND vi.image != ''"
            ):
                yield f"media/{path}"
    return _dedup(keys())


# ---- 3. ORA2 submissions -----------------------------------------------------

def ora_submissions(ctx):
    """`submissions_attachments/{student_id}/{course_id}/...` -- student_id (the ORA
    anonymous id) is FIRST, course_id second. Listing by `.../course-v1:{org}+` never
    matches anything (EDM's historical silent-0 bug); the (student_id, course_id) pairs
    come straight from `submissions_studentitem`, restricted to tenant members."""
    rows = ctx.edx_rows(
        "SELECT DISTINCT student_id, course_id FROM submissions_studentitem "
        f"WHERE item_type = 'openassessment' AND ({org_like_clause('course_id', ctx.course_orgs)}) "
        f"AND student_id IN ({anon_membership_subquery(ctx.sub_org_id)})"
    )
    ctx.stats["ora-submissions"] = {"student_course_pairs": len(rows)}

    def keys():
        for student_id, course_id in rows:
            yield from ctx.list_keys(f"submissions_attachments/{student_id}/{course_id}/")
    return _dedup(keys())


# ---- 4. profile images -------------------------------------------------------

_SEED_SAMPLE = 20


def profile_name_hash(seed: str, username: str) -> str:
    """edx-platform's profile image name: md5(PROFILE_IMAGE_HASH_SEED + username)."""
    return hashlib.md5((seed + username).encode("utf-8")).hexdigest()


def profile_images(ctx):
    """(SQL deliberately has no DISTINCT: `SELECT DISTINCT ... ORDER BY u.id` fails under MySQL
    ONLY_FULL_GROUP_BY because u.id is not selected; username is unique per auth_user row.)
    media/profile-images/{md5(seed+username)}_{size}.jpg for members with an upload. The seed must
    be Koa's real `PROFILE_IMAGE_HASH_SEED`; a wrong one silently finds nothing, so the first
    users are sample-verified and a miss on all of them is a hard error. Seed never leaves this function."""
    if not ctx.profile_seed:
        raise ResolverError("PROFILE_IMAGE_HASH_SEED is empty -- cannot compute profile image names")
    usernames = [r[0] for r in ctx.edx_rows(
        "SELECT u.username FROM auth_user u JOIN auth_userprofile up ON up.user_id = u.id "
        f"WHERE u.id IN ({membership_subquery(ctx.sub_org_id)}) AND up.profile_image_uploaded_at IS NOT NULL "
        "ORDER BY u.id"
    )]

    def keys():
        hits = 0
        for n, username in enumerate(usernames, 1):
            found = list(ctx.list_keys(f"media/profile-images/{profile_name_hash(ctx.profile_seed, username)}_"))
            hits += bool(found)
            yield from found
            if (n == _SEED_SAMPLE or n == len(usernames)) and hits == 0:
                raise ResolverError(
                    f"profile-images: 0 of the first {n} users with an uploaded image exist in the bucket -- "
                    "PROFILE_IMAGE_HASH_SEED (or the key prefix) is probably wrong"
                )
        ctx.stats["profile-images"] = {"users_with_uploads": len(usernames), "users_found": hits}
    return _dedup(keys())


# ---- 5. cert-template-assets (OPT-IN, platform-wide) ------------------------

CERT_TEMPLATE_PREFIX = "certificate_template_assets/"


def cert_template_assets(ctx):
    """WARNING: PLATFORM-WIDE, NOT tenant-scoped. Lists EVERY key under `certificate_template_assets/`
    in the edx-storage bucket (EDM CertificateTemplateAssetResolver, settings.py cert-template-assets
    entry): the DB table `certificates_certificatetemplateasset` has no org/course FK, so there is no
    way to attribute an asset to one tenant. Running this delivers OTHER tenants' certificate logos/CSS
    too. Hence OPT-IN only (`--buckets cert-template-assets`); not in the default bucket list nor in
    the manifest's expected keys. Consistent with the DB side, which excludes the templateasset table.
    Without it, certificate templates that reference these assets by direct S3 URL show dead links."""
    ctx.log("cert-template-assets: PLATFORM-WIDE prefix, includes other tenants' assets")
    return _dedup(ctx.list_keys(CERT_TEMPLATE_PREFIX))


def coverage(logical, ctx, counts):
    """Silent-zero guard for LISTING-based buckets (a wrong prefix/layout lists nothing, and an empty
    candidate set is otherwise indistinguishable from an empty tenant). Call AFTER the key generator
    was fully consumed. -> None | ("warning" | "error", message). A tenant with no inputs stays clean."""
    found = counts["candidates"]
    if logical == "ora-submissions":
        pairs = ctx.stats.get(logical, {}).get("student_course_pairs", 0)
        if pairs and not found:
            return "error", f"{pairs} (student, course) ORA pair(s) in the DB but 0 objects found -- wrong prefix/bucket?"
    elif logical == "grades":
        expected = len(ctx.course_ids)
        dirs = ctx.stats.get(logical, {}).get("course_dirs_found", 0)
        if expected and not dirs:
            return "error", f"0 of {expected} tenant course(s) have a grades directory -- wrong bucket/ROOT_PATH?"
        if dirs < expected:
            return "warning", f"only {dirs} of {expected} tenant course(s) have a grades directory (courses without reports are normal)"
    elif logical == "edx-storage":
        if ctx.course_ids and not found:
            return "warning", f"0 objects found for a tenant with {len(ctx.course_ids)} course(s) -- confirm the bucket/layout"
    return None


RESOLVERS = {
    "discovery": discovery,
    "credentials": credentials,
    "grades": grades,
    "edx-storage": edx_storage,
    "video-meta": video_meta,
    "ora-submissions": ora_submissions,
    "profile-images": profile_images,
    "cert-template-assets": cert_template_assets,   # opt-in, see s3_sources.OPT_IN_BUCKETS
}
