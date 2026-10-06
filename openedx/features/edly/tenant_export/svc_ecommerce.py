"""ecommerce DB (Koa, django-oscar based). Scope P = partner_partner.id where
short_code == slug.

!!! UNVERIFIED SCHEMA !!! There is no EDM tier file for ecommerce. Table and
column names below are derived from the Oscar model layout plus the queries in
EDM's migrate_ecommerce_to_wordpress.py and have NOT been checked against a
real Koa DB. Every name needs `DESCRIBE` verification on the demo site (run
`export_tenant_audit --db ecommerce`, which fails loudly on listed-but-missing tables, and
the redaction code fails loudly on a missing secret column). Native Koa dump
only -- no WooCommerce transform.

TABLE LIST == the tables EDM's migrate_ecommerce_to_wordpress.py reads (plus
offer_rangeproduct, kept so range membership is not lost). Tables EDM never
reads (basket_*, refund_*, payment_*, order_lineprice/lineattribute/
paymentevent/orderdiscount/ordernote/shippingaddress, voucher_voucherapplication,
partner_partner_users, partner_partneraddress) are in EXCLUDED, reason
'not read by EDM'. Scoping follows EDM: Coupon / Enrollment Code product
classes are not exported as products (EDM ~848; coupon products are reached
only through the catalog -> stock-record path, as EDM does), orders are
INNER JOINed to ecommerce_user (guest orders dropped, EDM ~1813) and users are
those with an order in P (EDM ~792).

Policy [plan]: customer/order PII is kept; only secrets are redacted. Whole
blob columns are blanked, not key-by-key redacted: site payment_processors /
oauth_settings / edly_client_theme_branding_settings. The branding blob
(DJANGO_SETTINGS_OVERRIDE.PAYMENT_PROCESSOR_CONFIG inside) is NOT blanked
surgically: that needs JSON_REMOVE on a column whose type (JSON vs longtext)
and key layout are UNVERIFIED on Koa, and invalid JSON would abort the dump.
Revisit after `export_tenant_audit --db ecommerce` on the demo site. EDM only
masks these in dry-run logs and copies real values; that would hand live
payment creds to MIT.
"""
from openedx.features.edly.tenant_export.services import EXCLUDED_GLOBAL, ServiceSpec, one_id

# UNVERIFIED -- see module docstring.
TABLES = [
    "partner_partner", "core_siteconfiguration", "partner_stockrecord",
    "catalogue_productclass", "catalogue_productattribute", "courses_course",
    "catalogue_product", "catalogue_productattributevalue",
    "catalogue_catalog", "catalogue_catalog_stock_records",
    "offer_conditionaloffer", "offer_benefit", "offer_condition", "offer_range", "offer_rangeproduct",
    "voucher_voucher", "voucher_voucher_offers",
    "order_order", "order_line", "order_billingaddress",
    "ecommerce_user",
]

# Blank the whole column; '{}' is valid for both JSON-typed and text columns.
SECRET_COLUMNS = {
    "ecommerce_user": {"password": "'!'"},
    "core_siteconfiguration": {
        "payment_processors": "'{}'",
        "oauth_settings": "'{}'",
        "edly_client_theme_branding_settings": "'{}'",
    },
}

_NOT_READ = "not read by EDM"
EXCLUDED = {
    **{t: (EXCLUDED_GLOBAL, "shared Oscar lookup (no tenant column); UNVERIFIED name")
       for t in ("catalogue_category", "catalogue_productcategory")},
    **{t: (EXCLUDED_GLOBAL, _NOT_READ) for t in (
        "basket_basket", "basket_line", "basket_lineattribute",
        "refund_refund", "refund_refundline",
        "payment_source", "payment_transaction", "payment_paymentprocessorresponse",
        "order_lineprice", "order_lineattribute", "order_paymentevent", "order_orderdiscount",
        "order_ordernote", "order_shippingaddress",
        "voucher_voucherapplication", "partner_partner_users", "partner_partneraddress")},
}


def resolve(cursor, slug, scope):
    pid = one_id(cursor, "SELECT id FROM partner_partner WHERE short_code = %s", (slug,),
                 f"ecommerce partner for {slug!r}")
    return {"partner_id": pid}


