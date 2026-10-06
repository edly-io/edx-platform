"""Offline Phase 3 forum tests (EDLYPRODUCT-8584): two-tenant leak, ObjectId->hex
subscription trap, case-insensitive org match, PII scoping, secret scan,
JSONL/manifest/resume semantics, dry-run writes nothing, mongo params."""
import json
import tempfile
import unittest
from pathlib import Path

from openedx.features.edly.tenant_export import forum, mongo, resume, scope
from openedx.features.edly.tenant_export.manifest import Manifest
from openedx.features.edly.tests.tenant_export_fakes import FakeMongo


class FakeOid:
    """ObjectId stand-in: str() is the hex, but it is NOT equal to that string."""

    def __init__(self, hexid):
        self.hexid = hexid

    def __str__(self):
        return self.hexid

    def __repr__(self):
        return f"Oid({self.hexid})"


A, B = "course-v1:MITx+6.00+2024", "course-v1:Other+X+1"
T_A, T_B = FakeOid("a" * 24), FakeOid("b" * 24)


def _db(extra_content=None):
    contents = [
        {"_id": T_A, "_type": "CommentThread", "course_id": A, "author_id": "1", "closed_by_id": "2",
         "votes": {"up": ["1", "9"], "down": []}, "group_id": 5},
        {"_id": FakeOid("c" * 24), "_type": "Comment", "course_id": "course-v1:mitx+6.00+2024",  # case differs
         "author_id": "2", "comment_thread_id": T_A, "endorsement": {"user_id": "1"},
         "edit_history": [{"author_id": "1"}], "abuse_flaggers": ["1"]},
        {"_id": T_B, "_type": "CommentThread", "course_id": B, "author_id": "3"},
    ] + (extra_content or [])
    users = [
        {"_id": "1", "username": "alice", "email": "alice@x.org", "external_id": "1",
         "read_states": [{"course_id": A}], "course_stats": [{"course_id": A}]},
        {"_id": "2", "username": "staff", "email": "staff@edly.io", "external_id": "2",
         "read_states": [{"course_id": A}, {"course_id": B}], "course_stats": [{"course_id": B}]},
        {"_id": "3", "username": "bob", "email": "bob@o.org", "read_states": [{"course_id": B}]},
    ]
    subs = [
        {"_id": "s1", "source_type": "CommentThread", "source_id": str(T_A), "subscriber_id": "1"},
        {"_id": "s2", "source_type": "CommentThread", "source_id": str(T_B), "subscriber_id": "3"},
        {"_id": "s3", "source_type": "User", "source_id": str(T_A), "subscriber_id": "2"},
    ]
    return FakeMongo(contents=contents, users=users, subscriptions=subs)


def _rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


class SelectionTests(unittest.TestCase):
    def test_filter_is_case_insensitive_and_escapes_orgs(self):
        flt = forum.build_content_filter(["MITx", "a.b"])
        self.assertEqual(flt["course_id"]["$options"], "i")
        self.assertIn(r"a\.b", flt["course_id"]["$regex"])
        self.assertTrue(flt["course_id"]["$regex"].startswith("^course-v1:("))

    def test_refs_cover_every_field_and_stringify(self):
        refs = set()
        forum.collect_user_refs(_db().collections["contents"].docs[1], refs)
        self.assertEqual(refs, {"1", "2"})
        refs = set()
        forum.collect_user_refs(_db().collections["contents"].docs[0], refs)
        self.assertEqual(refs, {"1", "2", "9"})

    def test_thread_id_is_hex_string(self):
        self.assertEqual(forum.thread_id({"_id": T_A, "_type": "CommentThread"}), "a" * 24)
        self.assertIsNone(forum.thread_id({"_id": T_A, "_type": "Comment"}))

    def test_secret_fields(self):
        self.assertEqual(forum.secret_fields({"_id": 1, "title": "x", "api_key": "k", "n": {"password": "p"}}),
                         {"api_key", "n.password"})
        self.assertEqual(forum.secret_fields({"_id": 1, "title": "x", "body": "y", "course_id": "c"}), set())


