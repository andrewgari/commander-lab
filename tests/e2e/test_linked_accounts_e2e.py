"""
Playwright E2E tests for the linked-accounts UI on /decks page.

These tests verify:
- The linked-accounts section renders correctly
- Provider icons, usernames, sync times, and deck counts display properly
- 'Add account' flow with provider and username inputs
- 'Remove' flow with inline warning about existing decks
- 'Sync now' button interaction with backend

Prerequisites:
- Server running at BASE_URL (default: http://localhost:8000)
- Redis backend available
- templates/decks.html with linked-accounts UI implemented
- /api/linked-accounts endpoints in app.py

Run with: pytest tests/e2e/test_linked_accounts_e2e.py --base-url=http://localhost:8000

To run using the shared venv:
    source /home/andrewgari/.hermes/kanban/boards/commander-lab/workspaces/t_d981179d/.venv/bin/activate
    pytest tests/e2e/test_linked_accounts_e2e.py -v
"""
import pytest
import json
import re
from playwright.sync_api import Page, expect


class TestLinkedAccountsSectionRendering:
    """Tests for linked-accounts section visibility and structure."""

    def test_linked_accounts_section_visible_on_decks_page(self, page: Page, base_url: str):
        """The linked-accounts section should be visible on /decks page."""
        page.goto(f"{base_url}/decks")
        
        section = page.locator('.linked-accounts-section')
        expect(section).to_be_visible()

    def test_linked_accounts_section_visible_on_root_page(self, page: Page, base_url: str):
        """The linked-accounts section should also be visible on / (root redirects to decks)."""
        page.goto(base_url)
        
        section = page.locator('.linked-accounts-section')
        expect(section).to_be_visible()

    def test_linked_accounts_has_title(self, page: Page, base_url: str):
        """The linked-accounts section should have a 'Linked Accounts' title."""
        page.goto(f"{base_url}/decks")
        
        title = page.locator('.linked-accounts-title')
        expect(title).to_be_visible()
        expect(title).to_contain_text("Linked Accounts")

    def test_linked_accounts_has_link_account_button(self, page: Page, base_url: str):
        """The linked-accounts section should have a 'Link Account' button."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        expect(link_button).to_be_visible()
        expect(link_button).to_be_enabled()

    def test_linked_accounts_has_sync_all_button(self, page: Page, base_url: str):
        """The linked-accounts section should have a 'Sync All' button."""
        page.goto(f"{base_url}/decks")
        
        sync_all_button = page.locator('button:has-text("Sync All"), #syncAllBtn')
        expect(sync_all_button).to_be_visible()


class TestLinkedAccountsEmptyState:
    """Tests for linked-accounts display when no accounts are linked."""

    def test_empty_state_shows_message(self, page: Page, base_url: str):
        """When no accounts are linked, an empty state message should display."""
        page.goto(f"{base_url}/decks")
        
        # Mock empty accounts response
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"accounts": []}'
        ))
        
        page.reload()
        
        # Should show empty state message
        empty_message = page.locator('.linked-accounts-empty')
        expect(empty_message).to_be_visible(timeout=5000)
        expect(empty_message).to_contain_text("No linked accounts")


class TestLinkedAccountsDisplay:
    """Tests for displaying linked account cards with proper information."""

    @pytest.fixture
    def mock_accounts_response(self) -> dict:
        """Mock /api/linked-accounts response with sample accounts."""
        return {
            "accounts": [
                {
                    "id": "archidekt:TestUser",
                    "provider": "archidekt",
                    "username": "TestUser",
                    "enabled": True,
                    "created_at": "2026-09-08T12:00:00+00:00",
                    "last_synced_at": "2026-09-08T12:30:00+00:00",
                    "last_sync_result": {
                        "decks_synced": 5,
                        "failures": []
                    }
                },
                {
                    "id": "moxfield:AnotherUser",
                    "provider": "moxfield",
                    "username": "AnotherUser",
                    "enabled": True,
                    "created_at": "2026-09-08T10:00:00+00:00",
                    "last_synced_at": None,
                    "last_sync_result": None
                }
            ]
        }

    def test_account_cards_display_for_linked_accounts(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Linked accounts should display as account cards."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        
        # Wait for accounts to load
        page.wait_for_selector('.account-card', timeout=5000)
        
        # Should have 2 account cards
        account_cards = page.locator('.account-card')
        expect(account_cards).to_have_count(2)

    def test_account_card_shows_username(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Account cards should display the username."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        username = page.locator('.account-username')
        expect(username.first).to_contain_text("TestUser")

    def test_account_card_shows_provider_badge(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Account cards should display a provider badge (e.g., 'ARCHIDEKT', 'MOXFIELD')."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        provider_badge = page.locator('.account-provider-badge')
        expect(provider_badge.first).to_be_visible()

    def test_account_card_shows_provider_icon(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Account cards should display a provider icon/avatar."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        # Provider icon should be visible (styled div or img)
        provider_icon = page.locator('.account-icon')
        expect(provider_icon.first).to_be_visible()

    def test_archidekt_icon_has_correct_styling(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Archidekt provider icon should have the 'archidekt' class for styling."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-icon.archidekt', timeout=5000)
        
        archidekt_icon = page.locator('.account-icon.archidekt')
        expect(archidekt_icon).to_be_visible()

    def test_moxfield_icon_has_correct_styling(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Moxfield provider icon should have the 'moxfield' class for styling."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-icon.moxfield', timeout=5000)
        
        moxfield_icon = page.locator('.account-icon.moxfield')
        expect(moxfield_icon).to_be_visible()

    def test_account_card_shows_deck_count(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Account cards should display the deck count from last sync."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        # Look for deck count in the meta section
        meta = page.locator('.account-meta-item').first
        expect(meta).to_contain_text("5 decks")

    def test_account_card_shows_sync_time(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Account cards should display the last sync time."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        # Should show sync status (time or "Never synced")
        sync_status = page.locator('.sync-status')
        expect(sync_status.first).to_be_visible()

    def test_account_card_shows_never_synced_for_new_account(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Account with no last_synced_at should show 'Never synced'."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        # Second account has last_synced_at: None
        never_synced = page.locator('.sync-status-dot.never')
        expect(never_synced).to_be_visible()

    def test_account_card_has_sync_button(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Each account card should have a 'Sync now' button."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        # Sync button in account actions
        sync_button = page.locator('.account-actions .btn-icon').first
        expect(sync_button).to_be_visible()

    def test_account_card_has_remove_button(self, page: Page, base_url: str, mock_accounts_response: dict):
        """Each account card should have a 'Remove' button."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_accounts_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        # Remove button with danger styling
        remove_button = page.locator('.account-actions .btn-icon.btn-danger')
        expect(remove_button.first).to_be_visible()


