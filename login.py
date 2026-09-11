"""Interactive sign-in. Opens a real browser window so you can complete
SSO auth yourself - password, 2FA, security key, whatever it asks for.

Nothing here types your password. You do. This script only waits for you to
finish, then saves the resulting session cookies to state/session.json.

Run it on a machine with a screen, then copy state/session.json to the server:

    tar czf profile.tgz -C state profile
    scp profile.tgz you@your-vps:/path/to/starshot_notif/
    # then on the server:  tar xzf profile.tgz -C state/
"""

import logging
import sys

import config
from scraper import Browser

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("login")

WAIT_SECONDS = config.LOGIN_TIMEOUT


def main() -> int:
    config.ensure_state_dir()

    # Always headed - the whole point is that a human drives this.
    config.HEADLESS = False

    print("\nOpening a browser window.")
    print("Sign in with your account when it appears.")
    print(f"Waiting up to {WAIT_SECONDS // 60} minutes for you to finish.\n")

    with Browser() as browser:
        browser.page.goto(config.TARGET_URL, wait_until="domcontentloaded")

        if browser.wait_until_logged_in(WAIT_SECONDS):
            browser.save_session()
            print(f"\nSigned in. Browser profile saved to {config.PROFILE_DIR}")
            print("To deploy, copy that whole directory to the server:")
            print("  tar czf profile.tgz -C state profile")
            return 0

        print("\nTimed out before sign-in completed. Nothing was saved.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