class ExportTests(unittest.TestCase):
    def _run(self, db=None, dry_run=False, out=None, members=("1",)):
        out = Path(out or tempfile.mkdtemp())
        mf = None if dry_run else Manifest(out / "MANIFEST.json", "mit", "sha", [forum.key(c) for c in forum.COLLECTIONS])
        logs = []
        stats = forum.export_forum(db or _db(), ["MITx"], members, out, "mit", mf, logs.append, dry_run=dry_run)
        return out, mf, stats

    def test_two_tenant_leak_and_counts(self):
        out, mf, stats = self._run()
        contents = _rows(out / "forum/contents.jsonl")
        self.assertEqual({c["course_id"].lower() for c in contents}, {A.lower()})
        self.assertEqual((stats["threads"], stats["comments"], stats["cohorted_threads"]), (1, 1, 1))
        self.assertEqual({u["_id"] for u in _rows(out / "forum/users.jsonl")}, {"1", "2"})  # not 3 (B), 9 has no doc
        self.assertEqual([s["_id"] for s in _rows(out / "forum/subscriptions.jsonl")], ["s1"])
        self.assertEqual(stats["user_follows_skipped"], 1)
        self.assertEqual(mf.finalize(), "complete")
        self.assertEqual(mf.data["tables"]["forum__users"]["ids_without_user_doc"], 1)  # voter-only id 9
        self.assertEqual(mf.data["tables"]["forum__contents"]["file"], "forum/contents.jsonl")

    def test_subscription_source_id_objectid_trap(self):
        # Querying with the ObjectId itself (the trap) would match nothing; hex strings match.
        db = _db()
        self.assertEqual(db["subscriptions"].count_documents({"source_id": {"$in": [T_A]}}), 0)
        out, _, stats = self._run(db)
        self.assertEqual(stats["subscriptions"], 1)

    def test_pii_scoping(self):
        out, _, stats = self._run(members=("1",))
        users = {u["_id"]: u for u in _rows(out / "forum/users.jsonl")}
        self.assertEqual(users["1"]["email"], "alice@x.org")
        self.assertEqual(users["2"]["email"], "")
        self.assertEqual(users["2"]["username"], "staff")
        self.assertEqual(users["2"]["read_states"], [{"course_id": A}])  # B's course filtered out
        self.assertEqual(users["2"]["course_stats"], [])
        self.assertEqual(stats["emails_blanked"], 1)
        self.assertNotIn("bob", (out / "forum/users.jsonl").read_text())

    def test_secret_field_blocks_export(self):
        db = _db([{"_id": FakeOid("d" * 24), "_type": "Comment", "course_id": A, "api_key": "k"}])
        out = Path(tempfile.mkdtemp())
        mf = Manifest(out / "MANIFEST.json", "mit", "sha", [])
        with self.assertRaises(forum.ForumError):
            forum.export_forum(db, ["MITx"], [], out, "mit", mf, print)
        self.assertEqual(mf.data["tables"]["forum__contents"]["status"], "error")
        self.assertFalse((out / "forum/contents.jsonl").exists())
        self.assertFalse((out / "forum/contents.jsonl.partial").exists())

    def test_soft_secret_name_is_blanked_and_recorded_not_aborted(self):
        db = _db([{"_id": FakeOid("d" * 24), "_type": "Comment", "course_id": A, "author_id": "1",
                   "signature": "sig-value", "meta": {"oauth_provider": "x", "ok": 1}}])
        out, mf, _ = self._run(db)
        rows = {r["_id"]: r for r in _rows(out / "forum/contents.jsonl")}
        row = rows["d" * 24]
        self.assertEqual((row["signature"], row["meta"]["oauth_provider"], row["meta"]["ok"]), ("", "", 1))
        self.assertNotIn("sig-value", (out / "forum/contents.jsonl").read_text())
        self.assertEqual(mf.data["tables"]["forum__contents"]["blanked_secret_fields"], ["meta.oauth_provider", "signature"])
        self.assertEqual(mf.data["tables"]["forum__users"]["blanked_secret_fields"], [])
        self.assertEqual(mf.finalize(), "complete")
        # source docs untouched (deep copy)
        self.assertEqual(db["contents"].docs[-1]["signature"], "sig-value")

    def test_soft_secret_in_users_blanked(self):
        db = _db()
        db["users"].docs[0]["private_notes"] = "hidden"
        out, mf, _ = self._run(db)
        users = {u["_id"]: u for u in _rows(out / "forum/users.jsonl")}
        self.assertEqual(users["1"]["private_notes"], "")
        self.assertEqual(mf.data["tables"]["forum__users"]["blanked_secret_fields"], ["private_notes"])

    def test_hard_vs_soft_split(self):
        hard, soft = forum.split_secrets({"api_key", "n.password", "x.token", "signature", "a.salt", "client_key"})
        self.assertEqual(hard, {"api_key", "n.password", "x.token", "client_key"})
        self.assertEqual(soft, {"signature", "a.salt"})

    def test_secret_field_in_users_or_subscriptions_blocks_export(self):
        for coll, doc in (("users", {"_id": "1", "username": "alice", "api_key": "k"}),
                          ("subscriptions", {"_id": "s9", "source_type": "CommentThread", "source_id": str(T_A),
                                             "subscriber_id": "1", "token": "t"})):
            db = _db()
            db[coll].docs.append(doc) if coll == "subscriptions" else db[coll].docs.__setitem__(0, doc)
            out = Path(tempfile.mkdtemp())
            mf = Manifest(out / "MANIFEST.json", "mit", "sha", [])
            with self.assertRaises(forum.ForumError):
                forum.export_forum(db, ["MITx"], ["1"], out, "mit", mf, print)
            self.assertEqual(mf.data["tables"][forum.key(coll)]["status"], "error")
            self.assertFalse((out / f"forum/{coll}.jsonl").exists())
            self.assertFalse((out / f"forum/{coll}.jsonl.partial").exists())

    def test_cohort_group_id_note_in_manifest(self):
        _, mf, _ = self._run()
        self.assertIn("course_groups_courseusergroup", mf.data["tables"]["forum__contents"]["notes"])

    def test_wrong_db_guard(self):
        with self.assertRaises(forum.ForumError):
            self._run(FakeMongo(contents=[]))

    def test_dry_run_writes_nothing(self):
        out = Path(tempfile.mkdtemp()) / "run"
        _, _, stats = self._run(dry_run=True, out=out)
        self.assertEqual(stats["contents"], 2)
        self.assertFalse(out.exists())

    def test_resume_and_downstream_invalidation(self):
        out, mf, _ = self._run()
        for c in forum.COLLECTIONS:
            self.assertTrue(resume.is_done(out, "mit", forum.key(c)))
        before = (out / "forum/contents.jsonl").read_bytes()
        db = _db()
        self._run(db, out=out)  # all done: nothing rewritten, no users/subs queries
        self.assertEqual(db["users"].queries, [])
        self.assertEqual((out / "forum/contents.jsonl").read_bytes(), before)
        # re-running contents clears the downstream markers (and re-derives them)
        (resume.state_dir(out, "mit") / "forum__contents.done").unlink()
        db2 = _db()
        self._run(db2, out=out)
        self.assertTrue(db2["users"].queries)

    def test_jsonl_sha_matches_file(self):
        import hashlib
        out, mf, _ = self._run()
        for c in forum.COLLECTIONS:
            entry = mf.data["tables"][forum.key(c)]
            self.assertEqual(entry["sha256"], hashlib.sha256((out / entry["file"]).read_bytes()).hexdigest())
            self.assertEqual(entry["rows"], len((out / entry["file"]).read_text().splitlines()))


