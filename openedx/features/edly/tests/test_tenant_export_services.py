"""Offline Phase 2 tests (EDLYPRODUCT-8584): per-service WHERE structure/golden
strings, sqlite two-tenant leak tests, resolve() 0/1/many, audit helpers,
redacted_table_dump PK lookup, service DB settings, manifest expected-table
persistence. No MySQL driver / Django settings needed.
"""
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from openedx.features.edly.tenant_export import audit, secrets as secrets_mod, services, tables
from openedx.features.edly.tenant_export import svc_credentials, svc_discovery, svc_ecommerce
from openedx.features.edly.tenant_export.manifest import Manifest
from openedx.features.edly.tenant_export.scope import ScopeError

SPECS = {"credentials": svc_credentials.SPEC, "discovery": svc_discovery.SPEC, "ecommerce": svc_ecommerce.SPEC}
CTX_KEY = {"credentials": "site_id", "discovery": "partner_id", "ecommerce": "partner_id"}

# ---- sqlite fixtures: table -> columns; SEED rows follow column order ------
S = {
 "credentials": {
  "django_site": "id", "core_siteconfiguration": "id,site_id,segment_key",
  "catalog_organization": "id,site_id", "catalog_course": "id,site_id", "catalog_program": "id,site_id",
  "credentials_coursecertificate": "id,site_id", "credentials_programcertificate": "id,site_id",
  "credentials_signatory": "id", "catalog_courserun": "id,course_id",
  "django_content_type": "id,app_label,model",
  "credentials_usercredential": "id,credential_content_type_id,credential_id,username",
  "records_usergrade": "id,course_run_id", "catalog_course_owners": "id,course_id",
  "catalog_program_authoring_organizations": "id,program_id", "catalog_program_course_runs": "id,program_id",
  "credentials_coursecertificate_signatories": "id,coursecertificate_id,signatory_id",
  "credentials_programcertificate_signatories": "id,programcertificate_id,signatory_id",
  "credentials_usercredentialattribute": "id,user_credential_id",
  "core_user": "id,username", "core_user_groups": "id,user_id", "social_auth_usersocialauth": "id,uid"},
 "discovery": {
  "core_partner": "id,site_id", "core_historicalpartner": "history_id,id", "django_site": "id",
  "course_metadata_organization": "id,partner_id", "course_metadata_historicalorganization": "id,partner_id",
  "course_metadata_person": "id,partner_id", "course_metadata_personsocialnetwork": "id,person_id",
  "course_metadata_subject": "id,partner_id", "course_metadata_subjecttranslation": "id,master_id",
  "course_metadata_video": "id", "course_metadata_course": "id,partner_id,video_id",
  "course_metadata_course_authoring_organizations": "id,course_id,organization_id",
  "course_metadata_course_subjects": "id,course_id,subject_id", "course_metadata_historicalcourse": "id,partner_id",
  "course_metadata_program": "id,partner_id,video_id",
  "course_metadata_program_authoring_organizations": "id,program_id,organization_id",
  "course_metadata_program_courses": "id,program_id,course_id",
  "course_metadata_program_credit_backing_organizations": "id,program_id,organization_id",
  "course_metadata_historicalprogram": "id,partner_id",
  "course_metadata_courserun": "id,course_id,video_id", "course_metadata_courserun_staff": "id,courserun_id,person_id",
  "course_metadata_seat": "id,course_run_id",
  "course_metadata_program_excluded_course_runs": "id,program_id,courserun_id"},
 "ecommerce": {
  "partner_partner": "id", "core_siteconfiguration": "id,partner_id", "partner_stockrecord": "id,partner_id,product_id",
  "catalogue_productclass": "id,name", "catalogue_productattribute": "id", "courses_course": "id",
  "catalogue_product": "id,parent_id,product_class_id,course_id",
  "catalogue_productattributevalue": "id,product_id,attribute_id",
  "catalogue_catalog": "id", "catalogue_catalog_stock_records": "id,catalog_id,stockrecord_id",
  "offer_conditionaloffer": "id,partner_id,benefit_id,condition_id", "offer_benefit": "id,range_id",
  "offer_condition": "id,range_id", "offer_range": "id,catalog_id", "offer_rangeproduct": "id,range_id,product_id",
  "voucher_voucher": "id", "voucher_voucher_offers": "id,voucher_id,conditionaloffer_id",
  "order_order": "id,partner_id,user_id,billing_address_id", "order_line": "id,order_id",
  "order_billingaddress": "id", "ecommerce_user": "id"},
}
# Tenant A = 1, tenant B = 2 (credentials: site 1/2; others: partner 1/2).
SEED = {
 "credentials": {
  "django_site": [(1,), (2,)], "core_siteconfiguration": [(1, 1, "k"), (2, 2, "k")],
  "catalog_organization": [(1, 1), (2, 2)], "catalog_course": [(10, 1), (20, 2)],
  "catalog_program": [(11, 1), (21, 2)], "credentials_coursecertificate": [(100, 1), (200, 2)],
  "credentials_programcertificate": [(101, 1), (201, 2)], "credentials_signatory": [(1,), (2,), (3,)],
  "catalog_courserun": [(1000, 10), (2000, 20)],
  "django_content_type": [(1, "credentials", "coursecertificate"), (2, "credentials", "programcertificate"),
                          (3, "other", "coursecertificate")],
  # 4: program-ct with a course-cert id; 5: wrong app_label -> both must stay out
  "credentials_usercredential": [(1, 1, 100, "alice"), (2, 2, 101, "bob"), (3, 1, 200, "carol"),
                                 (4, 2, 100, "dave"), (5, 3, 100, "erin")],
  "records_usergrade": [(1, 1000), (2, 2000)], "catalog_course_owners": [(1, 10), (2, 20)],
  "catalog_program_authoring_organizations": [(1, 11), (2, 21)], "catalog_program_course_runs": [(1, 11), (2, 21)],
  "credentials_coursecertificate_signatories": [(1, 100, 1), (2, 200, 2)],
  "credentials_programcertificate_signatories": [(1, 101, 3), (2, 201, 2)],
  "credentials_usercredentialattribute": [(1, 1), (2, 3)],
  "core_user": [(1, "alice"), (2, "bob"), (3, "carol"), (4, "dave"), (5, "erin")],
  "core_user_groups": [(1, 1), (2, 3), (3, 2)], "social_auth_usersocialauth": [(1, "alice"), (2, "carol")]},
 "discovery": {
  "core_partner": [(1, 1), (2, 2)], "core_historicalpartner": [(1, 1), (2, 2), (3, 1)], "django_site": [(1,), (2,)],
  "course_metadata_organization": [(1, 1), (2, 2)], "course_metadata_historicalorganization": [(1, 1), (2, 2)],
  "course_metadata_person": [(1, 1), (2, 2)], "course_metadata_personsocialnetwork": [(1, 1), (2, 2)],
  "course_metadata_subject": [(1, 1), (2, 2)], "course_metadata_subjecttranslation": [(1, 1), (2, 2)],
  "course_metadata_video": [(100,), (101,), (102,), (200,), (201,), (202,)],
  "course_metadata_course": [(10, 1, 100), (20, 2, 200)],
  # row 2 / 3: A's course linked to B's org / subject -> must stay out
  "course_metadata_course_authoring_organizations": [(1, 10, 1), (2, 10, 2), (3, 20, 2)],
  "course_metadata_course_subjects": [(1, 10, 1), (2, 10, 2), (3, 20, 2)],
  "course_metadata_historicalcourse": [(1, 1), (2, 2)], "course_metadata_program": [(11, 1, 102), (21, 2, 202)],
  "course_metadata_program_authoring_organizations": [(1, 11, 1), (2, 11, 2), (3, 21, 2)],
  "course_metadata_program_courses": [(1, 11, 10), (2, 11, 20), (3, 21, 20)],
  "course_metadata_program_credit_backing_organizations": [(1, 11, 1), (2, 21, 2)],
  "course_metadata_historicalprogram": [(1, 1), (2, 2)],
  "course_metadata_courserun": [(1000, 10, 101), (2000, 20, 201)],
  "course_metadata_courserun_staff": [(1, 1000, 1), (2, 1000, 2), (3, 2000, 2)],
  "course_metadata_seat": [(1, 1000), (2, 2000)],
  # 4: program_id 10 is a COURSE id of A (EDM bug shape) -> must stay out
  "course_metadata_program_excluded_course_runs": [(1, 11, 1000), (2, 21, 2000), (3, 11, 2000), (4, 10, 1000)]},
 "ecommerce": {
  "partner_partner": [(1,), (2,)], "core_siteconfiguration": [(1, 1), (2, 2)],
  "partner_stockrecord": [(1, 1, 10), (2, 2, 20), (3, 1, 40)],
  # class 1 = ordinary, 2 = Coupon (A's coupon product 40 must be excluded), 3 = B's
  "catalogue_productclass": [(1, "Seat"), (2, "Coupon"), (3, "Seat")],
  "catalogue_productattribute": [(1,), (2,), (3,)], "courses_course": [(1,), (2,), (3,)],
  "catalogue_product": [(5, None, 1, None), (6, None, 3, None), (10, 5, 1, 1), (20, 6, 3, 2), (30, None, 1, 3),
                        (40, None, 2, None)],
  "catalogue_productattributevalue": [(1, 10, 1), (2, 20, 3), (3, 5, 2), (4, 40, 3)],
  "catalogue_catalog": [(1,), (2,)], "catalogue_catalog_stock_records": [(1, 1, 1), (2, 1, 2), (3, 2, 2)],
  "offer_conditionaloffer": [(1, 1, 1, 1), (2, 2, 2, 2)], "offer_benefit": [(1, 1), (2, 2), (3, 1)],
  "offer_condition": [(1, 1), (2, 2)], "offer_range": [(1, 1), (2, 2), (3, None)],
  # row 2: B's product inside a shared range -> must stay out
  "offer_rangeproduct": [(1, 1, 10), (2, 1, 20), (3, 2, 20)],
  "voucher_voucher": [(1,), (2,)], "voucher_voucher_offers": [(1, 1, 1), (2, 2, 2)],
  # order 3: A's guest order (no user) and order 4: unknown user -> dropped (INNER JOIN ecommerce_user)
  "order_order": [(1, 1, 100, 1), (2, 2, 200, 2), (3, 1, None, 3), (4, 1, 999, 4)],
  "order_line": [(1, 1), (2, 2), (3, 3), (4, 4)],
  "order_billingaddress": [(1,), (2,), (3,), (4,)],
  "ecommerce_user": [(100,), (200,), (300,)]},
}
# Expected ids for tenant A (pk column `id` unless listed in PK).
PK = {"core_historicalpartner": "history_id"}
EXPECT_A = {
 "credentials": {
  "django_site": {1}, "core_siteconfiguration": {1}, "catalog_organization": {1}, "catalog_course": {10},
  "catalog_program": {11}, "credentials_coursecertificate": {100}, "credentials_programcertificate": {101},
  "credentials_signatory": {1, 3}, "catalog_courserun": {1000}, "credentials_usercredential": {1, 2},
  "records_usergrade": {1}, "catalog_course_owners": {1}, "catalog_program_authoring_organizations": {1},
  "catalog_program_course_runs": {1}, "credentials_coursecertificate_signatories": {1},
  "credentials_programcertificate_signatories": {1}, "credentials_usercredentialattribute": {1},
  "core_user": {1, 2}, "core_user_groups": {1, 3}, "social_auth_usersocialauth": {1}},
 "discovery": {
  "core_partner": {1}, "core_historicalpartner": {1, 3}, "django_site": {1},
  "course_metadata_organization": {1}, "course_metadata_historicalorganization": {1},
  "course_metadata_person": {1}, "course_metadata_personsocialnetwork": {1}, "course_metadata_subject": {1},
  "course_metadata_subjecttranslation": {1}, "course_metadata_video": {100, 101, 102},
  "course_metadata_course": {10}, "course_metadata_course_authoring_organizations": {1},
  "course_metadata_course_subjects": {1}, "course_metadata_historicalcourse": {1}, "course_metadata_program": {11},
  "course_metadata_program_authoring_organizations": {1}, "course_metadata_program_courses": {1},
  "course_metadata_program_credit_backing_organizations": {1}, "course_metadata_historicalprogram": {1},
  "course_metadata_courserun": {1000}, "course_metadata_courserun_staff": {1, 2}, "course_metadata_seat": {1},
  "course_metadata_program_excluded_course_runs": {1}},
 "ecommerce": {
  "partner_partner": {1}, "core_siteconfiguration": {1}, "partner_stockrecord": {1, 3},
  # product 40 (Coupon class) excluded; parent 5 included; class/attribute/course follow the products
  "catalogue_productclass": {1}, "catalogue_productattribute": {1, 2}, "courses_course": {1},
  "catalogue_product": {5, 10}, "catalogue_productattributevalue": {1, 3},
  "catalogue_catalog": {1}, "catalogue_catalog_stock_records": {1},
  "offer_conditionaloffer": {1}, "offer_benefit": {1}, "offer_condition": {1}, "offer_range": {1},
  "offer_rangeproduct": {1}, "voucher_voucher": {1}, "voucher_voucher_offers": {1},
  "order_order": {1}, "order_line": {1}, "order_billingaddress": {1}, "ecommerce_user": {100}},
}


