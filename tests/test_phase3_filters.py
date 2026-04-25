"""Tests for Phase 3 filter state restoration."""
import sys
import types
from unittest.mock import MagicMock


def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__all__ = []  # type: ignore[attr-defined]
    return mod


for _dep in [
    "streamlit",
    "streamlit.components",
    "streamlit.components.v1",
    "fitz",
    "pdfplumber",
]:
    if _dep not in sys.modules:
        sys.modules[_dep] = _make_stub(_dep)

if not isinstance(sys.modules["streamlit"], MagicMock):
    sys.modules["streamlit"] = MagicMock()
    sys.modules["streamlit.components"] = MagicMock()
    sys.modules["streamlit.components.v1"] = MagicMock()

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz  # noqa: F401
except Exception:
    _rfuzz_module = types.ModuleType("rapidfuzz")
    _rfuzz_fuzz = MagicMock()
    _rfuzz_fuzz.token_sort_ratio.return_value = 100.0
    _rfuzz_module.fuzz = _rfuzz_fuzz
    sys.modules["rapidfuzz"] = _rfuzz_module
    sys.modules["rapidfuzz.fuzz"] = _rfuzz_fuzz

sys.modules["streamlit"].session_state = {}
sys.modules.pop("ui.phase3_review", None)

from ui.phase3_review import _restore_filter_state  # noqa: E402


def test_restore_filter_state_seeds_default_selection_once():
    st = sys.modules["streamlit"]
    st.session_state = {}

    restored = _restore_filter_state(
        "p3_filter_type",
        ["exact", "fuzzy", "position_only", "unmatched"],
        ["fuzzy", "position_only", "unmatched"],
    )

    assert restored == ["fuzzy", "position_only", "unmatched"]
    assert st.session_state["p3_filter_type"] == restored


def test_restore_filter_state_preserves_latest_user_selection():
    st = sys.modules["streamlit"]
    st.session_state = {"p3_filter_type": ["manual"]}

    restored = _restore_filter_state(
        "p3_filter_type",
        ["exact", "fuzzy", "manual"],
        ["fuzzy", "position_only", "unmatched"],
    )

    assert restored == ["manual"]
    assert st.session_state["p3_filter_type"] == ["manual"]


def test_restore_filter_state_keeps_empty_user_selection():
    st = sys.modules["streamlit"]
    st.session_state = {"p3_filter_type": []}

    restored = _restore_filter_state(
        "p3_filter_type",
        ["exact", "fuzzy", "manual"],
        ["fuzzy", "position_only", "unmatched"],
    )

    assert restored == []
    assert st.session_state["p3_filter_type"] == []


def test_restore_filter_state_drops_unavailable_values():
    st = sys.modules["streamlit"]
    st.session_state = {"p3_filter_status": ["approved", "archived"]}

    restored = _restore_filter_state(
        "p3_filter_status",
        ["approved", "pending", "re-pairing"],
        [],
    )

    assert restored == ["approved"]
    assert st.session_state["p3_filter_status"] == ["approved"]
