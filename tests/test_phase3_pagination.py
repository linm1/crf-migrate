"""Tests for Phase 3 pagination partition logic.

The page navigator only paginates rows that have a real target field
(field_id set AND target_page >= 1). Rows without a real target
(unmatched / position_only / pre-assignment) bypass pagination and are
pinned across all pages.
"""
import sys
import types

# ---------------------------------------------------------------------------
# Stub heavy / optional deps so MatchRecord can import cleanly.
# ---------------------------------------------------------------------------
def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__all__ = []  # type: ignore[attr-defined]
    return mod


for _dep in ["fitz", "rapidfuzz", "rapidfuzz.fuzz", "pdfplumber"]:
    if _dep not in sys.modules:
        sys.modules[_dep] = _make_stub(_dep)

from src.models import MatchRecord  # noqa: E402


def _make_match(
    annotation_id: str,
    target_page: int,
    field_id: str | None,
    match_type: str = "fuzzy",
) -> MatchRecord:
    return MatchRecord(
        annotation_id=annotation_id,
        field_id=field_id,
        match_type=match_type,
        confidence=0.9,
        target_rect=[0.0, 0.0, 100.0, 20.0],
        target_page=target_page,
        status="re-pairing",
    )


def _partition(matches: list[MatchRecord]) -> tuple[list[MatchRecord], list[MatchRecord], list[int]]:
    """Replicate the partition logic in ui/phase3_review.py:_render_match_rows.

    Mirrors production logic so the pagination contract is enforced as a unit
    test even though the production code is inline in the Streamlit render path.
    """
    real = [m for m in matches if m.field_id is not None and m.target_page >= 1]
    unassigned = [m for m in matches if not (m.field_id is not None and m.target_page >= 1)]
    page_groups = sorted({m.target_page for m in real})
    return real, unassigned, page_groups


def test_partition_real_rows_only():
    matches = [
        _make_match("a", 3, "fld-a"),
        _make_match("b", 1, "fld-b"),
        _make_match("c", 2, "fld-c"),
    ]
    real, unassigned, page_groups = _partition(matches)
    assert [m.annotation_id for m in real] == ["a", "b", "c"]
    assert unassigned == []
    assert page_groups == [1, 2, 3]


def test_partition_unmatched_only_hides_nav():
    """target_page=0 + field_id=None → all unassigned, page_groups empty."""
    matches = [
        _make_match("a", 0, None, match_type="unmatched"),
        _make_match("b", 0, None, match_type="unmatched"),
    ]
    real, unassigned, page_groups = _partition(matches)
    assert real == []
    assert [m.annotation_id for m in unassigned] == ["a", "b"]
    assert page_groups == []


def test_partition_position_only_unassigned():
    """position_only has target_page=annot.page but field_id=None → unassigned."""
    matches = [
        _make_match("a", 6, None, match_type="position_only"),
        _make_match("b", 7, None, match_type="position_only"),
    ]
    real, unassigned, page_groups = _partition(matches)
    assert real == []
    assert [m.annotation_id for m in unassigned] == ["a", "b"]
    assert page_groups == []


def test_partition_mixed_real_and_unassigned():
    """fuzzy rows paginate by target_page; unmatched/position_only stay unassigned."""
    matches = [
        _make_match("fuzzy_p2", 2, "fld-1", match_type="fuzzy"),
        _make_match("unmatched", 0, None, match_type="unmatched"),
        _make_match("fuzzy_p1", 1, "fld-2", match_type="fuzzy"),
        _make_match("position_only", 5, None, match_type="position_only"),
    ]
    real, unassigned, page_groups = _partition(matches)
    assert sorted(m.annotation_id for m in real) == ["fuzzy_p1", "fuzzy_p2"]
    assert sorted(m.annotation_id for m in unassigned) == ["position_only", "unmatched"]
    assert page_groups == [1, 2]


def test_partition_dedupes_page_groups():
    matches = [
        _make_match("a", 1, "fld-a"),
        _make_match("b", 1, "fld-b"),
        _make_match("c", 2, "fld-c"),
    ]
    _, _, page_groups = _partition(matches)
    assert page_groups == [1, 2]


def test_partition_empty():
    real, unassigned, page_groups = _partition([])
    assert real == []
    assert unassigned == []
    assert page_groups == []
