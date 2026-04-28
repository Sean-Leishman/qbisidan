"""Base agent class with shared AI provider dispatch."""
import logging

logger = logging.getLogger(__name__)


class BaseAgent:
    """Base class for AI sub-agents. Handles provider dispatch."""

    def __init__(
        self,
        provider: str = "gemini",
        api_key: str = "",
        model: str | None = None,
        max_tokens: int = 1024,
    ):
        self.provider = provider
        self.api_key = api_key
        self.max_tokens = max_tokens

        if provider == "groq":
            self.model = model or "llama-3.3-70b-versatile"
            self._init_groq()
        elif provider == "gemini":
            self.model = model or "gemini-2.0-flash"
            self._init_gemini()
        else:
            self.model = model or "claude-sonnet-4-20250514"
            self._init_anthropic()

    def _init_groq(self):
        from groq import Groq
        self.client = Groq(api_key=self.api_key)

    def _init_gemini(self):
        from google import genai
        self.client = genai.Client(api_key=self.api_key)

    def _init_anthropic(self):
        from anthropic import Anthropic
        self.client = Anthropic(api_key=self.api_key)

    def _call(self, prompt: str) -> str:
        """Call the AI provider and return the response text."""
        if self.provider == "groq":
            return self._call_groq(prompt)
        elif self.provider == "gemini":
            return self._call_gemini(prompt)
        else:
            return self._call_anthropic(prompt)

    def _call_groq(self, prompt: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self.max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq API error: {e}")
            raise

    def _call_gemini(self, prompt: str) -> str:
        try:
            from google.genai import types
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=self.max_tokens),
            )
            return response.text
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise

    def _call_anthropic(self, prompt: str) -> str:
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise
