"""/ai <pasted answer>: a model's output saved verbatim as reference material in 02 Sources/AI."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.agents.vault_writer import VaultWriter
from src.commands import parse_message
from src.processor import Processor


class TestParse:
    def test_plain(self):
        cmd = parse_message("/ai Rust's borrow checker enforces aliasing XOR mutability.")
        assert cmd.command_type == "ai" and cmd.model is None
        assert cmd.text == "Rust's borrow checker enforces aliasing XOR mutability."

    def test_model_label(self):
        cmd = parse_message("/ai Claude: The answer is 42.")
        assert cmd.command_type == "ai" and cmd.model == "claude" and cmd.text == "The answer is 42."

    def test_only_known_labels(self):
        cmd = parse_message("/ai Note: this stays in the text")
        assert cmd.model is None and cmd.text == "Note: this stays in the text"

    def test_a_pasted_link_does_not_make_it_a_bookmark(self):
        cmd = parse_message("/ai chatgpt: see https://example.com for more\n\n- point one")
        assert cmd.command_type == "ai" and cmd.model == "chatgpt" and cmd.text.endswith("- point one")

    def test_other_slash_words_are_not_ai(self):
        assert parse_message("/aim high").command_type != "ai"


class TestNote:
    def test_content(self, tmp_path):
        text = "# Borrowing\n\nShared **or** mutable: never both.\n\n```rust\nlet x = &mut y;\n```"
        path = VaultWriter(vault_path=tmp_path).create_ai_output_note(text, "claude")
        assert path.parent == tmp_path / "02 Sources" / "AI"
        assert path.name == "Borrowing.md"
        head, body = path.read_text().split("---\n\n", 1)
        assert 'title: "Borrowing"' in head
        assert "type: ai-output" in head and "model: claude" in head and "  - ai-output" in head
        assert "created: " in head
        assert body == text + "\n"

    def test_no_model_line_without_a_label(self, tmp_path):
        path = VaultWriter(vault_path=tmp_path).create_ai_output_note("short answer")
        assert "model:" not in path.read_text()

    def test_same_title_does_not_clobber(self, tmp_path):
        w = VaultWriter(vault_path=tmp_path)
        first, second = w.create_ai_output_note("same words"), w.create_ai_output_note("same words")
        assert first != second and second.name == "same words (1).md"
        assert first.read_text().endswith("same words\n")


def test_reply_names_the_note(tmp_path):
    p = Processor.__new__(Processor)
    p.telegram = MagicMock()
    p.vault_writer = VaultWriter(vault_path=tmp_path)
    result = p._process_ai(parse_message("/ai gpt: Use a heap."), SimpleNamespace())
    assert result.success and result.message == "Saved AI output: Use a heap."
    assert "model: gpt" in (tmp_path / "02 Sources" / "AI" / "Use a heap..md").read_text()
