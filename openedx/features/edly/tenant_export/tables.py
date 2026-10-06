"""edxapp table lists (tiers 2-10 plus extras, and the EXCLUDED map) + per-table WHERE builders.

Table lists and WHERE logic are copied by hand from the real, production
EDM files (read-only reference, never imported):
  edlysaas_data_migrations/tables/edxapp/tier_3.txt, tier_5.txt, tier_6.txt, tier_7.txt
  migrate_tenant_tier_data.py:662-928 (tier 5/6/7 WHERE builders)

Ported essentially unchanged from the standalone reference implementation
(`mit-tenant-export/export_mit/tables.py`, EDLYPRODUCT-8584 Phase 1), which
was itself independently audited 2026-10-02 -- see the `[Audited
2026-10-02]` comments throughout, which are real, verified correctness/
security fixes, not suggestions to reconsider. Only the import path and the
addition of TIER_8/CSMH_TABLE/ALL_TIER_TABLES/EXPECTED_TABLES (previously
assembled in the standalone tool's `cli.py`, which no longer exists as a
single file now that this is five separate management commands) are new.

Every WHERE clause here uses a *live SQL subquery* for tenant membership
(`sqlutil.membership_subquery`), never a Python-collected user-id list -- see
sqlutil.py's docstring for why. Tier 8 (the ORA/assessment UUID chain) is
the one legitimate exception to that rule and lives in ora_chain.py.

Table-name trap [plan]: `tier_3.txt` lists `edly_features_app_edlymultisiteaccess`
(the Ulmo/target name, after EDM's migration renames it). On Koa (our only
source here) it is `edly_edlymultisiteaccess` -- confirmed against the real
model (`openedx/features/edly/models.py`'s `EdlyMultiSiteAccess`, app label
`edly` via `EdlyAppConfig.name`, which Django defaults the table prefix to)
-- the name used everywhere in this file and in ora_chain.py/scope.py is the
Koa source name on purpose. SOURCE_TABLE_NAME_OVERRIDES below documents the
mapping; nothing here reads tier_3.txt's name at runtime, so there is no
risk of silently picking up the wrong one.
"""
from openedx.features.edly.tenant_export.sqlutil import (
    _escape_like_value, membership_subquery, org_like_clause, validate_org_token,
)

SOURCE_TABLE_NAME_OVERRIDES = {
    # Ulmo/target name (as EDM's tier_*.txt lists it) -> Koa/source name (used here)
    "edly_features_app_edlymultisiteaccess": "edly_edlymultisiteaccess",
    # Verified: lms/djangoapps/certificates/models.py `CertificateWhitelist` (renamed to allowlist in Teak).
    "certificates_certificateallowlist": "certificates_certificatewhitelist",
    # Verified: openedx/features/edly/models.py `EdlyOrganization`.
    "edly_features_app_edlyorganization": "edly_edlyorganization",
    # UNVERIFIED semantic mapping: Ulmo's EdlyTenant is assumed to be what Koa calls EdlySubOrganization
    # (the table name itself is verified in models.py).
    "edly_features_app_edlytenant": "edly_edlysuborganization",
}