def _db(name):
    conn = sqlite3.connect(":memory:")
    for t, cols in S[name].items():
        conn.execute(f"CREATE TABLE {t} ({cols})")
    for t, rows in SEED[name].items():
        n = len(S[name][t].split(","))
        conn.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * n)})", rows)
    return conn


def _ids(conn, spec, table, ctx):
    pk = PK.get(table, "id")
    return {r[0] for r in conn.execute(f"SELECT {pk} FROM {table} WHERE {spec.where(table, ctx)}")}


class WhereStructureTests(unittest.TestCase):
    def test_every_table_has_scoped_where_with_literal(self):
        for name, spec in SPECS.items():
            ctx = {CTX_KEY[name]: 7}
            for t in spec.tables:
                w = spec.where(t, ctx)
                self.assertNotIn(w.strip(), ("", "1=1", "1 = 1"), t)
                self.assertRegex(w, r"\b7\b", f"{name}.{t} WHERE lacks the int scope literal")

    def test_no_excluded_table_is_dumped(self):
        for spec in SPECS.values():
            self.assertFalse(set(spec.tables) & set(spec.excluded))
            self.assertFalse(set(spec.tables) & secrets_mod.DENYLIST)

    def test_secret_columns_reference_dumped_tables(self):
        for spec in SPECS.values():
            self.assertLessEqual(set(spec.secret_columns), set(spec.tables))

    def test_golden_usercredential_chain(self):
        w = svc_credentials.where("credentials_usercredential", {"site_id": 7})
        self.assertIn("app_label = 'credentials' AND model = 'coursecertificate'", w)
        self.assertIn("credential_id IN (SELECT id FROM credentials_coursecertificate WHERE site_id = 7)", w)
        self.assertIn("credential_id IN (SELECT id FROM credentials_programcertificate WHERE site_id = 7)", w)

    def test_golden_courserun_indirection_and_voucher_chain(self):
        self.assertEqual(svc_discovery.where("course_metadata_courserun", {"partner_id": 9}),
                         "course_id IN (SELECT id FROM course_metadata_course WHERE partner_id = 9)")
        w = svc_ecommerce.where("voucher_voucher", {"partner_id": 9})
        self.assertIn("voucher_voucher_offers WHERE conditionaloffer_id IN "
                      "(SELECT id FROM offer_conditionaloffer WHERE partner_id = 9)", w)

    def test_edm_bug_not_copied(self):
        w = svc_discovery.where("course_metadata_program_excluded_course_runs", {"partner_id": 9})
        self.assertIn("program_id IN (SELECT id FROM course_metadata_program WHERE partner_id = 9)", w)
        self.assertNotIn("course_metadata_course WHERE partner_id = 9) AND courserun", w)

    def test_blob_columns_blanked(self):
        sc = svc_ecommerce.SECRET_COLUMNS
        self.assertEqual(set(sc["core_siteconfiguration"]),
                         {"payment_processors", "oauth_settings", "edly_client_theme_branding_settings"})
        self.assertIn("marketing_site_api_password", svc_discovery.SECRET_COLUMNS["core_historicalpartner"])

    def test_discovery_social_auth_excluded_so_audit_is_clean(self):
        spec = SPECS["discovery"]
        self.assertIn("social_auth_usersocialauth", spec.excluded)
        r = audit.coverage_report(list(spec.tables) + ["social_auth_usersocialauth"], spec)
        self.assertEqual(r["unlisted"], [])

    def test_stem_naming(self):
        self.assertEqual(services.stem("edxapp", "auth_user"), "auth_user")
        self.assertEqual(services.stem("credentials", "core_user"), "credentials__core_user")


