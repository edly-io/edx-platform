"""Per-DB service registry for the MIT off-boarding export, Phase 2
(EDLYPRODUCT-8584): credentials / discovery / ecommerce, alongside edxapp.

Ported from the standalone reference implementation
(`mit-tenant-export/export_mit/services.py`). Phase 1 files/state/manifest
keys stay exactly as they were (bare table name, edxapp). Everything else is
one flat out dir and one MANIFEST.json, named by a "stem":
  * edxapp  -> bare table name  (`auth_user`)
  * others  -> `<db>__<table>`  (`credentials__core_user`)
because core_user / django_site / core_siteconfiguration /
social_auth_usersocialauth exist in several DBs. Each manifest entry also
carries `db`.

DB CONNECTION DECISION: the LMS only has Django aliases for its own DBs
(`default`, `read_replica`, `student_module_history`); credentials,
discovery and ecommerce are separate databases. `service_connection()`
registers an extra alias `export_tenant_<db>` at runtime (no change to
lms/envs defaults), built from the LMS `default` connection's settings
(same host/user/password/port, NAME = the db's name) overridden per-db by
the optional Django setting

    EXPORT_TENANT_DATABASES = {
        'ecommerce': {'HOST': '...', 'USER': '...', 'PASSWORD': '...', 'NAME': '...'},
    }

(any DATABASES-style keys; set it in `lms/envs/private.py`, or via the LMS
env json since ENV_TOKENS are applied as settings). The DB user therefore
needs SELECT on those schemas (and mysqldump access) -- use a dedicated
read-only account via the setting in production.
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List

# manifest statuses for tables deliberately not dumped
EXCLUDED_GLOBAL = "excluded_global"

SERVICE_DBS = ("credentials", "discovery", "ecommerce")
ALL_DBS = ("edxapp",) + SERVICE_DBS


def stem(db: str, table: str) -> str:
    return table if db == "edxapp" else f"{db}__{table}"


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    tables: List[str]
    where: Callable[[str, dict], str]        # (table, ctx) -> WHERE text
    resolve: Callable                        # (cursor, slug, scope) -> ctx dict
    secret_columns: Dict[str, Dict[str, str]] = field(default_factory=dict)  # table -> {col: sentinel}
    excluded: Dict[str, tuple] = field(default_factory=dict)                  # table -> (status, reason)

    @property
    def expected_stems(self):
        return [stem(self.name, t) for t in self.tables]


def one_id(cursor, sql: str, params, what: str) -> int:
    """Resolve exactly one id; hard error on 0 or >1 matches (never guess
    which of several rows is the tenant's)."""
    from openedx.features.edly.tenant_export.scope import ScopeError

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise ScopeError(f"{what}: expected exactly 1 match, found {len(rows)}")
    return int(rows[0][0])


def get_spec(name: str) -> ServiceSpec:
    from openedx.features.edly.tenant_export import svc_credentials, svc_discovery, svc_ecommerce

    return {"credentials": svc_credentials.SPEC, "discovery": svc_discovery.SPEC,
            "ecommerce": svc_ecommerce.SPEC}[name]


def service_db_settings(default_settings: dict, overrides: dict, name: str) -> dict:
    """Pure helper: LMS `default` settings_dict -> settings_dict for service db `name`."""
    cfg = dict(default_settings)
    cfg["NAME"] = name
    cfg.update(overrides.get(name, {}))
    return cfg


def service_connection(name: str):
    """Django connection to a service DB, registered lazily as alias
    `export_tenant_<name>` (see module docstring)."""
    from django.conf import settings
    from django.db import connection, connections

    alias = f"export_tenant_{name}"
    if alias not in connections.databases:
        connections.databases[alias] = service_db_settings(
            connection.settings_dict, getattr(settings, "EXPORT_TENANT_DATABASES", {}), name,
        )
    return connections[alias]


def resolve_services(names, slug: str, scope: dict) -> dict:
    """Resolve each named service's scope ctx -> the `services` block of scope.json."""
    out = {}
    for name in names:
        if name not in SERVICE_DBS:
            raise ValueError(f"unknown service {name!r}; choose from {SERVICE_DBS}")
        with service_connection(name).cursor() as cursor:
            out[name] = get_spec(name).resolve(cursor, slug, scope)
    return out
