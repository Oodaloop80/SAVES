from src.extractors.base import BaseExtractor
from src.utils.url_parser import detect_platform


def get_extractor(url: str, config: dict) -> BaseExtractor:
    from src.extractors.facebook import FacebookExtractor
    from src.extractors.generic import GenericExtractor
    from src.extractors.instagram import InstagramExtractor
    from src.extractors.reddit import RedditExtractor
    from src.extractors.tiktok import TikTokExtractor
    from src.extractors.youtube import YouTubeExtractor

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
