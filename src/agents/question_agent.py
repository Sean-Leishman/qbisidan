"""Question generation sub-agent for video content."""
import json
import logging
import re

from .base import BaseAgent

logger = logging.getLogger(__name__)


class QuestionAgent(BaseAgent):
    """Generates Socratic review questions from video summaries."""

    PROMPT = """You are generating review questions for a video I just watched.

Your goal: help me deeply engage with and retain the material through thoughtful questions.

Video title: {title}

Summary:
{summary}

Generate 4-6 questions that:
- Test understanding of WHY and HOW, not just WHAT
- Challenge me to connect ideas to things I already know
- Push me to think about practical applications
- Include at least one "what if" or counterfactual question
- Are specific to this content (not generic like "what did you learn?")

Return ONLY a JSON array of strings, no other text:
["Question 1?", "Question 2?", ...]"""

    def run(self, summary_text: str, title: str) -> list[str]:
        """Generate review questions from a video summary."""
        prompt = self.PROMPT.format(title=title, summary=summary_text)

        try:
            response = self._call(prompt)
            return self._parse_questions(response)
        except Exception as e:
            logger.error(f"Question generation failed: {e}")
            return []

    def _parse_questions(self, response: str) -> list[str]:
        """Parse JSON array of questions from response."""
        # Try direct JSON parse
        try:
            questions = json.loads(response.strip())
            if isinstance(questions, list):
                return [q for q in questions if isinstance(q, str)]
        except json.JSONDecodeError:
            pass

        # Try extracting JSON array from markdown code block
        match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", response, re.DOTALL)
        if match:
            try:
                questions = json.loads(match.group(1))
                if isinstance(questions, list):
                    return [q for q in questions if isinstance(q, str)]
            except json.JSONDecodeError:
                pass

        # Try finding any JSON array
        match = re.search(r"\[.*?\]", response, re.DOTALL)
        if match:
            try:
                questions = json.loads(match.group(0))
                if isinstance(questions, list):
                    return [q for q in questions if isinstance(q, str)]
            except json.JSONDecodeError:
                pass

        logger.warning("Could not parse questions from response, returning empty list")
        return []
