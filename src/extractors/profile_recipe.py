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
import unicodedata
import urllib.parse

from src.extractors.base import ExtractedContent

logger = logging.getLogger(__name__)

# ── detection ────────────────────────────────────────────────────────
_RECIPE_INTENT = re.compile(
    r"\b(recipe|receta|recipes|ingredient|instructions?|how\s+to\s+make|full\s+method|"
    r"printable\s+recipe|get\s+the\s+recipe|written\s+recipe|"
    # cooking/baking cues so posts that describe a dish without the literal word "recipe"
    # (but still point off-site) qualify, e.g. "ready to bake … dough mix, link in bio":
    r"bake[sd]?|baking|dough|roast(?:ed)?|grill(?:ed)?|marina(?:de|te)|simmer|knead|"
    r"preheat|batter|frosting|proof(?:ed|ing)?)\b",
    re.I,
)
_REDIRECT_INTENT = re.compile(
    r"(link\s*in\s*(my\s*)?bio|in\s*my\s*bio|on\s*my\s*(profile|page|bio)|check\s*(my\s*)?bio|"
    r"see\s*(my\s*)?bio|bio\s*link|link\s*tree|linktree|my\s*profile|profile\s*for\s*the|"
    r"tap\s*the\s*link|link\s*below\s*my|👆|👇|☝️|🔗)",
    re.I,
)
# The literal recipe words (a subset of _RECIPE_INTENT, minus the loose cooking cues like
# "bake"/"dough"). A caption that names a website off-site is only treated as a recipe pointer
# when this stronger wording is present, so an ordinary cooking post that merely mentions some
# domain ("baked these on my sony.com camera") doesn't get followed.
_STRONG_RECIPE_INTENT = re.compile(
    r"\b(recipe|receta|recipes|ingredient|instructions?|how\s+to\s+make|full\s+method|"
    r"printable\s+recipe|get\s+the\s+recipe|written\s+recipe)\b",
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
    "blog", "dot", "com", "net", "org", "www", "http", "https", "url", "website", "site",
}


def normalize_caption(text: str | None) -> str:
    """Fold Instagram/TikTok 'fancy font' Unicode (mathematical bold/italic/sans-serif,
    fullwidth, etc.) down to plain letters so the regexes match. Food captions constantly
    render "𝐅𝐔𝐋𝐋 𝐑𝐄𝐂𝐈𝐏𝐄 𝐎𝐍 𝐌𝐘 𝐁𝐋𝐎𝐆" with U+1D400-block glyphs that are NOT ASCII 'r','e'…;
    NFKC compatibility normalization maps them back to ASCII (emoji are left untouched)."""
    if not text:
        return text or ""
    return unicodedata.normalize("NFKC", text)


def _caption_offsite_links(text: str) -> list[tuple[str, str]]:
    """Followable off-platform destinations named directly in the caption — a pasted URL or a
    domain written with a dot/"dot" ("lilsipper.com", "drveganblog dot com") — with social,
    store, and the poster's own platform hosts filtered out. Shared by detection here and the
    `direct` candidate pool in follow_profile_recipe, so both agree on what counts as a pointer."""
    return external_candidates(parse_links_from_text(text) + reconstruct_spelled_urls(text))


def wants_offsite_recipe(text: str | None) -> bool:
    """True when a caption talks about a recipe AND points the reader off-site to get it.

    A caption points off-site in one of two ways:
      * bio/profile phrasing ("recipe in bio", "on my profile", 🔗) — ``_REDIRECT_INTENT``; or
      * it names an explicit off-platform destination ("full recipe on lilsipper.com"). That
        second path is only honoured together with the literal recipe words
        (``_STRONG_RECIPE_INTENT``), so a stray domain in an ordinary cooking post ("baked on my
        sony.com camera") doesn't trigger a follow.
    Requiring a recipe signal AND a redirect signal avoids false positives on captions that merely
    say 'link in bio' with no recipe, or that name a website with no recipe intent."""
    if not text:
        return False
    text = normalize_caption(text)
    if not _RECIPE_INTENT.search(text):
        return False
    if _REDIRECT_INTENT.search(text):
        return True
    return bool(_STRONG_RECIPE_INTENT.search(text) and _caption_offsite_links(text))