# =============================================================================
# Documented deliberate deviations from EDM (kept on purpose; do not "fix")
# =============================================================================
# * journal_djangoapp_journalmodel: NULL-user, course-only rows are DROPPED (EDM's `OR (user IS NULL AND org)`
#   branch is kept out; see tier7_where) -- the `user IN (...)` branch of EDM leaks other tenants' rows.
# * assessment_trainingexample*: scoped through the tenant's own training-workflow items, not the shared
#   (content_hash-deduplicated) rubric as EDM does -- see ora_chain.py. Narrower than EDM by design.
# * certificates_certificatetemplateasset: EXCLUDED (EDM scopes it 1=1, a cross-tenant leak) -- see EXCLUDED.
# * LIKE patterns: org short names are validated and `_`/`%`/`\` escaped (sqlutil._escape_like_value), so a
#   literal underscore in an org name cannot over-match. EDM interpolates the raw value.
# * Empty course_org_filter is a hard error (scope.py), not EDM's silent `1=0`.
# * edxval_video/encodedvideo/videotranscript: scoped via edxval_coursevideo only. EDM also unions video ids
#   found in Mongo modulestore xblocks (video_discovery.py); that needs a Mongo connection, so Mongo-only videos
#   are a known GAP here (the S3 video-meta bucket does use the Mongo discovery).
# Table-name coverage: every table below that is defined by a pip-installed package rather than by this checkout
# (figures, proctoring, lti_consumer, edxval, edx_when, wiki, milestones, edly_panel_app) is listed in
# UNVERIFIED_IN_CHECKOUT. Their names/columns are taken from EDM, which reads the same Koa source DB; a missing
# table is recorded `skipped_not_in_source`, a wrong column surfaces as a per-table `error` in the manifest.

# =============================================================================
# TIER 3: Core identity (per-tenant)
# =============================================================================

TIER_3 = [
    "auth_user",
    "auth_userprofile",
    "auth_registration",
    "auth_accountrecovery",
    "auth_user_groups",
    "auth_user_user_permissions",
    # [Audited 2026-10-02] three tables missing from the original working
    # list -- see plan's "completeness gaps in tier 3".
    "organizations_organization",
    "organizations_organizationcourse",
    "edly_edlymultisiteaccess",
]


def tier3_where(table: str, sub_org_id, course_org_filter) -> str:
    membership = membership_subquery(sub_org_id)

    # [Audited 2026-10-02] auth_user is keyed on `id`, not `user_id` -- the
    # one tier-3 table that needs the special case.
    if table == "auth_user":
        return f"id IN ({membership})"

    # [Audited 2026-10-02] the membership table itself: scope directly by
    # sub_org_id, not via the generic subquery (it has no user_id column of
    # its own scoped the same way -- it IS the membership record).
    if table == "edly_edlymultisiteaccess":
        return f"sub_org_id = {int(sub_org_id)}"

    # [Audited 2026-10-02] org tables -- scoped by short_name matching
    # course_org_filter, not by membership at all.
    if table == "organizations_organization":
        orgs_csv = ",".join(f"'{o}'" for o in course_org_filter)
        return f"short_name IN ({orgs_csv})"
    if table == "organizations_organizationcourse":
        orgs_csv = ",".join(f"'{o}'" for o in course_org_filter)
        return f"organization_id IN (SELECT id FROM organizations_organization WHERE short_name IN ({orgs_csv}))"

    # auth_userprofile, auth_registration, auth_accountrecovery,
    # auth_user_groups, auth_user_user_permissions: plain `user_id IN (...)`.
    return f"user_id IN ({membership})"


# =============================================================================
# TIER 5: User-dependent simple tables (direct FK to auth_user)
# =============================================================================

TIER_5 = [
    "bookmarks_bookmark",
    "student_courseenrollmentallowed",
    "student_languageproficiency",
    "student_loginfailures",
    "student_pendingemailchange",
    "student_sociallink",
    "student_userattribute",
    "student_anonymoususerid",
    "user_api_usercoursetag",
    "user_api_userpreference",
    "user_api_userretirementrequest",
    "external_user_ids_externalid",
    "courseware_xmodulestudentinfofield",
    "courseware_xmodulestudentprefsfield",
    "social_auth_usersocialauth",
]

_TIER5_STUDENT_ID_TABLES = {
    "courseware_xmodulestudentinfofield",
    "courseware_xmodulestudentprefsfield",
}


def tier5_where(table: str, sub_org_id) -> str:
    membership = membership_subquery(sub_org_id)

    if table in ("student_languageproficiency", "student_sociallink"):
        return f"user_profile_id IN (SELECT id FROM auth_userprofile WHERE user_id IN ({membership}))"
    if table in _TIER5_STUDENT_ID_TABLES:
        return f"student_id IN ({membership})"
    return f"user_id IN ({membership})"


