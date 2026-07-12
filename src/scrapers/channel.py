"""Channel/playlist video enumeration via yt-dlp flat extraction.

This is *enumeration only* — list of (id, url, title, upload_date) stubs.
Full per-video metadata and transcripts are the job of YouTubeScraper later
in the pipeline; fetching that here per-video would be slow and defeats the
point of flat extraction.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime

logger = logging.getLogger(__name__)


class StaleCookiesError(RuntimeError):
    """Raised when a list= (playlist / Liked / Watch Later) URL enumerates to
    zero videos. This is almost always expired cookies, not an actually-empty
    playlist — surfaced as its own error so callers can tell the user to
    re-export youtube_cookies.txt rather than reporting "no videos"."""


@dataclass
class VideoStub:
    video_id: str
    url: str
    title: str
    upload_date: date | None


class ChannelEnumerator:
    """Enumerates videos from a YouTube channel or playlist using yt-dlp flat extraction."""

    def __init__(self, cookies_from_browser: str | None = None, cookies_file: str | None = None):
        self.cookies_from_browser = cookies_from_browser
        self.cookies_file = cookies_file
        # Set by list_videos() to the channel/playlist display title yt-dlp
        # reports, if any — cheap to expose, not part of the return value
        # since the contract is fixed. Callers that want a real display name
        # (e.g. for playlists, where the URL-guessing regex produces garbage)
        # can read this after calling list_videos().
        self.last_title: str | None = None

    @staticmethod
    def _is_playlist(url: str) -> bool:
        return "list=" in url

    @staticmethod
    def _is_shorts(entry: dict) -> bool:
        return "/shorts/" in (entry.get("url") or entry.get("webpage_url") or "")

    def list_videos(
        self,
        url: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[VideoStub]:
        """Enumerate a channel or playlist URL into VideoStubs.

        Channel URLs (@handle, /c/, /channel/, /user/) get /videos appended to
        target the videos tab (unless already pointing at a tab). Playlist URLs
        (anything with list=, including watch?v=..&list=..) are passed through
        unchanged — appending /videos to those is wrong.
        """
        import yt_dlp

        is_playlist = self._is_playlist(url)
        if is_playlist:
            target = url
        elif url.rstrip("/").endswith(("/videos", "/streams", "/shorts")):
            target = url
        else:
            target = url.rstrip("/") + "/videos"

        ydl_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
        }
        if self.cookies_file:
            ydl_opts["cookiefile"] = self.cookies_file
        elif self.cookies_from_browser:
            ydl_opts["cookiesfrombrowser"] = (self.cookies_from_browser,)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target, download=False)

        entries = (info or {}).get("entries") or []
        self.last_title = (info or {}).get("title")

        stubs = []
        for entry in entries:
            if not entry:
                continue
            video_id = entry.get("id")
            if not video_id:
                continue
            if not is_playlist and self._is_shorts(entry):
                continue

            # ponytail: flat extraction does not reliably return upload_date
            # for channel/playlist entries (yt-dlp only has it for some
            # extractors). Fail open — keep entries we cannot date rather than
            # silently dropping them. Upgrade path: a per-video metadata fetch
            # (extract_flat=False) if accurate date filtering is ever needed
            # badly enough to pay for it.
            upload_date = None
            raw_date = entry.get("upload_date")
            if raw_date:
                try:
                    upload_date = datetime.strptime(raw_date, "%Y%m%d").date()
                except ValueError:
                    upload_date = None

            if upload_date:
                if date_from and upload_date < date_from:
                    continue
                if date_to and upload_date > date_to:
                    continue

            video_url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
            stubs.append(
                VideoStub(
                    video_id=video_id,
                    url=video_url,
                    title=entry.get("title") or "Untitled",
                    upload_date=upload_date,
                )
            )

        if is_playlist and not stubs:
            raise StaleCookiesError(
                f"Playlist enumeration returned 0 videos for {url} — likely "
                "stale cookies, not an empty playlist. Re-export youtube_cookies.txt."
            )

        return stubs