class TwoTenantLeakTests(unittest.TestCase):
    def _run(self, name):
        spec, conn, k = SPECS[name], _db(name), CTX_KEY[name]
        self.assertEqual(set(EXPECT_A[name]), set(spec.tables), "expectation must cover every table")
        for t in spec.tables:
            a = _ids(conn, spec, t, {k: 1})
            b = _ids(conn, spec, t, {k: 2})
            self.assertEqual(a, EXPECT_A[name][t], f"{name}.{t}")
            self.assertFalse(a & b, f"{name}.{t} leaks across tenants")
            self.assertTrue(b, f"{name}.{t}: tenant B scope empty (fixture/WHERE bug)")

    def test_credentials(self): self._run("credentials")
    def test_discovery(self): self._run("discovery")
    def test_ecommerce(self): self._run("ecommerce")


class EcommerceEdmParityTests(unittest.TestCase):
    # tables migrate_ecommerce_to_wordpress.py reads (FROM/JOIN), read-only reference
    EDM_READS = {
        "partner_partner", "core_siteconfiguration", "partner_stockrecord", "catalogue_product",
        "catalogue_productclass", "catalogue_productattribute", "catalogue_productattributevalue",
        "courses_course", "catalogue_catalog", "catalogue_catalog_stock_records", "voucher_voucher",
        "voucher_voucher_offers", "offer_conditionaloffer", "offer_benefit", "offer_condition", "offer_range",
        "order_order", "order_line", "order_billingaddress", "ecommerce_user",
    }

    def test_table_list_is_edm_reads_plus_documented_extra(self):
        self.assertEqual(set(svc_ecommerce.TABLES) - self.EDM_READS, {"offer_rangeproduct"})
        self.assertEqual(self.EDM_READS - set(svc_ecommerce.TABLES), set())

    def test_unread_tables_excluded_with_reason(self):
        for t in ("basket_basket", "refund_refund", "payment_source", "order_lineprice", "order_shippingaddress",
                  "voucher_voucherapplication", "partner_partner_users", "partner_partneraddress"):
            self.assertEqual(svc_ecommerce.EXCLUDED[t][1], "not read by EDM", t)

    def test_coupon_classes_and_guest_orders_filtered(self):
        w = svc_ecommerce.where("catalogue_product", {"partner_id": 9})
        self.assertIn("name IN ('Coupon', 'Enrollment Code')", w)
        self.assertIn("user_id IN (SELECT id FROM ecommerce_user)", svc_ecommerce.where("order_order", {"partner_id": 9}))

    def test_branding_blob_blanked_whole(self):
        self.assertEqual(svc_ecommerce.SECRET_COLUMNS["core_siteconfiguration"]["edly_client_theme_branding_settings"], "'{}'")


