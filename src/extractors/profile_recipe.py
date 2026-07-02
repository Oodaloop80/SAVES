"""Follow a "recipe in bio / on my profile" pointer to the actual off-site recipe.

Many Instagram / TikTok / Facebook food posts don't contain the recipe — the caption says
something like *"Full recipe on my profile!"* or *"recipe 👉 link in bio"*. The real recipe
lives behind the poster's bio link, which is usually a link aggregator (Linktree, Beacons,
Stan.store, …) listing several recipes, or occasionally a direct blog post.

This module makes a best-effort attempt to:
  1. detect that the caption is pointing off-site for the recipe,
  2. resolve the poster's bio link(s) from their profile page,
  3. if that's an aggregator, pick the link that best matches the dish,
  4. follow it and extract the recipe (via the generic extractor),
  5. record `followed_recipe_url` + `followed_recipe_markdown` on the content so Claude can
     extract the recipe and the note can link straight to the source.

Everything network-facing is wrapped and NON-FATAL: on any failure the original content is
returned unchanged (plus, when we at least found a bio link, a `followed_recipe_hint`). The
pure helpers (detection, keyword extraction, redirect decoding, aggregator detection, link
parsing and scoring) are deterministic and unit-tested.
"""
import html as _html
import logging
import re
import urllib.parse

from src.extractors.base import ExtractedContent

logger = logging.getLogger(__name__)

# ── detection ────────────────────────────────────────────────────────
_RECIPE_INTENT = re.compile(
    r"\b(recipe|receta|recipes|ingredient|instructions?|how\s+to\s+make|full\s+method|"
    r"printable\s+recipe|get\s+the\s+recipe|written\s+recipe)\b",
    re.I,
)
_REDIRECT_INTENT = re.compile(
    r"(link\s*in\s*(my\s*)?bio|in\s*my\s*bio|on\s*my\s*(profile|page|bio)|check\s*(my\s*)?bio|"
    r"see\s*(my\s*)?bio|bio\s*link|link\s*tree|linktree|my\s*profile|profile\s*for\s*the|"
    r"tap\s*the\s*link|link\s*below\s*my|👆|👇|☝️|🔗)",
    re.I,
)

# Link-aggregator / bio-link hosts: a page LISTING several links rather than a recipe itself.
_AGGREGATOR_HOSTS = {
    "linktr.ee", "linktree.com", "beacons.ai", "beacons.page", "stan.store", "komi.io",
    "snipfeed.co", "flowpage.com", "milkshake.app", "tap.bio", "campsite.bio", "shor.by",
    "allmylinks.com", "lnk.bio", "bio.link", "solo.to", "msha.ke", "withkoji.com",
    "linkin.bio", "later.com", "hoo.be", "pillar.io", "linkpop.com", "carrd.co",
}

# Hosts that are never the recipe (social/store/streaming). Used to filter candidate links.
_NEGATIVE_HOSTS = (
    "instagram.com", "tiktok.com", "youtube.com", "youtu.be", "facebook.com", "fb.com",
    "twitter.com", "x.com", "threads.net", "snapchat.com", "spotify.com", "apple.com",
    "amazon.", "patreon.com", "cameo.com", "venmo.com", "paypal.", "cash.app", "ko-fi.com",
    "buymeacoffee.com", "discord.gg", "discord.com", "t.me", "whatsapp.com",
)
_NEGATIVE_WORDS = (
    "subscribe", "merch", "shop", "store", "sponsor", "discount", "coupon", "promo",
    "download the app", "follow me", "tip jar", "donate", "newsletter", "podcast",
    "my book", "cookbook", "presale", "pre-order", "giveaway",
)

_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "this", "that", "here", "from", "get",
    "recipe", "recipes", "full", "link", "bio", "profile", "page", "video", "reel", "make",
    "how", "new", "best", "easy", "quick", "our", "out", "now", "all", "see", "check",
    "tap", "click", "watch", "follow", "save", "saved", "today", "day", "week", "part",
    "ingredients", "instructions", "written", "printable", "comment", "comments",
}


