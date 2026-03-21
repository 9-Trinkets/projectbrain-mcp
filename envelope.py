"""Memo-style message envelope for structured agent handoffs.

Format
------
LABEL1: token1 token2
LABEL2: token3
---
Human-readable text visible to users and in the MCP get_messages output.

Rules
-----
- Preamble lines follow ``LABEL: space-separated-tokens`` (label is [A-Z][A-Z0-9_]*).
- Separator is a line containing exactly ``---``.
- Everything after the separator is the display text (shown to humans / non-agent readers).
- Messages without a ``---`` line are treated as plain body:
  empty preamble, display_text == full body.  Backward compatible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_LABEL_RE = re.compile(r"^([A-Z][A-Z0-9_]*):\s*(.+)$")
_SEPARATOR = "---"


@dataclass
class Envelope:
    """Parsed message envelope.

    Attributes
    ----------
    preamble:
        Mapping of label → list of tokens extracted from the structured
        header above the ``---`` separator.  Empty for plain messages.
    display_text:
        The human-readable portion of the message (below the separator,
        or the full body when there is no separator).
    """

    preamble: dict[str, list[str]] = field(default_factory=dict)
    display_text: str = ""


def parse(body: str) -> Envelope:
    """Parse a message body into an :class:`Envelope`.

    Falls back gracefully: if no ``---`` separator exists the entire body
    is returned as ``display_text`` with an empty preamble.
    """
    lines = body.split("\n")
    sep_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == _SEPARATOR:
            sep_idx = i
            break

    if sep_idx is None:
        return Envelope(preamble={}, display_text=body)

    preamble: dict[str, list[str]] = {}
    for line in lines[:sep_idx]:
        m = _LABEL_RE.match(line.strip())
        if m:
            label, tokens_str = m.group(1), m.group(2)
            preamble[label] = tokens_str.split()

    display_text = "\n".join(lines[sep_idx + 1 :]).strip()
    return Envelope(preamble=preamble, display_text=display_text)


def render(preamble: dict[str, list[str]], display_text: str) -> str:
    """Render a preamble dict and display text back into an envelope string.

    If *preamble* is empty the display text is returned as-is so that
    plain messages remain plain.
    """
    if not preamble:
        return display_text
    header = "\n".join(f"{label}: {' '.join(tokens)}" for label, tokens in preamble.items())
    return f"{header}\n{_SEPARATOR}\n{display_text}"