def extract_dish_keywords(*texts: str | None, limit: int = 8) -> list[str]:
    """Pull meaningful dish words from the title/caption to match against aggregator links.
    Drops stopwords and recipe-generic filler; preserves first-seen order; deduplicates."""
    seen: list[str] = []
    for text in texts:
        if not text:
            continue
        text = normalize_caption(text)
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


_TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "igsh", "aem", "_aem", "ref", "ref_src",
}


def clean_url(url: str) -> str:
    """Strip tracking cruft (utm_*, fbclid, igshid, …) so the stored 'Recipe source' link is tidy."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return url
    if not parsed.query:
        return url
    kept = [
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(kept)))


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
    caption = normalize_caption(" ".join(filter(None, [content.title, content.body_text])))
    if not wants_offsite_recipe(caption):
        return content

    logger.info("Detected off-site recipe pointer in %s post — attempting to follow bio link", content.platform)
    # Flag the detection so the note always renders the "Recipe from Bio/Link" section — even
    # if every fetch below fails, the reader should see that this post pointed off-site.
    content.metadata["offsite_recipe_detected"] = True
    keywords = extract_dish_keywords(content.title, content.body_text)

    # A caption often names the destination directly — either a pasted URL or a domain
    # spelled out to dodge link detection ("Drveganblog dot com"). Prefer that over scraping.
    direct = _caption_offsite_links(caption)
    bio_links = direct or await _resolve_bio_links(content, config)
    if not bio_links:
        logger.info("No usable bio link found for %s", content.url)
        return content

    # Build one candidate pool of followable links. If the bio link is an aggregator
    # (Linktree/Beacons/…), pull its listed links into the pool too.
    aggregators = [(u, a) for (u, a) in bio_links if is_aggregator(u)]
    pool = [(u, a) for (u, a) in bio_links if not is_aggregator(u)]
    if aggregators:
        agg_url = aggregators[0][0]
        try:
            agg_html, agg_final = await _render_html(agg_url, config)
            pool += external_candidates(parse_links(agg_html, base_url=agg_final),
                                        exclude_hosts={_host(agg_url)})
        except Exception as e:
            logger.info("Aggregator fetch failed for %s: %s", agg_url, e)

    if not pool:
        content.metadata["followed_recipe_hint"] = bio_links[0][0]
        logger.info("No followable off-site link; recorded bio hint %s", bio_links[0][0])
        return content

    # Prefer a link whose text/slug matches the dish; otherwise follow the primary bio/site
    # link and let the descend step below try to locate the dish page inside it.
    best = pick_best_link(pool, keywords, min_score=1.0)
    if best:
        chosen = best[0]
        logger.info("Chose recipe link %s (score %.1f)", chosen, best[1])
    else:
        chosen = pool[0][0]
        logger.info("No keyword match among bio links; following primary link %s and descending", chosen)

    # Follow the chosen recipe URL through the generic extractor. Capture the FULL article
    # (structured markdown with inline images + any schema.org Recipe JSON-LD), not just text,
    # so the page can be reproduced in the note and the recipe extracted accurately.
    art = await _extract_article(chosen, config)
    if art is None:
        content.metadata["followed_recipe_hint"] = chosen
        return content
    md = _article_text(art)

    # A bio link often lands on a site root / index that lists posts rather than the recipe
    # itself (e.g. "drveganblog.com" → homepage). When the landing page is thin or is a
    # domain root and isn't already an aggregator we handled above, descend one level by
    # scoring the page's own links against the dish and following the best match.
    if not is_aggregator(chosen) and (len(md.strip()) < _THIN_MARKDOWN_CHARS or _is_site_root(chosen)):
        deeper = await _descend_to_recipe(chosen, keywords, config)
        if deeper and deeper.rstrip("/") != chosen.rstrip("/"):
            deeper_art = await _extract_article(deeper, config)
            deeper_md = _article_text(deeper_art) if deeper_art else ""
            if deeper_art and len(deeper_md.strip()) > len(md.strip()):
                logger.info("Descended from %s → recipe page %s (%d chars)", chosen, deeper, len(deeper_md))
                chosen, art, md = deeper, deeper_art, deeper_md

    if not md.strip():
        content.metadata["followed_recipe_hint"] = chosen
        return content

    art_meta = art.metadata or {}
    content.metadata["followed_recipe_url"] = clean_url(chosen)
    # Text for Claude's recipe extraction (fallback / surrounding notes when there's no JSON-LD).
    content.metadata["followed_recipe_markdown"] = md[:12000]
    # Structured markdown (headings, formatting, inline image URLs) to reproduce the page in
    # the note — the images get downloaded + embedded by the article-image localizer.
    if art_meta.get("article_markdown"):
        content.metadata["followed_recipe_article_markdown"] = art_meta["article_markdown"]
    # Authoritative schema.org Recipe (exact ingredients/quantities/steps), if the page has it.
    if art_meta.get("recipe_data"):
        content.metadata["followed_recipe_data"] = art_meta["recipe_data"]
    if art.title:
        content.metadata["followed_recipe_title"] = art.title
    logger.info(
        "Followed bio recipe: %s (%d chars, %sstructured recipe)",
        clean_url(chosen), len(md), "with " if art_meta.get("recipe_data") else "no ",
    )
    return content


_THIN_MARKDOWN_CHARS = 400


def _is_site_root(url: str) -> bool:
    try:
        path = urllib.parse.urlparse(url).path
    except Exception:
        return False
    return path.strip("/") == ""


async def _extract_article(url: str, config: dict) -> ExtractedContent | None:
    """Run the generic (web-clipper) extractor and return its full ExtractedContent — the
    structured article markdown, inline images, and any schema.org Recipe JSON-LD — or None."""
    try:
        from src.extractors.generic import GenericExtractor
        return await GenericExtractor(config).extract(url)
    except Exception as e:
        logger.info("Failed to extract %s: %s", url, e)
        return None


def _article_text(art: ExtractedContent | None) -> str:
    """The article's Markdown (preferred) or plain body text, for length checks + Claude."""
    if art is None:
        return ""
    return (art.metadata or {}).get("article_markdown") or art.body_text or ""