# =============================================================================
# TIER 6: Course-dependent tables
# =============================================================================
#
# [Plan] certificates_certificatetemplateasset is deliberately EXCLUDED here:
# EDM scopes it "1=1" (global, shared template assets, justified only in a
# live-migration context). Copying every tenant's template assets into one
# client's archival export is a cross-tenant leak, not acceptable here.

TIER_6 = [
    "course_overviews_courseoverview",
    "course_overviews_courseoverviewtab",
    "course_modes_coursemode",
    "course_modes_historicalcoursemode",
    "course_groups_courseusergroup",
    "course_groups_coursecohortssettings",
    "course_action_state_coursererunstate",
    "bulk_email_courseemail",
    "certificates_certificategenerationcoursesetting",
    "certificates_certificatetemplate",
    "completion_blockcompletion",
    "courseware_studentmodule",
    "courseware_xmoduleuserstatesummaryfield",
    "instructor_task_instructortask",
    "django_comment_common_coursediscussionsettings",
    "bookmarks_xblockcache",
    "milestones_coursemilestone",
    "milestones_coursecontentmilestone",
]

# certificates_certificatetemplateasset intentionally NOT in TIER_6 -- see
# module docstring above. Keep it enumerated here so export_tenant_mysql can
# record an explicit "why this table is missing" manifest entry rather than
# leaving a silent gap.
EXCLUDED_CROSS_TENANT_LEAK = {
    "certificates_certificatetemplateasset": (
        "EDM scopes this table 1=1 (global/shared template assets); not "
        "acceptable for a single-tenant external handoff"
    ),
}

_TIER6_COURSE_COLUMN = {
    "course_overviews_courseoverview": "id",
    "course_overviews_courseoverviewtab": "course_overview_id",
    "completion_blockcompletion": "course_key",
    "certificates_certificategenerationcoursesetting": "course_key",
    "certificates_certificatetemplate": "course_key",
    "course_action_state_coursererunstate": "course_key",
    "courseware_xmoduleuserstatesummaryfield": "usage_id",
    "bookmarks_xblockcache": "course_key",
}


def tier6_where(table: str, course_org_filter) -> str:
    course_col = _TIER6_COURSE_COLUMN.get(table, "course_id")
    prefix = "block-v1" if course_col == "usage_id" else "course-v1"
    course_filter = org_like_clause(course_col, course_org_filter, prefix=prefix)

    if table == "certificates_certificatetemplate":
        # Org-wide templates (empty course_key) are matched via their
        # organization_id instead -- a live subquery, not a Python id list.
        orgs_csv = ",".join(f"'{o}'" for o in course_org_filter)
        org_filter = (
            "(organization_id IS NOT NULL AND organization_id IN "
            f"(SELECT id FROM organizations_organization WHERE short_name IN ({orgs_csv})))"
        )
        return f"({course_filter}) OR {org_filter}"

    return course_filter


# =============================================================================
# TIER 7: User-course junction tables
# =============================================================================

TIER_7 = [
    "student_courseenrollment",
    "student_courseaccessrole",
    "student_courseenrollmentattribute",
    "student_courseenrollment_history",
    "student_historicalmanualenrollmentaudit",
    "student_manualenrollmentaudit",
    "user_tasks_usertaskstatus",
    "user_tasks_usertaskartifact",
    "external_user_ids_historicalexternalid",
    "verify_student_manualverification",
    "verify_student_softwaresecurephotoverification",
    "verify_student_ssoverification",
    "verify_student_verificationdeadline",
    "user_api_userretirementstatus",
    "course_groups_courseusergroup_users",
    "course_groups_cohortmembership",
    "course_groups_coursecohort",
    "course_groups_courseusergrouppartitiongroup",
    "course_groups_unregisteredlearnercohortassignments",
    "certificates_generatedcertificate",
    "certificates_certificateinvalidation",
    "certificates_certificategenerationhistory",
    "grades_persistentcoursegrade",
    "grades_persistentsubsectiongrade",
    "grades_visibleblocks",
    "bulk_email_courseemail_targets",
    "bulk_email_cohorttarget",
    "course_goals_coursegoal",
    "course_creators_coursecreator",
    "kwl_djangoapp_kwlmodel",
    "journal_djangoapp_journalmodel",
]

