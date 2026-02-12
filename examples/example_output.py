#!/usr/bin/env python3
"""Example demonstrating the enhanced note generation output."""

import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.obsidian import ObsidianNoteGenerator
from src.scrapers.base import ScrapedContent
from src.summarizer import SummaryResult


def main():
    """Generate an example note to demonstrate all features."""

    # Simulated scraped content
    scraped = ScrapedContent(
        url="https://realpython.com/python-testing/",
        title="Getting Started With Testing in Python",
        author="Real Python",
        content="Full article content here...",
        content_type="article",
        published=datetime(2024, 3, 15),
        description="Learn how to write tests in Python",
    )

    # Simulated AI-generated summary with all fields
    summary = SummaryResult(
        summary="""This comprehensive guide covers Python testing fundamentals.

**Key Points:**
- Unit testing validates individual components in isolation
- pytest is the recommended testing framework for Python
- Test-driven development (TDD) improves code quality
- Fixtures help set up test dependencies efficiently
- Coverage tools measure how much code is tested""",
        category="Programming",
        tags=["Python", "Testing", "Software Development"],
        action_items=[
            "Install pytest with `pip install pytest`",
            "Create a tests/ directory in your project",
            "Write your first test function",
            "Run tests with `pytest -v`",
        ],
        review_questions=[
            "What is the difference between unit tests and integration tests?",
            "How does pytest discover test files and functions?",
            "What are fixtures and when should you use them?",
            "How can you measure test coverage?",
        ],
        key_concepts=["Unit Testing", "pytest", "Test Coverage", "Fixtures"],
    )

    # Simulated related links found in vault
    related_links = [
        "[[04 Indexes/Python|Python]]",
        "[[04 Indexes/Programming/Testing|Testing]]",
        "[[05 Base Notes/pytest Basics|pytest Basics]]",
    ]

    # Generate the note
    generator = ObsidianNoteGenerator(
        vault_path=Path("/tmp/vault"),
        output_folders={
            "youtube": "02 Sources/Videos",
            "article": "02 Sources/Articles",
            "default": "Clippings",
        },
    )

    note_content = generator.generate_note(
        scraped=scraped,
        summary=summary,
        user_notes="Focus on pytest fixtures for my project",
        related_links=related_links,
    )

    print("=" * 80)
    print("EXAMPLE GENERATED NOTE")
    print("=" * 80)
    print(note_content)
    print("=" * 80)


if __name__ == "__main__":
    main()
