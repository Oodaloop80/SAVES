#!/usr/bin/env python3
"""Guided browser cookie re-export instructions."""
import sys

INSTRUCTIONS = {
    "instagram": """
Instagram Cookie Refresh
========================
1. Open Firefox or Chrome and log in to instagram.com with your secondary account
2. Install the "Cookie-Editor" browser extension
3. Navigate to instagram.com
4. Open Cookie-Editor → Export → Export as Netscape (cookies.txt format)
5. Save to: saves_app/cookies/instagram.txt
6. Set permissions: chmod 600 cookies/instagram.txt
7. Restart the saves_app container: docker compose restart
""",
    "tiktok": """
TikTok Cookie Refresh
=====================
1. Open Firefox or Chrome and log in to tiktok.com
2. Install "Cookie-Editor" extension
3. Navigate to tiktok.com
4. Open Cookie-Editor → Export → Export as Netscape
5. Save to: saves_app/cookies/tiktok.txt
6. Set permissions: chmod 600 cookies/tiktok.txt
7. Restart: docker compose restart
""",
    "facebook": """
Facebook Cookie Refresh
=======================
1. Open Firefox or Chrome and log in to facebook.com
2. Install "Cookie-Editor" extension
3. Navigate to facebook.com
4. Open Cookie-Editor → Export → Export as Netscape
5. Save to: saves_app/cookies/facebook.txt
6. Set permissions: chmod 600 cookies/facebook.txt
7. Restart: docker compose restart
""",
    "reddit": """
Reddit Cookie Refresh
=====================
1. Open Firefox or Chrome and log in to reddit.com
2. Install "Cookie-Editor" extension
3. Navigate to reddit.com
4. Open Cookie-Editor → Export → Export as Netscape
5. Save to: saves_app/cookies/reddit.txt
6. Set permissions: chmod 600 cookies/reddit.txt
7. Restart: docker compose restart
""",
}

ALL_PLATFORMS = list(INSTRUCTIONS.keys())

if __name__ == "__main__":
    platform = sys.argv[1].lower() if len(sys.argv) > 1 else None
    if platform and platform in INSTRUCTIONS:
        print(INSTRUCTIONS[platform])
    elif platform == "all":
        for inst in INSTRUCTIONS.values():
            print(inst)
    else:
        print("Usage: python scripts/refresh_cookies.py <platform>")
        print(f"Platforms: {', '.join(ALL_PLATFORMS)}, all")
