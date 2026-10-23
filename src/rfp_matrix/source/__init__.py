"""Immutable native-text PDF artifacts and literal original-codepoint anchors."""
from .artifacts import (
    ParsePolicy, SourceRegistry, make_anchor, normalize_with_alignment,
    parse_pdf, parse_text_pages, resolve_anchor, validate_artifact, artifact_policy_status,
)
from .coverage import COVERAGE_POLICY, coverage_page_sha, effective_coverage, make_coverage_decision
from .structure import anchor_from_normalized, resolve_context, structural_segments
from .policy import policy_context, policy_status, policy_identity

__all__ = ["ParsePolicy", "SourceRegistry", "make_anchor", "normalize_with_alignment", "parse_pdf", "parse_text_pages", "resolve_anchor", "validate_artifact"]
__all__ += ["COVERAGE_POLICY", "coverage_page_sha", "effective_coverage", "make_coverage_decision"]
__all__ += ["anchor_from_normalized", "resolve_context", "structural_segments"]
__all__ += ["policy_context", "policy_status"]
__all__ += ["artifact_policy_status"]
__all__ += ["policy_identity"]
