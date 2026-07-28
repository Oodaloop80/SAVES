import json
import logging
import os
import re
import urllib.parse

from src.extractors.base import BaseExtractor, ExtractedContent
from src.utils.recipe_data import extract_recipe_jsonld

logger = logging.getLogger(__name__)

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
        self.cookies_dir = config.get("paths", {}).get("cookies_dir", "cookies")

    def can_handle(self, url: str) -> bool:
        return True

    async def extract(self, url: str) -> ExtractedContent:
        from playwright.async_api import async_playwright
        from readability import Document

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-service-autorun",
            "--password-store=basic",
        ]
        context_opts = dict(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )

        async with async_playwright() as p:
            # A captured persistent profile (cookies/<host>_profile/, made by
            # scripts/capture_session.py) is preferred when one exists for this domain: it
            # carries the FULL logged-in browser state on disk — crucially IndexedDB, where
            # Firebase-auth sites (provecho.co) keep their token. Cookies, localStorage, and
            # sessionStorage don't hold that token, so a portable .txt/_session.json can't
            # authenticate those sites; a real on-disk profile does. Everything else uses the
            # ephemeral launch + optional _session.json / .txt cookie loading below.
            profile_dir = _profile_dir_for_url(url, self.cookies_dir)
            session_state = None
            if profile_dir:
                context = await p.chromium.launch_persistent_context(
                    profile_dir, headless=True, args=launch_args, **context_opts
                )
                browser = None
                logger.info("generic extractor: using persistent profile %s for %s",
                            profile_dir, urllib.parse.urlparse(url).netloc)
            else:
                browser = await p.chromium.launch(headless=True, args=launch_args)
                context = await browser.new_context(**context_opts)
            # Mask the automation flag that Cloudflare and other bot-detection scripts check.
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            # For non-profile domains, load a portable session if one exists for this domain:
            # a _session.json (cookies + localStorage + sessionStorage, seeded after goto) is
            # preferred over a plain Netscape .txt cookie file. Skipped entirely in profile
            # mode — the profile already carries all of it.
            if not profile_dir:
                session_state = _load_session_for_url(url, self.cookies_dir)
                if session_state:
                    if "cookies" in session_state:
                        await context.add_cookies(session_state["cookies"])
                    logger.info("generic extractor: loaded session state for %s",
                                urllib.parse.urlparse(url).netloc)
                else:
                    pw_cookies = _load_cookies_for_url(url, self.cookies_dir)
                    if pw_cookies:
                        await context.add_cookies(pw_cookies)
                        logger.info("generic extractor: loaded %d cookie(s) for %s",
                                    len(pw_cookies), urllib.parse.urlparse(url).netloc)
            page = await context.new_page()
            wait = "networkidle" if self.wait_network_idle else "load"
            try:
                await page.goto(url, wait_until=wait, timeout=self.timeout)
            except Exception:
                await page.wait_for_timeout(3000)

            # Seed localStorage + sessionStorage from a portable _session.json (non-profile
            # domains only; session_state is None in profile mode). Must run after goto (both
            # are origin-scoped and cannot be written before navigation), then reload so the
            # page reads them on startup. NOTE: this does NOT cover IndexedDB-based auth
            # (Firebase, e.g. provecho.co) — those require a persistent profile above.
            if session_state:
                seeded = await _seed_web_storage(page, session_state)
                if seeded:
                    try:
                        await page.reload(wait_until=wait, timeout=self.timeout)
                    except Exception:
                        await page.wait_for_timeout(3000)

            # If Cloudflare's JS challenge is still running ("Just a moment..."), wait up
            # to 15 s for it to resolve before capturing the page.
            try:
                current_title = (await page.title()).lower()
                if "just a moment" in current_title or "checking your browser" in current_title:
                    logger.info("Cloudflare challenge detected — waiting up to 15s for resolution")
                    await page.wait_for_function(
                        "() => !document.title.toLowerCase().includes('just a moment') "
                        "     && !document.title.toLowerCase().includes('checking your browser')",
                        timeout=15000,
                    )
                    await page.wait_for_timeout(2000)
            except Exception:
                pass  # If the wait times out, proceed anyway and capture what we got

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

            # Most article sites LAZY-LOAD in-body images: the real URL only lands in
            # <img src> once the image scrolls into view, so a no-scroll capture leaves them
            # as empty/placeholder src and we collect none of them. Scroll the full page to
            # trigger the lazy loaders, then promote any data-src/srcset URLs into src so
            # trafilatura sees real image URLs.
            try:
                await page.evaluate(
                    """async () => {
                        await new Promise((resolve) => {
                            let total = 0;
                            const step = 600;
                            const timer = setInterval(() => {
                                window.scrollBy(0, step);
                                total += step;
                                if (total >= document.body.scrollHeight - window.innerHeight) {
                                    clearInterval(timer);
                                    window.scrollTo(0, 0);
                                    resolve();
                                }
                            }, 120);
                        });
                    }"""
                )
                await page.wait_for_timeout(2500)
                # Resolve every <img> to a real URL so trafilatura sees it. We try, in order:
                #   1) currentSrc — the URL the browser actually LOADED (covers srcset + most
                #      lazy loaders once the image has scrolled into view)
                #   2) data-src / data-lazy-src / data-original / data-img-url
                #   3) data-srcset / srcset (largest candidate)
                #   4) a sibling/ancestor <noscript> fallback (common WordPress lazy plugins
                #      keep the real <img src> inside <noscript> for no-JS users)
                await page.evaluate(
                    """() => {
                        const fromSrcset = (ss) => {
                            if (!ss) return '';
                            const parts = ss.split(',').map((s) => s.trim().split(/\\s+/)[0]).filter(Boolean);
                            return parts.length ? parts[parts.length - 1] : '';
                        };
                        document.querySelectorAll('img').forEach((img) => {
                            const cur = img.getAttribute('src') || '';
                            let url = '';
                            if (img.currentSrc && !img.currentSrc.startsWith('data:')) url = img.currentSrc;
                            if (!url) url = img.getAttribute('data-src') || img.getAttribute('data-lazy-src')
                                || img.getAttribute('data-original') || img.getAttribute('data-img-url') || '';
                            if (!url) url = fromSrcset(img.getAttribute('data-srcset') || img.getAttribute('srcset'));
                            if (!url) {
                                let sc = img.parentElement;
                                for (let d = 0; d < 3 && sc; d++, sc = sc.parentElement) {
                                    const ns = sc.querySelector && sc.querySelector('noscript');
                                    if (ns) {
                                        const m = ns.textContent.match(/<img[^>]+src=[\"']([^\"']+)[\"']/i);
                                        if (m) { url = m[1]; break; }
                                    }
                                }
                            }
                            if (url && !url.startsWith('data:') && url !== cur) {
                                img.setAttribute('src', url);
                                img.removeAttribute('srcset');
                                img.removeAttribute('loading');
                            }
                        });
                    }"""
                )
                # Strip class/id from image-WRAPPER elements (the <picture>/<div>/<figure>
                # chrome that holds only the image, no text). trafilatura discards nodes whose
                # class matches its UI-junk rules (e.g. XDA wraps images in
                # class="...image-expandable..." and "expandable" matches the discard list),
                # taking the nested <img> with them. Clearing only wrapper classes — never
                # text containers — lets trafilatura keep the images without affecting which
                # node it picks as the main content.
                await page.evaluate(
                    """() => {
                        document.querySelectorAll('img').forEach((img) => {
                            const s = img.currentSrc || img.getAttribute('src') || '';
                            if (!s || s.startsWith('data:')) return;
                            img.removeAttribute('class');
                            let el = img.parentElement;
                            for (let d = 0; d < 5 && el; d++, el = el.parentElement) {
                                const tag = el.tagName.toLowerCase();
                                if (!['picture', 'div', 'span', 'a', 'figure'].includes(tag)) break;
                                // Only strip wrappers holding essentially just the image (no
                                // real text of their own) so text/content containers are safe.
                                if (tag !== 'picture' && (el.textContent || '').trim().length > 0) break;
                                el.removeAttribute('class');
                                el.removeAttribute('id');
                            }
                        });
                    }"""
                )
            except Exception:
                pass

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

            if browser is not None:
                await browser.close()
            else:
                await context.close()

        # Diagnostic: how many <img> tags carry a real http(s) src after lazy-resolution.
        # Compared with the inline-image count below, this localizes where images are lost:
        #   many http imgs but 0 in markdown → trafilatura pruned them (extraction issue)
        #   0 http imgs                       → lazy-load resolution failed (capture issue)
        total_imgs = len(re.findall(r'<img\b', html, re.I))
        http_imgs = len(re.findall(r'<img\b[^>]*\bsrc=["\']https?://', html, re.I))
        logger.info("generic extractor: %d <img> tag(s) in HTML, %d with http src", total_imgs, http_imgs)

        # Primary path: trafilatura converts the article's main content to clean Markdown,
        # preserving headings, paragraphs, links, and inline images — the same shape the
        # Obsidian Web Clipper produces. We keep a plain-text copy for Claude's analysis and
        # the paywall check. Falls back to readability + tag-stripping if trafilatura yields
        # nothing (so a parse miss degrades gracefully rather than crashing).
        article_markdown = _extract_markdown(html, url)
        media_urls = []
        if article_markdown:
            article_markdown = _normalize_markdown(article_markdown)
            article_markdown = _trim_trailing_chrome(article_markdown)

            # Lead the article body with the feature image (og:image), routed through the
            # SAME local-image pipeline as the inline images (downloaded + embedded by the
            # localizer) so the title picture is archived too. Skip if it's already present
            # in the body. Because every image is now embedded inline, we clear media_urls so
            # the separate download step doesn't redundantly (and sometimes flakily) re-fetch.
            hero = og.get("og:image")
            if hero and hero not in article_markdown:
                article_markdown = f"![]({hero})\n\n{article_markdown}"
            logger.info("generic extractor: trafilatura produced %d inline image(s) in markdown",
                        article_markdown.count("!["))
            clean_text = _markdown_to_text(article_markdown)
        else:
            doc = Document(html)
            clean_text = _html_to_text(doc.summary())
            if og.get("og:image"):
                media_urls.append(og["og:image"])
        possible_paywall = len(clean_text) < 200

        meta = _extract_metadata(html, url)

        # schema.org Recipe JSON-LD: the exact ingredient list + steps the author entered.
        # Far more reliable than scraping the rendered body (which buries the recipe under a
        # long story preamble). Benefits direct recipe-page pastes and followed bio recipes.
        recipe_data = extract_recipe_jsonld(html)

        return ExtractedContent(
            url=url,
            platform="generic",
            title=og.get("og:title") or meta.get("title") or title,
            author=og.get("og:author") or meta.get("author"),
            body_text=clean_text,
            metadata={
                "article_markdown": article_markdown or None,
                "recipe_data": recipe_data,
                "og_description": og.get("og:description") or meta.get("description"),
                "published_time": og.get("article:published_time") or meta.get("date"),
                # Surfaced into the note's frontmatter `posted:` line.
                "upload_date": og.get("article:published_time") or meta.get("date"),
                "possible_paywall": possible_paywall,
                "domain": _domain(url),
            },
            media_urls=media_urls[:10],
        )


