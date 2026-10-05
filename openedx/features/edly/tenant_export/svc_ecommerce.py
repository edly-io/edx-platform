"""ecommerce DB (Koa, django-oscar based). Scope P = partner_partner.id where
short_code == slug.

!!! UNVERIFIED SCHEMA !!! There is no EDM tier file for ecommerce. Table and
column names below are derived from the Oscar model layout plus the queries in
EDM's migrate_ecommerce_to_wordpress.py and have NOT been checked against a
real Koa DB. Every name needs `DESCRIBE` verification on the demo site (run
`export_tenant_audit --db ecommerce`, which fails loudly on listed-but-missing tables, and
the redaction code fails loudly on a missing secret column). Native Koa dump
only -- no WooCommerce transform.

Policy [plan]: customer/order PII is kept; only secrets are redacted.
Abandoned baskets are excluded (baskets are exported only when an order of
P references them). Whole blob columns are blanked (not key-by-key redacted):
site payment_processors / oauth_settings / edly_client_theme_branding_settings
and payment_paymentprocessorresponse.response. EDM only masks these in
dry-run logs and copies real values; that would hand live payment creds to
MIT.
"""
from openedx.features.edly.tenant_export.services import EXCLUDED_GLOBAL, ServiceSpec, one_id

# UNVERIFIED -- see module docstring.
TABLES = [
    "partner_partner", "partner_partner_users", "partner_partneraddress", "core_siteconfiguration",
    "partner_stockrecord", "catalogue_product", "catalogue_productattributevalue",
    "offer_conditionaloffer", "offer_benefit", "offer_condition", "offer_range", "offer_rangeproduct",
    "voucher_voucher", "voucher_voucher_offers", "voucher_voucherapplication",
    "order_order", "order_line", "order_lineprice", "order_lineattribute", "order_paymentevent",
    "order_orderdiscount", "order_ordernote", "order_shippingaddress",
    "basket_basket", "basket_line", "basket_lineattribute",
    "payment_source", "payment_transaction", "payment_paymentprocessorresponse",
    "refund_refund", "refund_refundline",
    "ecommerce_user",
]

# Blank the whole column; '{}' is valid for both JSON-typed and text columns.
SECRET_COLUMNS = {
    "ecommerce_user": {"password": "'!'"},
    "payment_paymentprocessorresponse": {"response": "'{}'"},
    "core_siteconfiguration": {
        "payment_processors": "'{}'",
        "oauth_settings": "'{}'",
        "edly_client_theme_branding_settings": "'{}'",
    },
}

EXCLUDED = {
    t: (EXCLUDED_GLOBAL, "shared Oscar lookup (no tenant column); UNVERIFIED name")
    for t in ("catalogue_productclass", "catalogue_productattribute", "catalogue_category",
              "catalogue_productcategory")
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
    sr_products = f"SELECT product_id FROM partner_stockrecord WHERE partner_id = {p}"
    # stockrecord products + their parents (parent rows hold the shared class/title)
    products = (f"SELECT id FROM catalogue_product WHERE id IN ({sr_products}) "
                f"UNION SELECT parent_id FROM catalogue_product WHERE id IN ({sr_products}) AND parent_id IS NOT NULL")
    vouchers = f"SELECT voucher_id FROM voucher_voucher_offers WHERE conditionaloffer_id IN ({offers})"
    orders = f"SELECT id FROM order_order WHERE partner_id = {p}"
    voucher_apps = f"voucher_id IN ({vouchers}) AND order_id IN ({orders})"
    lines = f"SELECT id FROM order_line WHERE order_id IN ({orders})"
    baskets = f"SELECT basket_id FROM order_order WHERE partner_id = {p} AND basket_id IS NOT NULL"
    bklines = f"SELECT id FROM basket_line WHERE basket_id IN ({baskets})"
    sources = f"SELECT id FROM payment_source WHERE order_id IN ({orders})"
    refunds = f"SELECT id FROM refund_refund WHERE order_id IN ({orders})"
    return {
        "partner_partner": f"id = {p}",
        "partner_partner_users": f"partner_id = {p}",
        "partner_partneraddress": f"partner_id = {p}",
        "core_siteconfiguration": f"partner_id = {p}",
        "partner_stockrecord": f"partner_id = {p}",
        "catalogue_product": f"id IN ({products})",
        "catalogue_productattributevalue": f"product_id IN ({products})",
        "offer_conditionaloffer": f"partner_id = {p}",
        "offer_benefit": f"id IN ({benefits})",
        "offer_condition": f"id IN ({conditions})",
        "offer_range": f"id IN ({ranges})",
        # Ranges can be shared across partners: only P's products may appear.
        "offer_rangeproduct": f"range_id IN ({ranges}) AND product_id IN ({products})",
        "voucher_voucher": f"id IN ({vouchers})",
        "voucher_voucher_offers": f"conditionaloffer_id IN ({offers})",
        # UNVERIFIED: voucher_voucherapplication.order_id column name needs DESCRIBE.
        # A shared voucher (offers of several partners) must only show P's orders' applications.
        "voucher_voucherapplication": voucher_apps,
        "order_order": f"partner_id = {p}",
        "order_line": f"order_id IN ({orders})",
        "order_lineprice": f"order_id IN ({orders})",
        "order_lineattribute": f"line_id IN ({lines})",
        "order_paymentevent": f"order_id IN ({orders})",
        "order_orderdiscount": f"order_id IN ({orders})",
        "order_ordernote": f"order_id IN ({orders})",
        "order_shippingaddress":
            f"id IN (SELECT shipping_address_id FROM order_order WHERE partner_id = {p} AND shipping_address_id IS NOT NULL)",
        "basket_basket": f"id IN ({baskets})",
        "basket_line": f"basket_id IN ({baskets})",
        "basket_lineattribute": f"line_id IN ({bklines})",
        "payment_source": f"order_id IN ({orders})",
        "payment_transaction": f"source_id IN ({sources})",
        "payment_paymentprocessorresponse": f"basket_id IN ({baskets})",
        "refund_refund": f"order_id IN ({orders})",
        "refund_refundline": f"refund_id IN ({refunds})",
        "ecommerce_user": (
            f"id IN (SELECT user_id FROM order_order WHERE partner_id = {p} AND user_id IS NOT NULL) "
            f"OR id IN (SELECT user_id FROM voucher_voucherapplication WHERE {voucher_apps} AND user_id IS NOT NULL)"
        ),
    }[table]


SPEC = ServiceSpec("ecommerce", TABLES, where, resolve, SECRET_COLUMNS, EXCLUDED)