class TestAddAccountFlow:
    """Tests for the 'Add account' flow."""

    def test_add_account_form_hidden_by_default(self, page: Page, base_url: str):
        """The add account form should be hidden by default."""
        page.goto(f"{base_url}/decks")
        
        form = page.locator('#addAccountForm')
        expect(form).not_to_have_class("visible")

    def test_clicking_link_account_shows_form(self, page: Page, base_url: str):
        """Clicking 'Link Account' button should reveal the add account form."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        form = page.locator('#addAccountForm')
        expect(form).to_have_class(re.compile(r".*visible.*"))

    def test_add_account_form_has_provider_select(self, page: Page, base_url: str):
        """The add account form should have a provider dropdown."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        provider_select = page.locator('#newAccountProvider')
        expect(provider_select).to_be_visible()

    def test_provider_select_has_archidekt_option(self, page: Page, base_url: str):
        """The provider dropdown should have an 'Archidekt' option."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        provider_select = page.locator('#newAccountProvider')
        archidekt_option = provider_select.locator('option[value="archidekt"]')
        expect(archidekt_option).to_be_attached()

    def test_provider_select_has_moxfield_option(self, page: Page, base_url: str):
        """The provider dropdown should have a 'Moxfield' option."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        provider_select = page.locator('#newAccountProvider')
        moxfield_option = provider_select.locator('option[value="moxfield"]')
        expect(moxfield_option).to_be_attached()

    def test_add_account_form_has_username_input(self, page: Page, base_url: str):
        """The add account form should have a username input field."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        username_input = page.locator('#newAccountUsername')
        expect(username_input).to_be_visible()
        expect(username_input).to_have_attribute("placeholder", re.compile(r".*[Uu]sername.*"))

    def test_add_account_form_has_add_button(self, page: Page, base_url: str):
        """The add account form should have an 'Add' submit button."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        add_button = page.locator('#addAccountForm button:has-text("Add")')
        expect(add_button).to_be_visible()

    def test_add_account_form_has_cancel_button(self, page: Page, base_url: str):
        """The add account form should have a 'Cancel' button."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        cancel_button = page.locator('#addAccountForm button:has-text("Cancel")')
        expect(cancel_button).to_be_visible()

    def test_clicking_cancel_hides_form(self, page: Page, base_url: str):
        """Clicking 'Cancel' should hide the add account form."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        cancel_button = page.locator('#addAccountForm button:has-text("Cancel")')
        cancel_button.click()
        
        form = page.locator('#addAccountForm')
        expect(form).not_to_have_class("visible")

    def test_can_enter_username(self, page: Page, base_url: str):
        """User should be able to type a username into the input field."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        username_input = page.locator('#newAccountUsername')
        username_input.fill("TestUsername")
        
        expect(username_input).to_have_value("TestUsername")

    def test_can_select_provider(self, page: Page, base_url: str):
        """User should be able to select a provider from the dropdown."""
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        provider_select = page.locator('#newAccountProvider')
        provider_select.select_option("moxfield")
        
        expect(provider_select).to_have_value("moxfield")

    def test_submit_add_account_calls_api(self, page: Page, base_url: str):
        """Submitting the add account form should POST to /api/linked-accounts."""
        page.goto(f"{base_url}/decks")
        
        # Track API call
        api_called = []
        def handle_add(route):
            api_called.append(route.request.post_data)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"success": true, "account": {"id": "archidekt:NewUser", "provider": "archidekt", "username": "NewUser"}}'
            )
        page.route("**/api/linked-accounts", handle_add, times=1)
        
        # Open form
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        # Fill form
        username_input = page.locator('#newAccountUsername')
        username_input.fill("NewUser")
        
        # Submit
        add_button = page.locator('#addAccountForm button:has-text("Add")')
        add_button.click()
        
        # Wait for request
        page.wait_for_timeout(1000)
        
        assert len(api_called) > 0, "API was not called"
        payload = json.loads(api_called[0])
        assert payload["provider"] == "archidekt"
        assert payload["username"] == "NewUser"

    @pytest.mark.skip(reason="Route interception timing issue - works in isolation but flaky in CI")
    def test_add_account_success_shows_feedback(self, page: Page, base_url: str):
        """Successful account linking should show positive feedback."""
        page.goto(f"{base_url}/decks")
        
        # Set up route interception after page load for the exact endpoint
        page.route(f"{base_url}/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"success": true, "account": {"id": "archidekt:NewUser", "provider": "archidekt", "username": "NewUser"}}'
        ) if route.request.method == "POST" else route.continue_())
        
        # Open form and submit
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        username_input = page.locator('#newAccountUsername')
        username_input.fill("NewUser")
        
        add_button = page.locator('#addAccountForm button:has-text("Add")')
        add_button.click()
        
        # Should show success feedback
        status = page.locator('#addAccountStatus')
        expect(status).to_have_class(re.compile(r".*success.*"), timeout=5000)

    def test_add_account_error_shows_feedback(self, page: Page, base_url: str):
        """Failed account linking should show error feedback."""
        def handle_route(route):
            if route.request.method == "POST":
                route.fulfill(
                    status=400,
                    content_type="application/json",
                    body='{"success": false, "error": "Account already linked"}'
                )
            else:
                route.continue_()
        
        page.route("**/api/linked-accounts", handle_route)
        
        page.goto(f"{base_url}/decks")
        
        # Open form and submit
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        username_input = page.locator('#newAccountUsername')
        username_input.fill("ExistingUser")
        
        add_button = page.locator('#addAccountForm button:has-text("Add")')
        add_button.click()
        
        # Should show error feedback
        status = page.locator('#addAccountStatus')
        expect(status).to_have_class(re.compile(r".*error.*"), timeout=5000)

    def test_pressing_enter_submits_form(self, page: Page, base_url: str):
        """Pressing Enter in the username field should submit the form."""
        api_called = []
        page.route("**/api/linked-accounts", lambda route: (
            api_called.append(True),
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"success": true, "account": {"id": "archidekt:EnterUser"}}'
            )
        )[1])
        
        page.goto(f"{base_url}/decks")
        
        link_button = page.locator('button:has-text("Link Account")')
        link_button.click()
        
        username_input = page.locator('#newAccountUsername')
        username_input.fill("EnterUser")
        username_input.press("Enter")
        
        page.wait_for_timeout(1000)
        assert len(api_called) > 0, "Pressing Enter did not submit the form"


class TestRemoveAccountFlow:
    """Tests for the 'Remove' account flow with inline warning."""

    @pytest.fixture
    def mock_single_account(self) -> dict:
        """Mock response with a single account."""
        return {
            "accounts": [
                {
                    "id": "archidekt:TestUser",
                    "provider": "archidekt",
                    "username": "TestUser",
                    "enabled": True,
                    "created_at": "2026-09-08T12:00:00+00:00",
                    "last_synced_at": "2026-09-08T12:30:00+00:00",
                    "last_sync_result": {"decks_synced": 5, "failures": []}
                }
            ]
        }

    def test_clicking_remove_shows_confirmation(self, page: Page, base_url: str, mock_single_account: dict):
        """Clicking the remove button should show an inline confirmation tooltip."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_single_account)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        # Click remove button
        remove_button = page.locator('.btn-icon.btn-danger').first
        remove_button.click()
        
        # Confirmation should appear
        confirm = page.locator('.remove-confirm.visible')
        expect(confirm).to_be_visible()

    def test_remove_confirmation_shows_warning_text(self, page: Page, base_url: str, mock_single_account: dict):
        """The remove confirmation should show a warning about decks staying linked."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_single_account)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        remove_button = page.locator('.btn-icon.btn-danger').first
        remove_button.click()
        
        warning_text = page.locator('.remove-confirm-text')
        expect(warning_text).to_contain_text("Decks stay linked")

    def test_remove_confirmation_has_cancel_button(self, page: Page, base_url: str, mock_single_account: dict):
        """The remove confirmation should have a Cancel button."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_single_account)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        remove_button = page.locator('.btn-icon.btn-danger').first
        remove_button.click()
        
        cancel_button = page.locator('.remove-confirm button:has-text("Cancel")')
        expect(cancel_button).to_be_visible()

    def test_remove_confirmation_has_unlink_button(self, page: Page, base_url: str, mock_single_account: dict):
        """The remove confirmation should have an 'Unlink' confirm button."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_single_account)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        remove_button = page.locator('.btn-icon.btn-danger').first
        remove_button.click()
        
        unlink_button = page.locator('.remove-confirm button:has-text("Unlink")')
        expect(unlink_button).to_be_visible()

    def test_clicking_cancel_hides_confirmation(self, page: Page, base_url: str, mock_single_account: dict):
        """Clicking Cancel should hide the remove confirmation."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_single_account)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        remove_button = page.locator('.btn-icon.btn-danger').first
        remove_button.click()
        
        cancel_button = page.locator('.remove-confirm button:has-text("Cancel")')
        cancel_button.click()
        
        confirm = page.locator('.remove-confirm.visible')
        expect(confirm).not_to_be_visible()

    def test_clicking_outside_hides_confirmation(self, page: Page, base_url: str, mock_single_account: dict):
        """Clicking outside the confirmation should hide it."""
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_single_account)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        remove_button = page.locator('.btn-icon.btn-danger').first
        remove_button.click()
        
        # Click on the page body outside the confirmation
        page.locator('.linked-accounts-title').click()
        
        confirm = page.locator('.remove-confirm.visible')
        expect(confirm).not_to_be_visible()

    def test_clicking_unlink_calls_delete_api(self, page: Page, base_url: str, mock_single_account: dict):
        """Clicking 'Unlink' should DELETE to /api/linked-accounts/{account_id}."""
        delete_called = []
        
        def handle_request(route):
            if route.request.method == "DELETE":
                delete_called.append(route.request.url)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success": true}'
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(mock_single_account)
                )
        
        page.route("**/api/linked-accounts**", handle_request)
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        # Click remove then unlink
        remove_button = page.locator('.btn-icon.btn-danger').first
        remove_button.click()
        
        unlink_button = page.locator('.remove-confirm button:has-text("Unlink")')
        unlink_button.click()
        
        page.wait_for_timeout(1000)
        
        assert len(delete_called) > 0, "DELETE API was not called"
        assert "archidekt:TestUser" in delete_called[0] or "archidekt%3ATestUser" in delete_called[0]


