from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ExtractedContent:
    url: str
    platform: str
    title: str
    author: str | None = None
    body_text: str = ""
    metadata: dict = field(default_factory=dict)
    media_urls: list[str] = field(default_factory=list)
    captions: str | None = None
    chapters: list[dict] | None = None   # [{time_str, seconds, title}]
    top_comments: list[dict] | None = None  # [{author, score, text}]


class BaseExtractor(ABC):
    @abstractmethod
    async def extract(self, url: str) -> ExtractedContent:
        ...

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        ...
