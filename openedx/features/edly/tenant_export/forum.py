"""Forum (`cs_comments_service`, Mongo) export -> JSONL, MIT off-boarding
Phase 3 (EDLYPRODUCT-8584). Stdlib only (bson is imported lazily for Extended
JSON), duck-typed over a pymongo-like `db`, so it is tested offline against
`tests/tenant_export_fakes.FakeMongo`.

Selection logic is copied (not imported -- EDM is never touched) from
  edlysaas_data_migrations/utils/forum_migrate.py (read-only reference):
  * contents       -- `course_id` org prefix (threads AND comments carry it)
  * users          -- DERIVED: user ids referenced by the selected contents
  * subscriptions  -- DERIVED: thread-follows of the selected threads
Differences from EDM, on purpose: JSONL out (no target Mongo, NO id remap --
raw ids are kept; `users.jsonl` is the id->username map), case-insensitive
org match (`$options: "i"` on the course_id regex -- EDM's match is case-sensitive, so a
`course-v1:mitx+...` thread is included here but not by EDM), PII scoping of the `users` docs,
and a secret-field scan. The org set is `scope.derive_orgs` (EDM's sub-org M2M + case variants).

Traps pinned by tests:
  * a subscription's `source_id` is the thread `_id` as a hex STRING, not an ObjectId;
  * non-member referenced users (e.g. a staff user posting in a tenant course)
    keep their username (needed to read the data) but get `email` blanked, and
    their `read_states`/`course_stats` are filtered to tenant courses -- those
    arrays otherwise list OTHER tenants' course ids.

  * secret-looking field NAMES (audit.SECRET_NAME_RE): a HARD name (password/secret/token/api_key/
    `*_key`) aborts that collection (as before); any other match (credential/private/salt/signature/
    oauth/hash) has its VALUE blanked to "" and the field path recorded in the manifest entry as
    `blanked_secret_fields`, so one over-matching name no longer aborts the whole forum export;
  * forum cohort `group_id` values are deliberately kept as opaque ids (no remap); they
    join to Phase 1's `course_groups_courseusergroup` (manifest `forum__contents.notes`).

Output (flat, under `<out-dir>/forum/`): `contents.jsonl`, `users.jsonl`,
`subscriptions.jsonl`; manifest keys `forum__<collection>` carrying `file`,
`rows`, `sha256`. Written `.partial` then renamed; `.done` markers per key.
"""
import copy
import hashlib
import json
import os
import re
from pathlib import Path

from openedx.features.edly.tenant_export import audit, resume

COLLECTIONS = ("contents", "users", "subscriptions")
CHUNK = 1000
FORUM_DIR = "forum"


class ForumError(Exception):
    """Hard stop for an export_tenant_forum run."""


def key(collection: str) -> str:
    return f"forum__{collection}"


# ---- selection (pure) --------------------------------------------------------

def content_pattern(orgs) -> str:
    return rf"^course-v1:({'|'.join(re.escape(org) for org in orgs)})\+"


def build_content_filter(orgs) -> dict:
    """Mongo filter for a tenant's `contents`. Case-insensitive: the tenant's SQL
    scope (MySQL LIKE) is, and a thread in `course-v1:mit+...` belongs to `MIT`."""
    return {"course_id": {"$regex": content_pattern(orgs), "$options": "i"}}


_SINGLE_REF_FIELDS = ("author_id", "closed_by_id")
_LIST_REF_FIELDS = ("abuse_flaggers", "historical_abuse_flaggers")


def collect_user_refs(doc, refs: set) -> None:
    """Add every raw user-id string referenced by one content doc to `refs`."""
    for field in _SINGLE_REF_FIELDS:
        if doc.get(field):
            refs.add(str(doc[field]))
    for field in _LIST_REF_FIELDS:
        refs.update(str(u) for u in doc.get(field) or [] if u)
    votes = doc.get("votes")
    if isinstance(votes, dict):
        for direction in ("up", "down"):
            refs.update(str(u) for u in votes.get(direction) or [] if u)
    endorsement = doc.get("endorsement")
    if isinstance(endorsement, dict) and endorsement.get("user_id"):
        refs.add(str(endorsement["user_id"]))
    for entry in doc.get("edit_history") or []:
        if isinstance(entry, dict) and entry.get("author_id"):
            refs.add(str(entry["author_id"]))


def thread_id(doc):
    """Hex-STRING id of a CommentThread doc (what subscriptions' `source_id` holds), else None."""
    return str(doc["_id"]) if doc.get("_type") == "CommentThread" else None


def scope_user_doc(doc: dict, member_ids, course_re) -> tuple:
    """-> (scoped copy, email_blanked). Non-members lose `email`; everyone's
    `read_states` / `course_stats` keep only tenant-course entries. Every other field
    (e.g. `notification_ids`) is copied verbatim -- opaque ids, not tenant data."""
    out = dict(doc)
    blanked = False
    if str(doc.get("_id")) not in member_ids and out.get("email"):
        out["email"] = ""
        blanked = True
    for field in ("read_states", "course_stats"):
        if isinstance(out.get(field), list):
            out[field] = [
                e for e in out[field] if isinstance(e, dict) and course_re.match(str(e.get("course_id", "")))
            ]
    return out, blanked


