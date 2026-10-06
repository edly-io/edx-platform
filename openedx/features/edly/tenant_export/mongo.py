"""Mongo connection params + lazy pymongo for the Phase 3 forum / video-discovery
reads (EDLYPRODUCT-8584). Stdlib only at import time: pymongo is imported
inside `connect()`, so this module (and the offline tests) never need it.

FORUM CONNECTION DECISION: the forum (`cs_comments_service`) is a separate
Mongo database -- possibly a separate server -- from the LMS modulestore, so
it gets its own config, first match wins:

  1. Django setting `EXPORT_TENANT_FORUM_MONGO = {'URI': 'mongodb://ro:...@host/', 'DB': 'cs_comments_service'}`
     (or the discrete keys HOST/PORT/USER/PASSWORD/DB/AUTH_SOURCE)
  2. env `EXPORT_TENANT_FORUM_MONGO_URI` (+ optional `EXPORT_TENANT_FORUM_MONGO_DB`)

DB name defaults to `cs_comments_service` (UNVERIFIED on Koa -- the command
prints host/db and requires a non-empty `contents` collection before reading).
Use a read-only Mongo user. Credentials are never logged: `mask_uri()`.

The modulestore (Mongo video-id discovery) is read from the LMS's own
`DOC_STORE_CONFIG`, nothing new to configure.
"""
import re
from urllib.parse import quote_plus

DEFAULT_FORUM_DB = "cs_comments_service"
_CREDS_RE = re.compile(r"(://)[^/@]*@")


def mask_uri(uri: str) -> str:
    """`mongodb://user:pw@host/` -> `mongodb://***@host/` (never log credentials)."""
    return _CREDS_RE.sub(r"\1***@", uri)


def _uri_from_parts(cfg: dict) -> str:
    host, port = cfg.get("HOST") or "localhost", cfg.get("PORT")
    creds = ""
    if cfg.get("USER"):
        creds = f"{quote_plus(cfg['USER'])}:{quote_plus(cfg.get('PASSWORD') or '')}@"
    auth = f"?authSource={cfg['AUTH_SOURCE']}" if cfg.get("AUTH_SOURCE") else ""
    hostport = f"{host}:{port}" if port and ":" not in str(host) else str(host)
    return f"mongodb://{creds}{hostport}/{auth}"


def forum_params(setting, environ) -> dict:
    """`setting`: value of `EXPORT_TENANT_FORUM_MONGO` (or None); `environ`: os.environ-like.
    Returns {'uri', 'db'} or raises ValueError if nothing is configured."""
    if setting:
        uri = setting.get("URI") or _uri_from_parts(setting)
        return {"uri": uri, "db": setting.get("DB") or DEFAULT_FORUM_DB}
    uri = environ.get("EXPORT_TENANT_FORUM_MONGO_URI")
    if not uri:
        raise ValueError(
            "forum Mongo not configured: set the EXPORT_TENANT_FORUM_MONGO setting or "
            "the EXPORT_TENANT_FORUM_MONGO_URI env var"
        )
    return {"uri": uri, "db": environ.get("EXPORT_TENANT_FORUM_MONGO_DB") or DEFAULT_FORUM_DB}


def modulestore_params(doc_store_config: dict) -> dict:
    """Params from the LMS's `DOC_STORE_CONFIG` (host may be a list)."""
    cfg = dict(doc_store_config)
    host = cfg.get("host")
    if isinstance(host, (list, tuple)):
        host = ",".join(str(h) for h in host)
    uri = _uri_from_parts({
        "HOST": host, "PORT": cfg.get("port"), "USER": cfg.get("user"),
        "PASSWORD": cfg.get("password"), "AUTH_SOURCE": cfg.get("authsource"),
    })
    return {"uri": uri, "db": cfg["db"]}


def connect(params: dict, timeout_ms: int = 10000):
    """-> (client, db). Lazy pymongo import (3.10.1 API: MongoClient + serverSelectionTimeoutMS)."""
    from pymongo import MongoClient  # pylint: disable=import-outside-toplevel

    client = MongoClient(params["uri"], serverSelectionTimeoutMS=timeout_ms)
    return client, client[params["db"]]