class CredentialsSecretAndScopeTests(unittest.TestCase):
    def test_branding_and_django_settings_blob_blanked(self):
        sc = svc_credentials.SECRET_COLUMNS["core_siteconfiguration"]
        self.assertEqual(sc["edly_client_branding_and_django_settings"], "'{}'")

    def test_social_auth_scoped_by_uid_username(self):
        w = svc_credentials.where("social_auth_usersocialauth", {"site_id": 7})
        self.assertTrue(w.startswith("uid IN (SELECT username FROM credentials_usercredential WHERE"))


class DiscoveryEdmParityTests(unittest.TestCase):
    def test_courserun_staff_where_has_no_person_restriction(self):
        self.assertEqual(svc_discovery.where("course_metadata_courserun_staff", {"partner_id": 9}),
                         "courserun_id IN (SELECT id FROM course_metadata_courserun WHERE course_id IN "
                         "(SELECT id FROM course_metadata_course WHERE partner_id = 9))")

    def test_tier_0_1_lookups_registered_excluded_global(self):
        ex = svc_discovery.EXCLUDED
        for t in ("core_currency", "taggit_tag", "waffle_switch", "course_metadata_mode"):
            self.assertEqual(ex[t][0], services.EXCLUDED_GLOBAL)
        self.assertEqual(sum(1 for v in ex.values() if "tier 0+1" in v[1]), 16)


