"""S3 copy engine for the Phase 3 asset export (EDLYPRODUCT-8584). Stdlib only;
works over any boto3-like clients (tests use `tests/tenant_export_fakes.FakeS3`).

Per object: HEAD source -> (missing | HEAD dest -> skip if identical | copy ->
HEAD dest + verify size, and ETag when both are non-multipart). Default mode is
a SERVER-SIDE `copy()` with `SourceClient=` for HEAD on the source; the managed
copy still issues the actual copy with the DESTINATION credentials, which therefore
need s3:GetObject on every source bucket. Otherwise use `--copy-mode stream`
(GET with the source client -> upload_fileobj). No ACLs are ever set.

Delivery layout: `<dest_prefix>s3/<logical>/<source key verbatim>`; one JSONL
index per bucket (`<out>/s3/<logical>.index.jsonl`, `.partial` -> rename)
recording every candidate key + outcome; its sha256/row count go in the
manifest entry (`s3__<logical>`).

Guards: delivery bucket != source bucket; delivery prefix must be empty unless
resuming; candidates > 0 with nothing found/copied is an ERROR (a wrong prefix
or seed silently yielding zero is the failure mode that already bit EDM). For
listing-based buckets an empty candidate set is itself suspicious, so the caller may
pass `coverage(counts) -> None | ("warning"|"error", msg)`: an error flips the status,
a warning keeps `complete` but is recorded in the entry's `warnings` list.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openedx.features.edly.tenant_export.forum import JsonlWriter

_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


class CopyError(Exception):
    """Hard stop for the S3 copy (unsafe destination...)."""


def dest_key(dest_prefix: str, logical: str, key: str) -> str:
    return f"{dest_prefix}s3/{logical}/{key}"


def normalize_prefix(prefix: str) -> str:
    """'' stays '' ; anything else ends with exactly one '/' (and never starts with one)."""
    prefix = prefix.strip("/")
    return f"{prefix}/" if prefix else ""


def make_lister(client, bucket):
    """prefix -> generator of keys, via paged list_objects_v2."""
    def list_keys(prefix):
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]
    return list_keys


def check_dest_empty(dst, dest_bucket, dest_prefix, resume: bool) -> None:
    resp = dst.list_objects_v2(Bucket=dest_bucket, Prefix=f"{dest_prefix}s3/", MaxKeys=1)
    if resp.get("Contents") and not resume:
        raise CopyError(f"s3://{dest_bucket}/{dest_prefix}s3/ is not empty -- pass --resume to continue a previous run")


def _head(client, bucket, key):
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:  # pylint: disable=broad-except -- botocore ClientError, duck-typed
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if code in _MISSING_CODES:
            return None
        raise


def _same(src_head, dst_head) -> bool:
    if src_head["ContentLength"] != dst_head["ContentLength"]:
        return False
    s_etag, d_etag = src_head.get("ETag", ""), dst_head.get("ETag", "")
    return "-" in s_etag or "-" in d_etag or s_etag == d_etag  # multipart ETags are not comparable


def _copy_one(key, ctx):
    rec = {"key": key, "dest_key": ctx["dest_root"] + key}
    try:
        src_head = _head(ctx["src"], ctx["src_bucket"], key)
        if src_head is None:
            return {**rec, "status": "missing"}
        rec["size"] = src_head["ContentLength"]
        dst_head = _head(ctx["dst"], ctx["dst_bucket"], rec["dest_key"])
        if dst_head is not None and _same(src_head, dst_head):
            return {**rec, "status": "skipped", "etag": dst_head.get("ETag")}
        if ctx["mode"] == "stream":
            body = ctx["src"].get_object(Bucket=ctx["src_bucket"], Key=key)["Body"]
            ctx["dst"].upload_fileobj(body, ctx["dst_bucket"], rec["dest_key"])
        else:
            ctx["dst"].copy(
                {"Bucket": ctx["src_bucket"], "Key": key}, ctx["dst_bucket"], rec["dest_key"], SourceClient=ctx["src"],
            )
        out_head = _head(ctx["dst"], ctx["dst_bucket"], rec["dest_key"])
        if out_head is None or not _same(src_head, out_head):
            return {**rec, "status": "error", "error": "post-copy verification failed (missing or size/ETag mismatch)"}
        return {**rec, "status": "copied", "etag": out_head.get("ETag")}
    except Exception as exc:  # pylint: disable=broad-except -- one object must not abort the bucket
        code = getattr(exc, "response", {}).get("Error", {}).get("Code") or type(exc).__name__
        return {**rec, "status": "error", "error": str(code)}


def copy_bucket(logical, keys, src, src_bucket, dst, dst_bucket, dest_prefix, out_dir, *,
                mode="server", workers=8, dry_run=False, log=print, coverage=None) -> dict:
    """Copy every key of `keys` (a generator from s3_resolvers) and write the index.
    Returns manifest-ready fields incl. `status` ('complete' | 'error'). dry_run: count
    candidates only (no HEAD, no copy, no files). Resolver exceptions propagate (writer aborted)."""
    if not dry_run and src_bucket == dst_bucket:
        raise CopyError(f"{logical}: delivery bucket equals source bucket {src_bucket!r}")
    counts = {"candidates": 0, "copied": 0, "skipped": 0, "missing": 0, "errors": 0, "bytes": 0}
    if dry_run:
        for _ in keys:
            counts["candidates"] += 1
        return {"status": "dry_run", **counts}

    ctx = {"src": src, "src_bucket": src_bucket, "dst": dst, "dst_bucket": dst_bucket, "mode": mode,
           "dest_root": dest_key(dest_prefix, logical, "")}
    rel = f"s3/{logical}.index.jsonl"
    writer = JsonlWriter(Path(out_dir) / rel)
    try:
        batch_size = max(workers, 1) * 8
        with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
            batch = []

            def flush():
                for rec in pool.map(lambda k: _copy_one(k, ctx), batch):
                    counts["candidates"] += 1
                    status = rec["status"]
                    counts["errors" if status == "error" else status] += 1
                    if status in ("copied", "skipped"):
                        counts["bytes"] += rec.get("size", 0)
                    writer.write(rec)
                batch.clear()

            for key in keys:
                batch.append(key)
                if len(batch) >= batch_size:
                    flush()
                    log(f"  {logical}: {counts['candidates']} objects processed")
            flush()
    except Exception:
        writer.abort()
        raise
    sha = writer.finish()

    found = counts["copied"] + counts["skipped"]
    status, error = "complete", None
    if counts["candidates"] and not found:
        status, error = "error", "zero-result guard: candidate keys but none found/copied (wrong prefix/seed/bucket?)"
    elif counts["errors"]:
        status, error = "error", f"{counts['errors']} object(s) failed -- see the index; re-run with --resume"
    warnings = []
    if status == "complete" and coverage:
        verdict = coverage(counts)
        if verdict and verdict[0] == "error":
            status, error = "error", f"coverage guard: {verdict[1]}"
        elif verdict:
            warnings.append(verdict[1])
    out = {"status": status, "warnings": warnings, "file": rel, "sha256": sha, "rows": writer.rows, "copy_mode": mode,
           "dest_prefix": f"{dest_prefix}s3/{logical}/", **counts}
    if error:
        out["error"] = error
    return out
