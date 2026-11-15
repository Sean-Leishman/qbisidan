"""Re-drill question writer: one gap line in, one fresh question and its answer out.

The kind of question escalates with the miss count, because asking the same thing in new words
is not a new cue (learnharness practice doctrine; our own notes show "(4th miss)" lines that
were asked the same way every time):
  1st miss  recall      — ask for the idea itself
  2nd miss  situation   — a concrete case where it matters; the idea is never named
  3rd+      discriminate — a case where this idea and a tempting near-alternative differ
"""
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

{kind}
- Every fact the question or answer asserts as true must come from this line. You may set a
  scene (code, a bug, a measurement, a decision) but add no numbers, names or claims it lacks.
- Don't reuse the line's wording or give the answer away in the question.
- The question should be one to three sentences. The answer should be at most three short sentences.
- Plain text. No markdown.{avoid}

Answer with ONLY a JSON object: {{"question": "...", "answer": "..."}}"""

    KINDS = {
        1: "Write ONE new recall question that tests the correct version, plus its answer. Ask for "
           "the idea, e.g. \"why\", \"what happens when\", \"how many\".",
        2: "This is their SECOND miss, so asking for the idea again won't help: write ONE concrete "
           "situation in which the correct version matters, and ask what happens or what they should "
           "do. NEVER name the idea; the situation alone must bring it to mind. Then the answer.",
        3: "This is at least their THIRD miss: write ONE discrimination question, a situation where "
           "the correct version and a tempting near-alternative (the misconception in the line, if "
           "it names one) would lead to different outcomes. Ask which applies and why. NEVER name the "
           "idea in the question. Then the answer.",
    }

    def make(self, text: str, misses: int = 1, prior: list[str] | None = None) -> dict | None:
        """{"question", "answer"} or None when the reply can't be used."""
        kind = self.KINDS[min(max(misses, 1), 3)]
        avoid = ""
        if prior:
            avoid = "\n- It must differ in form and angle from every earlier question on this miss:\n" + \
                    "".join(f"  * {q}\n" for q in prior).rstrip("\n")
        response = self._call(self.PROMPT.format(text=text, kind=kind, avoid=avoid))
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