_TIER7_ENROLLMENT_FK = {
    "student_courseenrollmentattribute": "enrollment_id",
    "student_historicalmanualenrollmentaudit": "enrollment_id",
    "student_manualenrollmentaudit": "enrollment_id",
}


def tier7_where(table: str, sub_org_id, course_org_filter) -> str:
    membership = membership_subquery(sub_org_id)
    org_course_id = org_like_clause("course_id", course_org_filter)
    org_course_key = org_like_clause("course_key", course_org_filter)
    # Live subquery replacing EDM's Python-prefetched `enrollment_ids` set --
    # same reasoning as membership_subquery (plan: subquery, not a Python
    # id list, for tiers 3/5/6/7).
    enrollment_subq = (
        f"SELECT id FROM student_courseenrollment WHERE user_id IN ({membership}) AND ({org_course_id})"
    )

    if table == "student_courseenrollment":
        return f"user_id IN ({membership}) AND ({org_course_id})"

    if table == "student_courseaccessrole":
        orgs_csv = ",".join(f"'{o}'" for o in course_org_filter)
        # course_id can be empty for org-wide roles -- match course_id, org,
        # AND the global course_creator_group role (org='' AND course_id=''),
        # which would otherwise be silently dropped. Still gated by
        # membership, so no cross-tenant leakage on the shared source DB.
        return (
            f"user_id IN ({membership}) AND "
            f"(({org_course_id}) OR org IN ({orgs_csv}) OR role = 'course_creator_group')"
        )

    if table in _TIER7_ENROLLMENT_FK:
        fk_col = _TIER7_ENROLLMENT_FK[table]
        return f"{fk_col} IN ({enrollment_subq})"
    if table == "student_courseenrollment_history":
        return f"id IN ({enrollment_subq})"

    if table == "user_tasks_usertaskstatus":
        return f"user_id IN ({membership})"
    if table == "user_tasks_usertaskartifact":
        return f"status_id IN (SELECT id FROM user_tasks_usertaskstatus WHERE user_id IN ({membership}))"

    if table == "external_user_ids_historicalexternalid":
        return f"user_id IN ({membership})"

    if table in (
        "verify_student_manualverification",
        "verify_student_softwaresecurephotoverification",
        "verify_student_ssoverification",
    ):
        # [Audited 2026-10-02] verify_student_softwaresecurephotoverification
        # is NOT multi-table inheritance at the DB level (confirmed against
        # lms/djangoapps/verify_student/migrations/0001_initial.py:75-100) --
        # it has its own `id` and a direct `user` FK, so the plain
        # `user_id IN (...)` filter below is correct, not a ptr_id chain.
        return f"user_id IN ({membership})"
    if table == "verify_student_verificationdeadline":
        return f"({org_course_key})"

    if table == "user_api_userretirementstatus":
        return f"user_id IN ({membership})"

    if table == "course_groups_courseusergroup_users":
        return (
            f"user_id IN ({membership}) AND "
            f"courseusergroup_id IN (SELECT id FROM course_groups_courseusergroup WHERE ({org_course_id}))"
        )
    if table == "course_groups_cohortmembership":
        return (
            f"user_id IN ({membership}) AND "
            f"course_user_group_id IN (SELECT id FROM course_groups_courseusergroup WHERE ({org_course_id}))"
        )
    if table in (
        "course_groups_coursecohort",
        "course_groups_courseusergrouppartitiongroup",
        "course_groups_unregisteredlearnercohortassignments",
    ):
        return f"course_user_group_id IN (SELECT id FROM course_groups_courseusergroup WHERE ({org_course_id}))"

    if table == "certificates_generatedcertificate":
        return f"user_id IN ({membership}) AND ({org_course_id})"
    if table == "certificates_certificateinvalidation":
        return (
            "generated_certificate_id IN (SELECT id FROM certificates_generatedcertificate "
            f"WHERE user_id IN ({membership}) AND ({org_course_id}))"
        )
    if table == "certificates_certificategenerationhistory":
        return (
            "course_id IN (SELECT DISTINCT course_id FROM certificates_generatedcertificate "
            f"WHERE user_id IN ({membership}) AND ({org_course_id}))"
        )

    if table in ("grades_persistentcoursegrade", "grades_persistentsubsectiongrade"):
        return f"user_id IN ({membership}) AND ({org_course_id})"
    if table == "grades_visibleblocks":
        return (
            "course_id IN (SELECT DISTINCT course_id FROM grades_persistentcoursegrade "
            f"WHERE user_id IN ({membership}) AND ({org_course_id}))"
        )

    if table == "bulk_email_courseemail_targets":
        return f"courseemail_id IN (SELECT id FROM bulk_email_courseemail WHERE ({org_course_id}))"
    if table == "bulk_email_cohorttarget":
        return f"cohort_id IN (SELECT id FROM course_groups_courseusergroup WHERE ({org_course_id}))"

    if table == "course_goals_coursegoal":
        return f"user_id IN ({membership}) AND ({org_course_key})"

    if table == "course_creators_coursecreator":
        return f"user_id IN ({membership})"

    if table == "kwl_djangoapp_kwlmodel":
        # `user` stores integer user_id AS VARCHAR -- matches EDM exactly.
        return f"user IN ({membership}) AND ({org_course_id})"
    if table == "journal_djangoapp_journalmodel":
        # [Audited 2026-10-02 -- BLOCKING leak fix] EDM's clause is
        # `(user IN (...) OR (user IS NULL AND org))` -- the `user IN (...)`
        # branch has NO course/org filter, so a shared system account's
        # journal entries from a DIFFERENT tenant's course would leak into
        # this export. Drop the unfiltered-by-org OR branch entirely: always
        # require both membership AND org match. (This means NULL-user,
        # course-only journal rows are excluded from this export -- accepted,
        # documented in the deviations block at the top of this module.)
        return f"user IN ({membership}) AND ({org_course_id})"

    raise KeyError(f"no tier-7 WHERE builder for table {table!r}")


