# Tenant export: EDM parity (EDLYPRODUCT-8584)

## 1. Purpose and rule

This package exports one Edly tenant (Koa source) as an archival handoff: SQL dumps, OLX, forum JSONL, S3 copy.
The reference is the `edlysaas-data-migrations` (EDM) Koa to Ulmo tooling.

**Rule: same tables and scoping as EDM, except the explicit differences listed in this document.**
Anything not listed here as a difference is meant to match EDM. If you find one that does not, it is a bug in the export or in this doc.

EDM references (read-only, never imported): `tables/*/tier_*.txt`, `management/commands/migrate_tenant_tier_data.py`,
`migrate_users_and_courses.py`, `migrate_credentials_tenant_data.py`, `migrate_ecommerce_to_wordpress.py`,
`migrate_modulestore.py`, `utils/s3_resolvers.py`, `utils/forum_migrate.py`, `utils/video_discovery.py`.
Line numbers cited in code comments (e.g. `tables.py`, `ora_chain.py`) are the authority for the EDM column.

## 2. Status legend

| Status | Meaning |
|---|---|
| SAME | Same table, same scoping as EDM (modulo the global differences in section 5, e.g. LIKE escaping). |
| DIFFERENT-documented | Deliberately differs from EDM. Reason in section 5 or the note column. |
| EXCLUDED | Not dumped. Recorded in `MANIFEST.json` as `excluded_*` with a reason. |
| ADDITIVE | Exported although EDM does not migrate it. |
| UNVERIFIED | Table/column names or semantics not confirmed against a real Koa DB or package. See section 6. |

Conventions for every dump: scoping uses live SQL subqueries (never Python id lists); an unscoped dump (`""` or `1=1`) is refused;
tenant membership = `edly_edlymultisiteaccess WHERE sub_org_id = <id>`. Two org sets exist:

| Org set | Source | Used by |
|---|---|---|
| `course_org_filter` | `site_configuration_siteconfiguration.site_values` of the sub-org's LMS site (empty = hard error) | all edxapp SQL tiers (`export_tenant_mysql`), CSMH, `scope.course_ids` |
| `course_orgs` | EDM sub-org M2M (`edly_edlysuborganization_edx_organizations` to `organizations_organization.short_name`) plus real-case spellings from course ids; falls back to `course_org_filter` + course-id orgs if the M2M is empty | OLX modulestore lookup, forum, S3, notes |

Manifest `status` is `complete` only when every expected table is `complete`/`skipped_not_in_source` AND every `expected_excluded` table is recorded `excluded_*`.

## 3. Per-source parity

### 3.1 edxapp (`export_tenant_mysql`; 136 tables listed in `tables.ALL_TIER_TABLES`)

Table-name map (source = Koa): `edly_features_app_edlymultisiteaccess` -> `edly_edlymultisiteaccess`;
`certificates_certificateallowlist` -> `certificates_certificatewhitelist`; `edly_features_app_edlyorganization` -> `edly_edlyorganization`;
`edly_features_app_edlytenant` -> `edly_edlysuborganization` (semantic mapping UNVERIFIED).

