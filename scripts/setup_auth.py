import json
import os

from playwright.sync_api import sync_playwright

AUTH_DIR = ".auth"
STATE_FILE = os.path.join(AUTH_DIR, "state.json")


def capture_moxfield_user_id(page):
    """Read the signed-in Moxfield userId out of localStorage.

    providers/moxfield_writer.py needs it for
    `POST /v1/account/token/refresh` (Moxfield's own client sends it as
    `X-Session-User`). The key it lives under has changed between Moxfield
    releases, so scan every localStorage value for a `userId` field rather
    than hardcoding a key name. Returns None if not found -- the caller can
    still fall back to MOXFIELD_USER_ID in .env.
    """
    try:
        return page.evaluate(
            """() => {
                for (let i = 0; i < localStorage.length; i++) {
                    const raw = localStorage.getItem(localStorage.key(i));
                    if (!raw) continue;
                    try {
                        const parsed = JSON.parse(raw);
                        if (parsed && parsed.userId) return String(parsed.userId);
                    } catch (e) { /* not JSON, skip */ }
                }
                return null;
            }"""
        )
    except Exception as exc:  # noqa: BLE001 - never block the save on this
        print(f"  (could not read Moxfield userId automatically: {exc})")
        return None


def main():
    os.makedirs(AUTH_DIR, exist_ok=True)
    print("Starting Playwright to capture login sessions...")

    with sync_playwright() as p:
        # Launch visible browser so you can log in
        browser = p.chromium.launch(headless=False)

        # Load existing state if it exists (so you don't have to re-login if we run this again)
        context_args = {}
        if os.path.exists(STATE_FILE):
            context_args["storage_state"] = STATE_FILE

        context = browser.new_context(**context_args)
        page = context.new_page()

        # Moxfield Auth
        print("\n=== MOXFIELD ===")
        page.goto("https://www.moxfield.com/")
        input("1. Log in to Moxfield in the opened browser window.\n2. Once you are fully logged in and on the dashboard, press ENTER here to continue...")

        moxfield_user_id = capture_moxfield_user_id(page)
        if moxfield_user_id:
            print(f"  Captured Moxfield userId: {moxfield_user_id}")
        else:
            print("  WARNING: no Moxfield userId found in localStorage. If deck")
            print("  transcription fails with a userId error, set MOXFIELD_USER_ID in .env.")

        # CommanderTemplate Auth
        print("\n=== COMMANDERTEMPLATE ===")
        page.goto("https://commandertemplate.com/")
        input("1. Log in to CommanderTemplate in the browser.\n2. Once you are fully logged in, press ENTER here to save the sessions...")

        # Save the authenticated session state
        context.storage_state(path=STATE_FILE)
        print(f"\n✅ Success! Session state saved to {os.path.abspath(STATE_FILE)}")

        if moxfield_user_id:
            # Written separately from state.json: storage_state's localStorage
            # capture is best-effort per origin, and moxfield_writer.py reads
            # this file first so transcription works even if it's missing there.
            meta_path = os.path.join(AUTH_DIR, "moxfield.json")
            with open(meta_path, "w", encoding="utf-8") as handle:
                json.dump({"userId": moxfield_user_id}, handle)
            print(f"   Moxfield userId saved to {os.path.abspath(meta_path)}")

        browser.close()


if __name__ == "__main__":
    main()
