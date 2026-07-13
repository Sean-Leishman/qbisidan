"""Reader for Instagram's "Download your information" (DYI) export — pulls
saved-post and liked-post URLs out of the export JSON so they can be fed into
the generic crawl engine as VideoStubs.

No login, no scraping, no ban risk: the user requests the export from
Instagram's own settings and drops the resulting folder somewhere on disk.
Manual re-export is the price for that — fine for an occasional backfill.

Instagram's exact export shape has drifted across versions (the wrapper key
has been seen as both `saved_saved_media` and other names, similarly for
likes), so rather than hard-code those keys this walks the whole JSON tree
looking for any dict that has a `string_map_data` or `string_list_data` key —
that inner shape has been stable even as the outer wrapper key changed. Any
record that doesn't parse is skipped, not fatal — one bad record must not
sink an entire backfill.
"""
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

from .channel import VideoStub

logger = logging.getLogger(__name__)

_SHORTCODE_RE = re.compile(r"instagram\.com/(?:reels?|p)/([\w-]+)")

_EXPORT_FILENAMES = {
    "saved": "saved_posts.json",
    "likes": "liked_posts.json",
}


class ExportNotFoundError(RuntimeError):
    """Raised when the configured export_dir has no matching export file."""


def _extract_shortcode(url: str) -> str | None:
    match = _SHORTCODE_RE.search(url)
    return match.group(1) if match else None


def _find_media_entries(node) -> list[dict]:
    """Walk any nested list/dict structure and collect dicts that look like a
    single saved/liked media record — recognised by carrying string_map_data
    (saved posts) or string_list_data (likes), regardless of the wrapper key
    Instagram's export version put them under."""
    found: list[dict] = []

    def _walk(n):
        if isinstance(n, list):
            for item in n:
                _walk(item)
        elif isinstance(n, dict):
            if "string_map_data" in n or "string_list_data" in n:
                found.append(n)
            else:
                for value in n.values():
                    _walk(value)

    _walk(node)
    return found


def _href_and_timestamp(entry: dict) -> tuple[str | None, int | None]:
    """Pull an href + unix timestamp out of one record. Tolerates both known
    shapes: string_map_data (a dict of labelled fields, saved posts) and
    string_list_data (a list of fields, likes)."""
    smd = entry.get("string_map_data")
    if isinstance(smd, dict):
        for value in smd.values():
            if isinstance(value, dict) and value.get("href"):
                return value["href"], value.get("timestamp")

    sld = entry.get("string_list_data")
    if isinstance(sld, list):
        for value in sld:
            if isinstance(value, dict) and value.get("href"):
                return value["href"], value.get("timestamp")

    return None, None


def find_export_file(export_dir: str | Path, kind: str) -> Path | None:
    """Recursively search export_dir for the export file for `kind` ("saved"
    or "likes"). Instagram nests these under version-dependent subfolders
    (e.g. your_instagram_activity/saved/), so search rather than assume a
    fixed layout."""
    root = Path(export_dir)
    filename = _EXPORT_FILENAMES.get(kind)
    if not filename or not root.exists():
        return None
    matches = sorted(root.rglob(filename))
    return matches[0] if matches else None


def load_export_stubs(
    export_path: str | Path,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[VideoStub]:
    """Parse one export JSON file (saved_posts.json or liked_posts.json) into
    VideoStubs. Skips unparseable records instead of raising."""
    path = Path(export_path)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Could not read Instagram export {path}: {e}")
        return []

    stubs = []
    for entry in _find_media_entries(data):
        try:
            href, ts = _href_and_timestamp(entry)
            if not href:
                continue
            shortcode = _extract_shortcode(href)
            if not shortcode:
                continue

            upload_date = None
            if ts:
                upload_date = datetime.fromtimestamp(ts).date()
            if upload_date:
                if date_from and upload_date < date_from:
                    continue
                if date_to and upload_date > date_to:
                    continue

            poster = entry.get("title")
            title = f"Instagram post by {poster}" if poster else f"Instagram post {shortcode}"
            stubs.append(
                VideoStub(video_id=shortcode, url=href, title=title, upload_date=upload_date)
            )
        except Exception as e:
            logger.warning(f"Skipping unparseable Instagram export record: {e}")
            continue

    return stubs


def load_instagram_export(
    export_dir: str | Path,
    kind: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[VideoStub]:
    """Locate + parse the "saved" or "likes" export under export_dir."""
    path = find_export_file(export_dir, kind)
    if not path:
        raise ExportNotFoundError(
            f"Could not find {_EXPORT_FILENAMES.get(kind, kind)} under {export_dir} — "
            "check instagram.export_dir and that the DYI export was unzipped there."
        )
    return load_export_stubs(path, date_from=date_from, date_to=date_to)