| Tier | Tables | EDM equivalent and scoping | Export scoping | Status | Note |
|---|---|---|---|---|---|
| 2 (4) | `django_site`, `theming_sitetheme` | EDM tier_2: tenant-structure rows | `id`/`site_id` in the sub-org's lms/studio/preview site ids | DIFFERENT-documented | Koa-name mapping; EDM's Ulmo-side tables do not exist on Koa. |
| 2 | `edly_edlyorganization`, `edly_edlysuborganization` | EDM `edly_features_app_edlyorganization` / `_edlytenant` | the tenant's own org / sub-org row | DIFFERENT-documented | Name mapping; `edlytenant` = `edlysuborganization` UNVERIFIED. |
| 2 | `eox_tenant_tenantconfig`, `eox_tenant_route` | EDM tier_2 | none | EXCLUDED | eox_tenant not installed on Koa. Koa's equivalent `site_configuration_siteconfiguration` holds secrets. |
| 3 (9) | `auth_user` | membership | `id IN membership` | SAME | `password` blanked to `'!'`. |
| 3 | `auth_userprofile`, `auth_registration`, `auth_accountrecovery`, `auth_user_groups`, `auth_user_user_permissions` | membership | `user_id IN membership` | SAME | `auth_registration.activation_key` blanked. `auth_accountrecovery` has only `secondary_email`/`is_active` on Koa (`common/djangoapps/student/models.py` `AccountRecovery`), so no secret column to blank. |
| 3 | `organizations_organization`, `organizations_organizationcourse` | org short names | `short_name IN course_org_filter` (and via org id) | SAME | Exact `IN`, case-insensitive per column collation. |
| 3 | `edly_edlymultisiteaccess` | sub-org | `sub_org_id = <id>` | SAME | Koa name. |
| 4 (2) | `figures_sitedailymetrics`, `figures_sitemonthlymetrics` | `site_id`, plus `organization_id` where present | `site_id` = sub-org LMS site | UNVERIFIED | None of these two tables has `organization_id` (per code comment). Package-defined table. |
| 4 | `third_party_auth_oauth2providerconfig`, `_samlconfiguration`, `_samlproviderconfig` | EDM tier_4 | none | EXCLUDED | OAuth `secret` / SAML `private_key`. Also in `secrets.DENYLIST`. |
| 5 (15) | `bookmarks_bookmark`, `student_courseenrollmentallowed`, `student_loginfailures`, `student_pendingemailchange`, `student_userattribute`, `student_anonymoususerid`, `user_api_usercoursetag`, `user_api_userpreference`, `user_api_userretirementrequest`, `external_user_ids_externalid`, `social_auth_usersocialauth` | membership | `user_id IN membership` | SAME | `pendingemailchange.activation_key` blanked; `social_auth_usersocialauth.extra_data` blanked to `'{}'`. |
| 5 | `student_languageproficiency`, `student_sociallink` | membership via profile | `user_profile_id IN (profiles of members)` | SAME | |
| 5 | `courseware_xmodulestudentinfofield`, `courseware_xmodulestudentprefsfield` | membership | `student_id IN membership` | SAME | |
| 6 (18) | `course_overviews_courseoverview(+tab)`, `course_modes_coursemode`, `course_modes_historicalcoursemode`, `course_groups_courseusergroup`, `course_groups_coursecohortssettings`, `course_action_state_coursererunstate`, `bulk_email_courseemail`, `certificates_certificategenerationcoursesetting`, `completion_blockcompletion`, `courseware_studentmodule`, `courseware_xmoduleuserstatesummaryfield`, `instructor_task_instructortask`, `django_comment_common_coursediscussionsettings`, `bookmarks_xblockcache`, `milestones_coursemilestone`, `milestones_coursecontentmilestone` | course-id org LIKE | `course_id`/`course_key`/`id`/`course_overview_id` LIKE `course-v1:<org>+%` (`block-v1` for `usage_id`) | SAME | `milestones_*` names UNVERIFIED (package-defined; see `UNVERIFIED_IN_CHECKOUT` for `milestones_usermilestone`). |
| 6 | `certificates_certificatetemplate` | course LIKE OR org-wide | course LIKE OR `organization_id IN (org ids of course_org_filter)` | SAME | |
| 6 | `certificates_certificatetemplateasset` | EDM scopes `1=1` | none | EXCLUDED | Cross-tenant leak (section 5). Status `excluded_cross_tenant_leak`. |
| 7 (31) | `student_courseenrollment`, `student_courseaccessrole`, `student_courseenrollmentattribute`, `student_courseenrollment_history`, `student_historicalmanualenrollmentaudit`, `student_manualenrollmentaudit`, `user_tasks_*` (2), `external_user_ids_historicalexternalid`, `verify_student_*` (4), `user_api_userretirementstatus`, `course_groups_*` junctions (5), `certificates_generatedcertificate`, `_certificateinvalidation`, `_certificategenerationhistory`, `grades_*` (3), `bulk_email_courseemail_targets`, `bulk_email_cohorttarget`, `course_goals_coursegoal`, `course_creators_coursecreator`, `kwl_djangoapp_kwlmodel` | membership AND course org | membership subquery AND org LIKE; enrollment-FK tables via live enrollment subquery | SAME | `verify_student_softwaresecurephotoverification.photo_id_key` blanked. `courseaccessrole` also keeps org-wide rows (`org IN`, `course_creator_group`) but only for members. |
| 7 | `journal_djangoapp_journalmodel` | `user IN (...) OR (user IS NULL AND org)` | `user IN membership AND org LIKE` | DIFFERENT-documented | Leak fix; NULL-user rows dropped (section 5). |
| 8 (23) | `submissions_studentitem`, `problem_builder_answer`, `assessment_*`, `workflow_*`, `submissions_*` via the ORA UUID chain | anonymous-id membership + org, then chain | `ora_chain.py` (Python-prefetched id sets, chunked) | SAME | Hex-vs-dashed UUID trap guard run before dumping. |
| 8 | `assessment_trainingexample`, `assessment_trainingexample_options_selected` | via shared rubric (`rubric_id IN`) | via the tenant's own training-workflow items | DIFFERENT-documented | Leak fix; narrower than EDM (section 5). |
| 9 (29) | `teams_courseteam`, `teams_courseteammembership` | course LIKE; team subquery | same | SAME | |
| 9 | `wiki_article`, `wiki_articleforobject`, `wiki_articlerevision`, `wiki_urlpath` | by URL-path slug = org | `wiki_urlpath.slug = org OR LIKE 'org/%'`, articles via that set | UNVERIFIED | Package-defined; slug convention assumed from EDM. |
| 9 | `edxval_video`, `_encodedvideo`, `_videotranscript`, `_coursevideo`, `_videoimage` | `coursevideo` by course, plus Mongo-discovered videos | `coursevideo` by course only | DIFFERENT-documented, UNVERIFIED | Mongo-only videos are a known gap (section 6). |
| 9 | `edx_when_datepolicy`, `_contentdate`, `_userdate` | contentdate by course; datepolicy via contentdate; userdate by tenant users | same | SAME | Order matters (datepolicy, contentdate, userdate). UNVERIFIED names. |
| 9 | `figures_coursedailymetrics`, `_enrollmentdata`, `_learnercoursegrademetrics` | site and course/user | `site_id IN lms site AND (course LIKE | user IN membership)` | UNVERIFIED | Package-defined. |
| 9 | `proctoring_proctoredexam`, `_proctoredexamstudentattempt` | course; exam and user | same | UNVERIFIED | Package-defined. |
| 9 | `lti_consumer_ltiagslineitem`, `_ltiagsscore` | resource_link_id contains `block-v1:<org>+`; score via line item | same | UNVERIFIED | `ltiagsscore.user_id` is a varchar LTI id, so scoped through the line item, never membership. |
| 9 | `django_comment_client_role`, `_role_users`, `_permission_roles`, `django_comment_common_discussionsidmapping` | course; role subquery and membership | same | SAME | |
| 9 | `milestones_usermilestone`, `django_admin_log`, `experiments_experimentdata` | membership | `user_id IN membership` | SAME | `milestones_usermilestone` UNVERIFIED. |
| 9 | `block_structure` | `data_usage_key` contains `block-v1:<org>+` | same | SAME | |
| 9 | `django_comment_client_permission` | EDM scopes `1=0` | none | EXCLUDED | Global static table. |
| 9 | `celery_utils_failedtask` | EDM scopes `1=0` | none | EXCLUDED | Ephemeral. |
| 10 (1) | `certificates_certificatewhitelist` (EDM: `certificates_certificateallowlist`) | membership and course | `user_id IN membership AND course LIKE` | SAME | Koa name differs. |
| 10 | `oauth_dispatch_applicationaccess` | by the tenant's OAuth apps (panel DB client ids) | none | EXCLUDED | `oauth2_provider_application` (client secrets) is not exported, so the rows would dangle. |
| extra (4) | `edly_edlymultisiteaccess_groups` | moved in code (`migrate_users_and_courses.py`) | via the tenant's access rows | SAME | Not in any EDM tier file. |
| extra | `student_usersignupsource` | EDM synthesizes rows (users x LMS/Studio domains) | real source rows of members | DIFFERENT-documented | |
| extra | `edly_panel_app_edlyuseractivity` | moved in code | `edly_sub_organization_id = <id> AND user_id IN membership` | UNVERIFIED | Package-defined (edly-panel-edx-app). |
| extra | `lti_consumer_lticonfiguration` | `_migrate_lti_configurations` copies verbatim | XBLOCK configs by `location` org; DB configs by `organization_slug IN orgs` | DIFFERENT-documented, UNVERIFIED | `lti_1p1_client_secret`, `lti_1p3_private_key` blanked (EDM copies them). |
| hard denylist | `oauth2_provider_accesstoken/refreshtoken/grant/idtoken`, `oauth2_accesstoken/grant`, `oauthtoken`, `oauth2_client`, `django_session`, `social_auth_partial/code/nonce/association` | no tenant scoping precedent | none; `export_tenant_mysql` refuses the run if one is requested via `--tables` | EXCLUDED | Recorded `excluded_denylist`. |

