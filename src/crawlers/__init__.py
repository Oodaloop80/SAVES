"""Site crawlers: discover many content URLs from one index/profile page, then feed them
one at a time through the existing SAVES pipeline.

`get_crawler(url, config)` routes an entry URL to the crawler that claims it (via each
crawler's `matches()`), or returns None if no site-specific crawler applies. The only
site-specific logic lives in each crawler's `discover_urls()`; dedup + enqueue are shared
in `SiteCrawler` (base.py).
"""
from src.crawlers.base import SiteCrawler
from src.crawlers.provecho import ProvechoCrawler

# Registry, most-specific first. Add new site crawlers here.
_CRAWLERS = [ProvechoCrawler]


def get_crawler(url: str, config: dict) -> SiteCrawler | None:
    for cls in _CRAWLERS:
        if cls.matches(url):
            return cls(config)
    return None


__all__ = ["SiteCrawler", "ProvechoCrawler", "get_crawler"]
