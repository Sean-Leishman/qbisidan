"""Instagram reel/post scraper — downloads the video and uses Gemini for BOTH
speech transcription and on-screen visual understanding in one call.

No Whisper, no ffmpeg frame extraction, no new dependency: Gemini takes video
files directly via the Files API, and yt-dlp (already a dep) can download an
Instagram reel same as a YouTube video.

Video understanding is Gemini-only by design — Anthropic won't take raw video
and Groq is audio-only — so this reads `ai.gemini_api_key` directly rather
than whatever provider the summarizer is configured for. Missing key, over-
duration video, download failure, or Gemini failure: all degrade to
caption-only rather than raising. A thin note beats no note.

# ponytail: `_transcribe_video`'s one-call transcript+visual read is the
# natural fallback for YouTube Shorts, which usually have no captions
# (`YouTubeScraper._get_transcript()` returns "") and so make thin notes
# today. The hook point is exactly that empty-transcript case. Do not wire
# it up yet — it's an upgrade for when Shorts notes are annoying enough to
# justify the extra Gemini video cost, not before.
"""
import logging
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .base import BaseScraper, ScrapedContent

logger = logging.getLogger(__name__)

INSTAGRAM_PATTERNS = [
    r"(?:https?://)?(?:www\.)?instagram\.com/reels?/([\w-]+)",
    r"(?:https?://)?(?:www\.)?instagram\.com/p/([\w-]+)",
]

_TRANSCRIPT_VISUAL_PROMPT = (
    "Transcribe all speech in this video verbatim. Then, separately, describe "
    "what is shown on screen — on-screen text, locations, dishes, rooms, "
    "products, and anything else visually notable.\n\n"
    "Format your response EXACTLY as:\n"
    "TRANSCRIPT:\n"
    "<verbatim speech transcript, or \"(no speech)\" if there is none>\n\n"
    "VISUAL:\n"
    "<description of on-screen content>"
)


