"""Offline Phase 3 S3 tests (EDLYPRODUCT-8584): per-resolver two-tenant leak
fixtures over sqlite + FakeS3, ORA key order, prefix delimiters, grades
ROOT_PATH, profile seed handling, video SQL-union-Mongo, copy engine
(server/stream, skip/resume, guards, index/manifest), sources config, and
Phase 3 package wiring (--skip-*, entry["file"] verification)."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from openedx.features.edly.tenant_export import (
    manifest as manifest_mod, s3_copy, s3_resolvers as R, s3_sources, tables, video_discovery,
)
from openedx.features.edly.tests.tenant_export_fakes import FakeMongo, FakeS3

SUB_A, SUB_B = 1, 2
ORGS_A = ["MITx"]


def _sha1(s):
    return hashlib.sha1(s.encode()).hexdigest()


def _edx():
    c = sqlite3.connect(":memory:")
    for ddl in (
        "edly_edlymultisiteaccess (user_id, sub_org_id)", "auth_user (id, username)",
        "auth_userprofile (user_id, profile_image_uploaded_at)", "student_anonymoususerid (user_id, anonymous_user_id)",
        "submissions_studentitem (student_id, course_id, item_type)",
        "courseware_studentmodule (module_id, module_type, course_id)",
        "edxval_coursevideo (id, video_id, course_id)", "edxval_video (id, edx_video_id)",
        "edxval_videotranscript (video_id, transcript)", "edxval_videoimage (course_video_id, image)",
    ):
        c.execute(f"CREATE TABLE {ddl}")
    c.executemany("INSERT INTO edly_edlymultisiteaccess VALUES (?,?)", [(1, SUB_A), (2, SUB_A), (3, SUB_B)])
    c.executemany("INSERT INTO auth_user VALUES (?,?)", [(1, "alice"), (2, "bob"), (3, "carol"), (4, "outsider")])
    c.executemany("INSERT INTO auth_userprofile VALUES (?,?)",
                  [(1, "2024-01-01"), (2, None), (3, "2024-01-01"), (4, "2024-01-01")])
    c.executemany("INSERT INTO student_anonymoususerid VALUES (?,?)", [(1, "anonA1"), (2, "anonA2"), (3, "anonB3"), (4, "anonX")])
    c.executemany("INSERT INTO submissions_studentitem VALUES (?,?,?)", [
        ("anonA1", "course-v1:MITx+1+1", "openassessment"),
        ("anonA2", "course-v1:MITx+1+1", "sga"),                   # not ORA
        ("anonB3", "course-v1:Other+1+1", "openassessment"),       # other tenant
        ("anonX", "course-v1:MITx+1+1", "openassessment"),         # non-member in tenant course
    ])
    c.executemany("INSERT INTO courseware_studentmodule VALUES (?,?,?)", [
        ("block-v1:MITx+1+1+type@scorm+block@a", "scorm", "course-v1:MITx+1+1"),
        ("block-v1:Other+1+1+type@scorm+block@b", "scorm", "course-v1:Other+1+1"),
        ("block-v1:MITx+1+1+type@html+block@c", "html", "course-v1:MITx+1+1"),
    ])
    c.executemany("INSERT INTO edxval_video VALUES (?,?)", [(1, "ev-sql"), (2, "ev-mongo"), (3, "ev-other"), (4, "ev-def")])
    c.executemany("INSERT INTO edxval_coursevideo VALUES (?,?,?)", [(10, 1, "course-v1:MITx+1+1"), (11, 3, "course-v1:Other+1+1")])
    c.executemany("INSERT INTO edxval_videotranscript VALUES (?,?)",
                  [(1, "video-transcripts/t1.srt"), (2, "video-transcripts/t2.srt"), (3, "video-transcripts/t3.srt"),
                   (4, "video-transcripts/t4.srt"), (1, "")])
    c.executemany("INSERT INTO edxval_videoimage VALUES (?,?)", [(10, "video-images/i1.jpg"), (11, "video-images/i3.jpg")])
    return c


def _svc(db):
    c = sqlite3.connect(":memory:")
    if db == "discovery":
        c.execute("CREATE TABLE course_metadata_organization (partner_id, logo_image, certificate_logo_image, banner_image)")
        c.execute("CREATE TABLE course_metadata_program (partner_id, banner_image, card_image)")
        c.executemany("INSERT INTO course_metadata_organization VALUES (?,?,?,?)",
                      [(1, "org/logo.png", None, "org/banner.jpg"), (2, "org/other.png", None, None)])
        c.executemany("INSERT INTO course_metadata_program VALUES (?,?,?)",
                      [(1, "prog/b.jpg", "prog/c.jpg"), (2, "prog/b2.jpg", None)])
    else:
        c.execute("CREATE TABLE credentials_signatory (id, image)")
        c.execute("CREATE TABLE credentials_coursecertificate (id, site_id)")
        c.execute("CREATE TABLE credentials_programcertificate (id, site_id)")
        c.execute("CREATE TABLE credentials_coursecertificate_signatories (id, coursecertificate_id, signatory_id)")
        c.execute("CREATE TABLE credentials_programcertificate_signatories (id, programcertificate_id, signatory_id)")
        c.executemany("INSERT INTO credentials_signatory VALUES (?,?)", [(1, "sig/a.png"), (2, "sig/b.png"), (3, "sig/c.png"), (4, "")])
        c.executemany("INSERT INTO credentials_coursecertificate VALUES (?,?)", [(100, 1), (200, 2)])
        c.executemany("INSERT INTO credentials_programcertificate VALUES (?,?)", [(101, 1), (201, 2)])
        c.executemany("INSERT INTO credentials_coursecertificate_signatories VALUES (?,?,?)", [(1, 100, 1), (2, 200, 2)])
        c.executemany("INSERT INTO credentials_programcertificate_signatories VALUES (?,?,?)", [(1, 101, 3), (2, 201, 4)])
    return c


def _ctx(store, **kw):
    edx, dbs = _edx(), {"discovery": _svc("discovery"), "credentials": _svc("credentials")}
    base = dict(
        slug="mit", sub_org_id=SUB_A, course_orgs=ORGS_A, course_ids=["course-v1:MITx+1+1"],
        services={"discovery": {"partner_id": 1}, "credentials": {"site_id": 1}},
        edx_rows=lambda sql: edx.execute(sql).fetchall(),
        svc_rows=lambda db, sql: dbs[db].execute(sql).fetchall(),
        list_keys=s3_copy.make_lister(store, "src"), log=lambda *_: None,
    )
    base.update(kw)
    return R.ResolverContext(**base)


def _store(keys):
    return FakeS3({"src": {k: b"x" for k in keys}})


class ResolverTests(unittest.TestCase):
    def test_discovery_scoped_with_variations(self):
        keys = list(R.discovery(_ctx(_store([]))))
        self.assertIn("org/logo.png", keys)
        self.assertIn("prog/b.large.jpg", keys)
        self.assertIn("prog/c.card.jpg", keys)
        self.assertFalse([k for k in keys if "other" in k or "b2" in k])

    def test_credentials_scoped_via_phase2_where(self):
        self.assertEqual(sorted(R.credentials(_ctx(_store([])))), ["sig/a.png", "sig/c.png"])

    def test_service_block_missing_is_an_error_not_a_skip(self):
        with self.assertRaises(R.ResolverError) as cm:
            R.discovery(_ctx(_store([]), services={}))
        self.assertIn("export_tenant_scope --services discovery", str(cm.exception))

    def test_require_service_blocks_precheck(self):
        with self.assertRaises(R.ResolverError) as cm:
            R.require_service_blocks(["grades", "discovery", "credentials"], {"credentials": {"site_id": 1}})
        self.assertIn("services discovery", str(cm.exception).replace("--", "").replace("services.", "services "))
        R.require_service_blocks(["grades", "edx-storage"], {})                      # excluded via --buckets: fine
        R.require_service_blocks(["discovery", "credentials"], {"discovery": {"partner_id": 1}, "credentials": {"site_id": 1}})

    def test_profile_images_sample_query_is_ordered(self):
        sqls = []
        ctx = _ctx(_store([]), profile_seed="seed", edx_rows=lambda sql: sqls.append(sql) or [])
        list(R.profile_images(ctx))
        self.assertTrue(sqls[0].rstrip().endswith("ORDER BY u.id"))

    def test_ora_coverage_errors_when_pairs_but_no_objects(self):
        ctx = _ctx(_store([]))
        self.assertEqual(list(R.ora_submissions(ctx)), [])
        level, msg = R.coverage("ora-submissions", ctx, {"candidates": 0})
        self.assertEqual(level, "error")
        self.assertIsNone(R.coverage("ora-submissions", ctx, {"candidates": 3}))

    def test_ora_coverage_clean_for_tenant_without_pairs(self):
        ctx = _ctx(_store([]), sub_org_id=99)
        self.assertEqual(list(R.ora_submissions(ctx)), [])
        self.assertIsNone(R.coverage("ora-submissions", ctx, {"candidates": 0}))

    def test_grades_coverage_error_warning_and_clean(self):
        a, b = _sha1("course-v1:MITx+1+1"), _sha1("course-v1:MITx+2+1")
        cids = ["course-v1:MITx+1+1", "course-v1:MITx+2+1"]
        ctx = _ctx(_store([]), course_ids=cids)
        self.assertEqual(list(R.grades(ctx)), [])
        self.assertEqual(R.coverage("grades", ctx, {"candidates": 0})[0], "error")
        ctx = _ctx(_store([f"{a}/g.csv"]), course_ids=cids)
        self.assertEqual(list(R.grades(ctx)), [f"{a}/g.csv"])
        self.assertEqual(ctx.stats["grades"], {"course_dirs_expected": 2, "course_dirs_found": 1})
        self.assertEqual(R.coverage("grades", ctx, {"candidates": 1})[0], "warning")
        ctx = _ctx(_store([f"{a}/g.csv", f"{b}/g.csv"]), course_ids=cids)
        list(R.grades(ctx))
        self.assertIsNone(R.coverage("grades", ctx, {"candidates": 2}))
        ctx = _ctx(_store([]), course_ids=[])                                       # empty tenant stays clean
        list(R.grades(ctx))
        self.assertIsNone(R.coverage("grades", ctx, {"candidates": 0}))

    def test_edx_storage_coverage_warns_only_for_tenant_with_courses(self):
        self.assertEqual(R.coverage("edx-storage", _ctx(_store([])), {"candidates": 0})[0], "warning")
        self.assertIsNone(R.coverage("edx-storage", _ctx(_store([])), {"candidates": 1}))
        self.assertIsNone(R.coverage("edx-storage", _ctx(_store([]), course_ids=[]), {"candidates": 0}))

    def test_grades_honours_root_path_and_sha1_dir(self):
        a, b = _sha1("course-v1:MITx+1+1"), _sha1("course-v1:Other+1+1")
        store = _store([f"reports/{a}/g.csv", f"{a}/nonroot.csv", f"reports/{b}/g.csv"])
        self.assertEqual(list(R.grades(_ctx(store, root_path="reports"))), [f"reports/{a}/g.csv"])
        self.assertEqual(list(R.grades(_ctx(store, root_path=""))), [f"{a}/nonroot.csv"])

    def test_edx_storage_prefixes_end_in_delimiter_and_no_leak(self):
        scorm_a, scorm_b = _sha1("block-v1:MITx+1+1+type@scorm+block@a"), _sha1("block-v1:Other+1+1+type@scorm+block@b")
        wanted = ["block-v1:MITx+1+1+type@html+block@x/file.png", "h5pxblockmedia/MITx/h5p.zip", "MITx/sga/up.pdf",
                  "scormxblockmedia/MITx/old.zip", f"scorm/{scorm_a}/index.html"]
        decoys = ["block-v1:MITxy+1+1/f", "h5pxblockmedia/MITxx/h", "MITx2/f", "MITx-other/f", "scormxblockmedia/MITx2/f",
                  f"scorm/{scorm_b}/index.html", "block-v1:Other+1+1/f", "unrelated/f"]
        keys = list(R.edx_storage(_ctx(_store(wanted + decoys))))
        self.assertEqual(sorted(keys), sorted(wanted))

    def test_edx_storage_lists_real_case_and_site_config_case_orgs(self):
        keys = list(R.edx_storage(_ctx(_store(["MITx/a", "mitx/b"]), course_orgs=["MITx", "mitx"])))
        self.assertEqual(sorted(keys), ["MITx/a", "mitx/b"])

    def test_ora_key_order_student_first_members_only(self):
        good = "submissions_attachments/anonA1/course-v1:MITx+1+1/item1/0"
        store = _store([
            good, "submissions_attachments/anonA1/course-v1:MITx+1+1/item2",
            "submissions_attachments/anonB3/course-v1:Other+1+1/i",        # other tenant
            "submissions_attachments/anonX/course-v1:MITx+1+1/i",          # non-member
            "submissions_attachments/anonA2/course-v1:MITx+1+1/i",         # not an ORA item
            "submissions_attachments/course-v1:MITx+1+1/anonA1/i",         # the historical wrong order
        ])
        ctx = _ctx(store)
        self.assertEqual(sorted(R.ora_submissions(ctx)), sorted([good, "submissions_attachments/anonA1/course-v1:MITx+1+1/item2"]))
        self.assertEqual(ctx.stats["ora-submissions"]["student_course_pairs"], 1)

    def test_profile_images_seed(self):
        h = R.profile_name_hash("seed", "alice")
        store = _store([f"media/profile-images/{h}_120.jpg", f"media/profile-images/{h}_50.jpg",
                        f"media/profile-images/{R.profile_name_hash('seed', 'carol')}_120.jpg",   # other tenant
                        f"media/profile-images/{R.profile_name_hash('seed', 'outsider')}_120.jpg"])
        ctx = _ctx(store, profile_seed="seed")
        self.assertEqual(sorted(R.profile_images(ctx)), [f"media/profile-images/{h}_120.jpg", f"media/profile-images/{h}_50.jpg"])
        self.assertEqual(ctx.stats["profile-images"], {"users_with_uploads": 1, "users_found": 1})
        self.assertNotIn("seed", json.dumps(ctx.stats))

    def test_profile_images_wrong_seed_is_hard_error(self):
        h = R.profile_name_hash("real-seed", "alice")
        with self.assertRaises(R.ResolverError):
            list(R.profile_images(_ctx(_store([f"media/profile-images/{h}_120.jpg"]), profile_seed="placeholder")))
        with self.assertRaises(R.ResolverError):
            R.profile_images(_ctx(_store([]), profile_seed=""))


def _mongo():
    return FakeMongo(**{
        "modulestore.active_versions": [
            {"org": "MITx", "versions": {"published-branch": "S1", "draft-branch": "S2"}},
            {"org": "Other", "versions": {"published-branch": "S3"}},
        ],
        "modulestore.structures": [
            {"_id": "S1", "blocks": [{"block_type": "video", "fields": {"edx_video_id": "ev-mongo"}},
                                     {"block_type": "video", "fields": {}, "definition": "D1"},
                                     {"block_type": "video", "fields": {"edx_video_id": "ev-unknown"}},
                                     {"block_type": "html", "fields": {"edx_video_id": "nope"}}]},
            {"_id": "S2", "blocks": []},
            {"_id": "S3", "blocks": [{"block_type": "video", "fields": {"edx_video_id": "ev-other"}}]},
        ],
        "modulestore.definitions": [{"_id": "D1", "fields": {"edx_video_id": "ev-def"}}],
    })


class VideoTests(unittest.TestCase):
    def test_sql_union_mongo_with_unresolved_logged(self):
        logs = []
        ids, stats = video_discovery.discover_video_ids(
            lambda sql: _EDX.execute(sql).fetchall(), _mongo(), ORGS_A, logs.append)
        self.assertEqual(ids, {1, 2, 4})                      # ev-sql (SQL) + ev-mongo + ev-def (Mongo-only); not Other's 3
        self.assertEqual(stats["unresolved_edx_video_ids"], ["ev-unknown"])
        self.assertTrue(logs and "ev-unknown" in logs[0])

    def test_unsafe_video_id_is_rejected_not_interpolated(self):
        mongo = _mongo()
        mongo["modulestore.structures"].docs[1]["blocks"] = [{"block_type": "video", "fields": {"edx_video_id": "x' OR '1'='1"}}]
        ids, stats = video_discovery.discover_video_ids(lambda sql: _EDX.execute(sql).fetchall(), mongo, ORGS_A, lambda *_: None)
        self.assertIn("x' OR '1'='1", stats["unresolved_edx_video_ids"])
        self.assertEqual(ids, {1, 2, 4})

    def test_modulestore_required(self):
        with self.assertRaises(ValueError):
            video_discovery.discover_video_ids(lambda sql: [], None, ORGS_A)

    def test_resolver_keys_transcripts_and_images(self):
        ctx = _ctx(_store([]), modulestore_db=_mongo())
        keys = sorted(R.video_meta(ctx))
        self.assertEqual(keys, ["media/video-images/i1.jpg", "media/video-transcripts/t1.srt",
                                "media/video-transcripts/t2.srt", "media/video-transcripts/t4.srt"])
        self.assertEqual(ctx.stats["video-meta"]["total"], 3)


_EDX = _edx()


class CopyEngineTests(unittest.TestCase):
    def _run(self, keys, src=None, dst=None, **kw):
        src = src or FakeS3({"src": {k: k.encode() for k in keys}})
        dst = dst or FakeS3({"dest": {}})
        out = Path(tempfile.mkdtemp())
        kw.setdefault("workers", 2)
        res = s3_copy.copy_bucket("edx-storage", iter(keys), src, "src", dst, "dest", "mit/", out, log=lambda *_: None, **kw)
        return res, src, dst, out

    def test_server_side_copy_layout_and_index(self):
        res, _, dst, out = self._run(["a/1.png", "b/2.png", "b/3.png"])
        self.assertEqual(res["status"], "complete")
        self.assertEqual((res["candidates"], res["copied"]), (3, 3))
        self.assertEqual(set(dst.buckets["dest"]), {f"mit/s3/edx-storage/{k}" for k in ("a/1.png", "b/2.png", "b/3.png")})
        self.assertTrue(all(c[0] == "copy" and c[3] is None and c[4] for c in dst.calls))    # no ExtraArgs/ACL, SourceClient set
        rows = [json.loads(l) for l in (out / res["file"]).read_text().splitlines()]
        self.assertEqual({r["status"] for r in rows}, {"copied"})
        self.assertEqual(res["sha256"], hashlib.sha256((out / res["file"]).read_bytes()).hexdigest())
        self.assertEqual(res["rows"], 3)

    def test_rerun_skips_identical_and_resumes_after_partial(self):
        keys = ["a", "b", "c"]
        res1, src, dst, _ = self._run(keys)
        n_calls = len(dst.calls)
        res2, *_ = self._run(keys, src=src, dst=dst)
        self.assertEqual((res2["copied"], res2["skipped"]), (0, 3))
        self.assertEqual(len(dst.calls), n_calls)
        del dst.buckets["dest"]["mit/s3/edx-storage/b"]
        res3, *_ = self._run(keys, src=src, dst=dst)
        self.assertEqual((res3["copied"], res3["skipped"]), (1, 2))

    def test_size_mismatch_at_dest_is_recopied(self):
        src = FakeS3({"src": {"a": b"hello"}})
        dst = FakeS3({"dest": {"mit/s3/edx-storage/a": b"hi"}})
        res, *_ = self._run(["a"], src=src, dst=dst)
        self.assertEqual(res["copied"], 1)
        self.assertEqual(dst.buckets["dest"]["mit/s3/edx-storage/a"], b"hello")

    def test_missing_source_key_counted_and_all_missing_trips_guard(self):
        res, *_ = self._run(["ok", "gone"], src=FakeS3({"src": {"ok": b"1"}}))
        self.assertEqual((res["copied"], res["missing"], res["status"]), (1, 1, "complete"))
        res, *_ = self._run(["gone1", "gone2"], src=FakeS3({"src": {}}))
        self.assertEqual(res["status"], "error")
        self.assertIn("zero-result guard", res["error"])

    def test_genuinely_empty_tenant_is_complete(self):
        # no coverage expectation (no pairs/courses) -> an empty bucket is fine
        res, *_ = self._run([], coverage=lambda counts: None)
        self.assertEqual((res["status"], res["candidates"], res["warnings"]), ("complete", 0, []))
        res, *_ = self._run([])
        self.assertEqual(res["status"], "complete")

    def test_coverage_error_flips_status_and_warning_is_recorded(self):
        res, *_ = self._run([], coverage=lambda counts: ("error", "expected objects"))
        self.assertEqual(res["status"], "error")
        self.assertIn("expected objects", res["error"])
        res, *_ = self._run([], coverage=lambda counts: ("warning", "looks thin"))
        self.assertEqual((res["status"], res["warnings"]), ("complete", ["looks thin"]))
        res, *_ = self._run(["a"], coverage=lambda counts: ("error", "x") if counts["candidates"] == 0 else None)
        self.assertEqual((res["status"], res["warnings"]), ("complete", []))

    def test_copy_failure_is_error_status_but_others_proceed(self):
        dst = FakeS3({"dest": {}}, fail_keys={"mit/s3/edx-storage/bad"})
        res, *_ = self._run(["ok", "bad"], dst=dst)
        self.assertEqual((res["copied"], res["errors"], res["status"]), (1, 1, "error"))

    def test_post_copy_verification_catches_truncation(self):
        class Truncating(FakeS3):
            def copy(self, *a, **kw):
                super().copy(*a, **kw)
                self.buckets["dest"] = {k: v[:1] for k, v in self.buckets["dest"].items()}
        res, *_ = self._run(["a"], src=FakeS3({"src": {"a": b"hello"}}), dst=Truncating({"dest": {}}))
        self.assertEqual((res["errors"], res["status"]), (1, "error"))

    def test_stream_mode(self):
        res, _, dst, _ = self._run(["a"], mode="stream")
        self.assertEqual(res["copy_mode"], "stream")
        self.assertEqual(dst.calls[0][0], "upload")
        self.assertEqual(dst.buckets["dest"]["mit/s3/edx-storage/a"], b"a")

    def test_same_bucket_refused_and_nonempty_dest_needs_resume(self):
        store = FakeS3({"src": {"a": b"1"}})
        with self.assertRaises(s3_copy.CopyError):
            s3_copy.copy_bucket("x", iter(["a"]), store, "src", store, "src", "p/", tempfile.mkdtemp())
        dst = FakeS3({"dest": {"mit/s3/old": b"1"}})
        with self.assertRaises(s3_copy.CopyError):
            s3_copy.check_dest_empty(dst, "dest", "mit/", resume=False)
        s3_copy.check_dest_empty(dst, "dest", "mit/", resume=True)
        s3_copy.check_dest_empty(dst, "dest", "other/", resume=False)

    def test_dry_run_writes_and_copies_nothing(self):
        out = Path(tempfile.mkdtemp()) / "run"
        src, dst = FakeS3({"src": {"a": b"1"}}), FakeS3({"dest": {}})
        res = s3_copy.copy_bucket("x", iter(["a", "b"]), src, "src", dst, "dest", "p/", out, dry_run=True)
        self.assertEqual(res["candidates"], 2)
        self.assertEqual((dst.buckets["dest"], dst.calls), ({}, []))
        self.assertFalse(out.exists())

    def test_resolver_error_aborts_without_index(self):
        def boom():
            yield "a"
            raise R.ResolverError("bad seed")
        out = Path(tempfile.mkdtemp())
        src = FakeS3({"src": {"a": b"1"}})
        with self.assertRaises(R.ResolverError):
            s3_copy.copy_bucket("x", boom(), src, "src", FakeS3({"dest": {}}), "dest", "p/", out)
        self.assertEqual(list((out / "s3").glob("*")), [])

    def test_normalize_prefix(self):
        self.assertEqual([s3_copy.normalize_prefix(p) for p in ("", "/", "mit", "/mit/", "a/b")], ["", "", "mit/", "mit/", "a/b/"])

    def test_end_to_end_seed_never_in_manifest_or_index(self):
        h = R.profile_name_hash("TOP-SECRET-SEED", "alice")
        src = FakeS3({"src": {f"media/profile-images/{h}_120.jpg": b"img"}})
        dst, out = FakeS3({"dest": {}}), Path(tempfile.mkdtemp())
        ctx = _ctx(src, profile_seed="TOP-SECRET-SEED")
        res = s3_copy.copy_bucket("profile-images", R.profile_images(ctx), src, "src", dst, "dest", "mit/", out)
        mf = manifest_mod.Manifest(out / "MANIFEST.json", "mit", "sha", tables.S3_KEYS)
        mf.update_table("s3__profile-images", source=s3_sources.SourceCfg("profile-images", "src").public(), **ctx.stats["profile-images"], **res)
        text = (out / "MANIFEST.json").read_text() + (out / res["file"]).read_text()
        self.assertNotIn("TOP-SECRET-SEED", text)
        self.assertEqual(res["copied"], 1)


class SourcesTests(unittest.TestCase):
    def test_lms_defaults_and_unconfigured(self):
        lms = {"AWS_STORAGE_BUCKET_NAME": "edx-st", "GRADES_DOWNLOAD": {"BUCKET": "grd", "ROOT_PATH": "reports"}}
        cfgs, missing = s3_sources.resolve_sources({"discovery": {"bucket": "disc"}}, lambda n, d=None: lms.get(n, d),
                                                   list(s3_sources.LOGICAL_BUCKETS))
        self.assertEqual({k: v.bucket for k, v in cfgs.items()},
                         {"edx-storage": "edx-st", "ora-submissions": "edx-st", "grades": "grd", "discovery": "disc"})
        self.assertEqual(cfgs["grades"].root_path, "reports")
        self.assertEqual(sorted(missing), ["credentials", "profile-images", "video-meta"])

    def test_setting_overrides_default_and_public_has_no_credentials(self):
        cfgs, _ = s3_sources.resolve_sources({"edx-storage": {"bucket": "mine", "access_key": "AK", "secret_key": "SK"}},
                                             lambda n, d=None: {"AWS_STORAGE_BUCKET_NAME": "other"}.get(n, d), ["edx-storage"])
        self.assertEqual(cfgs["edx-storage"].bucket, "mine")
        self.assertNotIn("SK", json.dumps(cfgs["edx-storage"].public()))
        self.assertNotIn("AK", json.dumps(cfgs["edx-storage"].public()))

    def test_dest_must_not_be_a_source(self):
        cfgs, _ = s3_sources.resolve_sources({"discovery": {"bucket": "disc"}}, lambda n, d=None: d, ["discovery"])
        with self.assertRaises(ValueError):
            s3_sources.validate_dest("disc", cfgs)
        s3_sources.validate_dest("handoff", cfgs)


class PackageWiringTests(unittest.TestCase):
    def test_phase3_expected_and_skips(self):
        self.assertEqual(len(tables.S3_KEYS), 7)
        self.assertEqual(tables.phase3_expected(), tables.FORUM_KEYS + tables.S3_KEYS)
        self.assertEqual(tables.phase3_expected(skip_forum=True), tables.S3_KEYS)
        self.assertEqual(tables.phase3_expected(skip_s3=True), tables.FORUM_KEYS)
        self.assertEqual(tables.phase3_expected(True, True), [])
        self.assertEqual(set(tables.S3_KEYS), {f"s3__{b}" for b in s3_sources.LOGICAL_BUCKETS})
        self.assertEqual(set(tables.S3_LOGICAL_BUCKETS), set(R.RESOLVERS))

    def test_status_incomplete_until_phase3_done_and_skip_drops_expectation(self):
        out = Path(tempfile.mkdtemp())
        mf = manifest_mod.Manifest(out / "M.json", "t", "s", ["auth_user"] + tables.phase3_expected())
        mf.update_table("auth_user", status="complete")
        self.assertEqual(mf.finalize(), "incomplete")
        mf.expected_tables -= set(tables.FORUM_KEYS) | set(tables.S3_KEYS)   # what export_tenant_package does for --skip-*
        self.assertEqual(mf.finalize(), "complete")

    def test_verify_uses_entry_file_not_sql(self):
        out = Path(tempfile.mkdtemp())
        (out / "forum").mkdir()
        (out / "forum/contents.jsonl").write_text('{"a": 1}\n')
        (out / "forum__users.sql").write_text("decoy")  # a `<key>.sql` must NOT be what gets verified
        sha = hashlib.sha256(b'{"a": 1}\n').hexdigest()
        data = {"tables": {
            "forum__contents": {"status": "complete", "file": "forum/contents.jsonl", "sha256": sha},
            "forum__users": {"status": "complete", "file": "forum/users.jsonl", "sha256": sha},      # missing file
            "s3__grades": {"status": "complete", "file": "forum/contents.jsonl", "sha256": "bad"},   # tampered
            "auth_user": {"status": "complete", "sha256": hashlib.sha256(b"decoy").hexdigest()},     # legacy .sql default
            "olx": {"status": "complete"},
            "s3__discovery": {"status": "error"},
        }}
        (out / "auth_user.sql").write_text("decoy")
        problems = manifest_mod.verify_entry_files(data, out, skip_keys=("olx",))
        self.assertEqual(sorted(problems), ["forum__users", "s3__grades"])
        self.assertEqual(data["tables"]["forum__contents"]["status"], "complete")
        self.assertEqual(data["tables"]["s3__grades"]["error"], "sha256 mismatch at package time")
        self.assertEqual(data["tables"]["forum__users"]["error"], "file missing at package time")


if __name__ == "__main__":
    unittest.main()