# =============================================================================
# TIER 8: ORA/assessment chain -- table list only. The WHERE logic lives in
# ora_chain.py, since every tier-8 table's scope depends on the prefetched
# Tier8Ids chain, not a plain per-table builder function the way tiers
# 3/5/6/7 do.
# =============================================================================

TIER_8 = [
    "assessment_rubric",
    "assessment_criterion",
    "assessment_criterionoption",
    "assessment_trainingexample",
    "assessment_trainingexample_options_selected",
    "assessment_assessment",
    "assessment_assessmentfeedback",
    "assessment_assessmentfeedback_assessments",
    "assessment_assessmentfeedback_options",
    "assessment_assessmentpart",
    "assessment_peerworkflow",
    "assessment_peerworkflowitem",
    "assessment_staffworkflow",
    "assessment_studenttrainingworkflow",
    "assessment_studenttrainingworkflowitem",
    "workflow_assessmentworkflow",
    "workflow_assessmentworkflowstep",
    "submissions_studentitem",
    "submissions_submission",
    "submissions_score",
    "submissions_scoreannotation",
    "submissions_scoresummary",
    "problem_builder_answer",
]


# =============================================================================
# TIERS 2, 4, 9, 10 + non-tier tables EDM moves in code (EDLYPRODUCT-8584 audit)
# WHERE logic from migrate_tenant_tier_data.py:590-640 (tier 4), 1491-1648 (tier 9), 1363-1386 (tier 10),
# 3337+ (LTI), migrate_users_and_courses.py:697+/888+/993+. All tenant membership is a live subquery.
# =============================================================================