class InstagramScraper(BaseScraper):
    """Scraper for Instagram reels/posts.

    `get_metadata()` is the cheap path — a yt-dlp metadata-only fetch (no
    video download) that returns caption/uploader/thumbnail/duration. This is
    the path Phase 4's backfill filter calls to judge relevance BEFORE paying
    for a video download + Gemini transcription; `scrape()` calls it too, as
    the first step of the expensive path.
    """

    def __init__(
        self,
        gemini_api_key: str = "",
        video_model: str = "gemini-flash-latest",
        cookies_file: str | None = None,
        max_duration_seconds: int = 180,
    ):
        self.gemini_api_key = gemini_api_key
        self.video_model = video_model
        self.cookies_file = cookies_file
        self.max_duration_seconds = max_duration_seconds

    def can_handle(self, url: str) -> bool:
        """Check if URL is an Instagram reel or post."""
        return any(re.search(pattern, url) for pattern in INSTAGRAM_PATTERNS)

    def _extract_shortcode(self, url: str) -> str | None:
        for pattern in INSTAGRAM_PATTERNS:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    # ── Cheap path: metadata only, no video download ────────────────────────

    def get_metadata(self, url: str) -> dict:
        """Metadata-only yt-dlp fetch (skip_download=True) — caption, uploader,
        thumbnail, duration. Raises on failure so callers can fail loudly, same
        idiom as YouTubeScraper._get_metadata."""
        import yt_dlp

        ydl_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
        }
        if self.cookies_file:
            ydl_opts["cookiefile"] = self.cookies_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title") or (info.get("description") or "Untitled Reel")[:80],
                "caption": info.get("description") or "",
                "author": info.get("uploader"),
                "upload_date": info.get("upload_date"),  # YYYYMMDD format
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration"),
            }

    def _build_content(self, url: str, metadata: dict, content: str) -> ScrapedContent:
        published = None
        if metadata.get("upload_date"):
            try:
                published = datetime.strptime(metadata["upload_date"], "%Y%m%d")
            except ValueError:
                pass

        return ScrapedContent(
            url=url,
            title=metadata.get("title") or "Instagram Reel",
            author=metadata.get("author"),
            published=published,
            content=content,
            description=metadata.get("caption") or None,
            image_url=metadata.get("thumbnail"),
            content_type="instagram",
            extra={
                "shortcode": self._extract_shortcode(url),
                "duration": metadata.get("duration"),
            },
        )

    def _caption_only(self, url: str, metadata: dict, reason: str) -> ScrapedContent:
        logger.warning(f"Instagram reel degraded to caption-only for {url}: {reason}")
        return self._build_content(url, metadata, content=metadata.get("caption") or "")

    # ── Expensive path: download + Gemini video understanding ───────────────

    def scrape(self, url: str) -> ScrapedContent:
        """Scrape an Instagram reel/post: metadata, then (if possible) the
        actual spoken words and on-screen visuals via Gemini. Falls back to
        caption-only on any degradation condition — never crashes the bookmark
        pipeline over a missing key or a flaky download."""
        metadata = self.get_metadata(url)

        if not self.gemini_api_key:
            return self._caption_only(url, metadata, "GEMINI_API_KEY not configured")

        duration = metadata.get("duration")
        if duration and duration > self.max_duration_seconds:
            return self._caption_only(
                url, metadata,
                f"duration {duration}s exceeds max_duration_seconds={self.max_duration_seconds}",
            )

        try:
            transcript, visual = self._transcribe_video(url)
        except Exception as e:
            logger.warning(f"Instagram video understanding failed for {url}: {e}")
            return self._caption_only(url, metadata, f"Gemini video call failed: {e}")

        sections = []
        if transcript:
            sections.append(f"## Transcript\n\n{transcript}")
        if visual:
            sections.append(f"## On-Screen\n\n{visual}")
        content = "\n\n".join(sections) or (metadata.get("caption") or "")

        return self._build_content(url, metadata, content=content)

    def _download_video(self, url: str) -> str:
        """Download the reel to a temp mp4 file. Caller owns cleanup of the
        returned path — but a failed download must not leak the (possibly
        empty) temp file `NamedTemporaryFile` already created on disk, so
        this cleans up after itself on failure too."""
        import yt_dlp

        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp_path = tmp.name
        tmp.close()

        ydl_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": tmp_path,
            "format": "mp4/best",
            "overwrites": True,
        }
        if self.cookies_file:
            ydl_opts["cookiefile"] = self.cookies_file

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        return tmp_path

    def _wait_for_active(self, client, file, timeout: float = 60.0, interval: float = 2.0):
        """Poll the Gemini Files API until the upload leaves PROCESSING."""
        elapsed = 0.0
        state_name = getattr(file.state, "name", file.state)
        while state_name == "PROCESSING" and elapsed < timeout:
            time.sleep(interval)
            elapsed += interval
            file = client.files.get(name=file.name)
            state_name = getattr(file.state, "name", file.state)
        if state_name == "FAILED":
            raise RuntimeError(f"Gemini file processing failed for {file.name}")
        return file

    def _parse_transcript_visual(self, text: str) -> tuple[str, str]:
        if not text:
            return "", ""
        match = re.search(r"TRANSCRIPT:\s*(.*?)\s*VISUAL:\s*(.*)", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        # Model didn't follow the format — keep the raw text as the transcript
        # rather than discarding it.
        return text.strip(), ""

    def _transcribe_video(self, url: str) -> tuple[str, str]:
        """Download the reel, upload it to the Gemini Files API, run one prompt
        for verbatim speech transcript + on-screen visual description.

        Always deletes the temp file AND the uploaded Gemini file, including on
        failure — a try/finally so nothing leaks into Gemini's storage or /tmp.
        """
        from google import genai

        video_path = self._download_video(url)
        client = genai.Client(api_key=self.gemini_api_key)
        uploaded = None
        try:
            uploaded = client.files.upload(file=video_path)
            uploaded = self._wait_for_active(client, uploaded)

            response = client.models.generate_content(
                model=self.video_model,
                contents=[uploaded, _TRANSCRIPT_VISUAL_PROMPT],
            )
            return self._parse_transcript_visual(response.text)
        finally:
            if uploaded is not None and getattr(uploaded, "name", None):
                try:
                    client.files.delete(name=uploaded.name)
                except Exception as e:
                    logger.warning(f"Failed to delete Gemini file {uploaded.name}: {e}")
            try:
                Path(video_path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Failed to delete temp video {video_path}: {e}")
