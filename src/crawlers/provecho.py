import logging
import re

from src.crawlers.base import SiteCrawler
from src.extractors.generic import _profile_dir_for_url

logger = logging.getLogger(__name__)

_ORIGIN = "https://www.provecho.co"
# A creator entry page: https://www.provecho.co/platform/creator/<handle>
_CREATOR_RE = re.compile(
    r"^https?://(?:www\.)?provecho\.co/platform/creator/([A-Za-z0-9_.-]+)/?$", re.I
)
# An individual recipe link (relative or absolute): /platform/recipe/<id>
_RECIPE_RE = re.compile(r"/platform/recipe/([A-Za-z0-9_-]+)")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class ProvechoCrawler(SiteCrawler):
    """Crawl ONE provecho creator's recipes.

    provecho hosts many creators; `/crawl` is deliberately scoped to a single creator page
    (`/platform/creator/<handle>`) and never traverses beyond it. In practice the creator
    page lists ONLY that creator's recipes (verified: the page carries zero
    `/platform/creator/` links), so collecting every `/platform/recipe/<id>` anchor on it is
    automatically single-creator. The grid is a Next.js SPA that lazy-loads in chunks, so we
    scroll until the recipe count stops growing, then cross-check it against the "<N> Recipes"
    header the page shows. Requires the authenticated persistent profile
    (cookies/provecho.co_profile/) — recipe links only render for a logged-in session.
    """

    name = "provecho"

    @staticmethod
    def matches(url: str) -> bool:
        return bool(_CREATOR_RE.match((url or "").strip()))

    async def discover_urls(self, entry_url: str) -> list[str]:
        m = _CREATOR_RE.match(entry_url.strip())
        if not m:
            raise ValueError(
                "provecho /crawl expects a creator page "
                "(https://www.provecho.co/platform/creator/<handle>), got: " + entry_url
            )
        handle = m.group(1)
        cookies_dir = self.config.get("paths", {}).get("cookies_dir", "cookies")
        profile_dir = _profile_dir_for_url(entry_url, cookies_dir)
        if not profile_dir:
            raise RuntimeError(
                "No provecho.co login profile found. Run:\n"
                "  python scripts/capture_session.py "
                "https://www.provecho.co/platform/login provecho.co"
            )

        ids, shown = await self._scrape_recipe_ids(entry_url, profile_dir)
        urls = [f"{_ORIGIN}/platform/recipe/{rid}" for rid in ids]
        if shown is not None and shown != len(urls):
            logger.warning("provecho crawler: page header says %d recipes but discovered %d "
                           "for creator %s", shown, len(urls), handle)
        logger.info("provecho crawler: discovered %d recipe(s) for creator %s",
                    len(urls), handle)
        return urls

    async def _scrape_recipe_ids(self, creator_url: str, profile_dir: str):
        """Return (ordered unique recipe ids, header_count_or_None)."""
        from playwright.async_api import async_playwright

        pcfg = self.config.get("platforms", {}).get("generic", {})
        timeout = pcfg.get("playwright_timeout_seconds", 30) * 1000

        ids: list[str] = []
        seen: set[str] = set()
        shown: int | None = None

        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                profile_dir,
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
                user_agent=_UA,
                viewport={"width": 1920, "height": 1080},
            )
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                try:
                    await page.goto(creator_url, wait_until="domcontentloaded", timeout=timeout)
                except Exception:
                    await page.wait_for_timeout(3000)
                await page.wait_for_timeout(4000)

                # Scroll until the discovered-id count is stable for 3 consecutive passes.
                prev, stable = -1, 0
                for _ in range(60):
                    hrefs = await page.evaluate(
                        "() => Array.from(document.querySelectorAll("
                        "'a[href*=\"/platform/recipe/\"]')).map(a => a.getAttribute('href'))"
                    )
                    for h in hrefs:
                        mm = _RECIPE_RE.search(h or "")
                        if mm and mm.group(1) not in seen:
                            seen.add(mm.group(1))
                            ids.append(mm.group(1))
                    if len(ids) == prev:
                        stable += 1
                        if stable >= 3:
                            break
                    else:
                        stable = 0
                    prev = len(ids)
                    await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(1500)

                # Cross-check against the visible "<N> Recipes" header, if present.
                try:
                    body = await page.inner_text("body")
                    hm = re.search(r"(\d+)\s+recipes?\b", body, re.I)
                    if hm:
                        shown = int(hm.group(1))
                except Exception:
                    pass
            finally:
                await ctx.close()

        return ids, shown
