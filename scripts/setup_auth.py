"""Capture browser login sessions for Moxfield (and CommanderTemplate).

Run this on a machine WITH A DISPLAY — it opens a real browser you log into.
Your password is never read or stored by this script; only the resulting
session cookies are saved.

    python scripts/setup_auth.py
    python scripts/setup_auth.py --auth-dir ~/commander-lab-auth

The output directory defaults to `.auth` next to the repo, but the repo often
lives on a network share the desktop user cannot write to. Use --auth-dir (or
COMMANDER_LAB_AUTH_DIR) to write somewhere local, then copy the directory to
wherever the app runs, or point MOXFIELD_SESSION_FILE at it.
"""
import argparse
import json
import os

from playwright.sync_api import sync_playwright

DEFAULT_AUTH_DIR = os.getenv("COMMANDER_LAB_AUTH_DIR", ".auth")


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
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--auth-dir", default=DEFAULT_AUTH_DIR,
        help="where to write state.json/moxfield.json "
             f"(default: {DEFAULT_AUTH_DIR}; env: COMMANDER_LAB_AUTH_DIR)",
    )
    args = parser.parse_args()

    auth_dir = os.path.abspath(os.path.expanduser(args.auth_dir))
    state_file = os.path.join(auth_dir, "state.json")

    try:
        os.makedirs(auth_dir, exist_ok=True)
        # Fail here rather than after you've logged in twice.
        probe = os.path.join(auth_dir, ".write-test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.unlink(probe)
    except OSError as exc:
        raise SystemExit(
            f"cannot write to {auth_dir}: {exc}\n"
            "This usually means the repo is on a network share your user can't "
            "write to. Re-run with a local directory, e.g.:\n"
            "    python scripts/setup_auth.py --auth-dir ~/commander-lab-auth"
        )

    print(f"Starting Playwright to capture login sessions...\nOutput dir: {auth_dir}")

    with sync_playwright() as p:
        # Launch visible browser so you can log in
        browser = p.chromium.launch(headless=False)

        # Load existing state if it exists (so you don't have to re-login if we run this again)
        context_args = {}
        if os.path.exists(state_file):
            context_args["storage_state"] = state_file

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
        context.storage_state(path=state_file)
        print(f"\n✅ Success! Session state saved to {state_file}")

        if moxfield_user_id:
            # Written separately from state.json: storage_state's localStorage
            # capture is best-effort per origin, and moxfield_writer.py reads
            # this file first so transcription works even if it's missing there.
            meta_path = os.path.join(auth_dir, "moxfield.json")
            with open(meta_path, "w", encoding="utf-8") as handle:
                json.dump({"userId": moxfield_user_id}, handle)
            print(f"   Moxfield userId saved to {meta_path}")

        if auth_dir != os.path.join(os.getcwd(), ".auth"):
            print("\nNOTE: the app looks for `.auth/` next to the repo.")
            print("Copy it to where the app runs, e.g.:")
            print(f"    cp -r {auth_dir}/. <repo>/.auth/")

        browser.close()


if __name__ == "__main__":
    main()
