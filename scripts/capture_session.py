#!/usr/bin/env python3
"""Capture a full browser session (cookies + localStorage) for a login-required site.

Opens a visible Chromium window so you can log in manually, then saves the complete
browser state to cookies/<name>_session.json. GenericExtractor will prefer this file
over a plain .txt cookie file when both exist for the same domain.

Usage:
    python scripts/capture_session.py <url> <name>

    url   — the site's login page (or any page on the domain)
    name  — the cookie file stem. Use the bare hostname (e.g. "provecho.co") so the
            session covers every path/creator on the domain, not one creator.

Example:
    python scripts/capture_session.py https://www.provecho.co/platform/login provecho.co

After running, log in inside the browser window. Once you are on the logged-in home
page and can see protected content, press Enter in this terminal to save and exit.
The session is saved to cookies/provecho.co_session.json.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


async def capture(url: str, name: str, cookies_dir: str = "cookies") -> None:
    from playwright.async_api import async_playwright

    out_path = os.path.join(cookies_dir, f"{name}_session.json")
    os.makedirs(cookies_dir, exist_ok=True)

    print(f"\nOpening browser for: {url}")
    print("Log in, then press Enter here to save the session.\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
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

        page = await context.new_page()
        await page.goto(url, wait_until="load", timeout=60000)

        # Wait for the user to finish logging in
        await asyncio.get_event_loop().run_in_executor(None, input, "  → Press Enter when logged in: ")

        state = await context.storage_state()
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        await browser.close()

    cookie_count = len(state.get("cookies", []))
    origin_count = len(state.get("origins", []))
    print(f"\nSaved {cookie_count} cookie(s) and {origin_count} localStorage origin(s)")
    print(f"Session file: {out_path}")
    print("\nYou can now run process_one.py — GenericExtractor will use this session automatically.")


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    url, name = sys.argv[1], sys.argv[2]
    asyncio.run(capture(url, name))


if __name__ == "__main__":
    main()