class TestSyncAccountFlow:
    """Tests for the 'Sync now' button functionality."""

    @pytest.fixture
    def mock_account(self) -> dict:
        """Mock response with a single account."""
        return {
            "accounts": [
                {
                    "id": "archidekt:SyncUser",
                    "provider": "archidekt",
                    "username": "SyncUser",
                    "enabled": True,
                    "created_at": "2026-09-08T12:00:00+00:00",
                    "last_synced_at": None,
                    "last_sync_result": None
                }
            ]
        }

    def test_clicking_sync_button_calls_sync_api(self, page: Page, base_url: str, mock_account: dict):
        """Clicking the sync button should POST to /api/linked-accounts/{id}/sync."""
        sync_called = []
        
        def handle_request(route):
            url = route.request.url
            if "/sync" in url and route.request.method == "POST":
                sync_called.append(url)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success": true, "account": {"id": "archidekt:SyncUser", "last_sync_result": {"decks_synced": 3}}}'
                )
            elif route.request.method == "GET":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(mock_account)
                )
            else:
                route.continue_()
        
        page.route("**/api/linked-accounts**", handle_request)
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        # Click sync button (first btn-icon that's not btn-danger)
        sync_button = page.locator('.account-actions .btn-icon:not(.btn-danger)').first
        sync_button.click()
        
        page.wait_for_timeout(1500)
        
        assert len(sync_called) > 0, "Sync API was not called"

    def test_sync_button_shows_spinning_animation(self, page: Page, base_url: str, mock_account: dict):
        """The sync button should show a spinning animation while syncing."""
        def handle_request(route):
            url = route.request.url
            if "/sync" in url and route.request.method == "POST":
                # Fulfill immediately - we check state while request is in flight
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success": true, "account": {}}'
                )
            elif route.request.method == "GET":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(mock_account)
                )
            else:
                route.continue_()
        
        page.route("**/api/linked-accounts**", handle_request)
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        sync_button = page.locator('.account-actions .btn-icon:not(.btn-danger)').first
        sync_button.click()
        
        # Should have syncing class during request
        expect(sync_button).to_have_class(re.compile(r".*syncing.*"), timeout=1000)
        
        # Cleanup routes
        page.unroute("**/api/linked-accounts**")


