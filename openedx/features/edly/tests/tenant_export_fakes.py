"""Offline fakes for the Phase 3 tests (EDLYPRODUCT-8584): FakeMongo (just
enough of the pymongo query surface the export uses) and FakeS3 (just enough
of the boto3 client surface the copy engine uses). Not a test module."""
import hashlib
import io
import re


class FakeClientError(Exception):
    """Duck-typed botocore ClientError (the engine only reads `.response`)."""

    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


# ---- Mongo -------------------------------------------------------------------

def _match(doc, flt):
    for field, cond in flt.items():
        value = doc.get(field)
        if isinstance(cond, dict):
            if "$in" in cond:
                if isinstance(value, list):
                    if not set(map(str, value)) & set(map(str, cond["$in"])):
                        return False
                elif value not in cond["$in"]:
                    return False
            elif "$regex" in cond:
                flags = re.I if "i" in cond.get("$options", "") else 0
                if not isinstance(value, str) or not re.search(cond["$regex"], value, flags):
                    return False
            else:
                raise NotImplementedError(cond)
        elif value != cond:
            return False
    return True


class FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)
        self.queries = []

    def find(self, flt=None, projection=None):  # pylint: disable=unused-argument
        flt = flt or {}
        self.queries.append(flt)
        return iter([d for d in self.docs if _match(d, flt)])

    def find_one(self, flt):
        return next(self.find(flt), None)

    def count_documents(self, flt):
        return len(list(self.find(flt)))


class FakeMongo:
    def __init__(self, **collections):
        self.collections = {name: FakeCollection(docs) for name, docs in collections.items()}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection([]))


# ---- S3 ----------------------------------------------------------------------

class FakeS3:
    """One client over a dict of buckets {bucket: {key: bytes}}. `copy()` mimics
    boto3's managed copy (incl. `SourceClient`); `fail_keys` raise on copy."""

    def __init__(self, buckets=None, fail_keys=()):
        self.buckets = buckets if buckets is not None else {}
        self.fail_keys = set(fail_keys)
        self.calls = []

    @staticmethod
    def etag(data):
        return '"%s"' % hashlib.md5(data).hexdigest()

    def _get(self, bucket, key):
        try:
            return self.buckets[bucket][key]
        except KeyError:
            raise FakeClientError("404")

    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise FakeClientError("404")
        return {}

    def head_object(self, Bucket, Key):
        data = self._get(Bucket, Key)
        return {"ContentLength": len(data), "ETag": self.etag(data)}

    def list_objects_v2(self, Bucket, Prefix="", MaxKeys=1000, **kw):  # pylint: disable=unused-argument
        keys = sorted(k for k in self.buckets.get(Bucket, {}) if k.startswith(Prefix))[:MaxKeys]
        return {"Contents": [{"Key": k} for k in keys], "KeyCount": len(keys)}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        client = self

        class _P:
            def paginate(self, Bucket, Prefix=""):
                keys = sorted(k for k in client.buckets.get(Bucket, {}) if k.startswith(Prefix))
                for i in range(0, max(len(keys), 1), 2):  # tiny pages: exercises paging
                    yield {"Contents": [{"Key": k, "Size": len(client.buckets[Bucket][k])} for k in keys[i:i + 2]]}
        return _P()

    def copy(self, CopySource, Bucket, Key, ExtraArgs=None, SourceClient=None, **kw):  # pylint: disable=unused-argument
        self.calls.append(("copy", Bucket, Key, ExtraArgs, SourceClient is not None))
        if Key in self.fail_keys:
            raise FakeClientError("AccessDenied")
        src = SourceClient or self
        self.buckets.setdefault(Bucket, {})[Key] = src._get(CopySource["Bucket"], CopySource["Key"])

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self._get(Bucket, Key))}

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs=None):
        self.calls.append(("upload", Bucket, Key, ExtraArgs, False))
        if Key in self.fail_keys:
            raise FakeClientError("AccessDenied")
        self.buckets.setdefault(Bucket, {})[Key] = Fileobj.read()
