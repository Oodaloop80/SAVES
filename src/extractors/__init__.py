from src.utils.url_parser import detect_platform
from src.extractors.base import BaseExtractor


def get_extractor(url: str, config: dict) -> BaseExtractor:
    from src.extractors.reddit import RedditExtractor
    from src.extractors.youtube import YouTubeExtractor
    from src.extractors.instagram import InstagramExtractor
    from src.extractors.tiktok import TikTokExtractor
    from src.extractors.facebook import FacebookExtractor
    from src.extractors.generic import GenericExtractor

    platform = detect_platform(url)
    extractors = {
        "reddit": RedditExtractor,
        "youtube": YouTubeExtractor,
        "instagram": InstagramExtractor,
        "tiktok": TikTokExtractor,
        "facebook": FacebookExtractor,
        "generic": GenericExtractor,
    }
    cls = extractors.get(platform, GenericExtractor)
    return cls(config)
