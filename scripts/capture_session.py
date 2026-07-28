#!/usr/bin/env python3
"""Capture a full browser session (cookies + localStorage) for a login-required site.

Opens a visible Chromium window so you can log in manually, then saves the complete
browser state to cookies/<name>_session.json. GenericExtractor will prefer this file
over a plain .txt cookie file when both exist for the same domain.

Captures cookies + localStorage (via Playwright's storage_state) AND sessionStorage
(read explicitly — storage_state does NOT include it). sessionStorage is where some
SPAs, including provecho.co, keep the auth token, so grabbing it is what makes a
logged-in recipe actually unlock on replay.

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

        # storage_state() captures cookies + localStorage but NOT sessionStorage. Read
        # sessionStorage from every open page in the context and merge it into the saved
        # state under the matching origin, so the auth token (which provecho.co keeps in
        # sessionStorage) survives into the file. Reading from all pages handles a login
        # that opened the recipe in a second tab.
        ss_by_origin: dict[str, dict[str, str]] = {}
        for pg in context.pages:
            try:
                origin = await pg.evaluate("() => window.location.origin")
                items = await pg.evaluate(
                    "() => Object.entries(sessionStorage).map(([name, value]) => ({name, value}))"
                )
            except Exception:
                continue
            if items:
                bucket = ss_by_origin.setdefault(origin, {})
                for it in items:
                    bucket[it["name"]] = it["value"]
        for origin, kv in ss_by_origin.items():
            entry = next((o for o in state.get("origins", []) if o.get("origin") == origin), None)
            if entry is None:
                entry = {"origin": origin, "localStorage": []}
                state.setdefault("origins", []).append(entry)
            entry["sessionStorage"] = [{"name": k, "value": v} for k, v in kv.items()]

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        await browser.close()

    cookie_count = len(state.get("cookies", []))
    origin_count = len(state.get("origins", []))
    ss_keys = [i["name"] for o in state.get("origins", []) for i in o.get("sessionStorage", [])]
    print(f"\nSaved {cookie_count} cookie(s), {origin_count} localStorage origin(s), "
          f"{len(ss_keys)} sessionStorage key(s)")
    if ss_keys:
        print("  sessionStorage keys:", ", ".join(ss_keys))
    else:
        print("  WARNING: no sessionStorage captured — if the recipe stays locked, the auth")
        print("  token may live elsewhere. Make sure you were logged in when you pressed Enter.")
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