async def _descend_to_recipe(page_url: str, keywords: list[str], config: dict) -> str | None:
    """Given a listing/index page, find the link that best matches the dish. Prefers links
    on the same site (a blog's own recipe post) and falls back to a strong off-site match."""
    try:
        html_text, final_url = await _render_html(page_url, config)
    except Exception as e:
        logger.info("Descend fetch failed for %s: %s", page_url, e)
        return None
    links = parse_links(html_text, base_url=final_url)
    host = _host(final_url)
    internal = [
        (u, a) for (u, a) in links
        if _host(u) == host and u.rstrip("/") != final_url.rstrip("/")
        and not any(bad in u.lower() for bad in _NEGATIVE_HOSTS)
    ]
    best = (
        pick_best_link(internal, keywords, min_score=1.0)
        or pick_best_link(links, keywords, exclude_hosts={host}, min_score=1.5)
    )
    return best[0] if best else None


_URL_IN_TEXT_RE = re.compile(r"https?://[^\s)\]\"'<>]+", re.I)
# Spelled-out domains that dodge Instagram's link detection, e.g. "Drveganblog dot com"
# or "mysite . com". Requires a plausible TLD so ordinary "…and dot the i's" text is skipped.
_SPELLED_URL_RE = re.compile(
    r"\b([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\s*(?:dot|\.)\s*"
    r"(com|net|org|io|co|blog|shop|store|kitchen|recipes?|food|us|uk|ca|au|eu)\b",
    re.I,
)


def parse_links_from_text(text: str | None) -> list[tuple[str, str]]:
    """Find bare URLs pasted into caption text, returned in the (url, anchor) shape."""
    if not text:
        return []
    text = normalize_caption(text)
    out, seen = [], set()
    for m in _URL_IN_TEXT_RE.finditer(text):
        u = decode_redirect(m.group(0).rstrip(".,;"))
        if u not in seen:
            seen.add(u)
            out.append((u, ""))
    return out


def reconstruct_spelled_urls(text: str | None) -> list[tuple[str, str]]:
    """Rebuild domains a poster spelled out to evade link detection ("Drveganblog dot com"
    → https://drveganblog.com). Returned in the (url, anchor) shape as extra candidates."""
    if not text:
        return []
    text = normalize_caption(text)
    out, seen = [], set()
    for m in _SPELLED_URL_RE.finditer(text):
        host = f"{m.group(1).lower()}.{m.group(2).lower()}"
        url = "https://" + host
        if host not in seen:
            seen.add(host)
            out.append((url, ""))
    return out
