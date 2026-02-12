"""YouTube video scraper - extracts metadata and transcripts."""
import re
from datetime import datetime

from .base import BaseScraper, ScrapedContent


class YouTubeScraper(BaseScraper):
    """Scraper for YouTube videos using yt-dlp and youtube-transcript-api."""

    YOUTUBE_PATTERNS = [
        r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)",
        r"(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]+)",
        r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)",
    ]

    def __init__(self, transcript_languages: list[str] | None = None):
        self.transcript_languages = transcript_languages or ["en", "en-US", "en-GB"]

    def can_handle(self, url: str) -> bool:
        """Check if URL is a YouTube video."""
        return any(re.search(pattern, url) for pattern in self.YOUTUBE_PATTERNS)

    def _extract_video_id(self, url: str) -> str | None:
        """Extract video ID from YouTube URL."""
        for pattern in self.YOUTUBE_PATTERNS:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _get_metadata(self, url: str) -> dict:
        """Get video metadata using yt-dlp."""
        try:
            import yt_dlp

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "skip_download": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    "title": info.get("title", "Untitled"),
                    "author": info.get("uploader", info.get("channel")),
                    "upload_date": info.get("upload_date"),  # YYYYMMDD format
                    "description": info.get("description"),
                    "thumbnail": info.get("thumbnail"),
                    "duration": info.get("duration"),
                    "view_count": info.get("view_count"),
                }
        except Exception as e:
            print(f"Error getting YouTube metadata: {e}")
            return {}

    def _get_transcript(self, video_id: str) -> str:
        """Get video transcript using youtube-transcript-api."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi

            # Try to get transcript in preferred languages
            for lang in self.transcript_languages:
                try:
                    transcript = YouTubeTranscriptApi.get_transcript(
                        video_id, languages=[lang]
                    )
                    return " ".join(entry["text"] for entry in transcript)
                except Exception:
                    continue

            # Fall back to any available transcript
            try:
                transcript = YouTubeTranscriptApi.get_transcript(video_id)
                return " ".join(entry["text"] for entry in transcript)
            except Exception:
                pass

        except Exception as e:
            print(f"Error getting YouTube transcript: {e}")

        return ""

    def scrape(self, url: str) -> ScrapedContent:
        """Scrape YouTube video metadata and transcript."""
        video_id = self._extract_video_id(url)
        if not video_id:
            raise ValueError(f"Could not extract video ID from URL: {url}")

        # Get metadata
        metadata = self._get_metadata(url)

        # Parse upload date
        published = None
        if metadata.get("upload_date"):
            try:
                published = datetime.strptime(metadata["upload_date"], "%Y%m%d")
            except ValueError:
                pass

        # Get transcript
        transcript = self._get_transcript(video_id)

        return ScrapedContent(
            url=url,
            title=metadata.get("title", "Untitled Video"),
            author=metadata.get("author"),
            published=published,
            content=transcript,
            description=metadata.get("description"),
            image_url=metadata.get("thumbnail"),
            content_type="youtube",
            extra={
                "video_id": video_id,
                "duration": metadata.get("duration"),
                "view_count": metadata.get("view_count"),
            },
        )