Missing tables: an edxapp table absent from the source DB is recorded `skipped_not_in_source` (counts as terminal, so the run can still read `complete`). A wrong column surfaces as a per-table `error`.

### 3.2 CSMH (`export_tenant_csmh`)

| Table | EDM equivalent and scoping | Export scoping | Status | Note |
|---|---|---|---|---|
| `coursewarehistoryextended_studentmodulehistoryextended` (DB `edxapp_csmh`) | by `student_module_id` of the tenant's `courseware_studentmodule` rows | ids collected on the edxapp side (course_id LIKE `course_org_filter`, keyset-paginated), dumped in 1000-id chunks | SAME | Cross-DB, so no subquery. Dumped by `mysqldump`, not redacted. |

### 3.3 OLX (`export_tenant_olx`, run via `manage.py cms`)

| Item | EDM equivalent and scoping | Export scoping | Status | Note |
|---|---|---|---|---|
| Courses | modulestore `active_versions` by org (`migrate_modulestore.py`) | `scope.json course_ids` (`course_overviews`, any-case LIKE) UNION `get_course_summaries(org=<course_orgs>)` | DIFFERENT-documented | Superset of EDM: also courses found only via `course_overviews` with a different-case org. |
| Libraries | included (`run=library`) | `get_library_summaries(org=...)` exported with `export_library_to_xml` | SAME, UNVERIFIED | Library path not run on a real modulestore (section 6). |
| Org match | case-exact | case-exact for the modulestore side; case-insensitive for the `course_overviews` side | DIFFERENT-documented | |
| Manifest | | key `olx` with `tree_sha256`; one failed course makes the key `error` and the command exit non-zero | ADDITIVE | |

