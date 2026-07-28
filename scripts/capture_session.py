#!/usr/bin/env python3
"""Capture a full logged-in browser profile for a login-required site.

Opens a visible Chromium window backed by a PERSISTENT profile directory so you can log
in manually; on exit the whole browser state — cookies, localStorage, sessionStorage,
AND IndexedDB — is left on disk in cookies/<name>_profile/. GenericExtractor prefers that
profile over a plain .txt cookie file / _session.json when one exists for the same domain.

Why a persistent profile (not a JSON export): sites built on Firebase Authentication
(e.g. provecho.co) keep their auth token in IndexedDB, which neither a Netscape .txt
export nor Playwright's storage_state() captures. A real on-disk profile carries IndexedDB
naturally, so a headless relaunch with the same profile is still logged in.

Usage:
    python scripts/capture_session.py <url> <name>

    url   — the site's login page (or any page on the domain)
    name  — the profile stem. Use the bare hostname (e.g. "provecho.co") so the profile
            covers every path/creator on the domain, not one creator.

Example:
    python scripts/capture_session.py https://www.provecho.co/platform/login provecho.co

After running, log in inside the browser window, open a protected page (e.g. a recipe)
to CONFIRM it is unlocked, then press Enter in this terminal to save and exit. The profile
is stored in cookies/provecho.co_profile/.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# Reads the number of records in Firebase's auth IndexedDB store, so we can confirm the
# login actually persisted before the user walks away. Returns the record count, or a
# negative sentinel: -1 = no firebaseLocalStorageDb (site may not use Firebase), other
# negatives = probe error.
_FIREBASE_AUTH_COUNT_JS = """async () => {
    const names = (await indexedDB.databases()).map(d => d.name);
    if (!names.includes('firebaseLocalStorageDb')) return -1;
    return await new Promise((resolve) => {
        const req = indexedDB.open('firebaseLocalStorageDb');
        req.onerror = () => resolve(-2);
        req.onsuccess = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains('firebaseLocalStorage')) { db.close(); resolve(0); return; }
            const tx = db.transaction('firebaseLocalStorage', 'readonly');
            const c = tx.objectStore('firebaseLocalStorage').count();
            c.onsuccess = () => { db.close(); resolve(c.result); };
            c.onerror = () => { db.close(); resolve(-3); };
        };
    });
}"""


async def capture(url: str, name: str, cookies_dir: str = "cookies") -> None:
    from playwright.async_api import async_playwright

    profile_dir = os.path.join(cookies_dir, f"{name}_profile")
    os.makedirs(profile_dir, exist_ok=True)

    print(f"\nOpening browser for: {url}")
    print(f"Profile dir: {profile_dir}")
    print("Log in, open a recipe to confirm it is UNLOCKED, then press Enter here to save.\n")

    async with async_playwright() as p:
        # A persistent context writes the whole profile (incl. IndexedDB) to profile_dir.
        context = await p.chromium.launch_persistent_context(
            profile_dir,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="load", timeout=60000)

        # Wait for the user to finish logging in
        await asyncio.get_event_loop().run_in_executor(None, input, "  → Press Enter when logged in: ")

        # Best-effort confirmation that Firebase auth actually persisted to the profile.
        try:
            auth_count = await page.evaluate(_FIREBASE_AUTH_COUNT_JS)
        except Exception:
            auth_count = -9

        await context.close()

    print(f"\nProfile saved to: {profile_dir}")
    if auth_count > 0:
        print(f"Firebase auth records in profile: {auth_count}  ✓ (looks logged in)")
    elif auth_count == -1:
        print("No firebaseLocalStorageDb found — this site may not use Firebase auth. That is")
        print("fine as long as the protected page was visible before you pressed Enter.")
    else:
        print("WARNING: no Firebase auth record captured. Make sure you were logged in and a")
        print("protected page was UNLOCKED before pressing Enter, then re-run this script.")
    print("\nRun process_one.py — GenericExtractor will use this profile automatically.")


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    url, name = sys.argv[1], sys.argv[2]
    asyncio.run(capture(url, name))


if __name__ == "__main__":
    main()