# Below this many characters we treat a Readability extraction as a "miss" (e.g. a paywalled or
# JS-only page it couldn't isolate) and fall back to trafilatura.
_READABILITY_MIN_CHARS = 400


def _extract_markdown(html: str, url: str) -> str | None:
    """Extract the main article content as clean Markdown, mimicking the Obsidian Web Clipper.

    Primary path = Mozilla Readability (isolate the main article, drop nav/ads/chrome) + our
    lxml Turndown-style serializer (``utils.html_to_markdown``) — the exact stack the Web
    Clipper uses. It emits faithful `#`..`######` headings, bold run-ins with correct spacing,
    intact links, lists, and lazy-resolved images, and promotes FAQ accordion titles to
    headings. trafilatura (precision mode) is kept as a fallback for pages Readability
    under-extracts. Returns None only if both converters come up empty."""
    read_md = _readability_markdown(html, url)
    traf_md = _trafilatura_markdown(html, url)
    if read_md and len(read_md) >= _READABILITY_MIN_CHARS:
        # Guard against Readability under-extraction (e.g. a listicle where it grabbed only the
        # intro): if trafilatura found substantially more content, trust trafilatura instead.
        if traf_md and len(traf_md) > len(read_md) * 1.8:
            return traf_md
        return read_md
    return traf_md or read_md or None


