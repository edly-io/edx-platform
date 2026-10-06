"""Source-bucket configuration for the Phase 3 S3 asset copy (EDLYPRODUCT-8584).
Stdlib only; boto3 is touched only by the management command.

Koa keeps assets in several buckets (EDM `S3_BUCKET_CONFIGS`, read-only
reference). Which bucket holds what is deployment config, not code, so:

    EXPORT_TENANT_S3_SOURCES = {          # Django setting, e.g. lms/envs/private.py
        'profile-images': {'bucket': '...', 'region': 'us-east-1'},
        'video-meta':     {'bucket': '...'},
        'discovery':      {'bucket': '...'},
        'credentials':    {'bucket': '...'},
        # optional per entry: region, endpoint_url, access_key, secret_key, root_path
    }

LMS defaults (UNVERIFIED on the Koa deployment -- confirm with `head_bucket`
preflight / the printed bucket names, override via the setting if wrong):
  * edx-storage     <- settings.AWS_STORAGE_BUCKET_NAME
  * ora-submissions <- same bucket as edx-storage (EDM: ORA2 attachments live there)
  * grades          <- settings.GRADES_DOWNLOAD['BUCKET'] + ['ROOT_PATH']
Everything else must be configured explicitly. Credentials in the setting are
used only to build that bucket's boto3 client; they never reach the manifest.
"""
from dataclasses import dataclass
from typing import Optional

from openedx.features.edly.tenant_export import tables

LOGICAL_BUCKETS = tuple(tables.S3_LOGICAL_BUCKETS)


@dataclass(frozen=True)
class SourceCfg:
    logical: str
    bucket: str
    region: Optional[str] = None
    endpoint_url: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    root_path: str = ""

    def public(self) -> dict:
        """What may appear in the manifest (no credentials)."""
        return {"bucket": self.bucket, "region": self.region, "root_path": self.root_path}


def _cfg(logical, raw, root_path=""):
    return SourceCfg(
        logical=logical, bucket=raw["bucket"], region=raw.get("region"), endpoint_url=raw.get("endpoint_url"),
        access_key=raw.get("access_key"), secret_key=raw.get("secret_key"),
        root_path=raw.get("root_path", root_path) or "",
    )


def resolve_sources(configured: dict, lms_get, wanted) -> tuple:
    """-> (cfgs {logical: SourceCfg}, unconfigured [logical]). `configured` is the
    EXPORT_TENANT_S3_SOURCES setting; `lms_get(name, default=None)` reads LMS settings."""
    configured = configured or {}
    raw = {k: dict(v) for k, v in configured.items()}
    if "edx-storage" not in raw and lms_get("AWS_STORAGE_BUCKET_NAME"):
        raw["edx-storage"] = {"bucket": lms_get("AWS_STORAGE_BUCKET_NAME")}
    if "ora-submissions" not in raw and "edx-storage" in raw:
        raw["ora-submissions"] = dict(raw["edx-storage"])
    grades = lms_get("GRADES_DOWNLOAD") or {}
    root = grades.get("ROOT_PATH") or ""
    if "grades" not in raw and grades.get("BUCKET"):
        raw["grades"] = {"bucket": grades["BUCKET"]}

    cfgs, unconfigured = {}, []
    for logical in wanted:
        if logical in raw and raw[logical].get("bucket"):
            cfgs[logical] = _cfg(logical, raw[logical], root if logical == "grades" else "")
        else:
            unconfigured.append(logical)
    return cfgs, unconfigured


def validate_dest(dest_bucket: str, cfgs) -> None:
    """Never let the delivery bucket be (or alias) a source bucket."""
    for cfg in cfgs.values():
        if cfg.bucket == dest_bucket:
            raise ValueError(f"--dest-bucket {dest_bucket!r} is the {cfg.logical} SOURCE bucket -- refusing")