### 3.4 credentials (`export_tenant_services`, scope S = `core_siteconfiguration.site_id`)

EDM: `tables/credentials/tier_{2,4,5,6,7}.txt` (known incomplete) and `migrate_credentials_tenant_data.py`. 20 tables dumped.

| Table(s) | EDM equivalent and scoping | Export scoping | Status | Note |
|---|---|---|---|---|
| site resolution | credentials site domain (`django_site.domain`, CLI arg) | `core_siteconfiguration.edx_org_short_name = <slug>`, hard error on 0 or >1 | DIFFERENT-documented, UNVERIFIED | Assumes `edx_org_short_name == slug`. |
| `django_site` | site | `id = S` | SAME | |
| `core_siteconfiguration` | site; EDM rewrites `edly_client_branding_and_django_settings` | `site_id = S` | DIFFERENT-documented | `segment_key` blanked; `edly_client_branding_and_django_settings` blanked whole to `'{}'` (carries OAuth client secrets). |
| `catalog_organization`, `catalog_course`, `catalog_program`, `credentials_coursecertificate`, `credentials_programcertificate` | `site_id` | `site_id = S` | SAME | |
| `credentials_signatory` + the two `*_signatories` M2Ms | via the site's certificates | via the site's course and program certificates | SAME | |
| `catalog_courserun`, `records_usergrade`, `catalog_course_owners`, `catalog_program_authoring_organizations`, `catalog_program_course_runs` | via site courses / programs | same | SAME | |
| `credentials_usercredential`, `credentials_usercredentialattribute` | content-type + certificate-id chain | same (no site column on UserCredential) | SAME | |
| `core_user`, `core_user_groups` | users holding the site's credentials | `username IN usercredential usernames` | SAME | `core_user.password` blanked to `'!'`. |
| `social_auth_usersocialauth` | `uid IN credential usernames` | same | SAME | `extra_data` blanked. |
| `auth_permission`, `auth_group_permissions`, `django_content_type` | EDM tier 0+1 | none | EXCLUDED | `excluded_global`: global lookups. |

### 3.5 discovery (`export_tenant_services`, scope P = `core_partner.id` where `short_code = <slug>`)

EDM: `tables/discovery/tier_{2,3,4}.txt` (known incomplete). 23 tables dumped.

| Table(s) | EDM equivalent and scoping | Export scoping | Status | Note |
|---|---|---|---|---|
| `core_partner`, `core_historicalpartner` | partner | `id = P` | SAME | `marketing_site_api_password`, `analytics_token` blanked on both. |
| `django_site` | partner site | `id IN (partner site)` | SAME | |
| `course_metadata_organization`, `_historicalorganization`, `_person`, `_subject`, `_course`, `_historicalcourse`, `_program`, `_historicalprogram` | `partner_id` | `partner_id = P` | SAME | |
| `_personsocialnetwork`, `_subjecttranslation`, `_courserun`, `_seat`, `_courserun_staff`, `_course_subjects`, `_program_courses` | via partner rows | via partner rows | SAME | `courserun_staff` scoped by courserun only (no person restriction), like EDM. |
| `_course_authoring_organizations`, `_program_authoring_organizations`, `_program_credit_backing_organizations` | EDM filters by organization | BOTH ends must belong to P | DIFFERENT-documented | Net rows should be identical. |
| `_program_excluded_course_runs` | EDM compares program ids to course ids (bug) | `program_id IN P programs AND courserun_id IN P runs` | DIFFERENT-documented | EDM not copied. |
| `course_metadata_video` | EDM migrates zero rows | videos referenced by P's courses, runs, programs | ADDITIVE, UNVERIFIED | Keeps video FKs from dangling. FK column names on course/courserun/program UNVERIFIED. |
| 16 tier 0+1 lookups (`core_currency`, language tags, course/run types, modes, tracks, program types, `taggit_tag`, `waffle_switch`, ...) | EDM tier_0+1 | none | EXCLUDED | `excluded_global`. FKs from tenant rows to them dangle by design. |
| `core_salesforceconfiguration` | | none | EXCLUDED | Global, holds Salesforce credentials. |
| `core_user`, `core_user_groups`, `social_auth_usersocialauth` | | none | EXCLUDED | Discovery service accounts; the social-auth table holds tokens. |

### 3.6 ecommerce (`export_tenant_services`, scope P = `partner_partner.id` where `short_code = <slug>`)