def wants_offsite_recipe(text: str | None) -> bool:
    """True when a caption both talks about a recipe AND points the reader off-site to get
    it (bio link, profile, link tree …). Requires both signals to avoid false positives on
    ordinary captions that merely say 'link in bio'."""
    if not text:
        return False
    return bool(_RECIPE_INTENT.search(text) and _REDIRECT_INTENT.search(text))


def extract_dish_keywords(*texts: str | None, limit: int = 8) -> list[str]:
    """Pull meaningful dish words from the title/caption to match against aggregator links.
    Drops stopwords and recipe-generic filler; preserves first-seen order; deduplicates."""
    seen: list[str] = []
    for text in texts:
        if not text:
            continue
        for tok in re.findall(r"[a-zA-Z][a-zA-Z'\-]{2,}", text.lower()):
            tok = tok.strip("-'")
            if len(tok) < 3 or tok in _STOPWORDS or tok in seen:
                continue
            seen.append(tok)
            if len(seen) >= limit:
                return seen
    return seen


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def is_aggregator(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in _AGGREGATOR_HOSTS)


def decode_redirect(url: str) -> str:
    """Unwrap the redirect wrappers platforms put around outbound bio links, e.g.
    ``https://l.instagram.com/?u=https%3A%2F%2Fsite.com%2Frecipe&e=...`` →
    ``https://site.com/recipe``. Returns the URL unchanged when there's nothing to unwrap."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return url
    host = parsed.netloc.lower()
    is_wrapper = (
        host.startswith("l.instagram.com") or host.startswith("l.facebook.com")
        or host.startswith("lm.facebook.com") or host.startswith("out.reddit.com")
        or host.endswith("l.tiktok.com") or "/redirect" in parsed.path
    )
    qs = urllib.parse.parse_qs(parsed.query)
    for key in ("u", "url", "target", "redirect", "q", "link"):
        if key in qs and qs[key]:
            inner = urllib.parse.unquote(qs[key][0])
            if inner.startswith("http") and (is_wrapper or key in ("url", "target", "redirect")):
                return inner
    return url


_ANCHOR_RE = re.compile(r"<a\b[^>]*?href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(fragment: str) -> str:
    return _html.unescape(_TAG_RE.sub(" ", fragment)).strip()


def parse_links(html_text: str, base_url: str = "") -> list[tuple[str, str]]:
    """Extract (absolute_url, anchor_text) pairs from rendered HTML, decoding redirect
    wrappers and skipping non-navigational hrefs. Deduplicated by URL, order preserved."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_href, raw_text in _ANCHOR_RE.findall(html_text or ""):
        href = _html.unescape(raw_href).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        if base_url and not href.lower().startswith(("http://", "https://")):
            href = urllib.parse.urljoin(base_url, href)
        href = decode_redirect(href)
        if not href.lower().startswith(("http://", "https://")) or href in seen:
            continue
        seen.add(href)
        out.append((href, _strip_tags(raw_text)))
    return out


def _slug_words(url: str) -> str:
    try:
        path = urllib.parse.urlparse(url).path
    except Exception:
        path = url
    return re.sub(r"[-_/]+", " ", urllib.parse.unquote(path)).lower()


def score_candidate(url: str, anchor: str, keywords: list[str]) -> float:
    """Heuristic score for how likely a link is THE recipe the post refers to."""
    u = url.lower()
    hay = (anchor or "").lower() + " " + _slug_words(url)
    score = 0.0
    for kw in keywords:
        if kw in hay:
            score += 1.0
    if "recipe" in anchor.lower() or "recipe" in u:
        score += 1.5
    if any(w in hay for w in ("ingredient", "instruction", "how to make", "method", "print recipe")):
        score += 0.5
    if any(bad in u for bad in _NEGATIVE_HOSTS):
        score -= 3.0
    if any(bad in hay for bad in _NEGATIVE_WORDS):
        score -= 1.0
    return score