class ResolveTests(unittest.TestCase):
    class Cur:
        def __init__(self, rows): self.rows = rows
        def execute(self, sql, params=None): self.sql = sql
        def fetchall(self): return self.rows

    def test_zero_one_many(self):
        for mod, key in ((svc_credentials, "site_id"), (svc_discovery, "partner_id"), (svc_ecommerce, "partner_id")):
            self.assertEqual(mod.resolve(self.Cur([(5,)]), "MIT", {}), {key: 5})
            with self.assertRaises(ScopeError):
                mod.resolve(self.Cur([]), "MIT", {})
            with self.assertRaises(ScopeError):
                mod.resolve(self.Cur([(1,), (2,)]), "MIT", {})


class AuditTests(unittest.TestCase):
    def test_coverage(self):
        spec = SPECS["credentials"]
        r = audit.coverage_report(list(spec.tables) + ["credentials_sitebadgeprovider", "django_content_type"], spec)
        self.assertEqual(r["unlisted"], ["credentials_sitebadgeprovider"])
        r = audit.coverage_report(spec.tables[1:], spec)
        self.assertEqual(r["missing_in_source"], [spec.tables[0]])

    def test_secret_scan(self):
        spec = SPECS["credentials"]
        cols = [("core_user", "password"), ("core_siteconfiguration", "segment_key"),
                ("core_siteconfiguration", "api_key"), ("catalog_course", "key"), ("other_table", "token")]
        self.assertEqual(audit.secret_scan(cols, spec), ["catalog_course.key", "core_siteconfiguration.api_key"])


