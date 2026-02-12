"""Webpage scraper - extracts article content and metadata."""
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedContent


class WebpageScraper(BaseScraper):
    """Scraper for general webpages using trafilatura and BeautifulSoup."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )

    def can_handle(self, url: str) -> bool:
        """This scraper handles any HTTP(S) URL as fallback."""
        return url.startswith(("http://", "https://"))

    def _fetch_html(self, url: str) -> str:
        """Fetch HTML content from URL."""
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    def _extract_with_trafilatura(self, html: str, url: str) -> dict:
        """Extract content using trafilatura."""
        try:
            import trafilatura

            # Extract main content
            content = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
            )

            # Extract metadata
            metadata = trafilatura.extract_metadata(html)

            return {
                "content": content or "",
                "title": metadata.title if metadata else None,
                "author": metadata.author if metadata else None,
                "date": metadata.date if metadata else None,
                "description": metadata.description if metadata else None,
                "image": metadata.image if metadata else None,
            }
        except Exception as e:
            print(f"Trafilatura extraction failed: {e}")
            return {}

    def _extract_with_beautifulsoup(self, html: str) -> dict:
        """Fallback extraction using BeautifulSoup."""
        soup = BeautifulSoup(html, "html.parser")

        # Extract title
        title = None
        if soup.title:
            title = soup.title.string
        if not title:
            og_title = soup.find("meta", property="og:title")
            if og_title:
                title = og_title.get("content")

        # Extract author
        author = None
        author_meta = soup.find("meta", attrs={"name": "author"})
        if author_meta:
            author = author_meta.get("content")

        # Extract description
        description = None
        desc_meta = soup.find("meta", attrs={"name": "description"})
        if desc_meta:
            description = desc_meta.get("content")
        if not description:
            og_desc = soup.find("meta", property="og:description")
            if og_desc:
                description = og_desc.get("content")

        # Extract image
        image = None
        og_image = soup.find("meta", property="og:image")
        if og_image:
            image = og_image.get("content")

        # Extract date
        date = None
        date_meta = soup.find("meta", attrs={"name": "date"})
        if date_meta:
            date = date_meta.get("content")
        if not date:
            time_tag = soup.find("time")
            if time_tag:
                date = time_tag.get("datetime")

        # Extract main content (simple approach)
        content = ""
        article = soup.find("article")
        if article:
            content = article.get_text(separator="\n", strip=True)
        else:
            main = soup.find("main")
            if main:
                content = main.get_text(separator="\n", strip=True)

        return {
            "title": title,
            "author": author,
            "date": date,
            "description": description,
            "image": image,
            "content": content,
        }

    def scrape(self, url: str) -> ScrapedContent:
        """Scrape webpage content and metadata."""
        html = self._fetch_html(url)

        # Try trafilatura first
        data = self._extract_with_trafilatura(html, url)

        # Fall back to BeautifulSoup for missing fields
        if not data.get("title") or not data.get("content"):
            bs_data = self._extract_with_beautifulsoup(html)
            for key in ["title", "author", "date", "description", "image", "content"]:
                if not data.get(key) and bs_data.get(key):
                    data[key] = bs_data[key]

        # Parse date
        published = None
        if data.get("date"):
            try:
                # Try common date formats
                for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
                    try:
                        published = datetime.strptime(data["date"][:19], fmt)
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

        return ScrapedContent(
            url=url,
            title=data.get("title") or "Untitled Article",
            author=data.get("author"),
            published=published,
            content=data.get("content", ""),
            description=data.get("description"),
            image_url=data.get("image"),
            content_type="article",
        )
