"""AI summarization using Groq, Google Gemini, or Claude API."""
import json
import re
from dataclasses import dataclass, field

from .scrapers.base import ScrapedContent


@dataclass
class SummaryResult:
    """Structured result from AI summarization."""

    summary: str
    category: str = "Other"
    tags: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    review_questions: list[str] = field(default_factory=list)
    key_concepts: list[str] = field(default_factory=list)


class Summarizer:
    """Generate summaries using AI APIs."""

    YOUTUBE_PROMPT = """You are summarizing a YouTube video for personal notes.

Based on the transcript and metadata below, create a concise summary with:
1. A brief overview (2-3 sentences) of what the video is about
2. Key points or takeaways as bullet points (3-7 points)
3. Any actionable advice or recommendations mentioned

Keep the summary focused and practical. Use markdown formatting.

Video Title: {title}
Channel: {author}
Description: {description}
{user_notes_section}
Transcript:
{content}

Write the summary now, then provide structured metadata.

{structured_extraction_prompt}"""

    ARTICLE_PROMPT = """You are summarizing a web article for personal notes.

Based on the content below, create a concise summary with:
1. A brief overview (2-3 sentences) of the main topic
2. Key points or insights as bullet points (3-7 points)
3. Any conclusions or recommendations from the article

Keep the summary focused and practical. Use markdown formatting.

Title: {title}
Author: {author}
{user_notes_section}
Content:
{content}

Write the summary now, then provide structured metadata.

{structured_extraction_prompt}"""

    STRUCTURED_EXTRACTION = """
After your summary, extract the following metadata as JSON. Place this at the very end of your response:

```json
{{
  "category": "<one of: {categories}>",
  "tags": ["topic1", "topic2", "topic3"],
  "action_items": ["actionable task 1", "actionable task 2"],
  "review_questions": ["question for spaced repetition?", "another review question?"],
  "key_concepts": ["main concept 1", "main concept 2", "main concept 3"]
}}
```

Guidelines:
- category: Choose the SINGLE best category from the list provided
- tags: 2-5 topic keywords that describe the main subjects (e.g., "Python", "Machine Learning", "Investing")
- action_items: Specific actionable tasks mentioned or implied (leave empty if none)
- review_questions: 2-4 questions to test understanding of key points
- key_concepts: 3-5 main concepts or terms that could link to other notes"""

    def __init__(
        self,
        provider: str = "groq",
        api_key: str = "",
        model: str | None = None,
        max_tokens: int = 1024,
        routing_categories: list[str] | None = None,
    ):
        self.provider = provider
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.routing_categories = routing_categories or ["Other"]

        if provider == "groq":
            self.model = model or "llama-3.3-70b-versatile"
            self._init_groq()
        elif provider == "gemini":
            self.model = model or "gemini-2.0-flash-lite"
            self._init_gemini()
        else:  # anthropic
            self.model = model or "claude-sonnet-4-20250514"
            self._init_anthropic()

    def _init_groq(self):
        """Initialize Groq client."""
        from groq import Groq

        self.client = Groq(api_key=self.api_key)

    def _init_gemini(self):
        """Initialize Google Gemini client."""
        from google import genai

        self.client = genai.Client(api_key=self.api_key)

    def _init_anthropic(self):
        """Initialize Anthropic client."""
        from anthropic import Anthropic

        self.client = Anthropic(api_key=self.api_key)

    def _truncate_content(self, content: str, max_chars: int = 50000) -> str:
        """Truncate content to fit within context limits."""
        if len(content) <= max_chars:
            return content
        return content[:max_chars] + "\n\n[Content truncated...]"

    def _summarize_groq(self, prompt: str) -> str:
        """Generate summary using Groq."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self.max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error generating summary with Groq: {e}")
            return f"*Summary generation failed: {e}*"

    def _summarize_gemini(self, prompt: str) -> str:
        """Generate summary using Gemini."""
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            print(f"Error generating summary with Gemini: {e}")
            return f"*Summary generation failed: {e}*"

    def _summarize_anthropic(self, prompt: str) -> str:
        """Generate summary using Claude."""
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            print(f"Error generating summary with Claude: {e}")
            return f"*Summary generation failed: {e}*"

    def _parse_structured_response(self, response: str) -> SummaryResult:
        """Parse AI response to extract summary and structured JSON metadata."""
        # Try to find JSON block in the response
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)

        if json_match:
            json_str = json_match.group(1)
            # Extract summary (everything before the JSON block)
            summary = response[: json_match.start()].strip()

            try:
                data = json.loads(json_str)
                return SummaryResult(
                    summary=summary,
                    category=data.get("category", "Other"),
                    tags=data.get("tags", []),
                    action_items=data.get("action_items", []),
                    review_questions=data.get("review_questions", []),
                    key_concepts=data.get("key_concepts", []),
                )
            except json.JSONDecodeError:
                pass

        # Fallback: try to find raw JSON object without code fence
        json_match = re.search(r"\{[^{}]*\"category\"[^{}]*\}", response, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                summary = response[: json_match.start()].strip()
                return SummaryResult(
                    summary=summary,
                    category=data.get("category", "Other"),
                    tags=data.get("tags", []),
                    action_items=data.get("action_items", []),
                    review_questions=data.get("review_questions", []),
                    key_concepts=data.get("key_concepts", []),
                )
            except json.JSONDecodeError:
                pass

        # If no JSON found, return response as summary with defaults
        return SummaryResult(summary=response.strip())

    def summarize(self, scraped: ScrapedContent, user_notes: str | None = None) -> SummaryResult:
        """Generate a structured summary for the scraped content."""
        # Choose prompt based on content type
        if scraped.content_type == "youtube":
            prompt_template = self.YOUTUBE_PROMPT
        else:
            prompt_template = self.ARTICLE_PROMPT

        # Build user notes section if provided
        user_notes_section = ""
        if user_notes:
            user_notes_section = f"\nUser's specific interests/notes: {user_notes}\nPlease focus on these aspects in the summary.\n"

        # Build structured extraction prompt with categories
        categories_str = ", ".join(self.routing_categories)
        structured_prompt = self.STRUCTURED_EXTRACTION.format(categories=categories_str)

        # Build prompt with available data
        prompt = prompt_template.format(
            title=scraped.title or "Unknown",
            author=scraped.author or "Unknown",
            description=scraped.description or "No description available",
            content=self._truncate_content(scraped.content) or "No content available",
            user_notes_section=user_notes_section,
            structured_extraction_prompt=structured_prompt,
        )

        if self.provider == "groq":
            response = self._summarize_groq(prompt)
        elif self.provider == "gemini":
            response = self._summarize_gemini(prompt)
        else:
            response = self._summarize_anthropic(prompt)

        return self._parse_structured_response(response)