def _readability_markdown(html: str, url: str) -> str | None:
    """Readability isolates the article; ``html_to_markdown`` serializes it Web-Clipper-style."""
    try:
        from readability import Document

        from src.utils.html_to_markdown import html_to_markdown

        summary = Document(html).summary(html_partial=True)
        md = html_to_markdown(summary)
        return md if md and md.strip() else None
    except Exception:
        logger.debug("readability+serializer markdown extraction failed", exc_info=True)
        return None


def _trafilatura_markdown(html: str, url: str) -> str | None:
    """Fallback converter. trafilatura's default (precision-favoring) mode excludes site chrome
    and places inline images correctly; on listicles it keeps every content image. Used when
    Readability under-extracts."""
    try:
        import trafilatura
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


_LIST_ITEM_RE = re.compile(r'(?:[-*+]\s|\d+[.)]\s)')

# Heading text that marks the end of real article content on most sites: reader comments,
# newsletter/subscribe prompts, and "related/more" link farms. Matched only against Markdown
# HEADING lines — real article bodies almost never use these exact phrases as headings, so
# this trims trailing site chrome (e.g. Future plc's "Join the Conversation" / "All Comments")
# without touching listicle section headings like "## Negroni".
_TRAILING_CHROME_RE = re.compile(
    r"^\s*#{1,6}\s*("
    r"join the conversation|all comments|comments|leave a (comment|reply)|post a comment|"
    r"related( articles| stories| posts| reading| content| topics)?|"
    r"more (from|stories|on|to explore)|you might( also)? like|recommended( for you)?|"
    r"most popular|trending( now)?|sign up|subscribe|newsletter|follow us"
    r")\s*$",
    re.I,
)