def secret_fields(doc, prefix="") -> set:
    """Names (dotted) of secret-looking keys in a doc, via `audit.SECRET_NAME_RE`.
    Over-matches on purpose; `_id` and Mongo `$` operators are ignored."""
    found = set()
    for name, value in doc.items():
        path = f"{prefix}{name}"
        if name != "_id" and not name.startswith("$") and audit.SECRET_NAME_RE.search(name) \
                and not audit._BENIGN.search(name):  # pylint: disable=protected-access
            found.add(path)
        if isinstance(value, dict):
            found |= secret_fields(value, path + ".")
    return found


# Names that ALWAYS abort the collection (never blanked): password/secret/token/api key/`*_key`.
HARD_SECRET_RE = re.compile(r"pass(word|wd)?|secret|token|api_?key|_key$|^key$", re.I)


def split_secrets(found) -> tuple:
    """-> (hard, soft) sets of dotted paths; decided on the LAST path segment (the field name)."""
    hard = {p for p in found if HARD_SECRET_RE.search(p.rsplit(".", 1)[-1])}
    return hard, set(found) - hard


def blank_fields(doc: dict, paths) -> dict:
    """Deep copy of `doc` with the value at each dotted path set to "" (input untouched)."""
    out = copy.deepcopy(doc)
    for path in paths:
        *parents, leaf = path.split(".")
        node = out
        for part in parents:
            node = node[part]
        node[leaf] = ""
    return out


def sanitize(doc: dict, hard: set, soft: set) -> dict:
    """Scan one doc: HARD paths are added to `hard` (caller aborts); soft ones are blanked in the
    returned doc and added to `soft` for the manifest."""
    h, sft = split_secrets(secret_fields(doc))
    hard |= h
    if sft:
        soft |= sft
        return blank_fields(doc, sft)
    return doc


# ---- output ------------------------------------------------------------------

def dumps(doc) -> str:
    """One Extended-JSON line (canonical: lossless ObjectId/Date/Int64)."""
    try:
        from bson import json_util  # pylint: disable=import-outside-toplevel
    except ImportError:  # offline tests / no pymongo: plain JSON, str() for ObjectId & co
        return json.dumps(doc, sort_keys=True, default=str)
    return json_util.dumps(doc, sort_keys=True, json_options=json_util.CANONICAL_JSON_OPTIONS)


class JsonlWriter:
    """`<path>.partial` -> rename on `finish()`; tracks rows + sha256 of what it wrote."""

    def __init__(self, path):
        self.path = Path(path)
        self.partial = Path(str(path) + ".partial")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rows = 0
        self._sha = hashlib.sha256()
        self._fh = open(self.partial, "wb")  # pylint: disable=consider-using-with

    def write(self, doc) -> None:
        data = (dumps(doc) + "\n").encode("utf-8")
        self._fh.write(data)
        self._sha.update(data)
        self.rows += 1

    def finish(self) -> str:
        self._fh.close()
        os.replace(self.partial, self.path)
        return self._sha.hexdigest()

    def abort(self) -> None:
        self._fh.close()
        if self.partial.exists():
            self.partial.unlink()


# ---- orchestration -----------------------------------------------------------

def preflight(db) -> int:
    """Wrong-DB guard: the forum's `contents` collection must be non-empty globally."""
    total = db["contents"].count_documents({})
    if total <= 0:
        raise ForumError("`contents` is empty -- wrong Mongo DB name/host? (check EXPORT_TENANT_FORUM_MONGO)")
    return total


def _refuse_secrets(mf, collection, found, writer=None) -> None:
    """Abort a collection's export if its docs carry HARD secret-named fields (`found` = hard set only)."""
    if not found:
        return
    msg = f"HARD secret-named field(s) in forum {collection}, refusing to export: {sorted(found)}"
    if writer:
        writer.abort()
        mf.update_table(key(collection), status="error", error=msg)
    raise ForumError(msg)


def _chunks(values, size=CHUNK):
    values = sorted(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]


