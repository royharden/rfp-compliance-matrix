"""Immutable native-text PDF artifacts and literal original-codepoint anchors."""
from .artifacts import (
    ParsePolicy, SourceRegistry, make_anchor, normalize_with_alignment,
    parse_pdf, parse_text_pages, resolve_anchor, validate_artifact,
)

__all__ = ["ParsePolicy", "SourceRegistry", "make_anchor", "normalize_with_alignment", "parse_pdf", "parse_text_pages", "resolve_anchor", "validate_artifact"]
