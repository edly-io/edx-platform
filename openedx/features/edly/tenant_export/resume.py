"""Per-table idempotency markers for the resumable `export_tenant_mysql` /
`export_tenant_csmh` commands -- tracked under `<out_dir>/.state/<slug>/
<table>.done`, i.e. always relative to the run's own `--out-dir`, never to
the process's current working directory (which, run via `manage.py`, is the
edx-platform checkout itself) -- so a later run targeting a *different*
`--out-dir` never picks up another run's completion state, and a `.state`
directory never ends up loose in the repo checkout.
"""
from pathlib import Path


def state_dir(out_dir, slug: str) -> Path:
    return Path(out_dir) / ".state" / slug


def is_done(out_dir, slug: str, table: str) -> bool:
    return (state_dir(out_dir, slug) / f"{table}.done").exists()


def mark_done(out_dir, slug: str, table: str) -> None:
    directory = state_dir(out_dir, slug)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{table}.done").touch()