No EDM tier file exists. The table list equals the tables EDM's `migrate_ecommerce_to_wordpress.py` reads, plus `offer_rangeproduct`.
All names are UNVERIFIED against a real Koa ecommerce DB (Oscar layout assumed). Native Koa dump, no WooCommerce transform.
Redaction note: EDM only masks secrets in dry-run logs and copies real values; the export blanks them.

| Table(s) | EDM read and scoping | Export scoping | Status | Note |
|---|---|---|---|---|
| `partner_partner` | partner | `id = P` | SAME, UNVERIFIED | |
| `core_siteconfiguration` | partner site config | `partner_id = P` | DIFFERENT-documented, UNVERIFIED | `payment_processors`, `oauth_settings`, `edly_client_theme_branding_settings` blanked whole to `'{}'`. |
| `partner_stockrecord` | `partner_id` | `partner_id = P` | SAME, UNVERIFIED | |
| `catalogue_product` | stockrecord products; Coupon / Enrollment Code classes not migrated as products (EDM ~848) | stockrecord products (+ parents) excluding those classes, UNION products reached via P's catalogs -> stockrecords | SAME, UNVERIFIED | Coupon products are reached ONLY via the catalog path, as EDM does. |
| `catalogue_productclass`, `catalogue_productattribute`, `catalogue_productattributevalue`, `courses_course` | via products | via the product set | SAME, UNVERIFIED | |
| `catalogue_catalog`, `catalogue_catalog_stock_records` | via offer ranges | catalogs of ranges of P's offers; stockrecords limited to P's | SAME, UNVERIFIED | |
| `offer_conditionaloffer`, `offer_benefit`, `offer_condition`, `offer_range` | `partner_id` and FK chain | same | SAME, UNVERIFIED | |
| `offer_rangeproduct` | not read by EDM | `range_id IN P ranges AND product_id IN P products` | ADDITIVE | Keeps range membership. |
| `voucher_voucher`, `voucher_voucher_offers` | via P's offers | same | SAME, UNVERIFIED | |
| `order_order`, `order_line`, `order_billingaddress` | orders INNER JOINed to users (EDM ~1813) | `partner_id = P AND user_id IN ecommerce_user`; lines and billing address via those orders | SAME, UNVERIFIED | Guest orders dropped. |
| `ecommerce_user` | users with an order in P (EDM ~792) | `id IN (user_id of P's orders)` | SAME, UNVERIFIED | `password` blanked to `'!'`. Customer/order PII is kept by policy. |
| `basket_basket`, `basket_line`, `basket_lineattribute`, `refund_refund`, `refund_refundline`, `payment_source`, `payment_transaction`, `payment_paymentprocessorresponse`, `order_lineprice`, `order_lineattribute`, `order_paymentevent`, `order_orderdiscount`, `order_ordernote`, `order_shippingaddress`, `voucher_voucherapplication`, `partner_partner_users`, `partner_partneraddress` | not read by EDM | none | EXCLUDED | `excluded_global`, reason "not read by EDM". |
| `catalogue_category`, `catalogue_productcategory` | | none | EXCLUDED | Shared Oscar lookup; names UNVERIFIED. |

### 3.7 notes (`export_tenant_services`, single table in `edx_notes_api`)

| Table | EDM equivalent and scoping | Export scoping | Status | Note |
|---|---|---|---|---|
| `v1_note` | `course_id LIKE <org>` | `user_id IN (anonymous ids of tenant members, course-less row)` AND `course_id LIKE <course_orgs>` | DIFFERENT-documented, UNVERIFIED | Leak fix (section 5). Needs: same MySQL HOST/PORT as edxapp, and SELECT grant on the edxapp schema (checked for HOST/PORT only in `resolve`; the grant is a deployment requirement). Assumes the course-independent `student_anonymoususerid` row has `course_id` NULL or `''`. |

### 3.8 forum (`export_tenant_forum`, Mongo `cs_comments_service` to `forum/*.jsonl`)

| Collection | EDM equivalent and scoping | Export scoping | Status | Note |
|---|---|---|---|---|
| `contents` | `course_id` org prefix, case-sensitive | `course_id` regex `^course-v1:(<course_orgs>)\+`, case-INSENSITIVE | DIFFERENT-documented | Threads like `course-v1:mitx+...` are included here, not by EDM. |
| `users` | referenced users, remapped to target | user ids referenced by the selected contents; no id remap | DIFFERENT-documented | Non-members: `email` blanked. Everyone: `read_states` / `course_stats` filtered to tenant courses. `users.jsonl` is the id to username map. |
| `subscriptions` | thread follows of selected threads | same (`source_id` = hex-string thread id); user follows skipped | SAME | |
| secret-name scan | n/a | HARD names (password/secret/token/api key/`*_key`) abort the collection; softer matches (credential/private/salt/signature/oauth/hash) have the value blanked and paths listed in `blanked_secret_fields` | ADDITIVE | |
| cohort `group_id` | remapped | kept opaque | DIFFERENT-documented | Joins to `course_groups_courseusergroup` (no remap, section 5). |