# Don't trim if the chrome heading appears before this many characters of article body —
# guards against nuking a genuinely short article whose first heading happens to match.
_MIN_KEEP_CHARS = 400


def _trim_trailing_chrome(md: str) -> str:
    """Cut the article at the first comment/subscribe/related-links heading once enough real
    body has accumulated, so trailing site chrome trafilatura leaves in doesn't reach the note."""
    lines = md.splitlines()
    offset = 0
    for i, line in enumerate(lines):
        if _TRAILING_CHROME_RE.match(line) and offset >= _MIN_KEEP_CHARS:
            return "\n".join(lines[:i]).rstrip()
        offset += len(line) + 1
    return md


def _normalize_markdown(md: str) -> str:
    """Clean trafilatura's Markdown so Obsidian renders it as prose:

    - De-indent lines with 4+ leading spaces (outside fenced code blocks). Markdown treats
      a 4-space indent as a code block; trafilatura sometimes indents paragraphs that follow
      images/figures, which would otherwise render the article body as monospace code. Real
      code from trafilatura comes in fenced ``` blocks, so a bare 4-space indent is always
      spurious here. Nested list items keep their indentation.
    - Strip trailing whitespace (also removes the stray space trafilatura leaves after inline
      images, so image replacement stays clean).
    - Collapse 3+ blank lines to a single blank line.
    """
    out, in_fence = [], False
    for line in md.splitlines():
        stripped_lead = line.lstrip(" ")
        if stripped_lead.startswith("```"):
            in_fence = not in_fence
            out.append(stripped_lead.rstrip())
            continue
        if in_fence:
            out.append(line.rstrip())
            continue
        indent = len(line) - len(stripped_lead)
        if indent >= 4 and not _LIST_ITEM_RE.match(stripped_lead):
            out.append(stripped_lead.rstrip())
        else:
            out.append(line.rstrip())
    text = "\n".join(out)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


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
    return urllib.parse.urlparse(url).netloc.lstrip("www.")


def _profile_dir_for_url(url: str, cookies_dir: str) -> str | None:
    """Find a persistent browser-profile directory for the URL's domain.

    Looks for a `<stem>_profile/` directory where stem equals the hostname / bare hostname or
    appears as a URL path segment (same matching as the .txt / _session.json loaders). Returns
    the directory path or None. Preferred over the portable session files because the on-disk
    profile carries IndexedDB (where Firebase-auth sites keep their token), which JSON capture
    cannot reach. Created by scripts/capture_session.py.
    """
    if not os.path.isdir(cookies_dir):
        return None
    parsed = urllib.parse.urlparse(url)
    path_parts = [p.lower() for p in parsed.path.strip("/").split("/") if p]
    hostname = parsed.netloc.lower()
    bare_host = hostname.lstrip("www.")
    for entry in os.listdir(cookies_dir):
        if not entry.endswith("_profile"):
            continue
        full = os.path.join(cookies_dir, entry)
        if not os.path.isdir(full):
            continue
        stem = entry[: -len("_profile")].lower()
        if stem in path_parts or stem in (hostname, bare_host):
            return full
    return None


