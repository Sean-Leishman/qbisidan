"""AI summarization using Groq, Google Gemini, or Claude API."""
import json
import re
from dataclasses import dataclass, field

from .agents.base import BaseAgent
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
    content_truncated: bool = False


class Summarizer(BaseAgent):
    """Generate summaries using AI APIs with Obsidian-optimized outputs."""

    YOUTUBE_PROMPT = """You are summarizing a YouTube video for high-quality personal notes.

Your goal is to extract signal over noise — focus on insights, not surface-level points.

Based on the transcript and metadata below, produce:

## 1. Overview
2–4 sentences covering:
- What the video is about
- Who it's for
- The main value or thesis

## 2. Section-by-Section Breakdown
Identify the video's natural segments. Represent each as a top-level list item with its timestamp range and title, then nest key points beneath it as sub-bullets.
Format:
- **[MM:SS–MM:SS] Segment Title**
  - Key point (as many as the content warrants)
  - ...

Rules:
- CRITICAL: Cover the ENTIRE video from start to finish — do not stop early. Every minute of content must appear in at least one segment.
- No sub-headings — everything is a nested list under this section
- Denser, higher-value segments deserve more sub-bullets; thin or transitional ones fewer
- Each sub-bullet must be specific and insight-rich (avoid generic phrasing)
- Capture what was *learned*, not just what was *said*
- State the conclusion or outcome directly, not that something was discussed — e.g. "Sleep deprivation reduces recall by ~40%" not "Sleep and memory were discussed"
- If the answer, finding, or recommendation is clear, lead with it
- Include brief reasoning where relevant ("X leads to Y because...")
- Avoid vague advice like "be consistent" unless it comes with a concrete mechanism
- If the transcript includes timestamps, use them to anchor each segment; the last segment must end at or near the video's total duration

## 3. Actionable Insights
Concrete actions, habits, or strategies derived from the video.
Infer actions if not explicitly stated.

## 4. Notable Ideas / Concepts (optional)
Frameworks, mental models, or unique perspectives introduced.

Style Guidelines:
- Use markdown
- Be concise but dense
- Avoid fluff or repetition
- Prioritize depth over coverage

Video Title: {title}
Channel: {author}
Description: {description}
{user_notes_section}
{existing_notes_section}

Transcript:
{content}

Write the summary now, then provide structured metadata.

{structured_extraction_prompt}"""

    ARTICLE_PROMPT = """You are summarizing a web article for high-quality personal knowledge retention.

Your goal is to extract the core insights and reasoning, not just restate content.

## 1. Overview
2–4 sentences covering:
- The topic and central argument
- Why it matters

## 2. Section-by-Section Breakdown
Identify the article's natural sections or logical blocks (use headings if present, or infer them). Represent each as a top-level list item, then nest key points beneath it as sub-bullets.
Format:
- **Section Title**
  - Key point (as many as the content warrants)
  - ...

Rules:
- No sub-headings — everything is a nested list under this section
- Denser, higher-value sections deserve more sub-bullets; thin sections fewer
- Each sub-bullet must contain a meaningful idea with reasoning or context
- Capture what was *learned*, not just what was *said*
- State the conclusion or outcome directly — e.g. "The study found X increases Y by Z%" not "The study examined the relationship between X and Y"
- If the finding, argument, or recommendation is clear, lead with it rather than describing that it was made
- Explain *why* each point matters, not just what it says
- Avoid generic restatements

## 3. Conclusions / Implications
What should be taken away. Broader meaning or consequences.

## 4. Practical Applications
Concrete ways to apply the ideas. Infer if not explicit.

Style Guidelines:
- Dense, useful, non-generic
- Avoid repetition
- Focus on clarity

Title: {title}
Author: {author}
{user_notes_section}
{existing_notes_section}

Content:
{content}

Write the summary now, then provide structured metadata.

{structured_extraction_prompt}"""

    STRUCTURED_EXTRACTION = """
After your summary, extract metadata as JSON at the very end enclosed in ```json fences:

```json
{{
  "category": "<one of: {categories}>",
  "tags": [],
  "action_items": [],
  "review_questions": [],
  "key_concepts": []
}}
```

Fields:

category: choose ONE from the list above

tags:
- Generate 3–6 plain text topic terms — NO [[brackets]], NO # symbols, NO special formatting
- Each tag is simply the concept name, e.g. "Machine Learning", "Python", "Sleep Science"
- Use Title Case for multi-word terms
- Pick specific, meaningful topics (not vague labels like "interesting" or "overview")

action_items:
- Must start with verbs
- Be concrete and actionable

review_questions:
- Focus on understanding (why/how)

key_concepts:
- 3–5 important ideas or frameworks mentioned in the content"""

    def __init__(
        self,
        provider: str = "groq",
        api_key: str = "",
        model: str | None = None,
        max_tokens: int = 1024,
        routing_categories: list[str] | None = None,
        existing_notes: list[str] | None = None,
    ):
        super().__init__(provider=provider, api_key=api_key, model=model, max_tokens=max_tokens)
        self.routing_categories = routing_categories or ["Other"]
        self.existing_notes = existing_notes or []

    def _truncate_content(self, content: str, max_chars: int = 50000) -> tuple[str, bool]:
        if len(content) <= max_chars:
            return content, False
        return content[:max_chars] + "\n\n[Content truncated...]", True

    def _build_existing_notes_section(self) -> str:
        if not self.existing_notes:
            return ""
        notes = "\n".join(f"- {n}" for n in self.existing_notes[:50])
        return f"\nExisting notes in my knowledge base:\n{notes}\n\nIf a tag matches one of these, use the EXACT [[Wiki Link]] name.\n"

    def _extract_json_from_response(self, response: str) -> tuple[dict | None, str]:
        """Extract the metadata JSON and clean summary text from the AI response.

        Returns (data_dict_or_None, clean_summary_text).
        """
        # 1. Fenced ```json ... ``` block
        match = re.search(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                return data, response[: match.start()].strip()
            except json.JSONDecodeError:
                pass

        # 2. Walk the string to find the last top-level { } block containing "category"
        last_start = -1
        depth = 0
        for i, ch in enumerate(response):
            if ch == "{":
                if depth == 0:
                    last_start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and last_start >= 0:
                    candidate = response[last_start : i + 1]
                    if '"category"' in candidate:
                        try:
                            data = json.loads(candidate)
                            return data, response[:last_start].strip()
                        except json.JSONDecodeError:
                            last_start = -1  # reset and keep searching

        # 3. No parseable JSON found — return the response as-is but strip any
        #    trailing raw-JSON-looking block so it doesn't end up in the note.
        clean = re.sub(r"\s*\{[^{}]{0,2000}\}\s*$", "", response, flags=re.DOTALL).strip()
        return None, clean

    def _parse_structured_response(self, response: str) -> SummaryResult:
        data, summary = self._extract_json_from_response(response)
        if data is None:
            return SummaryResult(summary=summary)
        return SummaryResult(
            summary=summary,
            category=data.get("category", "Other"),
            tags=data.get("tags", []),
            action_items=data.get("action_items", []),
            review_questions=data.get("review_questions", []),
            key_concepts=data.get("key_concepts", []),
        )

    def summarize(self, scraped: ScrapedContent, user_notes: str | None = None) -> SummaryResult:
        if scraped.content_type == "youtube":
            prompt_template = self.YOUTUBE_PROMPT
        else:
            prompt_template = self.ARTICLE_PROMPT

        user_notes_section = ""
        if user_notes:
            user_notes_section = f"\nUser priorities:\n{user_notes}\n\nPrioritize these topics when extracting insights.\n"

        structured_prompt = self.STRUCTURED_EXTRACTION.format(
            categories=", ".join(self.routing_categories)
        )

        truncated_content, was_truncated = self._truncate_content(scraped.content or "")

        prompt = prompt_template.format(
            title=scraped.title or "Unknown",
            author=scraped.author or "Unknown",
            description=scraped.description or "",
            content=truncated_content,
            user_notes_section=user_notes_section,
            existing_notes_section=self._build_existing_notes_section(),
            structured_extraction_prompt=structured_prompt,
        )

        response = self._call(prompt)
        result = self._parse_structured_response(response)
        result.content_truncated = was_truncated
        return result