### 3.9 S3 (`export_tenant_s3`; 7 default buckets + 1 opt-in)

Keys are delivered verbatim (no EDM-style target transform). ACLs are dropped (bytes only). Per-bucket manifest key `s3__<bucket>`.

| Bucket | EDM equivalent and scoping | Export scoping | Status | Note |
|---|---|---|---|---|
| `discovery` | org logos/banners, program banner/card images (+ StdImage variations) by partner | same, via scope.json partner id | SAME | Person images are CloudFront/WordPress-hosted, not S3. Needs `services.discovery` in scope.json. |
| `credentials` | signatory images of PROGRAM-certificate signatories of the site | same, via scope.json site id | SAME | Course-certificate-only signatories not copied (their DB rows are in the dump). |
| `grades` | `sha1(course_id)/` per course, no root | `[ROOT_PATH/]sha1(course_id)/` per course in `course_ids` | SAME only if `ROOT_PATH` empty | Effective `root_path` and `keys_match_edm` recorded in the manifest. |
| `edx-storage` | `block-v1:<org>+`, `h5pxblockmedia/<org>/`, `<org>/`, `scormxblockmedia/<org>/`, `scorm/sha1(module_id)/` | same prefixes over `course_orgs`; scorm via `courseware_studentmodule` rows | SAME | SCORM blocks nobody opened are not found (same gap as EDM). |
| `video-meta` | `media/<transcript|image>` for videos from `edxval_coursevideo` UNION Mongo modulestore video xblocks | same discovery | DIFFERENT-documented | Needs Mongo `DOC_STORE_CONFIG`. `edx_video_id` values with characters outside `[A-Za-z0-9-_.]` are rejected and listed in `unresolved_edx_video_ids` (EDM binds parameters). |
| `ora-submissions` | `submissions_attachments/<student_id>/<course_id>/` by course org | same keys, restricted to tenant members' anonymous ids | DIFFERENT-documented | Leak fix (section 5): a non-member's attachment in a tenant course is not copied. |
| `profile-images` | `media/profile-images/<md5(seed+username)>_*` for users with uploads | members with `profile_image_uploaded_at` set | SAME | Needs Koa's real `PROFILE_IMAGE_HASH_SEED`; a miss on the first 20 users is a hard error. SQL has no `DISTINCT` (breaks `ORDER BY` under `ONLY_FULL_GROUP_BY`). |
| `cert-template-assets` (OPT-IN) | `certificate_template_assets/` prefix in edx-storage | same prefix, PLATFORM-WIDE | ADDITIVE, UNVERIFIED | Only via `--buckets cert-template-assets`. Not in `S3_LOGICAL_BUCKETS`, not an expected manifest key. Copies every tenant's template assets. Consistent with the DB side excluding `certificates_certificatetemplateasset`. Without it, templates referencing assets by direct S3 URL have dead links. |

Default bucket sources: `edx-storage` = `AWS_STORAGE_BUCKET_NAME`; `ora-submissions` and `cert-template-assets` = same bucket; `grades` = `GRADES_DOWNLOAD['BUCKET']`.
All others need `EXPORT_TENANT_S3_SOURCES`. Defaults are UNVERIFIED on the Koa deployment. The delivery bucket may not equal a source bucket.

## 4. Secrets redaction

Redaction happens in the SELECT itself (the secret never leaves the DB). A configured secret column missing from the table raises, never silently ships.

| Source | Table | Blanked column(s) | Sentinel |
|---|---|---|---|
| edxapp | `auth_user` | `password` | `'!'` |
| edxapp | `auth_registration` | `activation_key` | `''` |
| edxapp | `social_auth_usersocialauth` | `extra_data` | `'{}'` |
| edxapp | `student_pendingemailchange` | `activation_key` | `''` |
| edxapp | `verify_student_softwaresecurephotoverification` | `photo_id_key` | `''` |
| edxapp | `lti_consumer_lticonfiguration` | `lti_1p1_client_secret`, `lti_1p3_private_key` | `''` (column names UNVERIFIED) |
| credentials | `core_user` | `password` | `'!'` |
| credentials | `social_auth_usersocialauth` | `extra_data` | `'{}'` |
| credentials | `core_siteconfiguration` | `segment_key`, `edly_client_branding_and_django_settings` | `''`, `'{}'` |
| discovery | `core_partner`, `core_historicalpartner` | `marketing_site_api_password`, `analytics_token` | `''` |
| ecommerce | `ecommerce_user` | `password` | `'!'` |
| ecommerce | `core_siteconfiguration` | `payment_processors`, `oauth_settings`, `edly_client_theme_branding_settings` | `'{}'` |
| forum | `users`, `contents`, `subscriptions` | `users.email` for non-members; soft secret-named fields | `""` |