class MongoParamsTests(unittest.TestCase):
    def test_setting_wins_then_env(self):
        self.assertEqual(mongo.forum_params({"URI": "mongodb://u:p@h/", "DB": "d"}, {}), {"uri": "mongodb://u:p@h/", "db": "d"})
        self.assertEqual(mongo.forum_params(None, {"EXPORT_TENANT_FORUM_MONGO_URI": "mongodb://h/"})["db"], "cs_comments_service")
        with self.assertRaises(ValueError):
            mongo.forum_params(None, {})

    def test_parts_and_masking(self):
        params = mongo.forum_params({"HOST": "h", "PORT": 27017, "USER": "u", "PASSWORD": "p@ss"}, {})
        self.assertEqual(params["uri"], "mongodb://u:p%40ss@h:27017/")
        self.assertEqual(mongo.mask_uri(params["uri"]), "mongodb://***@h:27017/")
        self.assertNotIn("p%40ss", mongo.mask_uri(params["uri"]))

    def test_modulestore_params(self):
        p = mongo.modulestore_params({"host": ["a", "b"], "port": 27017, "db": "edxapp", "user": "u", "password": "x"})
        self.assertEqual(p["db"], "edxapp")
        self.assertTrue(p["uri"].startswith("mongodb://u:x@a,b"))


