"""Obsidian note generation - creates markdown files matching vault format."""
import re
from datetime import datetime
from pathlib import Path

from .scrapers.base import ScrapedContent
from .summarizer import SummaryResult


class ObsidianNoteGenerator:
    """Generate Obsidian-compatible markdown notes."""

    def __init__(self, vault_path: Path, output_folders: dict):
        self.vault_path = vault_path
        self.output_folders = output_folders

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize string for use as filename."""
        # Remove or replace invalid characters
        name = re.sub(r'[<>:"/\\|?*]', "", name)
        # Replace multiple spaces with single space
        name = re.sub(r"\s+", " ", name)
        # Trim and limit length
        name = name.strip()[:200]
        return name

    def _format_author_wikilink(self, author: str | None) -> str:
        """Format author as Obsidian wikilink."""
        if not author:
            return ""
        return f"[[{author}]]"

    def _format_date(self, dt: datetime | None) -> str:
        """Format datetime for YAML frontmatter."""
        if not dt:
            return ""
        return dt.strftime("%Y-%m-%d")

    def _format_datetime_iso(self, dt: datetime | None) -> str:
        """Format datetime as ISO string."""
        if not dt:
            return datetime.now().isoformat()
        return dt.isoformat()

    def _get_output_folder(self, content_type: str) -> str:
        """Get the output folder based on content type."""
        if content_type == "youtube":
            return self.output_folders.get("youtube", "02 Sources/Videos")
        elif content_type == "instagram":
            return self.output_folders.get("instagram", "02 Sources/Reels")
        elif content_type == "article":
            return self.output_folders.get("article", "02 Sources/Articles")
        return self.output_folders.get("default", "Clippings")

    def _generate_filename(self, scraped: ScrapedContent) -> str:
        """Generate filename for the note."""
        if scraped.content_type == "youtube" and scraped.author:
            # Format: "Author – Title.md" (but avoid duplication if author in title)
            if scraped.author.lower() in scraped.title.lower():
                name = scraped.title
            else:
                name = f"{scraped.author} – {scraped.title}"
        else:
            name = scraped.title

        return self._sanitize_filename(name) + ".md"

    def _format_tag_links(self, resolved_tags: list[str]) -> str:
        """Join pre-resolved tag wikilinks into a space-separated string."""
        return " ".join(resolved_tags) if resolved_tags else ""

    def generate_note(
        self,
        scraped: ScrapedContent,
        summary: SummaryResult,
        user_notes: str | None = None,
        related_links: list[str] | None = None,
        resolved_tags: list[str] | None = None,
        resolved_sources: list[str] | None = None,
    ) -> str:
        """Generate the markdown content for an Obsidian note."""
        now = datetime.now()
        tag = "videos" if scraped.content_type == "youtube" else "articles"

        # Build YAML frontmatter
        frontmatter_lines = [
            "---",
            f'title: "{scraped.title}"',
            "author:",
            f'  - "{self._format_author_wikilink(scraped.author)}"' if scraped.author else '  - ""',
            f"published: {self._format_date(scraped.published)}",
            f'source: "{scraped.url}"',
        ]

        if scraped.image_url:
            frontmatter_lines.append(f'image: "{scraped.image_url}"')

        frontmatter_lines.extend(
            [
                f"created: {self._format_date(now)}",
                "tags:",
                f'  - "{tag}"',
                "---",
            ]
        )

        frontmatter = "\n".join(frontmatter_lines)

        # Build tags line with pre-resolved vault wikilinks
        tags_line = self._format_tag_links(resolved_tags or [])

        # Build properties callout
        properties = f"""> [!note]- **Properties**
> **Created:** {self._format_datetime_iso(now)}
> **Published:** {self._format_datetime_iso(scraped.published) if scraped.published else ""}
> **Source:** {scraped.url}
> **Origin:**
> **Status:** #Inbox
> **Tags:** {tags_line}"""

        # Build content sections
        content_parts = [
            frontmatter,
            properties,
            "",
            "---",
            "# Overview",
            "",
        ]

        if summary.content_truncated:
            content_parts.append(
                "> [!warning] Source content was truncated before summarisation"
                " — the summary may not cover the full video/article."
            )
            content_parts.append("")

        content_parts += [
            "## Summary",
            summary.summary,
        ]

        # Mentioned sources — pre-resolved wikilinks, generator only renders
        if resolved_sources:
            content_parts.append("")
            content_parts.append("Mentioned sources: " + ", ".join(resolved_sources))

        # Add Key Concepts section with related note links
        if related_links:
            content_parts.append("")
            content_parts.append("## Key Concepts")
            for link in related_links:
                content_parts.append(f"- {link}")

        # Add Action Items as checklist
        if summary.action_items:
            content_parts.append("")
            content_parts.append("## Action Items")
            for item in summary.action_items:
                content_parts.append(f"- [ ] {item}")

        # Add Review Questions
        if summary.review_questions:
            content_parts.append("")
            content_parts.append("## Review Questions")
            for i, question in enumerate(summary.review_questions, 1):
                content_parts.append(f"{i}. {question}")

        # Add Notes section
        content_parts.append("")
        content_parts.append("## Notes")

        # Add user notes if provided
        if user_notes:
            content_parts.append(f"> [!tip] My Notes")
            content_parts.append(f"> {user_notes}")
            content_parts.append("")

        content_parts.append("")
        content_parts.append("---")

        # Add embed for YouTube videos
        if scraped.content_type == "youtube":
            content_parts.append(f"![{scraped.title}]({scraped.url})")

        return "\n".join(content_parts)

    def save_note(
        self,
        scraped: ScrapedContent,
        summary: SummaryResult,
        user_notes: str | None = None,
        related_links: list[str] | None = None,
        folder_override: str | None = None,
        resolved_tags: list[str] | None = None,
        resolved_sources: list[str] | None = None,
    ) -> tuple[Path, str]:
        """Save the note to the appropriate folder in the vault.

        Args:
            scraped: Scraped content from URL
            summary: Structured summary result from AI
            user_notes: Optional user-provided notes
            related_links: List of wikilinks to related notes
            folder_override: Optional folder path to override default routing

        Returns:
            Tuple of (file_path, relative_folder_path)
        """
        # Use override folder if provided, otherwise default
        if folder_override:
            folder = folder_override
        else:
            folder = self._get_output_folder(scraped.content_type)

        filename = self._generate_filename(scraped)
        content = self.generate_note(
            scraped, summary, user_notes, related_links, resolved_tags, resolved_sources
        )

        # Create full path
        full_folder = self.vault_path / folder
        full_folder.mkdir(parents=True, exist_ok=True)
        file_path = full_folder / filename

        # Handle duplicate filenames
        counter = 1
        while file_path.exists():
            base_name = self._sanitize_filename(
                f"{scraped.author} – {scraped.title}" if scraped.author else scraped.title
            )
            filename = f"{base_name} ({counter}).md"
            file_path = full_folder / filename
            counter += 1

        # Write file
        file_path.write_text(content, encoding="utf-8")

        return file_path, folder
