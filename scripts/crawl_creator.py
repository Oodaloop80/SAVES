#!/usr/bin/env python3
"""Crawl one creator's recipes and (optionally) queue them through the pipeline.

Discovers every recipe URL on a single creator/index page (scoped to that creator — never
the whole site), dedups against processing_state.json, and prints a summary. By default it
is a DRY RUN (prints the URLs, queues nothing). With --to-inbox it appends the new URLs to
the inbox file so a running `python src/main.py` picks them up and posts an approval card
for each — the same path a manual paste takes.

Usage:
    python scripts/crawl_creator.py <creator-url>              # dry run: list only
    python scripts/crawl_creator.py <creator-url> --to-inbox   # append new URLs to inbox

Example:
    python scripts/crawl_creator.py https://www.provecho.co/platform/creator/davespizzaoven
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("src").setLevel(logging.INFO)

from src.config import load_config  # noqa: E402
from src.crawlers import get_crawler  # noqa: E402
from src.queue_manager import ProcessingState  # noqa: E402


async def run(creator_url: str, to_inbox: bool = False):
    config = load_config()
    crawler = get_crawler(creator_url, config)
    if crawler is None:
        print(f"No site crawler handles this URL:\n  {creator_url}")
        print("Supported today: provecho.co creator pages "
              "(https://www.provecho.co/platform/creator/<handle>).")
        sys.exit(1)

    paths = config.get("paths", {})
    state = ProcessingState(paths.get("state_file", "processing_state.json"))

    print(f"Crawler: {crawler.name}")
    print(f"Discovering recipes on: {creator_url}")
    urls = await crawler.discover_urls(creator_url)
    new, dup = crawler.partition(urls, state)

    max_recipes = config.get("crawl", {}).get("max_recipes", 300)
    capped = new[:max_recipes]

    print(f"\n=== Found {len(urls)} recipe(s): {len(new)} new, {len(dup)} already saved ===")
    if len(capped) < len(new):
        print(f"  (capped to crawl.max_recipes={max_recipes}; {len(new) - len(capped)} not shown)")
    for u in capped:
        print(f"  NEW  {u}")
    for u in dup[:10]:
        print(f"  seen {u}")
    if len(dup) > 10:
        print(f"  ... and {len(dup) - 10} more already saved")

    if not to_inbox:
        print("\n[dry-run] nothing queued. Re-run with --to-inbox to feed the pipeline.")
        return

    inbox = paths.get("inbox_file", "")
    if not inbox:
        print("\nNo paths.inbox_file configured — cannot append.")
        sys.exit(1)
    if not capped:
        print("\nNothing new to append.")
        return
    os.makedirs(os.path.dirname(inbox), exist_ok=True)
    with open(inbox, "a", encoding="utf-8") as f:
        for u in capped:
            f.write(u + "\n")
    print(f"\nAppended {len(capped)} new URL(s) to {inbox}")
    print("A running `python src/main.py` will process them (one approval card each).")


def main():
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__)
        sys.exit(1)
    to_inbox = "--to-inbox" in args
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print(__doc__)
        sys.exit(1)
    asyncio.run(run(positional[0], to_inbox=to_inbox))


if __name__ == "__main__":
    main()