def pick_best_link(
    candidates: list[tuple[str, str]], keywords: list[str],
    exclude_hosts: set[str] | None = None, min_score: float = 1.0,
) -> tuple[str, float] | None:
    """Choose the highest-scoring candidate recipe link, or None if none clears the bar."""
    exclude = {h.lstrip("www.") for h in (exclude_hosts or set())}
    best: tuple[str, float] | None = None
    for url, anchor in candidates:
        host = _host(url)
        if not host or host in exclude or any(bad in url.lower() for bad in _NEGATIVE_HOSTS):
            continue
        sc = score_candidate(url, anchor, keywords)
        if best is None or sc > best[1]:
            best = (url, sc)
    if best and best[1] >= min_score:
        return best
    return None


def external_candidates(
    candidates: list[tuple[str, str]], exclude_hosts: set[str] | None = None
) -> list[tuple[str, str]]:
    """Candidate links that point off-platform (drop social/store hosts and excluded hosts)."""
    exclude = {h.lstrip("www.") for h in (exclude_hosts or set())}
    out = []
    for url, anchor in candidates:
        host = _host(url)
        if not host or host in exclude:
            continue
        if any(bad in url.lower() for bad in _NEGATIVE_HOSTS):
            continue
        out.append((url, anchor))
    return out


# ── network (best-effort) ─────────────────────────────────────────────

def _profile_url(platform: str, handle: str) -> str | None:
    handle = (handle or "").strip().lstrip("@")
    if not handle or any(c.isspace() for c in handle):
        return None
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "facebook":
        return f"https://www.facebook.com/{handle}"
    return None


def _parse_netscape_cookies(path: str) -> list[dict]:
    """Parse a Netscape cookies.txt file into Playwright cookie dicts. Best-effort."""
    cookies: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) != 7:
                    continue
                domain, _flag, cpath, secure, expiry, name, value = parts
                cookie = {
                    "name": name, "value": value, "domain": domain, "path": cpath or "/",
                    "secure": secure.upper() == "TRUE",
                }
                try:
                    exp = int(expiry)
                    if exp > 0:
                        cookie["expires"] = exp
                except ValueError:
                    pass
                cookies.append(cookie)
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.debug("Failed parsing cookies %s: %s", path, e)
    return cookies


async def _render_html(url: str, config: dict, cookies_path: str | None = None) -> tuple[str, str]:
    """Load a URL in headless Chromium and return (rendered_html, final_url). Optionally
    seeds cookies from a Netscape cookies.txt. Raises on hard failure; caller wraps it."""
    from playwright.async_api import async_playwright

    pcfg = config.get("platforms", {}).get("generic", {})
    timeout = pcfg.get("playwright_timeout_seconds", 30) * 1000
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run", "--no-service-autorun", "--password-store=basic",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        if cookies_path:
            cookies = _parse_netscape_cookies(cookies_path)
            if cookies:
                try:
                    await context.add_cookies(cookies)
                except Exception as e:
                    logger.debug("add_cookies failed: %s", e)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout)
        except Exception:
            await page.wait_for_timeout(3000)
        await page.wait_for_timeout(1500)
        html_text = await page.content()
        final_url = page.url
        await browser.close()
    return html_text, final_url


def _cookies_path_for(platform: str, config: dict) -> str | None:
    import os
    cookies_dir = config.get("paths", {}).get("cookies_dir", "cookies")
    candidate = os.path.join(cookies_dir, f"{platform}.txt")
    return candidate if os.path.exists(candidate) else None


async def _resolve_bio_links(content: ExtractedContent, config: dict) -> list[tuple[str, str]]:
    """Return candidate external links found on the poster's profile page (best-effort)."""
    handle = (content.metadata or {}).get("author_handle") or content.author or ""
    profile = _profile_url(content.platform, handle)
    if not profile:
        return []
    cookies = _cookies_path_for(content.platform, config)
    try:
        html_text, _ = await _render_html(profile, config, cookies_path=cookies)
    except Exception as e:
        logger.info("Could not load profile %s for bio link: %s", profile, e)
        return []
    links = parse_links(html_text, base_url=profile)
    return external_candidates(links, exclude_hosts={_host(profile)})