def where(table: str, ctx: dict) -> str:
    p = int(ctx["partner_id"])
    offers = f"SELECT id FROM offer_conditionaloffer WHERE partner_id = {p}"
    benefits = f"SELECT benefit_id FROM offer_conditionaloffer WHERE partner_id = {p}"
    conditions = f"SELECT condition_id FROM offer_conditionaloffer WHERE partner_id = {p}"
    ranges = (f"SELECT range_id FROM offer_benefit WHERE id IN ({benefits}) AND range_id IS NOT NULL "
              f"UNION SELECT range_id FROM offer_condition WHERE id IN ({conditions}) AND range_id IS NOT NULL")
    catalogs = f"SELECT catalog_id FROM offer_range WHERE id IN ({ranges}) AND catalog_id IS NOT NULL"
    stockrecords = f"SELECT id FROM partner_stockrecord WHERE partner_id = {p}"
    sr_products = f"SELECT product_id FROM partner_stockrecord WHERE partner_id = {p}"
    # EDM ~848: Coupon / Enrollment Code classes are not migrated as products.
    not_coupon = ("(product_class_id IS NULL OR product_class_id NOT IN "
                  "(SELECT id FROM catalogue_productclass WHERE name IN ('Coupon', 'Enrollment Code')))")
    # stockrecord products + their parents (parent rows hold the shared class/title),
    # plus products reached via P's catalogs (EDM's coupon read path)
    products = (f"SELECT id FROM catalogue_product WHERE id IN ({sr_products}) AND {not_coupon} "
                f"UNION SELECT parent_id FROM catalogue_product WHERE id IN ({sr_products}) "
                f"AND {not_coupon} AND parent_id IS NOT NULL "
                f"UNION SELECT product_id FROM partner_stockrecord WHERE id IN "
                f"(SELECT stockrecord_id FROM catalogue_catalog_stock_records WHERE catalog_id IN ({catalogs}) "
                f"AND stockrecord_id IN ({stockrecords}))")
    vouchers = f"SELECT voucher_id FROM voucher_voucher_offers WHERE conditionaloffer_id IN ({offers})"
    # INNER JOIN ecommerce_user (EDM ~1813): guest orders (NULL / unknown user) are dropped
    orders = f"SELECT id FROM order_order WHERE partner_id = {p} AND user_id IN (SELECT id FROM ecommerce_user)"
    return {
        "partner_partner": f"id = {p}",
        "core_siteconfiguration": f"partner_id = {p}",
        "partner_stockrecord": f"partner_id = {p}",
        "catalogue_productclass": f"id IN (SELECT product_class_id FROM catalogue_product WHERE id IN ({products}))",
        "catalogue_productattribute":
            f"id IN (SELECT attribute_id FROM catalogue_productattributevalue WHERE product_id IN ({products}))",
        "courses_course":
            f"id IN (SELECT course_id FROM catalogue_product WHERE id IN ({products}) AND course_id IS NOT NULL)",
        "catalogue_product": f"id IN ({products})",
        "catalogue_productattributevalue": f"product_id IN ({products})",
        "catalogue_catalog": f"id IN ({catalogs})",
        "catalogue_catalog_stock_records":
            f"catalog_id IN ({catalogs}) AND stockrecord_id IN ({stockrecords})",
        "offer_conditionaloffer": f"partner_id = {p}",
        "offer_benefit": f"id IN ({benefits})",
        "offer_condition": f"id IN ({conditions})",
        "offer_range": f"id IN ({ranges})",
        # Ranges can be shared across partners: only P's products may appear.
        "offer_rangeproduct": f"range_id IN ({ranges}) AND product_id IN ({products})",
        "voucher_voucher": f"id IN ({vouchers})",
        "voucher_voucher_offers": f"conditionaloffer_id IN ({offers})",
        "order_order": f"id IN ({orders})",
        "order_line": f"order_id IN ({orders})",
        "order_billingaddress":
            f"id IN (SELECT billing_address_id FROM order_order WHERE id IN ({orders}) AND billing_address_id IS NOT NULL)",
        "ecommerce_user": f"id IN (SELECT user_id FROM order_order WHERE partner_id = {p})",
    }[table]


SPEC = ServiceSpec("ecommerce", TABLES, where, resolve, SECRET_COLUMNS, EXCLUDED)
