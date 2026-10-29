"""Unit & template tests for the multi-select and bulk decommission UI in the deck view."""
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.server.app.main import app


class TestBulkDecommissionUI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.html = self.client.get("/decks").text

    def test_select_mode_button_present(self):
        """Check that the 'Select' mode button is in the controls bar with shortcut S."""
        self.assertIn('id="selectBtn"', self.html)
        self.assertIn('aria-keyshortcuts="S"', self.html)
        self.assertIn('aria-pressed="false"', self.html)
        btn = re.search(r'<button[^>]*id="selectBtn"[^>]*>(.*?)</button>', self.html)
        assert btn is not None
        self.assertIn("Select", btn.group(1))

    def test_bulk_action_bar_present(self):
        """Check that the contextual bulk action bar is in the markup with all controls."""
        self.assertIn('id="bulkBar"', self.html)
        self.assertIn('role="region"', self.html)
        self.assertIn('aria-label="Bulk actions"', self.html)
        self.assertIn('id="bulkAll"', self.html)
        self.assertIn('id="bulkCount"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('id="bulkClear"', self.html)
        self.assertIn('id="bulkDone"', self.html)
        self.assertIn('id="bulkGo"', self.html)
        self.assertIn("Decommission (0)", self.html)

    def test_bulk_confirmation_dialog_present(self):
        """Check that the confirmation dialog modal is defined with alertdialog role."""
        self.assertIn('id="bulkDecommissionModal"', self.html)
        self.assertIn('id="bulkDialogPanel"', self.html)
        self.assertIn('role="alertdialog"', self.html)
        self.assertIn('aria-modal="true"', self.html)

    def test_single_deck_decommission_present_in_deck_modal(self):
        """Check that the single-deck decommission button exists in the quick view modal."""
        self.assertIn('id="modalDecommissionBtn"', self.html)
        self.assertIn("decommissionCurrentDeck()", self.html)

    def test_toasts_container_present(self):
        """Check that the toast notifications container is defined."""
        self.assertIn('id="toasts"', self.html)
        self.assertIn('role="region"', self.html)

    def test_api_contract_wiring_in_javascript(self):
        """Verify the JavaScript wires up POST /api/decks/decommission with deck_ids."""
        self.assertIn("fetch('/api/decks/decommission'", self.html)
        self.assertIn("method: 'POST'", self.html)
        self.assertIn("deck_ids:", self.html)

    def test_type_to_confirm_threshold(self):
        """Verify that type-to-confirm is implemented for batches of 5 or more."""
        self.assertIn("n >= 5", self.html)
        self.assertIn("bulkType", self.html)
        self.assertIn("handleTypeToConfirm", self.html)

    def test_partial_failure_handling(self):
        """Verify that failed decks stay in selectedDeckIds and are marked failed."""
        self.assertIn("failed.forEach", self.html)
        self.assertIn("failedDeckIds.set", self.html)
        self.assertIn("selectedDeckIds.add", self.html)
        self.assertIn("is-failed", self.html)

    def test_keyboard_shortcuts_registered(self):
        """Verify that S, Escape, Space, and Ctrl+A shortcuts are wired."""
        self.assertIn("e.key === 's'", self.html)
        self.assertIn("e.key === 'Escape'", self.html)
        self.assertIn("handleSelectAll(true)", self.html)
        self.assertIn("deck-pick", self.html)


if __name__ == "__main__":
    unittest.main()