async def _seed_web_storage(page, session_state: dict) -> bool:
    """Seed localStorage + sessionStorage from a captured session onto the current page.

    Both are origin-scoped and must be written AFTER navigating to the origin, so this runs
    post-goto; the caller reloads if anything was seeded so the SPA reads it on startup.
    sessionStorage matters because Playwright's storage_state() does not persist it, yet some
    SPAs (provecho.co) keep their auth token there; capture_session.py saves it explicitly
    under each origin's "sessionStorage" key. Returns True if any entry was seeded.
    """
    seeded = False
    for origin in session_state.get("origins", []):
        for item in origin.get("localStorage", []):
            try:
                await page.evaluate("([k, v]) => localStorage.setItem(k, v)",
                                    [item["name"], item["value"]])
                seeded = True
            except Exception:
                pass
        for item in origin.get("sessionStorage", []):
            try:
                await page.evaluate("([k, v]) => sessionStorage.setItem(k, v)",
                                    [item["name"], item["value"]])
                seeded = True
            except Exception:
                pass
    return seeded


def _load_session_for_url(url: str, cookies_dir: str) -> dict | None:
    """Find a Playwright storageState JSON file for the given URL's domain.

    Looks for <stem>_session.json where stem appears in the URL path or matches
    the hostname. Returns the parsed dict (keys: cookies, origins) or None.
    """
    if not os.path.isdir(cookies_dir):
        return None

    parsed = urllib.parse.urlparse(url)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    hostname = parsed.netloc.lower()
    bare_host = hostname.lstrip("www.")

    for fname in os.listdir(cookies_dir):
        if not fname.endswith("_session.json"):
            continue
        stem = fname[: -len("_session.json")].lower()
        if stem in [p.lower() for p in path_parts] or stem in (hostname, bare_host):
            fpath = os.path.join(cookies_dir, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Failed to load session file %s: %s", fpath, e)

    return None


def _load_cookies_for_url(url: str, cookies_dir: str) -> list[dict]:
    """Find and parse a Netscape cookie file for the given URL's domain.

    Matches a cookie file to the URL by hostname (e.g. "provecho.co.txt" covers
    every path on provecho.co — all creators — via the bare-hostname check) or,
    as a narrower fallback, by a path segment (e.g. "somecreator.txt" matches
    provecho.co/somecreator). Prefer the hostname form for a site whose login
    spans multiple creators/sections.
    Returns a list of Playwright-format cookie dicts, or [] if no file matches.
    """
    if not os.path.isdir(cookies_dir):
        return []

    parsed = urllib.parse.urlparse(url)
    # Candidates: path segments + hostname variations, all lowercased
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    hostname = parsed.netloc.lower()
    bare_host = hostname.lstrip("www.")

    for fname in os.listdir(cookies_dir):
        if not fname.endswith(".txt"):
            continue
        stem = fname[:-4].lower()
        # Match if the cookie file stem equals the hostname / bare hostname
        # (e.g. "provecho.co" — covers all creator paths on the domain) or appears
        # as a URL path segment (narrower per-creator/section match).
        if stem in [p.lower() for p in path_parts] or stem in (hostname, bare_host):
            fpath = os.path.join(cookies_dir, fname)
            cookies = _parse_netscape_cookies(fpath)
            if cookies:
                return cookies

    return []


def _parse_netscape_cookies(path: str) -> list[dict]:
    """Parse a Netscape-format cookie file into Playwright add_cookies() dicts."""
    cookies = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _, cookie_path, secure, expires, name, value = parts[:7]
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": domain.lstrip("."),
                    "path": cookie_path,
                    "secure": secure.upper() == "TRUE",
                    "expires": int(expires) if expires.isdigit() else -1,
                })
    except Exception as e:
        logger.warning("Failed to parse cookie file %s: %s", path, e)
    return cookies
