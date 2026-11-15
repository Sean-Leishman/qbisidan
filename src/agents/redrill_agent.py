"""Re-drill question writer: one gap line in, one fresh recall question and its answer out."""
import json
import logging
import re

from .base import BaseAgent

logger = logging.getLogger(__name__)


class RedrillQuestionAgent(BaseAgent):
    THINKING_BUDGET = 0  # rewording one line is mechanical; see BaseAgent._call_gemini

    PROMPT = """A learner got something wrong in an earlier study session. This line records the miss
and, usually after " — ", the correct version:

{text}

Write ONE new recall question that tests the correct version, plus its answer.
- Use ONLY what this line says. Add no facts, numbers, names or examples it does not contain.
- Don't reuse the line's wording or give the answer away in the question. Ask for the idea,
  e.g. "why", "what happens when", "how many".
- The question should be one or two sentences. The answer should be at most three short sentences.
- Plain text. No markdown.

Answer with ONLY a JSON object: {{"question": "...", "answer": "..."}}"""

    def make(self, text: str) -> dict | None:
        """{"question", "answer"} or None when the reply can't be used."""
        response = self._call(self.PROMPT.format(text=text))
        match = re.search(r"\{.*\}", response or "", re.DOTALL)
        try:
            data = json.loads(match.group(0)) if match else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            logger.warning("Re-drill question reply unparseable")
            return None
        q, a = str(data.get("question") or "").strip(), str(data.get("answer") or "").strip()
        return {"question": q, "answer": a} if q and a else None
