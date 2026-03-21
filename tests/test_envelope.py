"""Unit tests for mcp/envelope.py."""
import sys
import os

# Ensure the mcp package root is on the path so `envelope` can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from envelope import Envelope, parse, render


class TestParse:
    def test_plain_body_no_separator(self):
        body = "Hello, this is a plain message."
        env = parse(body)
        assert env.preamble == {}
        assert env.display_text == body

    def test_empty_body(self):
        env = parse("")
        assert env.preamble == {}
        assert env.display_text == ""

    def test_basic_envelope(self):
        body = "TASK: abc-123\nSTAGE: design\n---\nPlease review the design."
        env = parse(body)
        assert env.preamble == {"TASK": ["abc-123"], "STAGE": ["design"]}
        assert env.display_text == "Please review the design."

    def test_multi_token_label(self):
        body = "TASKS: id-1 id-2 id-3\n---\nMultiple tasks."
        env = parse(body)
        assert env.preamble["TASKS"] == ["id-1", "id-2", "id-3"]

    def test_display_text_trimmed(self):
        body = "TASK: x\n---\n\n  Hello world  \n"
        env = parse(body)
        assert env.display_text == "Hello world"

    def test_separator_only_line(self):
        """A lone --- with no body gives empty display_text."""
        body = "TASK: x\n---"
        env = parse(body)
        assert env.display_text == ""

    def test_body_with_dashes_not_separator(self):
        """A line with more or fewer dashes is not the separator."""
        body = "Some text\n----\nMore text"
        env = parse(body)
        assert env.preamble == {}
        assert env.display_text == body

    def test_invalid_preamble_lines_ignored(self):
        """Lines that don't match LABEL: tokens are silently skipped."""
        body = "not a label\nTASK: abc\n---\nBody."
        env = parse(body)
        assert "not a label" not in env.preamble
        assert env.preamble == {"TASK": ["abc"]}

    def test_lowercase_label_ignored(self):
        body = "task: abc\n---\nBody."
        env = parse(body)
        assert env.preamble == {}

    def test_first_separator_wins(self):
        """Only the first --- splits; subsequent --- are part of display_text."""
        body = "TASK: x\n---\nParagraph one.\n---\nParagraph two."
        env = parse(body)
        assert env.display_text == "Paragraph one.\n---\nParagraph two."


class TestRender:
    def test_plain_message(self):
        assert render({}, "Hello.") == "Hello."

    def test_single_label(self):
        result = render({"TASK": ["abc"]}, "Body here.")
        assert result == "TASK: abc\n---\nBody here."

    def test_multiple_labels(self):
        result = render({"TASK": ["abc"], "STAGE": ["design"]}, "Body.")
        assert "TASK: abc" in result
        assert "STAGE: design" in result
        assert "---" in result
        assert result.endswith("Body.")

    def test_render_parse_roundtrip(self):
        preamble = {"TASK": ["xyz-999"], "DIRECTION": ["forward"], "RUN_ID": ["run-1"]}
        display = "Task has been handed off."
        body = render(preamble, display)
        env = parse(body)
        assert env.preamble == preamble
        assert env.display_text == display
