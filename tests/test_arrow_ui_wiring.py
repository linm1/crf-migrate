"""Tests for the arrow/line connector migration's minimal UI wiring.

Covers the pure-logic pieces that can be unit tested without a live
Streamlit runtime: ui.components.invalidate_phases's arrow_matches
invalidation, and app.py's CLEARABLE_STATE_KEYS list. Full interactive UI
behavior (Phase 1 extract button, Phase 4 Generate button,
session-switching popover) requires `streamlit run app.py` and is out of
scope for non-interactive test execution — see the task's final report for
what remains for human/visual verification.
"""
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
sys.modules.pop("ui.components", None)

from ui.components import invalidate_phases  # noqa: E402


class TestInvalidatePhasesArrowMatches:
    def test_invalidating_phase_4_pops_arrow_matches(self):
        st = sys.modules["streamlit"]
        st.session_state = {
            "phases_complete": {1: True, 2: True, 3: True, 4: True},
            "output_pdf_path": "out.pdf",
            "qc_report": {"written": 1},
            "arrow_matches": [{"arrow_id": "a1"}],
        }

        invalidate_phases([4])

        assert st.session_state["phases_complete"][4] is False
        assert "output_pdf_path" not in st.session_state
        assert "qc_report" not in st.session_state
        assert "arrow_matches" not in st.session_state

    def test_invalidating_phase_3_does_not_touch_arrow_matches(self):
        """arrow_matches is a Phase-4 output (resolve_arrows runs at Generate
        time per plan D2) — invalidating phase 3 alone (e.g. a manual
        re-pair) must not itself clear arrow_matches; the next Generate
        click recomputes it fresh regardless."""
        st = sys.modules["streamlit"]
        st.session_state = {
            "phases_complete": {1: True, 2: True, 3: True, 4: True},
            "matches": [{"annotation_id": "a1"}],
            "arrow_matches": [{"arrow_id": "a1"}],
        }

        invalidate_phases([3])

        assert st.session_state["phases_complete"][3] is False
        assert "matches" not in st.session_state
        assert st.session_state["arrow_matches"] == [{"arrow_id": "a1"}]

    def test_invalidating_neither_3_nor_4_leaves_arrow_matches_untouched(self):
        st = sys.modules["streamlit"]
        st.session_state = {
            "phases_complete": {1: True, 2: True, 3: True, 4: True},
            "arrow_matches": [{"arrow_id": "a1"}],
        }

        invalidate_phases([1])

        assert st.session_state["arrow_matches"] == [{"arrow_id": "a1"}]


class TestClearableStateKeys:
    def test_arrows_and_arrow_matches_in_clearable_keys(self):
        """app.py's CLEARABLE_STATE_KEYS must include arrows/arrow_matches
        so switching sessions doesn't leak stale arrow state from a
        previously loaded session.

        app.py is not imported directly here: it transitively imports
        src.pdf_utils, which type-annotates with fitz.Page at module scope —
        incompatible with this file's stubbed-out `fitz` module (a bare
        types.ModuleType with no Page attribute). Reading the source text
        avoids re-stubbing fitz with a fuller fake just for one constant.
        """
        from pathlib import Path

        app_source = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
        start = app_source.index("CLEARABLE_STATE_KEYS = [")
        end = app_source.index("]", start)
        keys_block = app_source[start:end]

        assert '"arrows"' in keys_block
        assert '"arrow_matches"' in keys_block
