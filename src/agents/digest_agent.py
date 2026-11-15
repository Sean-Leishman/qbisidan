"""Digest agent: turns a grounded digest prompt (src/digest.py) into section lists."""
import json
import logging
import re

from .base import BaseAgent

logger = logging.getLogger(__name__)


class DigestAgent(BaseAgent):
    TEMPERATURE = 0.2  # a draft of the user's own week: low creativity is the point

    def draft(self, prompt: str) -> dict | None:
        """The model's JSON object, or None when the call or the parse fails."""
        try:
            response = self._call(prompt)
        except Exception as e:
            logger.warning(f"Digest call failed: {e}")
            return None
        return _parse_object(response)


def _parse_object(response: str) -> dict | None:
    """Fenced-then-bare JSON object, as place_classifier._parse_array does for arrays."""
    for candidate in (
        (response or "").strip(),
        *(m.group(1) for m in [re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response or "", re.DOTALL)] if m),
        *(m.group(1) for m in [re.search(r"(\{.*\})", response or "", re.DOTALL)] if m),
    ):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    logger.warning("Digest response unparseable")
    return None
