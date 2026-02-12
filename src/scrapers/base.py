"""Base scraper interface and shared data structures."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ScrapedContent:
    """Content extracted from a URL."""

    url: str
    title: str
    author: str | None = None
    published: datetime | None = None
    content: str = ""  # Main content (transcript or article text)
    description: str | None = None
    image_url: str | None = None
    content_type: str = "article"  # "youtube" or "article"
    extra: dict = field(default_factory=dict)  # Additional metadata


class BaseScraper(ABC):
    """Abstract base class for content scrapers."""

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Check if this scraper can handle the given URL."""
        pass

    @abstractmethod
    def scrape(self, url: str) -> ScrapedContent:
        """Scrape content from the URL."""
        pass
