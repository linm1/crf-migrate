"""Tests for Phase 3 page-group helper."""
import sys
import types
import pytest

# ---------------------------------------------------------------------------
# Stub out heavy / optional dependencies so the module can be imported in
# environments where streamlit, fitz, rapidfuzz, etc. are not installed.
# ---------------------------------------------------------------------------
def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__all__ = []  # type: ignore[attr-defined]
    return mod


for _dep in [
    "streamlit",
    "streamlit.components",
    "streamlit.components.v1",
    "fitz",
    "rapidfuzz",
    "rapidfuzz.fuzz",
    "pdfplumber",
]:
    if _dep not in sys.modules:
        sys.modules[_dep] = _make_stub(_dep)

# Streamlit needs attribute access (st.session_state, etc.) — use a MagicMock.
from unittest.mock import MagicMock  # noqa: E402  (after sys.modules patch)

if not isinstance(sys.modules["streamlit"], MagicMock):
    sys.modules["streamlit"] = MagicMock()
    sys.modules["streamlit.components"] = MagicMock()
    sys.modules["streamlit.components.v1"] = MagicMock()

# rapidfuzz.fuzz must expose a callable attribute `token_sort_ratio`
_rfuzz_stub = MagicMock()
sys.modules["rapidfuzz"] = _rfuzz_stub
sys.modules["rapidfuzz.fuzz"] = _rfuzz_stub.fuzz

# ---------------------------------------------------------------------------

from src.models import MatchRecord  # noqa: E402
from ui.phase3_review import _build_page_groups  # noqa: E402


def _make_match(annotation_id: str, target_page: int, match_type: str = "exact") -> MatchRecord:
    return MatchRecord(
        annotation_id=annotation_id,
        field_id="fld1" if target_page > 0 else None,
        match_type=match_type if target_page > 0 else "unmatched",
        confidence=0.9,
        target_rect=[0.0, 0.0, 100.0, 20.0],
        target_page=target_page,
        status="re-pairing",
    )


def test_build_page_groups_matched_only():
    matches = [_make_match("a", 3), _make_match("b", 1), _make_match("c", 2)]
    assert _build_page_groups(matches) == [1, 2, 3]


def test_build_page_groups_with_unmatched():
    matches = [_make_match("a", 2), _make_match("b", 0), _make_match("c", 1)]
    assert _build_page_groups(matches) == [1, 2, 0]


def test_build_page_groups_unmatched_only():
    matches = [_make_match("a", 0), _make_match("b", 0)]
    assert _build_page_groups(matches) == [0]


def test_build_page_groups_empty():
    assert _build_page_groups([]) == []


def test_build_page_groups_deduplicates():
    matches = [_make_match("a", 1), _make_match("b", 1), _make_match("c", 2)]
    assert _build_page_groups(matches) == [1, 2]