class Phase1GapTests(unittest.TestCase):
    def test_tables_golden(self):
        self.assertEqual(tables.tier3_where("auth_user", 5, ["MITx"]),
                         "id IN (SELECT user_id FROM edly_edlymultisiteaccess WHERE sub_org_id = 5)")
        self.assertEqual(tables.tier3_where("edly_edlymultisiteaccess", 5, ["MITx"]), "sub_org_id = 5")
        self.assertIn("auth_user", tables.TIER_3)
        self.assertNotIn("certificates_certificatetemplateasset", tables.TIER_6)

    class Cur:
        """sqlite-backed cursor faking INFORMATION_SCHEMA and the DB-API `.connection.literal`."""
        def __init__(self, conn, cols):
            self.c, self.cols, self.last = conn, cols, None
            self.connection = self
        def execute(self, sql):
            self.sql = sql
            self.last = None if "INFORMATION_SCHEMA" in sql else self.c.execute(sql)
        def fetchall(self):
            return self.cols if self.last is None else self.last.fetchall()
        def literal(self, v):
            return b"NULL" if v is None else b"'" + str(v).replace("'", "''").encode() + b"'"

    def _dump(self, table, cols_info, cols, rows, secret_cols=None, where="id >= 0", batch=2):
        conn = sqlite3.connect(":memory:")
        conn.execute(f"CREATE TABLE {table} ({','.join(cols)})")
        conn.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(cols))})", rows)
        cur = self.Cur(conn, cols_info)
        # redacted_table_dump calls execute(sql) for the INFORMATION_SCHEMA query with a params tuple
        orig = cur.execute
        cur.execute = lambda sql, params=None: orig(sql)
        with tempfile.NamedTemporaryFile("r", suffix=".sql") as f:
            n = secrets_mod.redacted_table_dump(cur, table, where, f.name, batch_size=batch, secret_cols=secret_cols)
            return n, Path(f.name).read_text()

    def test_pk_is_not_assumed_id(self):
        info = [("history_id", "PRI"), ("id", ""), ("marketing_site_api_password", ""), ("analytics_token", "")]
        rows = [(h, 1, f"HUNTER{h}", f"SECRETVAL{h}") for h in (1, 2, 3, 4, 5)]
        n, out = self._dump("core_historicalpartner", info, [c for c, _ in info], rows,
                            secret_cols=svc_discovery.SECRET_COLUMNS["core_historicalpartner"])
        self.assertEqual(n, 5)
        self.assertEqual(out.count("INSERT INTO"), 5)
        self.assertNotIn("HUNTER", out)
        self.assertNotIn("SECRETVAL", out)

    def test_refuses_unscoped_where(self):
        info = [("id", "PRI"), ("password", "")]
        for w in ("", "1=1"):
            with self.assertRaises(AssertionError):
                self._dump("auth_user", info, ["id", "password"], [(1, "h")], where=w)

    def test_missing_secret_column_fails_loudly(self):
        info = [("id", "PRI"), ("name", "")]
        with self.assertRaises(RuntimeError):
            self._dump("t", info, ["id", "name"], [(1, "a")], secret_cols={"password": "'!'"})

    def test_edxapp_default_secret_cols(self):
        info = [("id", "PRI"), ("username", ""), ("password", "")]
        n, out = self._dump("auth_user", info, ["id", "username", "password"], [(1, "u", "hash")])
        self.assertEqual(n, 1)
        self.assertNotIn("hash", out)


