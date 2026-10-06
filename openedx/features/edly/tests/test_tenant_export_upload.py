"""export_tenant_upload (EDLYPRODUCT-8584 Phase 4): gating, allowlist, ordering, resume. Offline fake S3."""
import hashlib
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management.base import CommandError

from openedx.features.edly.management.commands import export_tenant_upload as up
from openedx.features.edly.tenant_export import manifest as manifest_mod


class FakeUploadS3:
    def __init__(self, deny_head=False):
        self.objs, self.order, self.args, self.deleted, self.deny_head = {}, [], {}, [], deny_head

    def head_bucket(self, Bucket):
        pass

    def head_object(self, Bucket, Key):
        if self.deny_head or Key not in self.objs:
            raise KeyError(Key)
        data, meta = self.objs[Key]
        return {"ContentLength": len(data), "Metadata": meta, "ETag": '"%s"' % hashlib.md5(data).hexdigest()}

    def upload_file(self, filename, Bucket, Key, ExtraArgs=None):
        self.order.append(Key)
        self.args[Key] = ExtraArgs or {}
        self.objs[Key] = (Path(filename).read_bytes(), (ExtraArgs or {}).get("Metadata", {}))

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objs.pop(Key, None)


def _bundle(status="complete", packaged=True):
    out = Path(tempfile.mkdtemp())
    (out / "a.sql").write_text("x")
    (out / "olx").mkdir()
    (out / "olx" / "c.tar.gz").write_text("o")
    (out / "olx_export_failures.json").write_text("[]")  # stray, un-manifested
    (out / "scope.json").write_text("{}")  # stray
    (out / ".state" / "mit").mkdir(parents=True)
    (out / ".state" / "mit" / "a.done").write_text("")
    (out / "b.sql.partial").write_text("junk")
    manifest = {"tenant_slug": "mit", "scope_sha256": "s", "status": status, "tables": {
        "a": {"status": "complete", "sha256": hashlib.sha256(b"x").hexdigest()},
        "olx": {"status": "complete", "dir": "olx", "tree_sha256": manifest_mod.tree_sha256(out / "olx")},
        "excluded": {"status": "excluded_global"},
    }}
    if packaged:
        manifest.update(skipped_dbs=[], skipped_phase3=[])
    (out / "MANIFEST.json").write_text(json.dumps(manifest))
    return out


def _run(out, fake, **opts):
    options = dict(slug="mit", out_dir=str(out), dest_bucket="b", dest_prefix=None, workers=1, dry_run=False,
                   allow_errors=False, overwrite=False, sse="AES256", no_acl=False)
    options.update(opts)
    cmd = up.Command(stdout=StringIO(), stderr=StringIO())
    with mock.patch.object(up, "_client", return_value=fake):
        cmd.handle(**options)
    return cmd


class UploadTests(unittest.TestCase):
    def test_allowlist_is_manifest_files_plus_olx_tree(self):
        out = _bundle()
        data = json.loads((out / "MANIFEST.json").read_text())
        self.assertEqual(up.allowed_files(out, data), ["a.sql", "olx/c.tar.gz"])

    def test_stray_files_not_uploaded_and_warned(self):
        out, fake = _bundle(), FakeUploadS3()
        cmd = _run(out, fake)
        self.assertNotIn("mit/scope.json", fake.objs)
        self.assertNotIn("mit/olx_export_failures.json", fake.objs)
        self.assertIn("scope.json", cmd.stderr.getvalue())

    def test_refuses_unpackaged_manifest(self):
        with self.assertRaises(CommandError):
            _run(_bundle(packaged=False), FakeUploadS3())

    def test_refuses_non_complete(self):
        with self.assertRaises(CommandError):
            _run(_bundle("incomplete"), FakeUploadS3())

    def test_refuses_tampered_file_or_olx_tree(self):
        out = _bundle()
        (out / "a.sql").write_text("tampered")
        with self.assertRaises(CommandError):
            _run(out, FakeUploadS3())
        out = _bundle()
        (out / "olx" / "c.tar.gz").write_text("tampered")
        with self.assertRaises(CommandError):
            _run(out, FakeUploadS3())

    def test_manifest_last_encrypted_and_resume_skips(self):
        out, fake = _bundle(), FakeUploadS3()
        _run(out, fake)
        self.assertEqual(fake.order[-1], "mit/MANIFEST.json")
        self.assertEqual(sorted(fake.order[:-1]), ["mit/a.sql", "mit/olx/c.tar.gz"])
        self.assertEqual(fake.args["mit/a.sql"]["ServerSideEncryption"], "AES256")
        self.assertEqual(fake.args["mit/a.sql"]["ACL"], "bucket-owner-full-control")
        fake.order.clear()
        _run(out, fake)
        self.assertEqual(fake.order, [])

    def test_sse_and_acl_can_be_disabled(self):
        fake = FakeUploadS3()
        _run(_bundle(), fake, sse="none", no_acl=True)
        self.assertEqual(set(fake.args["mit/a.sql"]), {"Metadata"})

    def test_different_remote_manifest_refused_unless_overwrite(self):
        out, fake = _bundle(), FakeUploadS3()
        fake.objs["mit/MANIFEST.json"] = (b"old", {"sha256": "other"})
        with self.assertRaises(CommandError):
            _run(out, fake)
        self.assertEqual(fake.order, [])
        _run(out, fake, overwrite=True)
        self.assertEqual(fake.deleted, ["mit/MANIFEST.json"])
        self.assertEqual(fake.order[-1], "mit/MANIFEST.json")

    def test_write_only_bucket_uploads_unverified(self):
        fake = FakeUploadS3(deny_head=True)
        cmd = _run(_bundle(), fake)
        self.assertIn("unverified 3", cmd.stdout.getvalue())

    def test_post_upload_mismatch_raises(self):
        fake = FakeUploadS3()
        real = fake.upload_file
        fake.upload_file = lambda f, B, K, ExtraArgs=None: (real(f, B, K, ExtraArgs), fake.objs.__setitem__(K, (b"zz", fake.objs[K][1])))[0]
        with self.assertRaises(CommandError):
            _run(_bundle(), fake)

    def test_dry_run_uploads_nothing(self):
        fake = FakeUploadS3()
        _run(_bundle(), fake, dry_run=True)
        self.assertEqual(fake.objs, {})