# Koa has no eox_tenant tables (not installed); the other two map via SOURCE_TABLE_NAME_OVERRIDES.
TIER_2 = ["django_site", "theming_sitetheme", "edly_edlyorganization", "edly_edlysuborganization"]

# third_party_auth_* are secret-bearing (see EXCLUDED) and are not dumped.
TIER_4 = ["figures_sitedailymetrics", "figures_sitemonthlymetrics"]

TIER_9 = [
    "teams_courseteam", "teams_courseteammembership",
    "wiki_article", "wiki_articleforobject", "wiki_articlerevision", "wiki_urlpath",
    "edxval_video", "edxval_encodedvideo", "edxval_videotranscript", "edxval_coursevideo", "edxval_videoimage",
    # Order: datepolicy, contentdate, userdate (EDM: do not reorder; DatePolicy has no content_date_id).
    "edx_when_datepolicy", "edx_when_contentdate", "edx_when_userdate",
    "figures_coursedailymetrics", "figures_enrollmentdata", "figures_learnercoursegrademetrics",
    "proctoring_proctoredexam", "proctoring_proctoredexamstudentattempt",
    "lti_consumer_ltiagslineitem", "lti_consumer_ltiagsscore",
    "django_comment_client_role", "django_comment_client_role_users", "django_comment_client_permission_roles",
    "django_comment_common_discussionsidmapping",
    "milestones_usermilestone", "django_admin_log", "block_structure", "experiments_experimentdata",
]

TIER_10 = ["certificates_certificatewhitelist"]  # EDM: certificates_certificateallowlist (Teak name)

# Not in any EDM tier file; EDM moves these in migrate_users_and_courses.py / _migrate_lti_configurations.
# edly_panel_app_edlyuseractivity is EDM's *source* name (Koa, from the edly-panel-edx-app package).
TIER_EXTRA = [
    "edly_edlymultisiteaccess_groups",
    "student_usersignupsource",
    "edly_panel_app_edlyuseractivity",
    "lti_consumer_lticonfiguration",
]

UNVERIFIED_IN_CHECKOUT = [
    "figures_sitedailymetrics", "figures_sitemonthlymetrics", "figures_coursedailymetrics",
    "figures_enrollmentdata", "figures_learnercoursegrademetrics",
    "proctoring_proctoredexam", "proctoring_proctoredexamstudentattempt",
    "lti_consumer_ltiagslineitem", "lti_consumer_ltiagsscore", "lti_consumer_lticonfiguration",
    "edxval_video", "edxval_encodedvideo", "edxval_videotranscript", "edxval_coursevideo", "edxval_videoimage",
    "edx_when_datepolicy", "edx_when_contentdate", "edx_when_userdate",
    "wiki_article", "wiki_articleforobject", "wiki_articlerevision", "wiki_urlpath",
    "milestones_usermilestone", "edly_panel_app_edlyuseractivity",
]

# EDM-listed (tiers 2-10) tables deliberately NOT dumped: table -> reason. Recorded in the manifest as
# `excluded_*`; a run cannot read "complete" until each is recorded (Manifest.expected_excluded).
EXCLUDED = {
    **EXCLUDED_CROSS_TENANT_LEAK,
    "eox_tenant_tenantconfig": "eox_tenant is not installed on Koa (Koa tenant config is site_configuration_siteconfiguration, which holds secrets)",
    "eox_tenant_route": "eox_tenant is not installed on Koa",
    "third_party_auth_oauth2providerconfig": "carries OAuth `secret`; secrets are never exported",
    "third_party_auth_samlconfiguration": "carries SAML `private_key`; secrets are never exported",
    "third_party_auth_samlproviderconfig": "third_party_auth_* is treated as secret-bearing as a family",
    "oauth_dispatch_applicationaccess": (
        "EDM scopes it by the tenant's OAuth apps (panel-DB client_ids); oauth2_provider_application holds "
        "client secrets and is not exported, so these rows would dangle"
    ),
    "django_comment_client_permission": "global static table; EDM itself scopes it 1=0",
    "celery_utils_failedtask": "ephemeral task failures; EDM itself scopes it 1=0",
}


