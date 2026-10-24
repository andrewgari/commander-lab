"""Template-level checks: Reconstruct draft export UI is wired to the export endpoint."""
import re
import unittest

from fastapi.testclient import TestClient

from app import app


class TestReconstructExportUI(unittest.TestCase):
    def setUp(self):
        self.html = TestClient(app).get("/reconstruct/recon_abc").text

    def test_modal_present(self):
        for el in ("exportModal", "exportProvMoxfield", "exportProvArchidekt",
                   "exportPrivate", "exportDryRun", "exportConfirmBtn", "exportStatus"):
            self.assertIn(f'id="{el}"', self.html)
        self.assertIn('role="dialog"', self.html)

    def test_wired_to_draft_export_endpoint(self):
        self.assertIn("/drafts/${exportDraftIndex}/export", self.html)
        self.assertIn("function submitExport", self.html)
        self.assertIn("openExportModal(${idx})", self.html)

    def test_handles_partial_207(self):
        self.assertIn("res.status === 207", self.html)

    def test_inline_script_balanced(self):
        script = self.html.split("function openExportModal")[1].split("function closeAcceptModal")[0]
        self.assertEqual(script.count("{"), script.count("}"))


if __name__ == "__main__":
    unittest.main()