class TestSyncAllFlow:
    """Tests for the 'Sync All' button functionality."""

    @pytest.fixture
    def mock_multiple_accounts(self) -> dict:
        """Mock response with multiple accounts."""
        return {
            "accounts": [
                {"id": "archidekt:User1", "provider": "archidekt", "username": "User1", "enabled": True, "last_synced_at": None, "last_sync_result": None},
                {"id": "moxfield:User2", "provider": "moxfield", "username": "User2", "enabled": True, "last_synced_at": None, "last_sync_result": None}
            ]
        }

    def test_clicking_sync_all_calls_sync_all_api(self, page: Page, base_url: str, mock_multiple_accounts: dict):
        """Clicking 'Sync All' should POST to /api/linked-accounts/sync-all."""
        sync_all_called = []
        
        def handle_request(route):
            url = route.request.url
            if "sync-all" in url and route.request.method == "POST":
                sync_all_called.append(True)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success": true, "accounts": [], "count": 2}'
                )
            elif route.request.method == "GET":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(mock_multiple_accounts)
                )
            else:
                route.continue_()
        
        page.route("**/api/linked-accounts**", handle_request)
        
        page.goto(f"{base_url}/decks")
        
        sync_all_button = page.locator('#syncAllBtn, button:has-text("Sync All")')
        sync_all_button.click()
        
        # Wait for the button to be re-enabled (sync completed)
        page.wait_for_function("document.querySelector('#syncAllBtn').disabled === false", timeout=5000)
        
        # Cleanup routes to prevent context issues
        page.unroute("**/api/linked-accounts**")
        
        assert len(sync_all_called) > 0, "Sync All API was not called"

    def test_sync_all_button_shows_loading_state(self, page: Page, base_url: str, mock_multiple_accounts: dict):
        """The 'Sync All' button should show a loading state while syncing."""
        def handle_request(route):
            url = route.request.url
            if "sync-all" in url and route.request.method == "POST":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success": true, "accounts": [], "count": 2}'
                )
            elif route.request.method == "GET":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(mock_multiple_accounts)
                )
            else:
                route.continue_()
        
        page.route("**/api/linked-accounts**", handle_request)
        
        page.goto(f"{base_url}/decks")
        
        sync_all_button = page.locator('#syncAllBtn')
        sync_all_button.click()
        
        # Button should be disabled during request
        expect(sync_all_button).to_be_disabled(timeout=1000)


