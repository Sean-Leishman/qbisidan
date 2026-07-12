"""Topic-relevance filter sub-agent for channel crawls.

Two stages, both driven by the same free-text topic string:
  - filter_titles: cheap title-only pre-filter, before anything is scraped.
  - is_relevant: transcript/description-excerpt filter, after a scrape.

Both FAIL OPEN — on any LLM or parse error, keep the content. A video wrongly
dropped is invisible; a video wrongly kept is just a note you delete.
"""
import json
import logging
import re

from .base import BaseAgent

logger = logging.getLogger(__name__)


class TopicFilterAgent(BaseAgent):
    """Filters crawl candidates against a free-text topic."""

    TITLE_PROMPT = """You are filtering a list of video titles for relevance to a topic.

Topic: {topic}

Titles (0-indexed):
{numbered_titles}

Return ONLY a JSON array of the indices of titles that ARE relevant to the topic. No other text.
Example: [0, 2, 5]"""

    RELEVANCE_PROMPT = """Does this video match the topic below? Answer with a single word: YES or NO.

Topic: {topic}

Title: {title}

Excerpt:
{excerpt}

Answer (YES or NO):"""

    def filter_titles(self, titles: list[str], topic: str) -> list[int]:
        """Return indices of titles relevant to topic. Keeps everything on failure."""
        if not titles:
            return []
        keep_all = list(range(len(titles)))
        numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(titles))
        prompt = self.TITLE_PROMPT.format(topic=topic, numbered_titles=numbered)

        try:
            response = self._call(prompt)
        except Exception as e:
            logger.warning(f"Title filter LLM call failed, keeping all {len(titles)}: {e}")
            return keep_all

        indices = self._parse_index_array(response)
        if indices is None:
            logger.warning("Title filter response unparseable, keeping all")
            return keep_all

        # A valid empty array means "nothing here matches the topic" — a real answer,
        # not a failure. Only an *unparseable* response (indices is None, above) falls
        # back to keep-all. Conflating the two turns a decisive filter into no filter.
        if not indices:
            return []

        # Guard against a hallucinating model returning out-of-range/garbage indices —
        # the caller does stubs[i] for i in keep_ids, so a bad index would crash the crawl.
        valid = sorted({i for i in indices if isinstance(i, int) and 0 <= i < len(titles)})
        if not valid:
            logger.warning("Title filter returned only garbage indices, keeping all")
            return keep_all
        return valid

    def is_relevant(self, title: str, excerpt: str, topic: str) -> bool:
        """Stage-2 filter over description+transcript excerpt. Keeps on any failure."""
        prompt = self.RELEVANCE_PROMPT.format(topic=topic, title=title, excerpt=excerpt)
        try:
            response = self._call(prompt)
        except Exception as e:
            logger.warning(f"Relevance filter LLM call failed for '{title}', keeping: {e}")
            return True

        answer = response.strip().upper()
        has_no = bool(re.search(r"\bNO\b", answer))
        has_yes = bool(re.search(r"\bYES\b", answer))
        if has_no and not has_yes:
            return False
        # Anything else (YES, garbage, empty) — keep. A false "drop" is the expensive mistake.
        return True

    def _parse_index_array(self, response: str):
        """Parse a JSON array from the response.

        ponytail: mirrors question_agent._parse_questions's fenced-then-bare
        approach rather than sharing a util — two call sites don't earn an
        abstraction yet.
        """
        try:
            data = json.loads(response.strip())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

        match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", response, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        match = re.search(r"\[.*?\]", response, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        return None
