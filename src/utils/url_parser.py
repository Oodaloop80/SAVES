import re
import urllib.parse

import requests

URL_RE = re.compile(r'https?://[^\s\)\]\>"\']+')

_TRACKING_PARAMS = {"igsh", "igshid", "fbclid", "ref", "share_id"}


def _is_tracking_param(key: str) -> bool:
    # Strip all utm_* params (utm_source, utm_name, utm_content, ...) plus the explicit set
    return key.startswith("utm_") or key in _TRACKING_PARAMS


def extract_urls(text: str) -> list[str]:
    return [u.rstrip(".,;!?") for u in URL_RE.findall(text)]


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {k: v for k, v in params.items() if not _is_tracking_param(k)}
    new_query = urllib.parse.urlencode(cleaned, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def detect_platform(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().lstrip("www.")
    if host in ("reddit.com", "redd.it") or host.endswith(".reddit.com"):
        return "reddit"
    if host in ("youtube.com", "youtu.be") or host.endswith(".youtube.com"):
        return "youtube"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "instagram"
    if host in ("tiktok.com", "vm.tiktok.com") or host.endswith(".tiktok.com"):
        return "tiktok"
    if host in ("facebook.com", "fb.com", "fb.watch") or host.endswith(".facebook.com"):
        return "facebook"
    return "generic"


def resolve_reddit_short_url(url: str) -> str:
    """Resolve /r/sub/s/XXXX short links to canonical URL."""
    if re.search(r'/s/[A-Za-z0-9]+', url):
        try:
            resp = requests.head(url, allow_redirects=True, timeout=10)
            return resp.url
        except Exception:
            pass
    return url
