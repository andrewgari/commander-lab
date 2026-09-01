import os
from playwright.sync_api import sync_playwright

AUTH_DIR = ".auth"
STATE_FILE = os.path.join(AUTH_DIR, "state.json")

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

        # CommanderTemplate Auth
        print("\n=== COMMANDERTEMPLATE ===")
        page.goto("https://commandertemplate.com/")
        input("1. Log in to CommanderTemplate in the browser.\n2. Once you are fully logged in, press ENTER here to save the sessions...")

        # Save the authenticated session state
        context.storage_state(path=STATE_FILE)
        print(f"\n✅ Success! Session state saved to {os.path.abspath(STATE_FILE)}")
        
        browser.close()

if __name__ == "__main__":
    main()
