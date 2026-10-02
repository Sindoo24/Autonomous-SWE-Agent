"""Text normalisation helpers."""

from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_length: int = 60) -> str:
    """Lower-case ASCII slug: "Hello, World 2024!" -> "hello-world-2024"."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = _NON_WORD.sub("-", ascii_text.lower()).strip("-")
    return slug[:max_length].rstrip("-")


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())
