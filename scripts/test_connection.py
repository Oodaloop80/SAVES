#!/usr/bin/env python3
"""Smoke test: verify all connections before running the pipeline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Windows consoles default to cp1252, which can't encode the ✅/❌ status glyphs and
# crashes on the first print. Force UTF-8 so the smoke test runs on every platform
# (no-op where stdout is already UTF-8, e.g. Linux/Docker).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import load_config  # noqa: E402
from src.credentials import load_credentials

load_credentials()
config = load_config()
paths = config.get("paths", {})

results = []


def check(name: str, ok: bool, detail: str = ""):
    status = "✅" if ok else "❌"
    msg = f"{status} {name}"
    if detail:
        msg += f": {detail}"
    print(msg)
    results.append(ok)


# 1. Anthropic API
try:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=config["ai"]["model"],
        max_tokens=1,
        messages=[{"role": "user", "content": "ping"}],
    )
    check("Anthropic API", True, config["ai"]["model"])
except Exception as e:
    check("Anthropic API", False, str(e))

# 2. Discord token
try:
    import requests as _requests
    resp = _requests.get(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}"},
        timeout=10,
    )
    if resp.status_code == 200:
        check("Discord bot", True, f"Logged in as {resp.json().get('username')}")
    else:
        check("Discord bot", False, f"HTTP {resp.status_code}: {resp.text[:100]}")
except Exception as e:
    check("Discord bot", False, str(e))

# 3. Reddit JSON API — exercise the SAME path the extractor uses (browser UA +
#    reddit.txt cookies via the shared session). A naive cookieless request gets a
#    Cloudflare 403 and reports a false negative even though extraction works fine.
try:
    from src.extractors.reddit import _SESSION, _load_cookies
    have_cookies = _load_cookies(paths.get("cookies_dir", "cookies"))
    resp = _SESSION.get("https://www.reddit.com/r/test/new.json", timeout=10)
    resp.raise_for_status()
    if "application/json" not in resp.headers.get("Content-Type", ""):
        raise RuntimeError("non-JSON response (Cloudflare challenge) — refresh cookies/reddit.txt")
    check("Reddit JSON API", True, "via extractor session" + ("" if have_cookies else " (no reddit.txt cookies!)"))
except Exception as e:
    check("Reddit JSON API", False, str(e))

# 4. Vault path
vault_root = paths.get("vault_root", "")
check("Vault root exists", os.path.exists(vault_root), vault_root)

# 5. Media root
media_root = paths.get("media_root", "")
check("Media root exists", os.path.exists(media_root), media_root)

print()
passed = sum(results)
total = len(results)
print(f"{passed}/{total} checks passed")
if passed < total:
    sys.exit(1)
