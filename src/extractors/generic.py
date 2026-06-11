import asyncio
import re

from src.extractors.base import BaseExtractor, ExtractedContent

COOKIE_BANNER_SELECTORS = [
    "#accept", "#accept-cookies", "#acceptCookies",
    ".accept-cookies", ".cookie-accept", ".btn-accept",
    "[aria-label*='accept' i]", "[aria-label*='agree' i]",
    "button:has-text('Accept')", "button:has-text('Accept All')",
    "button:has-text('I agree')", "button:has-text('Got it')",
]


class GenericExtractor(BaseExtractor):
    def __init__(self, config: dict):
        self.config = config
        pcfg = config.get("platforms", {}).get("generic", {})
        self.timeout = pcfg.get("playwright_timeout_seconds", 30) * 1000
        self.wait_network_idle = pcfg.get("wait_for_network_idle", True)
        self.auto_click_banners = pcfg.get("auto_click_cookie_banners", True)

    def can_handle(self, url: str) -> bool:
        return True

    async def extract(self, url: str) -> ExtractedContent:
        from playwright.async_api import async_playwright
        from readability import Document

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            wait = "networkidle" if self.wait_network_idle else "load"
            try:
                await page.goto(url, wait_until=wait, timeout=self.timeout)
            except Exception:
                await page.wait_for_timeout(3000)

            if self.auto_click_banners:
                for sel in COOKIE_BANNER_SELECTORS:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=500):
                            await btn.click(timeout=500)
                            await page.wait_for_timeout(800)
                            break
                    except Exception:
                        continue

            html = await page.content()
            title = await page.title()

            og = {}
            for prop in ["og:title", "og:description", "og:image", "og:author",
                         "article:published_time"]:
                try:
                    val = await page.get_attribute(f'meta[property="{prop}"]', "content", timeout=200)
                    if val:
                        og[prop] = val
                except Exception:
                    pass

            await browser.close()

        doc = Document(html)
        clean_text = _html_to_text(doc.summary())
        possible_paywall = len(clean_text) < 200

        media_urls = []
        if og.get("og:image"):
            media_urls.append(og["og:image"])
        for img in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', doc.summary()):
            if img not in media_urls and img.startswith("http"):
                media_urls.append(img)

        return ExtractedContent(
            url=url,
            platform="generic",
            title=og.get("og:title") or title,
            author=og.get("og:author"),
            body_text=clean_text,
            metadata={
                "og_description": og.get("og:description"),
                "published_time": og.get("article:published_time"),
                "possible_paywall": possible_paywall,
                "domain": _domain(url),
            },
            media_urls=media_urls[:10],
        )


def _html_to_text(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _domain(url: str) -> str:
    import urllib.parse
    return urllib.parse.urlparse(url).netloc.lstrip("www.")
