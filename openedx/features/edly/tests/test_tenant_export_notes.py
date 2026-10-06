"""Phase 4 notes spec (EDLYPRODUCT-8584): registration + scoping clause shape. Offline."""
import unittest

from openedx.features.edly.tenant_export import services, svc_notes

CTX = {"sub_org_id": 7, "edxapp_db": "edxapp", "course_orgs": ["MITx", "My_Org"]}


class NotesSpecTests(unittest.TestCase):
    def test_registered(self):
        self.assertIn("notes", services.SERVICE_DBS)
        self.assertIs(services.get_spec("notes"), svc_notes.SPEC)
        self.assertEqual(svc_notes.SPEC.expected_stems, ["notes__v1_note"])

    def test_default_schema_name_is_real_notes_db(self):
        cfg = services.service_db_settings({"NAME": "edxapp", "HOST": "h"}, {}, "notes")
        self.assertEqual(cfg["NAME"], "edx_notes_api")
        self.assertEqual(services.service_db_settings({"NAME": "edxapp"}, {}, "ecommerce")["NAME"], "ecommerce")
        self.assertEqual(services.service_db_settings({"NAME": "x"}, {"notes": {"NAME": "n"}}, "notes")["NAME"], "n")

    def test_where_requires_member_anon_id_and_tenant_course_org(self):
        w = svc_notes.where("v1_note", CTX)
        self.assertIn("edxapp.student_anonymoususerid", w)
        self.assertIn("edxapp.edly_edlymultisiteaccess WHERE sub_org_id = 7", w)
        self.assertIn("course_id IS NULL OR course_id = ''", w)
        # ...AND the note's own course must be a tenant course (no other-tenant course text leaks)
        self.assertIn("AND (course_id LIKE 'course-v1:MITx+%' OR course_id LIKE 'course-v1:My\\_Org+%')", w)

    def test_no_orgs_refuses(self):
        with self.assertRaises(ValueError):
            svc_notes.where("v1_note", {**CTX, "course_orgs": []})

    def test_unsafe_schema_rejected(self):
        with self.assertRaises(ValueError):
            svc_notes.where("v1_note", {**CTX, "edxapp_db": "x; DROP"})
