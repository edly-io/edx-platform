"""The 7 per-bucket key resolvers for the Phase 3 S3 asset copy (EDLYPRODUCT-8584).

Each is a GENERATOR of source keys (verbatim -- the delivery layout keeps the
source key; no EDM-style target transform), taking a `ResolverContext`. The
key-scoping logic is copied from edlysaas_data_migrations/utils/s3_resolvers.py
(read-only reference, never imported). Stdlib only: SQL and S3 listing come in
as callables (`edx_rows`, `svc_rows`, `list_keys`), so the offline tests run
them over sqlite + FakeS3.

Differences from EDM, deliberately: ACLs dropped (we copy bytes, not serving
config); `SHA1(module_id)` and md5 hashing done in Python; discovery/
credentials scoped through our scope.json service ids / Phase 2 WHERE builders;
ORA2 additionally membership-filtered; org prefixes use `course_orgs`
(site-config orgs UNION real-case orgs in course ids) because S3 is case-sensitive.
"""
import hashlib
from dataclasses import dataclass, field
from typing import Callable, Optional

from openedx.features.edly.tenant_export import svc_credentials, video_discovery
from openedx.features.edly.tenant_export.sqlutil import anon_membership_subquery, membership_subquery, org_like_clause


class ResolverError(Exception):
    """Hard stop for one bucket (wrong seed, missing prerequisite...)."""


class ResolverSkip(Exception):
    """Bucket does not apply to this scope (e.g. no discovery partner in scope.json)."""


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


def _service_ctx(ctx, db):
    svc = ctx.services.get(db)
    if not svc:
        raise ResolverSkip(f"scope.json has no services.{db} block (run export_tenant_scope --services {db})")
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
    """Signatory images; the signatory set is the Phase 2 WHERE (course + program certificates of the site)."""
    site = _service_ctx(ctx, "credentials")
    where = svc_credentials.where("credentials_signatory", site)
    rows = ctx.svc_rows(
        "credentials", f"SELECT DISTINCT image FROM credentials_signatory WHERE ({where}) AND image IS NOT NULL AND image != ''",
    )
    return _dedup(r[0] for r in rows)


def grades(ctx):
    """Grade/report CSVs: `[ROOT_PATH/]sha1(course_id)/...` per tenant course."""
    root = f"{ctx.root_path.strip('/')}/" if ctx.root_path.strip("/") else ""

    def keys():
        for course_id in ctx.course_ids:
            yield from ctx.list_keys(f"{root}{hashlib.sha1(str(course_id).encode('utf-8')).hexdigest()}/")
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
    """media/profile-images/{md5(seed+username)}_{size}.jpg for members with an upload. The seed must
    be Koa's real `PROFILE_IMAGE_HASH_SEED`; a wrong one silently finds nothing, so the first
    users are sample-verified and a miss on all of them is a hard error. Seed never leaves this function."""
    if not ctx.profile_seed:
        raise ResolverError("PROFILE_IMAGE_HASH_SEED is empty -- cannot compute profile image names")
    usernames = [r[0] for r in ctx.edx_rows(
        "SELECT DISTINCT u.username FROM auth_user u JOIN auth_userprofile up ON up.user_id = u.id "
        f"WHERE u.id IN ({membership_subquery(ctx.sub_org_id)}) AND up.profile_image_uploaded_at IS NOT NULL"
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


RESOLVERS = {
    "discovery": discovery,
    "credentials": credentials,
    "grades": grades,
    "edx-storage": edx_storage,
    "video-meta": video_meta,
    "ora-submissions": ora_submissions,
    "profile-images": profile_images,
}
