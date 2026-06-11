#!/usr/bin/env python3
"""Smoke test: verify all connections before running the pipeline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import load_config
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
    import urllib.request, json as _json
    req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = _json.loads(resp.read())
    check("Discord bot", True, f"Logged in as {data.get('username')}")
except Exception as e:
    check("Discord bot", False, str(e))

# 3. Vault path
vault_root = paths.get("vault_root", "")
check("Vault root exists", os.path.exists(vault_root), vault_root)

# 4. Media root
media_root = paths.get("media_root", "")
check("Media root exists", os.path.exists(media_root), media_root)

# 5. PRAW
try:
    import praw
    reddit = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ["REDDIT_USER_AGENT"],
    )
    sub = reddit.subreddit("test")
    _ = sub.display_name
    check("Reddit PRAW", True)
except Exception as e:
    check("Reddit PRAW", False, str(e))

print()
passed = sum(results)
total = len(results)
print(f"{passed}/{total} checks passed")
if passed < total:
    sys.exit(1)