Hard denylist (`secrets.DENYLIST`, refused if requested via `--tables`): `oauth2_provider_accesstoken`, `oauth2_provider_refreshtoken`, `oauth2_provider_grant`,
`oauth2_provider_idtoken`, `oauth2_accesstoken`, `oauth2_grant`, `oauthtoken`, `oauth2_client`, `django_session`, `social_auth_partial`, `social_auth_code`,
`social_auth_nonce`, `social_auth_association`, `third_party_auth_oauth2providerconfig`, `third_party_auth_samlconfiguration`, `third_party_auth_samlproviderconfig`.

Excluded secret-bearing tables (`tables.EXCLUDED`): the three `third_party_auth_*` tables, `oauth_dispatch_applicationaccess`, `eox_tenant_tenantconfig`/`_route`
(and Koa's `site_configuration_siteconfiguration`, which holds secrets, is not in any tier list). Discovery `core_salesforceconfiguration` and
`social_auth_usersocialauth`, and credentials/ecommerce rows listed above, are excluded or blanked at the service level.

## 5. Deliberate differences and reasons

| Difference | EDM behavior | Export behavior | Reason |
|---|---|---|---|
| Journal NULL-user rows | `user IN (...) OR (user IS NULL AND org)`; the first branch has no org filter | `user IN membership AND org LIKE`; NULL-user course-only rows dropped | A shared system account's entries from another tenant's course would leak. |
| Training examples | scoped by shared rubric `rubric_id IN` | scoped via the tenant's own `assessment_studenttrainingworkflowitem` | `assessment_rubric.content_hash` is unique, so identical rubrics are one platform-wide row; sharing one would pull another client's staff-authored examples. |
| `certificates_certificatetemplateasset` | `1=1` | excluded (`excluded_cross_tenant_leak`) | Global table, cross-tenant leak. |
| ORA2 membership | S3 attachments by course org only | attachments limited to tenant members' anonymous ids; tier 8 chain also member-scoped | PII scoping; narrower than EDM on purpose. |
| Notes membership | `course_id LIKE` org only | author must be a tenant member AND course in tenant orgs | Membership alone leaks a multi-tenant member's notes on another tenant's course text; org alone leaks non-members. Notes by non-members on tenant courses are not exported. |
| LIKE escaping | org interpolated raw, `_` acts as wildcard | org validated (`[A-Za-z0-9._-]`) and `\`, `_`, `%` escaped | A literal underscore in `My_Org` must not match `MyXOrg`. |
| Case-insensitivity | MySQL LIKE ci; forum and S3 case-sensitive | SQL LIKE ci (unchanged); forum regex `$options: "i"`; S3/Mongo use real-case spellings added to `course_orgs` | Keep forum consistent with the SQL scope; S3 keys are case-sensitive so need the real spelling. |
| Org-set derivation | sub-org M2M | M2M + real-case variants of those same orgs; fallback to `course_org_filter` + course-id orgs when M2M is empty | Fallback is a superset and can include orgs EDM would not copy; `export_tenant_scope` warns on mismatch. |
| Empty `course_org_filter` | silent `1=0` | hard error (`ScopeError`) | A silent `1=0` makes every subcommand "succeed" with zero rows. |
| No id remap | forum/user ids remapped to the target | raw ids kept; forum `users.jsonl` is the id to username map; cohort `group_id` opaque | Archival handoff, no target system. |
| Discovery video | migrates zero rows | `course_metadata_video` exported (ADDITIVE) | Keeps video FKs from dangling. |
| Ecommerce coupon products | Coupon/Enrollment Code classes not products; reached via catalog -> stockrecord | same | Matches EDM (this is parity, listed because it is subtle). |
| Branding blob | EDM masks secrets in dry-run logs, copies real values | `edly_client_theme_branding_settings` (ecommerce) and `edly_client_branding_and_django_settings` (credentials) blanked whole | Surgical `JSON_REMOVE` needs a column type and key layout not verified on Koa; invalid JSON would abort the dump. Revisit after the demo-site audit. |
| `core_siteconfiguration` / secret tables | copied (EDM) | blanked columns (section 4) | Secrets are never exported. |
| Credentials site resolution | domain argument | `edx_org_short_name = slug` | See section 6. |
| Discovery `*_authoring_organizations`, `program_excluded_course_runs` | by organization / buggy id compare | both ends in P / `program_id` and `courserun_id` in P | See section 3.5. |
| `edx_video_id` characters | any string (bound param) | rejected outside `[A-Za-z0-9-_.]` | SQL is hand-built (it carries literal `%`). |
| Grades `ROOT_PATH` | ignored | prepended to the listing prefix | Keys equal EDM's only when it is empty. |
| Partial tables | | missing edxapp table = `skipped_not_in_source`; missing service table = `error` | Service schemas are inferred, so a miss is a spec mismatch. |

## 6. Known gaps and UNVERIFIED items (check on the Koa demo site)

| Item | What to check |
|---|---|
| Mongo-only videos in video tables | `edxval_video`, `_encodedvideo`, `_videotranscript` are scoped via `edxval_coursevideo` only. Videos written inline by Studio with no coursevideo row are missing from the DB dump (the `video-meta` S3 bucket does use Mongo discovery). Compare counts vs `video_discovery` `total`. |
| Package-defined table names | Confirm existence and columns of figures (`figures_*`), proctoring (`proctoring_*`), lti_consumer, edxval, edx_when, wiki, milestones, `edly_panel_app_edlyuseractivity` (24 in `UNVERIFIED_IN_CHECKOUT`). Missing = `skipped_not_in_source`; wrong column = per-table `error`. |
| Ecommerce names | All table and column names in section 3.6; also `courses_course`, `catalogue_catalog*`, `core_siteconfiguration` columns and types (JSON vs longtext). Run `export_tenant_audit --db ecommerce`. |
| Credentials / discovery completeness | EDM tier files are known incomplete; run `export_tenant_audit --db credentials` and `--db discovery` and extend TABLES / EXCLUDED until the coverage scan is clean. |
| LTI secret column names | `lti_1p1_client_secret`, `lti_1p3_private_key` on `lti_consumer_lticonfiguration`; a wrong name raises (no secret shipped) but fails the table. |
| `edx_org_short_name` slug | Credentials site resolution assumes `core_siteconfiguration.edx_org_short_name == slug`. Hard-errors on 0 or >1 matches. |
| OLX library path | `get_library_summaries` + `export_library_to_xml` not run against a real modulestore. |
| `edlytenant` = `edlysuborganization` | Semantic tier-2 mapping. |
| Notes | `student_anonymoususerid.course_id` of the course-less row is NULL or `''`; cross-schema SELECT grant for the notes DB user. |
| Discovery video FK columns | `video_id` on course / courserun / program. |
| S3 sources | Bucket names and regions for every logical bucket; `PROFILE_IMAGE_HASH_SEED`; `GRADES_DOWNLOAD['ROOT_PATH']`. |
| Package command and `expected_excluded` | `export_tenant_package` still builds its `Manifest` without edxapp `expected_excluded`; it enforces what earlier `export_tenant_mysql` / `export_tenant_services` runs persisted into the manifest (service-level `EXCLUDED` stems are now persisted too, and dropped again for `--skip-db`). A manifest written before this change (no `expected_excluded` key) can still read `complete` without the excluded tables recorded. |
| Cert-template manifest entry | `export_tenant_s3 --buckets cert-template-assets` writes a normal `s3__cert-template-assets` entry (and, if `complete`, `export_tenant_package` verifies its index file generically). It is outside `S3_KEYS`, so its absence never blocks `complete`. Not exercised end to end. |
| Stale docstring | `tables.py` module docstring still says "Phase 1 table lists (tiers 3/5/6/7/8)"; the module now also holds tiers 2/4/9/10 and extras. |

## 7. Demo-site verification checklist

1. `export_tenant_scope <slug> --services credentials,discovery,ecommerce,notes`: no empty `course_org_filter`; read the `course_orgs_source` and the mismatch warning vs `course_org_filter`.
2. `export_tenant_audit --db credentials|discovery|ecommerce|notes`: coverage scan clean; every inferred table/column name resolves.
3. `export_tenant_mysql --dry-run`: 136 tables listed; note every `skipped_not_in_source` (expect only package-defined tables absent from this deployment); compare counts with `proof/reference_counts.sql` and with EDM `--dry-run` for the same tenant on 3+ tables per tier.
4. Real run, then grep the output tree for secrets: OAuth keys, password hashes, `activation_key`, `photo_id_key`, recovery tokens, payment processor config.
5. Manifest: `redacted_columns` present for every table in section 4; all `expected_excluded` tables recorded `excluded_*`; status `complete` only when every table is dumped or excluded.
6. `sql_mode` includes `ONLY_FULL_GROUP_BY`: run the `profile-images` bucket and confirm no SQL error.
7. Leak spot checks: no journal rows for other tenants' courses; no training examples from non-tenant workflows; notes limited to member authors on tenant courses; ORA attachments only for members; forum `users.jsonl` has no non-member emails and no other-tenant `read_states`.
8. Mongo-only videos: compare `edxval_video` rows in the dump with `video-meta` `total` from the S3 dry-run.
9. S3 `--dry-run` for all 7 buckets: check `coverage` warnings/errors (grades dirs found, ORA pairs vs objects, edx-storage non-empty), `keys_match_edm` for grades, `unresolved_edx_video_ids` empty.
10. OLX: run `manage.py cms export_tenant_olx`; confirm courses plus libraries, and that `olx` manifest key has `tree_sha256`.
11. `export_tenant_package <slug>`: status `complete`, no checksum problems; then re-run against a manifest created fresh (not a stale one) to confirm `expected_excluded` is enforced.
12. One read-only pass on prod MIT before any real delivery.
