"""Full-text vault search — scores and returns ranked note snippets."""
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SearchResult:
    title: str
    folder: str
    snippet: str
    score: float


# Folders that are low-signal for general search (trackers, daily notes)
_DEPRIORITIZE = {"06 Daily Notes", "08 Trackers"}

_MAX_RESULTS = 5
_SNIPPET_CHARS = 200


class VaultSearch:
    """Keyword search across all markdown notes in the vault."""

    def __init__(self, vault_path: Path):
        self.vault_path = vault_path

    def search(self, query: str) -> list[SearchResult]:
        terms = [t.lower() for t in re.split(r"\s+", query.strip()) if t]
        if not terms:
            return []

        results: list[SearchResult] = []

        for md_file in self.vault_path.rglob("*.md"):
            # Skip hidden dirs (e.g. .smart-connections, .obsidian)
            if any(part.startswith(".") for part in md_file.parts):
                continue

            try:
                content = md_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            title = md_file.stem
            title_lower = title.lower()
            content_lower = content.lower()

            # Score: title hits count 5x, content hits count 1x
            title_hits = sum(t in title_lower for t in terms)
            content_hits = sum(content_lower.count(t) for t in terms)
            if title_hits == 0 and content_hits == 0:
                continue

            score = title_hits * 5.0 + min(content_hits, 20) * 1.0

            # Deprioritise daily notes and trackers
            rel = md_file.relative_to(self.vault_path)
            top_folder = rel.parts[0] if rel.parts else ""
            if top_folder in _DEPRIORITIZE:
                score *= 0.3

            folder = str(rel.parent) if str(rel.parent) != "." else ""
            snippet = self._extract_snippet(content, terms)
            results.append(SearchResult(title=title, folder=folder, snippet=snippet, score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:_MAX_RESULTS]

    def _extract_snippet(self, content: str, terms: list[str]) -> str:
        """Find the sentence/line with the most term hits and return a short excerpt."""
        # Strip YAML frontmatter
        body = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL)
        # Strip markdown syntax for cleaner display
        body = re.sub(r"[#*`>\[\]_]", "", body)

        lines = [l.strip() for l in body.splitlines() if l.strip()]
        if not lines:
            return ""

        best_line = max(lines, key=lambda l: sum(t in l.lower() for t in terms))

        # Trim to SNIPPET_CHARS, breaking at a word boundary
        if len(best_line) > _SNIPPET_CHARS:
            best_line = best_line[:_SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"

        return best_line


def format_search_results(query: str, results: list[SearchResult]) -> str:
    """Format results as a Telegram Markdown message."""
    if not results:
        return f"No results for *{query}*"

    lines = [f"*{len(results)} result{'s' if len(results) != 1 else ''} for \"{query}\"*\n"]
    for r in results:
        folder_label = f"`{r.folder}`  " if r.folder else ""
        lines.append(f"*{r.title}*")
        lines.append(f"{folder_label}")
        if r.snippet:
            lines.append(f"_{r.snippet}_")
        lines.append("")

    return "\n".join(lines).strip()
