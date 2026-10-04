"""Place classifier — one classifier for every saved thing, whatever it came from.

Sources do not get their own classifiers. Each scraper turns its item into text first (an
Instagram caption; a screenshot read by Gemini vision inside its scraper, the same way reels
are), and this classifies the text. So adding a source never touches classification, and
classification works on any provider, because it never sees an image.
"""
import json
import logging
import re
from dataclasses import dataclass

from .base import BaseAgent

logger = logging.getLogger(__name__)

CATEGORIES = ("pub", "nightlife", "food", "activity", "travel", "other")
BATCH = 20


@dataclass
class Placement:
    category: str            # one of CATEGORIES, or "unsorted" when classification failed
    venue: str | None = None  # the place's own name, if the text names one
    area: str | None = None   # neighbourhood/city to help geocoding, if stated


# "other" and "unsorted" are deliberately different answers. "other" is the model deciding
# this is a meme, a recipe or an article -- not a place. "unsorted" is the classifier failing
# to answer at all. Conflating them is the same mistake TopicFilterAgent fixed: a decisive
# answer and a failure must not look alike, or failures silently vanish into "not a place".
UNSORTED = "unsorted"


class PlaceClassifierAgent(BaseAgent):
    PROMPT = """You are sorting things someone saved (Instagram posts, screenshots) into a places list.

For each numbered item decide:
- category: one of {categories}
    pub       = a pub or bar mainly for drinking
    nightlife = clubs, late venues, gigs, events at night
    food      = restaurants, cafes, bakeries, food markets
    activity  = things to do: climbing, galleries, classes, walks, sport
    travel    = a destination elsewhere -- a city, country, hotel or trip, not a local venue
    other     = not about a place at all (memes, recipes, news, products, people)
- venue: the place's own name exactly as written, or null if no specific place is named
- area: neighbourhood, city or country if the text says one, else null

Do not invent names. "best spot in town" names no venue: venue is null.

Items:
{items}

Answer with ONLY a JSON array, one object per item, in order:
[{{"i": 0, "category": "food", "venue": "Bao Soho", "area": "Soho, London"}}, ...]"""

    def classify(self, texts: list[str]) -> list[Placement]:
        """One Placement per text, in order. Failures come back UNSORTED, never dropped."""
        out: list[Placement] = []
        for start in range(0, len(texts), BATCH):
            out.extend(self._classify_batch(texts[start:start + BATCH]))
        return out

    def _classify_batch(self, texts: list[str]) -> list[Placement]:
        unsorted = [Placement(UNSORTED) for _ in texts]
        if not texts:
            return []
        items = "\n".join(f"{i}: {_clip(t)}" for i, t in enumerate(texts))
        prompt = self.PROMPT.format(categories=", ".join(CATEGORIES), items=items)
        try:
            response = self._call(prompt)
        except Exception as e:
            logger.warning(f"Place classifier call failed, {len(texts)} left unsorted: {e}")
            return unsorted

        rows = _parse_array(response)
        if rows is None:
            logger.warning("Place classifier response unparseable, batch left unsorted")
            return unsorted

        for row in rows:
            if not isinstance(row, dict):
                continue
            i = row.get("i")
            if not isinstance(i, int) or not 0 <= i < len(texts):
                continue  # a hallucinated index must not overwrite a real item
            category = str(row.get("category") or "").strip().lower()
            if category not in CATEGORIES:
                continue  # an invented category is a failure for that item, so it stays unsorted
            unsorted[i] = Placement(category, _name(row.get("venue")), _name(row.get("area")))
        return unsorted


def _clip(text: str, limit: int = 600) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _name(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and value.lower() not in ("null", "none", "n/a", "unknown") else None


def _parse_array(response: str):
    """Fenced-then-bare JSON array, mirroring TopicFilterAgent._parse_index_array.

    ponytail: duplicated rather than shared -- three call sites now, which is the point to
    extract a util if a fourth appears.
    """
    try:
        data = json.loads(response.strip())
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, AttributeError):
        pass
    match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", response or "", re.DOTALL) \
        or re.search(r"(\[.*\])", response or "", re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return None
