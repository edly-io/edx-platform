-- Reference row counts, independent of export_mit's WHERE builders (EDM-equivalent
-- logic, written from EDM's commands). Run read-only on the demo site, compare
-- with `export_tenant_services MIT --db X --dry-run`. Ecommerce names are UNVERIFIED (DESCRIBE first).
-- EXPECTED DIFFS vs EDM: discovery program_excluded_course_runs (EDM bug: program
-- ids vs course ids), *_authoring_organizations (EDM scopes by course only),
-- video; ecommerce catalogue_product (+parent products); maybe social_auth.

-- ===== credentials ===== (USE credentials;)
SET @site_id = 0;  -- core_siteconfiguration.site_id for the tenant
SELECT 'catalog_course' t, COUNT(*) FROM catalog_course WHERE site_id=@site_id;
SELECT 'credentials_coursecertificate', COUNT(*) FROM credentials_coursecertificate WHERE site_id=@site_id;
SELECT 'credentials_programcertificate', COUNT(*) FROM credentials_programcertificate WHERE site_id=@site_id;
SELECT 'catalog_courserun', COUNT(*) FROM catalog_courserun WHERE course_id IN (SELECT id FROM catalog_course WHERE site_id=@site_id);
SELECT 'credentials_usercredential', COUNT(*) FROM credentials_usercredential uc
 JOIN django_content_type ct ON ct.id=uc.credential_content_type_id
 WHERE (ct.model='coursecertificate' AND uc.credential_id IN (SELECT id FROM credentials_coursecertificate WHERE site_id=@site_id))
    OR (ct.model='programcertificate' AND uc.credential_id IN (SELECT id FROM credentials_programcertificate WHERE site_id=@site_id));
-- invariant: distinct usernames should all be tenant members in edxapp
SELECT COUNT(DISTINCT username) FROM credentials_usercredential uc
 JOIN django_content_type ct ON ct.id=uc.credential_content_type_id
 WHERE (ct.model='coursecertificate' AND uc.credential_id IN (SELECT id FROM credentials_coursecertificate WHERE site_id=@site_id))
    OR (ct.model='programcertificate' AND uc.credential_id IN (SELECT id FROM credentials_programcertificate WHERE site_id=@site_id));

-- ===== discovery ===== (USE discovery;)
SET @partner_id = 0;  -- core_partner.id where short_code = slug
SELECT 'organization', COUNT(*) FROM course_metadata_organization WHERE partner_id=@partner_id;
SELECT 'course', COUNT(*) FROM course_metadata_course WHERE partner_id=@partner_id;
SELECT 'program', COUNT(*) FROM course_metadata_program WHERE partner_id=@partner_id;
SELECT 'courserun', COUNT(*) FROM course_metadata_courserun WHERE course_id IN (SELECT id FROM course_metadata_course WHERE partner_id=@partner_id);
SELECT 'seat', COUNT(*) FROM course_metadata_seat WHERE course_run_id IN (SELECT id FROM course_metadata_courserun WHERE course_id IN (SELECT id FROM course_metadata_course WHERE partner_id=@partner_id));
SELECT 'program_excluded_course_runs', COUNT(*) FROM course_metadata_program_excluded_course_runs WHERE program_id IN (SELECT id FROM course_metadata_program WHERE partner_id=@partner_id);

-- ===== ecommerce ===== (USE ecommerce;) UNVERIFIED names
SET @partner_id = 0;  -- partner_partner.id where short_code = slug
SELECT 'order_order', COUNT(*) FROM order_order WHERE partner_id=@partner_id;
SELECT 'order_line', COUNT(*) FROM order_line WHERE order_id IN (SELECT id FROM order_order WHERE partner_id=@partner_id);
SELECT 'partner_stockrecord', COUNT(*) FROM partner_stockrecord WHERE partner_id=@partner_id;
SELECT 'offer_conditionaloffer', COUNT(*) FROM offer_conditionaloffer WHERE partner_id=@partner_id;
SELECT 'voucher_voucher', COUNT(*) FROM voucher_voucher WHERE id IN (SELECT voucher_id FROM voucher_voucher_offers WHERE conditionaloffer_id IN (SELECT id FROM offer_conditionaloffer WHERE partner_id=@partner_id));
SELECT 'basket_basket(with order)', COUNT(DISTINCT basket_id) FROM order_order WHERE partner_id=@partner_id;