class ServiceConnectionTests(unittest.TestCase):
    DEFAULT = {"ENGINE": "django.db.backends.mysql", "NAME": "edxapp", "HOST": "h", "USER": "u", "PASSWORD": "p"}

    def test_defaults_to_lms_credentials_with_service_name(self):
        cfg = services.service_db_settings(self.DEFAULT, {}, "ecommerce")
        self.assertEqual((cfg["NAME"], cfg["HOST"], cfg["USER"], cfg["PASSWORD"]), ("ecommerce", "h", "u", "p"))
        self.assertEqual(self.DEFAULT["NAME"], "edxapp")  # default settings dict not mutated

    def test_per_service_override(self):
        cfg = services.service_db_settings(self.DEFAULT, {"ecommerce": {"HOST": "x", "NAME": "shop"}}, "ecommerce")
        self.assertEqual((cfg["HOST"], cfg["NAME"], cfg["USER"]), ("x", "shop", "u"))
        other = services.service_db_settings(self.DEFAULT, {"ecommerce": {"HOST": "x"}}, "credentials")
        self.assertEqual((other["HOST"], other["NAME"]), ("h", "credentials"))


class ManifestExpectedPersistenceTests(unittest.TestCase):
    def test_package_expectation_requires_all_service_dbs(self):
        path = f"{tempfile.mkdtemp()}/MANIFEST.json"
        # what export_tenant_package seeds: edxapp + every service db's stems
        mf = Manifest(path, "t", "sha", ["auth_user"] + services.expected_service_stems())
        mf.update_table("auth_user", status="complete")
        for s in svc_credentials.SPEC.expected_stems:
            mf.update_table(s, status="complete")
        self.assertEqual(mf.finalize(), "incomplete")  # discovery + ecommerce never ran
        # explicit opt-out drops only that db's stems
        self.assertFalse(set(svc_discovery.SPEC.expected_stems) & set(services.expected_service_stems({"discovery"})))
        self.assertTrue(set(svc_ecommerce.SPEC.expected_stems) <= set(services.expected_service_stems({"discovery"})))

    def test_other_dbs_expected_tables_survive_reopen(self):
        path = f"{tempfile.mkdtemp()}/MANIFEST.json"
        mf = Manifest(path, "t", "sha", ["auth_user"])
        mf.update_table("auth_user", status="complete")
        self.assertEqual(mf.finalize(), "complete")
        mf2 = Manifest(path, "t", "sha", svc_credentials.SPEC.expected_stems)
        self.assertEqual(mf2.finalize(), "incomplete")  # credentials stems not dumped yet
        # a later reopen with no db list (export_tenant_package) still remembers both
        self.assertIn("credentials__core_user", Manifest(path, "t", "sha", []).expected_tables)
        self.assertIn("auth_user", Manifest(path, "t", "sha", []).expected_tables)


if __name__ == "__main__":
    unittest.main()
