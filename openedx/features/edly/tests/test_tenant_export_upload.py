"""export_tenant_upload (EDLYPRODUCT-8584 Phase 4): gating, ordering, resume. Offline fake S3."""
import hashlib
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management.base import CommandError

from openedx.features.edly.management.commands import export_tenant_upload as up


class FakeUploadS3:
    def __init__(self):
        self.objs, self.order = {}, []

    def head_bucket(self, Bucket):
        pass

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise KeyError(Key)
        data, meta = self.objs[Key]
        return {"ContentLength": len(data), "Metadata": meta}

    def upload_file(self, filename, Bucket, Key, ExtraArgs=None):
        self.order.append(Key)
        self.objs[Key] = (Path(filename).read_bytes(), (ExtraArgs or {}).get("Metadata", {}))


def _bundle(status="complete"):
    out = Path(tempfile.mkdtemp())
    (out / "a.sql").write_text("x")
    (out / "olx").mkdir()
    (out / "olx" / "c.tar.gz").write_text("o")
    (out / ".state" / "mit").mkdir(parents=True)
    (out / ".state" / "mit" / "a.done").write_text("")
    (out / "b.sql.partial").write_text("junk")
    manifest = {"tenant_slug": "mit", "scope_sha256": "s", "status": status,
                "tables": {"a": {"status": "complete", "sha256": hashlib.sha256(b"x").hexdigest()}}}
    (out / "MANIFEST.json").write_text(json.dumps(manifest))
    return out


def _run(out, fake, **opts):
    options = dict(slug="mit", out_dir=str(out), dest_bucket="b", dest_prefix=None, workers=1,
                   dry_run=False, allow_errors=False)
    options.update(opts)
    cmd = up.Command(stdout=StringIO(), stderr=StringIO())
    with mock.patch.object(up, "_client", return_value=fake):
        cmd.handle(**options)
    return cmd


class UploadTests(unittest.TestCase):
    def test_bundle_files_skips_state_partial_manifest(self):
        self.assertEqual(up.bundle_files(_bundle()), ["a.sql", "olx/c.tar.gz"])

    def test_refuses_non_complete(self):
        with self.assertRaises(CommandError):
            _run(_bundle("incomplete"), FakeUploadS3())

    def test_refuses_tampered_file(self):
        out = _bundle()
        (out / "a.sql").write_text("tampered")
        with self.assertRaises(CommandError):
            _run(out, FakeUploadS3())

    def test_manifest_last_and_resume_skips(self):
        out, fake = _bundle(), FakeUploadS3()
        _run(out, fake)
        self.assertEqual(fake.order[-1], "mit/MANIFEST.json")
        self.assertEqual(sorted(fake.order[:-1]), ["mit/a.sql", "mit/olx/c.tar.gz"])
        fake.order.clear()
        _run(out, fake)
        self.assertEqual(fake.order, [])

    def test_dry_run_uploads_nothing(self):
        fake = FakeUploadS3()
        _run(_bundle(), fake, dry_run=True)
        self.assertEqual(fake.objs, {})
