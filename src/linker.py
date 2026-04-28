"""Note linking engine - finds and links to existing notes in the vault."""
import re
import time
from pathlib import Path


class NoteLinkEngine:
    """Find and link to existing notes across the vault."""

    SCAN_FOLDERS = ["03 Tags", "04 Indexes", "05 Base Notes"]
    CACHE_TTL_SECONDS = 300  # 5 minutes

    def __init__(self, vault_path: Path):
        self.vault_path = vault_path
        self._index_cache: dict[str, Path] | None = None
        self._cache_time: float | None = None

    def _build_index(self) -> dict[str, Path]:
        """Build index of note titles -> file paths.

        Scans: 03 Tags, 04 Indexes, 05 Base Notes
        Returns: {normalized_title: path}
        """
        # Return cached index if still valid
        if (
            self._index_cache is not None
            and self._cache_time is not None
            and (time.time() - self._cache_time) < self.CACHE_TTL_SECONDS
        ):
            return self._index_cache

        index: dict[str, Path] = {}

        for folder_name in self.SCAN_FOLDERS:
            folder_path = self.vault_path / folder_name
            if not folder_path.exists():
                continue

            for md_file in folder_path.rglob("*.md"):
                title = md_file.stem  # Filename without extension
                # Store with lowercase key for case-insensitive matching
                index[title.lower()] = md_file

        self._index_cache = index
        self._cache_time = time.time()
        return index

    def find_related_notes(
        self, concepts: list[str], tags: list[str]
    ) -> list[str]:
        """Find existing notes that match concepts/tags.

        Uses fuzzy matching to find relevant notes.
        Returns list of wikilinks like '[[04 Indexes/Python|Python]]'
        """
        index = self._build_index()
        related: list[str] = []
        seen_paths: set[Path] = set()

        # Combine and deduplicate search terms
        search_terms = list(dict.fromkeys(concepts + tags))

        for term in search_terms:
            normalized = term.lower().strip()
            if not normalized:
                continue

            for title, path in index.items():
                if path in seen_paths:
                    continue

                # Match conditions:
                # 1. Exact match
                # 2. Term is contained in title
                # 3. Title is contained in term
                if (
                    normalized == title
                    or normalized in title
                    or title in normalized
                ):
                    # Use the original filename's casing for display
                    display_name = path.stem
                    wikilink = self._make_wikilink(path, display_name)
                    related.append(wikilink)
                    seen_paths.add(path)

        # Limit to 7 most relevant
        return related[:7]

    def _make_wikilink(self, path: Path, display: str) -> str:
        """Create wikilink with path relative to vault root."""
        rel_path = path.relative_to(self.vault_path)
        # Remove .md extension for wikilink
        link_path = str(rel_path.with_suffix(""))
        return f"[[{link_path}|{display}]]"

    def find_note_by_url(self, url: str) -> str | None:
        """Return the note title if any existing vault file references this URL.

        Scans all markdown files; returns the stem of the first match.
        """
        for md_file in self.vault_path.rglob("*.md"):
            if any(part.startswith(".") for part in md_file.parts):
                continue
            try:
                if url in md_file.read_text(encoding="utf-8", errors="ignore"):
                    return md_file.stem
            except OSError:
                pass
        return None

    def resolve_tags(self, raw_tags: list[str]) -> list[str]:
        """Resolve plain-text tag terms against the vault.

        For each term:
        - If a note exists in 03 Tags or 04 Indexes → wikilink to it
        - Otherwise → create a minimal note in 03 Tags and wikilink to it

        Returns a list of wikilink strings like ``[[03 Tags/Python|Python]]``.
        """
        index = self._build_index()
        result: list[str] = []
        seen: set[str] = set()

        for raw in raw_tags:
            term = self._normalize_tag(raw)
            if not term or term.lower() in seen:
                continue
            seen.add(term.lower())

            if term.lower() in index:
                path = index[term.lower()]
                rel = path.relative_to(self.vault_path)
                link_path = str(rel.with_suffix(""))
                result.append(f"[[{link_path}|{path.stem}]]")
            else:
                # Create a minimal tag note
                tag_file = self.vault_path / "03 Tags" / f"{term}.md"
                tag_file.parent.mkdir(parents=True, exist_ok=True)
                if not tag_file.exists():
                    tag_file.write_text(f"# {term}\n", encoding="utf-8")
                # Add to cache so duplicate terms in the same run resolve correctly
                index[term.lower()] = tag_file
                self._index_cache = index
                result.append(f"[[03 Tags/{term}|{term}]]")

        return result

    @staticmethod
    def _normalize_tag(raw: str) -> str:
        """Strip [[...]], #, and extra whitespace from an AI-generated tag term."""
        raw = raw.strip()
        # [[Display|Target]] or [[Target]]
        raw = re.sub(r"^\[\[(?:[^\]|]+\|)?([^\]]+)\]\]$", r"\1", raw)
        # Leading #
        raw = raw.lstrip("#").strip()
        return raw

    def clear_cache(self) -> None:
        """Clear the note index cache."""
        self._index_cache = None
        self._cache_time = None