def excluded_keys() -> set:
    """Every table that must appear in the manifest as `excluded_*` (EXCLUDED + secrets.DENYLIST)."""
    from openedx.features.edly.tenant_export import secrets as secrets_mod
    return set(EXCLUDED) | set(secrets_mod.DENYLIST)


def _orgs_contain_like(column: str, orgs, prefix: str) -> str:
    parts = []
    for org in orgs:
        validate_org_token(org)
        parts.append(f"{column} LIKE '%{prefix}:{_escape_like_value(org)}+%'")
    return " OR ".join(parts)


def _site_subq(sub_org_id, col="lms_site_id") -> str:
    return f"SELECT {col} FROM edly_edlysuborganization WHERE id = {int(sub_org_id)}"


def tier_other_where(table: str, sub_org_id, orgs) -> str:
    """WHERE for TIER_2/4/9/10/EXTRA tables (one clause each; every subquery is live, never an id list)."""
    sub = int(sub_org_id)
    member = membership_subquery(sub)
    course = org_like_clause("course_id", orgs)
    lms_site = _site_subq(sub)

    # ---- tier 2 (Koa equivalents; EDM's tenant-structure rows for this tenant only)
    if table in ("django_site",):
        return " OR ".join(f"id IN ({_site_subq(sub, c)})" for c in ("lms_site_id", "studio_site_id", "preview_site_id"))
    if table == "theming_sitetheme":
        return " OR ".join(f"site_id IN ({_site_subq(sub, c)})" for c in ("lms_site_id", "studio_site_id", "preview_site_id"))
    if table == "edly_edlyorganization":
        return f"id IN ({_site_subq(sub, 'edly_organization_id')})"
    if table == "edly_edlysuborganization":
        return f"id = {sub}"

    # ---- tier 4: site_id only (EDM also ORs organization_id where that column exists; none of these have it)
    if table in ("figures_sitedailymetrics", "figures_sitemonthlymetrics"):
        return f"site_id IN ({lms_site})"

    # ---- tier 9
    team = f"SELECT id FROM teams_courseteam WHERE ({course})"
    wiki_articles = (
        "SELECT DISTINCT article_id FROM wiki_urlpath WHERE article_id IS NOT NULL AND ("
        + " OR ".join(f"slug = '{o}' OR slug LIKE '{_escape_like_value(o)}/%'" for o in orgs) + ")"
    )
    coursevideo_videos = f"SELECT DISTINCT video_id FROM edxval_coursevideo WHERE ({course})"
    exam = f"SELECT id FROM proctoring_proctoredexam WHERE ({course})"
    role = f"SELECT id FROM django_comment_client_role WHERE ({course})"
    line_item = f"SELECT id FROM lti_consumer_ltiagslineitem WHERE ({_orgs_contain_like('resource_link_id', orgs, 'block-v1')})"
    simple_course = {
        "teams_courseteam", "edxval_coursevideo", "edx_when_contentdate", "proctoring_proctoredexam",
        "django_comment_client_role", "django_comment_common_discussionsidmapping",
    }
    if table in simple_course:
        return f"({course})"
    mapping = {
        "teams_courseteammembership": f"team_id IN ({team})",
        "wiki_article": f"id IN ({wiki_articles})",
        "wiki_articleforobject": f"article_id IN ({wiki_articles})",
        "wiki_articlerevision": f"article_id IN ({wiki_articles})",
        "wiki_urlpath": f"article_id IN ({wiki_articles})",
        "edxval_video": f"id IN ({coursevideo_videos})",
        "edxval_encodedvideo": f"video_id IN ({coursevideo_videos})",
        "edxval_videotranscript": f"video_id IN ({coursevideo_videos})",
        "edxval_videoimage": f"course_video_id IN (SELECT id FROM edxval_coursevideo WHERE ({course}))",
        "edx_when_datepolicy": (
            f"id IN (SELECT DISTINCT policy_id FROM edx_when_contentdate WHERE ({course}) AND policy_id IS NOT NULL)"
        ),
        # EDM: userdate is scoped by tenant users only (no course filter).
        "edx_when_userdate": f"user_id IN ({member})",
        "figures_coursedailymetrics": f"site_id IN ({lms_site}) AND ({course})",
        "figures_enrollmentdata": f"site_id IN ({lms_site}) AND user_id IN ({member})",
        "figures_learnercoursegrademetrics": f"site_id IN ({lms_site}) AND user_id IN ({member})",
        "proctoring_proctoredexamstudentattempt": f"proctored_exam_id IN ({exam}) AND user_id IN ({member})",
        "lti_consumer_ltiagslineitem": f"({_orgs_contain_like('resource_link_id', orgs, 'block-v1')})",
        # user_id here is a varchar LTI id, NOT auth_user.id -- scope through the line item (EDM 1292 note).
        "lti_consumer_ltiagsscore": f"line_item_id IN ({line_item})",
        "django_comment_client_role_users": f"role_id IN ({role}) AND user_id IN ({member})",
        "django_comment_client_permission_roles": f"role_id IN ({role})",
        "milestones_usermilestone": f"user_id IN ({member})",
        "django_admin_log": f"user_id IN ({member})",
        "block_structure": f"({_orgs_contain_like('data_usage_key', orgs, 'block-v1')})",
        "experiments_experimentdata": f"user_id IN ({member})",
        # ---- tier 10 (Koa name of certificates_certificateallowlist)
        "certificates_certificatewhitelist": f"user_id IN ({member}) AND ({course})",
        # ---- non-tier tables
        "edly_edlymultisiteaccess_groups": (
            f"edlymultisiteaccess_id IN (SELECT id FROM edly_edlymultisiteaccess WHERE sub_org_id = {sub})"
        ),
        # EDM synthesizes rows (users x LMS/Studio domains); we copy the real source rows for tenant users.
        "student_usersignupsource": f"user_id IN ({member})",
        "edly_panel_app_edlyuseractivity": f"edly_sub_organization_id = {sub} AND user_id IN ({member})",
        # _migrate_lti_configurations: XBLOCK configs by block location, DB (shared) configs by org slug.
        "lti_consumer_lticonfiguration": (
            f"(config_store = 'CONFIG_ON_XBLOCK' AND ({_orgs_contain_like('location', orgs, 'block-v1')})) OR "
            "(config_store = 'CONFIG_ON_DB' AND organization_slug IN ("
            + ",".join(f"'{o}'" for o in orgs) + "))"
        ),
    }
    try:
        return mapping[table]
    except KeyError:
        raise KeyError(f"no WHERE builder for table {table!r}") from None


OTHER_TIER_TABLES = TIER_2 + TIER_4 + TIER_9 + TIER_10 + TIER_EXTRA
ALL_TIER_TABLES = TIER_2 + TIER_3 + TIER_4 + TIER_5 + TIER_6 + TIER_7 + TIER_8 + TIER_9 + TIER_10 + TIER_EXTRA

# CSMH lives in a genuinely separate database (`edxapp_csmh`, see
# tenant_export/csmh.py) -- but is still part of the one full expected-table
# set every `export_tenant_*` subcommand's shared MANIFEST.json is scored
# against (manifest.Manifest's `status` only reads "complete" once every one
# of these tables reaches a terminal status, whichever subcommand produced
# it -- export_tenant_mysql or export_tenant_csmh).
CSMH_TABLE = "coursewarehistoryextended_studentmodulehistoryextended"
# Manifest key (not a table) for the OLX course export, written by export_tenant_olx.
OLX_KEY = "olx"
EXPECTED_TABLES = ALL_TIER_TABLES + [CSMH_TABLE, OLX_KEY]
