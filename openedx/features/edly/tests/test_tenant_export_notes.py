"""Phase 4 notes spec (EDLYPRODUCT-8584): registration + scoping clause shape. Offline."""
import unittest

from openedx.features.edly.tenant_export import services, svc_notes

CTX = {"sub_org_id": 7, "edxapp_db": "edxapp"}


class NotesSpecTests(unittest.TestCase):
    def test_registered(self):
        self.assertIn("notes", services.SERVICE_DBS)
        self.assertIs(services.get_spec("notes"), svc_notes.SPEC)
        self.assertEqual(svc_notes.SPEC.expected_stems, ["notes__v1_note"])

    def test_where_scopes_by_member_anon_id_not_course_org(self):
        w = svc_notes.where("v1_note", CTX)
        self.assertIn("edxapp.student_anonymoususerid", w)
        self.assertIn("edxapp.edly_edlymultisiteaccess WHERE sub_org_id = 7", w)
        self.assertIn("course_id IS NULL OR course_id = ''", w)
        self.assertNotIn("LIKE", w)

    def test_unsafe_schema_rejected(self):
        with self.assertRaises(ValueError):
            svc_notes.where("v1_note", {**CTX, "edxapp_db": "x; DROP"})