class ScopeOrgsTests(unittest.TestCase):
    def test_derive_orgs_m2m_base_plus_case_variants_only(self):
        ids = [A, "course-v1:mitx+7+1", "course-v1:Stray+1+1"]
        # M2M is the base; `mitx` is added as a documented case variant; `Stray` (not in M2M) is NOT.
        self.assertEqual(scope.derive_orgs(["MITx"], ["MITx", "Stray"], ids), ["MITx", "mitx"])
        self.assertEqual(scope.derive_orgs(["MITx", "Extra"], ["MITx"], [A]), ["Extra", "MITx"])

    def test_derive_orgs_falls_back_when_m2m_empty(self):
        self.assertEqual(scope.derive_orgs([], ["mitx"], [A]), scope.merge_orgs(["mitx"], [A]))
        self.assertEqual(scope.derive_orgs(None, ["mitx"], [A]), ["MITx", "mitx"])

    def test_scope_orgs_uses_m2m_for_scope_files_without_course_orgs(self):
        data = {"course_org_filter": ["MITx", "Stray"], "course_ids": [A, "course-v1:Stray+1+1"], "edx_orgs_m2m": ["MITx"]}
        self.assertEqual(scope.scope_orgs(data), ["MITx"])

    def test_resolve_scope_course_orgs_from_m2m(self):
        class Cur:
            def __init__(self):
                self.q, self.last = [], None

            def execute(self, sql, params=None):
                self.last = sql

            def fetchone(self):
                if "FROM edly_edlysuborganization WHERE" in self.last:
                    return (7, "n", 3)
                if "COUNT" in self.last:
                    return (2,)
                return ('{"course_org_filter": ["mitx", "Stray"]}',)

            def fetchall(self):
                if "short_name" in self.last:
                    return [("MITx",)]
                return [("course-v1:MITx+1+1",), ("course-v1:mitx+2+1",), ("course-v1:Stray+1+1",)]

        out = scope.resolve_scope(Cur(), "mit")
        self.assertEqual(out["course_orgs"], ["MITx", "mitx"])
        self.assertEqual(out["course_orgs_source"], "edx_orgs_m2m")
        self.assertEqual(out["edx_orgs_m2m"], ["MITx"])

    def test_merge_keeps_real_case(self):
        self.assertEqual(scope.merge_orgs(["mitx"], [A, "course-v1:MITx+7+1"]), ["MITx", "mitx"])

    def test_scope_orgs_computed_for_old_scope_files(self):
        data = {"course_org_filter": ["mitx"], "course_ids": [A]}
        self.assertEqual(scope.scope_orgs(data), ["MITx", "mitx"])
        self.assertEqual(scope.scope_orgs({**data, "course_orgs": ["X"]}), ["X"])
        with self.assertRaises(ValueError):
            scope.scope_orgs({**data, "course_orgs": ["a'b"]})


if __name__ == "__main__":
    unittest.main()