async def follow_profile_recipe(content: ExtractedContent, config: dict) -> ExtractedContent:
    """Best-effort: if this looks like a 'recipe in bio' post, follow the bio link to the
    real recipe and attach it. Always returns content (unchanged on any miss/failure)."""
    if content.platform not in ("instagram", "tiktok", "facebook"):
        return content
    caption = " ".join(filter(None, [content.title, content.body_text]))
    if not wants_offsite_recipe(caption):
        return content

    logger.info("Detected off-site recipe pointer in %s post — attempting to follow bio link", content.platform)
    keywords = extract_dish_keywords(content.title, content.body_text)

    # A caption sometimes pastes the destination URL directly — prefer that over scraping.
    direct = external_candidates(parse_links_from_text(caption))
    bio_links = direct or await _resolve_bio_links(content, config)
    if not bio_links:
        logger.info("No usable bio link found for %s", content.url)
        return content

    # Split into aggregator pages vs. links that could be the recipe directly.
    aggregators = [(u, a) for (u, a) in bio_links if is_aggregator(u)]
    directish = [(u, a) for (u, a) in bio_links if not is_aggregator(u)]

    chosen: str | None = None
    # Prefer a strong direct match among non-aggregator bio links.
    best_direct = pick_best_link(directish, keywords, min_score=1.0)
    if best_direct:
        chosen = best_direct[0]
    elif len(directish) == 1 and not aggregators:
        chosen = directish[0][0]  # single off-site link, no aggregator — follow it

    # Otherwise dig into the first aggregator page and pick the best-matching recipe link.
    if not chosen and aggregators:
        agg_url = aggregators[0][0]
        try:
            agg_html, agg_final = await _render_html(agg_url, config)
            agg_links = parse_links(agg_html, base_url=agg_final)
            best = pick_best_link(agg_links, keywords, exclude_hosts={_host(agg_url)}, min_score=1.0)
            if best:
                chosen = best[0]
                logger.info("Aggregator %s → chose %s (score %.1f)", agg_url, chosen, best[1])
        except Exception as e:
            logger.info("Aggregator fetch failed for %s: %s", agg_url, e)

    if not chosen:
        # Record the bio link as a hint even though we couldn't pin down the recipe.
        content.metadata["followed_recipe_hint"] = bio_links[0][0]
        logger.info("Could not pin down the exact recipe link; recorded bio hint %s", bio_links[0][0])
        return content

    # Follow the chosen recipe URL through the generic extractor.
    try:
        from src.extractors.generic import GenericExtractor
        recipe_content = await GenericExtractor(config).extract(chosen)
    except Exception as e:
        logger.info("Failed to extract followed recipe %s: %s", chosen, e)
        content.metadata["followed_recipe_hint"] = chosen
        return content

    md = (recipe_content.metadata or {}).get("article_markdown") or recipe_content.body_text or ""
    if not md.strip():
        content.metadata["followed_recipe_hint"] = chosen
        return content

    content.metadata["followed_recipe_url"] = chosen
    content.metadata["followed_recipe_markdown"] = md[:8000]
    logger.info("Followed bio recipe: %s (%d chars extracted)", chosen, len(md))
    return content


_URL_IN_TEXT_RE = re.compile(r"https?://[^\s)\]\"'<>]+", re.I)


def parse_links_from_text(text: str | None) -> list[tuple[str, str]]:
    """Find bare URLs pasted into caption text, returned in the (url, anchor) shape."""
    if not text:
        return []
    out, seen = [], set()
    for m in _URL_IN_TEXT_RE.finditer(text):
        u = decode_redirect(m.group(0).rstrip(".,;"))
        if u not in seen:
            seen.add(u)
            out.append((u, ""))
    return out