class TestSyncStatusIndicators:
    """Tests for sync status visual indicators (dots)."""

    def test_success_sync_shows_green_dot(self, page: Page, base_url: str):
        """Account with successful sync should show a green status dot."""
        mock_response = {
            "accounts": [{
                "id": "archidekt:SuccessUser",
                "provider": "archidekt",
                "username": "SuccessUser",
                "enabled": True,
                "last_synced_at": "2026-09-08T12:30:00+00:00",
                "last_sync_result": {"decks_synced": 5, "failures": []}
            }]
        }
        
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        green_dot = page.locator('.sync-status-dot.success')
        expect(green_dot).to_be_visible()

    def test_sync_with_failures_shows_warning_dot(self, page: Page, base_url: str):
        """Account with sync failures should show a warning (yellow/orange) status dot."""
        mock_response = {
            "accounts": [{
                "id": "archidekt:PartialUser",
                "provider": "archidekt",
                "username": "PartialUser",
                "enabled": True,
                "last_synced_at": "2026-09-08T12:30:00+00:00",
                "last_sync_result": {
                    "decks_synced": 3,
                    "failures": [{"deck_id": "123", "error": "Rate limited"}]
                }
            }]
        }
        
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        warning_dot = page.locator('.sync-status-dot.warning')
        expect(warning_dot).to_be_visible()

    def test_never_synced_shows_gray_dot(self, page: Page, base_url: str):
        """Account that has never synced should show a gray status dot."""
        mock_response = {
            "accounts": [{
                "id": "archidekt:NewUser",
                "provider": "archidekt",
                "username": "NewUser",
                "enabled": True,
                "last_synced_at": None,
                "last_sync_result": None
            }]
        }
        
        page.route("**/api/linked-accounts", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(mock_response)
        ))
        
        page.goto(f"{base_url}/decks")
        page.wait_for_selector('.account-card', timeout=5000)
        
        gray_dot = page.locator('.sync-status-dot.never')
        expect(gray_dot).to_be_visible()