def export_forum(db, orgs, member_ids, out_dir, slug, mf, log, dry_run=False) -> dict:
    """Export one tenant's forum. `mf` is a manifest.Manifest (None when dry_run);
    `log(str)` prints. Returns the stats dict (also what dry-run prints)."""
    out_dir = Path(out_dir)
    flt = build_content_filter(orgs)
    course_re = re.compile(content_pattern(orgs), re.I)
    member_ids = {str(m) for m in member_ids}
    preflight(db)

    done = {c: (not dry_run and resume.is_done(out_dir, slug, key(c))) for c in COLLECTIONS}
    if not done["contents"]:
        for c in ("users", "subscriptions"):  # downstream of contents: stale once contents re-runs
            done[c] = False
            if not dry_run:
                marker = resume.state_dir(out_dir, slug) / f"{key(c)}.done"
                if marker.exists():
                    marker.unlink()

    stats = {"contents": 0, "threads": 0, "comments": 0, "cohorted_threads": 0, "referenced_users": 0,
             "user_docs": 0, "emails_blanked": 0, "subscriptions": 0, "user_follows_skipped": 0}
    thread_ids, refs, secrets, blanked_contents = set(), set(), set(), set()
    writer = None if (dry_run or done["contents"]) else JsonlWriter(out_dir / FORUM_DIR / "contents.jsonl")

    try:
        # pass 1: contents (also derives thread ids + user refs for the other two)
        for doc in db["contents"].find(flt):
            stats["contents"] += 1
            tid = thread_id(doc)
            if tid:
                thread_ids.add(tid)
                stats["threads"] += 1
                if doc.get("group_id") is not None:
                    stats["cohorted_threads"] += 1
            else:
                stats["comments"] += 1
            collect_user_refs(doc, refs)
            doc = sanitize(doc, secrets, blanked_contents)
            if writer:
                writer.write(doc)
        if secrets:
            raise ForumError(f"HARD secret-named field(s) in forum contents, refusing to export: {sorted(secrets)}")
    except Exception as exc:
        if writer:
            writer.abort()
            mf.update_table(key("contents"), status="error", error=str(exc))
        raise
    stats["referenced_users"] = len(refs)

    if writer:
        sha = writer.finish()
        mf.update_table(key("contents"), status="complete", file=f"{FORUM_DIR}/contents.jsonl", format="jsonl",
                        rows=writer.rows, sha256=sha, threads=stats["threads"], comments=stats["comments"],
                        cohorted_threads=stats["cohorted_threads"], blanked_secret_fields=sorted(blanked_contents),
                        notes="cohort group_id values are intentionally opaque (not remapped); they join to "
                              "Phase 1 course_groups_courseusergroup")
        resume.mark_done(out_dir, slug, key("contents"))
        log(f"wrote {key('contents')}: {writer.rows} rows")

    # pass 2: subscriptions (thread-follows only)
    sub_secrets, blanked_subs = set(), set()
    sub_writer = None if (dry_run or done["subscriptions"]) else JsonlWriter(out_dir / FORUM_DIR / "subscriptions.jsonl")
    for chunk in ([] if done["subscriptions"] else _chunks(thread_ids)):
        for sub in db["subscriptions"].find({"source_id": {"$in": chunk}}):
            if sub.get("source_type") != "CommentThread":
                stats["user_follows_skipped"] += 1
                continue
            stats["subscriptions"] += 1
            sub = sanitize(sub, sub_secrets, blanked_subs)
            if sub_writer:
                sub_writer.write(sub)
    _refuse_secrets(mf, "subscriptions", sub_secrets, sub_writer)
    if sub_writer:
        sha = sub_writer.finish()
        mf.update_table(key("subscriptions"), status="complete", file=f"{FORUM_DIR}/subscriptions.jsonl",
                        format="jsonl", rows=sub_writer.rows, sha256=sha,
                        user_follows_skipped=stats["user_follows_skipped"], blanked_secret_fields=sorted(blanked_subs))
        resume.mark_done(out_dir, slug, key("subscriptions"))
        log(f"wrote {key('subscriptions')}: {sub_writer.rows} rows")

    # pass 3: users (derived from refs; PII-scoped)
    user_secrets, blanked_users = set(), set()
    user_writer = None if (dry_run or done["users"]) else JsonlWriter(out_dir / FORUM_DIR / "users.jsonl")
    for chunk in ([] if done["users"] else _chunks(refs)):
        for user in db["users"].find({"_id": {"$in": chunk}}):
            scoped, blanked = scope_user_doc(user, member_ids, course_re)
            stats["user_docs"] += 1
            stats["emails_blanked"] += blanked
            scoped = sanitize(scoped, user_secrets, blanked_users)
            if user_writer:
                user_writer.write(scoped)
    _refuse_secrets(mf, "users", user_secrets, user_writer)
    if user_writer:
        sha = user_writer.finish()
        mf.update_table(key("users"), status="complete", file=f"{FORUM_DIR}/users.jsonl", format="jsonl",
                        rows=user_writer.rows, sha256=sha, referenced_ids=len(refs),
                        ids_without_user_doc=len(refs) - user_writer.rows, emails_blanked=stats["emails_blanked"],
                        blanked_secret_fields=sorted(blanked_users),
                        pii_policy="non-member emails blanked; read_states/course_stats filtered to tenant courses")
        resume.mark_done(out_dir, slug, key("users"))
        log(f"wrote {key('users')}: {user_writer.rows} rows")

    return stats
