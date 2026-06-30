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

        # Primary path: trafilatura converts the article's main content to clean Markdown,
        # preserving headings, paragraphs, links, and inline images — the same shape the
        # Obsidian Web Clipper produces. We keep a plain-text copy for Claude's analysis and
        # the paywall check. Falls back to readability + tag-stripping if trafilatura yields
        # nothing (so a parse miss degrades gracefully rather than crashing).
        article_markdown = _extract_markdown(html, url)
        if article_markdown:
            clean_text = _markdown_to_text(article_markdown)
        else:
            doc = Document(html)
            clean_text = _html_to_text(doc.summary())
        possible_paywall = len(clean_text) < 200

        meta = _extract_metadata(html, url)

        media_urls = []
        if og.get("og:image"):
            media_urls.append(og["og:image"])

        return ExtractedContent(
            url=url,
            platform="generic",
            title=og.get("og:title") or meta.get("title") or title,
            author=og.get("og:author") or meta.get("author"),
            body_text=clean_text,
            metadata={
                "article_markdown": article_markdown or None,
                "og_description": og.get("og:description") or meta.get("description"),
                "published_time": og.get("article:published_time") or meta.get("date"),
                # Surfaced into the note's frontmatter `posted:` line.
                "upload_date": og.get("article:published_time") or meta.get("date"),
                "possible_paywall": possible_paywall,
                "domain": _domain(url),
            },
            media_urls=media_urls[:10],
        )


def _extract_markdown(html: str, url: str) -> str | None:
    """Extract the main article content as Markdown using trafilatura. Returns None on
    failure so the caller can fall back to readability."""
    try:
        import trafilatura
        # Default precision/recall balance: on real articles this captures the full body
        # cleanly. (favor_recall is deliberately NOT set — it can duplicate paragraphs on
        # some pages, which hurts readability more than the marginal extra recall helps.)
        md = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_images=True,
            include_formatting=True,
            url=url,
        )
        return md.strip() if md and md.strip() else None
    except Exception:
        return None


def _extract_metadata(html: str, url: str) -> dict:
    """Pull author/date/title/description from the page via trafilatura's metadata parser."""
    try:
        import trafilatura
        doc = trafilatura.extract_metadata(html, default_url=url)
        if not doc:
            return {}
        return {
            "title": getattr(doc, "title", None),
            "author": getattr(doc, "author", None),
            "date": getattr(doc, "date", None),
            "description": getattr(doc, "description", None),
        }
    except Exception:
        return {}


def _markdown_to_text(md: str) -> str:
    """Strip Markdown syntax to plain text for Claude's analysis + the paywall check."""
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', md)          # images
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)    # links → link text
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)  # heading markers
    text = re.sub(r'[*_`>]', '', text)                      # emphasis/quote markers
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


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
