"""Tests for src/matcher.py::resolve_arrows — Phase 4 arrow head resolution.

resolve_arrows runs at Phase 4 (Generate) time against the final approved
MatchRecord list, not at Phase 3 — see plan D2. No Pass B "proximity_field"
(plan D1): fuzzy_in_field -> fuzzy_on_page (only when zero fields on the
target page) -> unresolved.
"""
from pathlib import Path

import fitz
import pytest

from src.matcher import resolve_arrows
from src.models import ArrowRecord, ArrowStyle, FieldRecord, MatchRecord
from src.profile_loader import load_profile

PROFILES_DIR = Path(__file__).parent.parent / "profiles"


def _profile(**arrow_overrides):
    profile = load_profile(PROFILES_DIR / "cdisc_standard.yaml")
    if arrow_overrides:
        profile = profile.model_copy(
            update={"arrows": profile.arrows.model_copy(update=arrow_overrides)}
        )
    return profile


def _style() -> ArrowStyle:
    return ArrowStyle(
        stroke_color=(1.0, 0.0, 0.0), width=1.0, dashes=[],
        tail_line_end=0, head_line_end=4,
    )


def _arrow(arrow_id="arrow-1", tail_annotation_id="annot-1", head_text="Yes", **kwargs) -> ArrowRecord:
    defaults = dict(
        arrow_id=arrow_id,
        source_page=1,
        tail_vertex=(200.0, 60.0),
        head_vertex=(290.0, 62.0),
        head_source_rect=None,
        tail_annotation_id=tail_annotation_id,
        head_text=head_text,
        style=_style(),
    )
    defaults.update(kwargs)
    return ArrowRecord(**defaults)


def _match(annotation_id="annot-1", field_id="field-1", target_page=1,
           target_rect=None, status="approved") -> MatchRecord:
    return MatchRecord(
        annotation_id=annotation_id,
        field_id=field_id,
        match_type="exact",
        confidence=1.0,
        target_rect=target_rect or [50.0, 80.0, 200.0, 100.0],
        target_page=target_page,
        status=status,
    )


def _make_target_pdf(path: Path, texts: list[tuple[str, tuple[float, float]]]) -> Path:
    """Write a 1-page target PDF with the given (text, (x,y baseline)) pairs."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for text, pos in texts:
        page.insert_text(fitz.Point(*pos), text, fontsize=10, color=(0, 0, 0))
    doc.save(str(path))
    doc.close()
    return path


def _make_multi_page_target_pdf(path: Path, pages_texts: list[list[tuple[str, tuple[float, float]]]]) -> Path:
    doc = fitz.open()
    for texts in pages_texts:
        page = doc.new_page(width=595, height=842)
        for text, pos in texts:
            page.insert_text(fitz.Point(*pos), text, fontsize=10, color=(0, 0, 0))
    doc.save(str(path))
    doc.close()
    return path


class TestEmptyArrowsEarlyReturn:
    def test_empty_arrows_returns_empty_without_opening_pdf(self, tmp_path):
        """Early return [] before opening the target PDF (plan step 5)."""
        missing = tmp_path / "does_not_exist.pdf"
        result = resolve_arrows([], [_match()], [], missing, _profile())
        assert result == []


class TestFuzzyInFieldHit:
    def test_head_text_matches_block_inside_inflated_target_rect(self, tmp_path):
        target = _make_target_pdf(tmp_path / "t.pdf", [("Yes", (60.0, 95.0))])
        arrow = _arrow(head_text="Yes")
        match = _match(target_rect=[50.0, 80.0, 200.0, 100.0], target_page=1)
        result = resolve_arrows([arrow], [match], [], target, _profile())

        assert len(result) == 1
        r = result[0]
        assert r.head_match_method == "fuzzy_in_field"
        assert r.target_page == 1
        assert r.target_field_id == "field-1"
        assert r.head_target_rect is not None
        assert r.head_confidence >= 0.85


class TestFuzzyOnPageFallback:
    def test_fallback_when_zero_fields_on_target_page(self, tmp_path):
        """No FieldRecords on the target page -> fuzzy_on_page across the
        whole page (not restricted to the inflated parent rect)."""
        target = _make_target_pdf(
            tmp_path / "t.pdf", [("Yes", (400.0, 700.0))]  # far from parent's rect
        )
        arrow = _arrow(head_text="Yes")
        match = _match(target_rect=[50.0, 80.0, 200.0, 100.0], target_page=1)
        result = resolve_arrows([arrow], [match], [], target, _profile())

        assert len(result) == 1
        r = result[0]
        assert r.head_match_method == "fuzzy_on_page"
        assert r.head_confidence >= 0.85

    def test_no_fallback_when_fields_exist_on_page_even_if_no_match(self, tmp_path):
        """Plan D1: fuzzy_on_page fires ONLY when zero fields exist on the
        target page — not merely when Pass A fails to find a match."""
        target = _make_target_pdf(
            tmp_path / "t.pdf", [("Completely Different Text", (400.0, 700.0))]
        )
        arrow = _arrow(head_text="Yes")
        match = _match(target_rect=[50.0, 80.0, 200.0, 100.0], target_page=1)
        field = FieldRecord(
            id="field-1", page=1, label="Some Field",
            rect=[10.0, 10.0, 30.0, 20.0], field_type="text_field",
        )
        result = resolve_arrows([arrow], [match], [field], target, _profile())

        assert result[0].head_match_method == "unresolved"


class TestUnresolved:
    def test_unresolved_no_parent_match(self, tmp_path):
        """tail_annotation_id has no corresponding MatchRecord."""
        target = _make_target_pdf(tmp_path / "t.pdf", [("Yes", (60.0, 95.0))])
        arrow = _arrow(tail_annotation_id="ghost-annot")
        match = _match(annotation_id="annot-1")
        result = resolve_arrows([arrow], [match], [], target, _profile())
        assert result[0].head_match_method == "unresolved"
        assert result[0].head_confidence == 0.0
        assert result[0].target_page is None
        # Plain "no parent match" is distinct from the duplicate-id case
        # (plan D8) and must NOT carry the duplicate-id skip_reason.
        assert result[0].skip_reason is None

    def test_unresolved_below_threshold(self, tmp_path):
        target = _make_target_pdf(tmp_path / "t.pdf", [("Zzz Qqq Www", (60.0, 95.0))])
        arrow = _arrow(head_text="Yes")
        match = _match(target_rect=[50.0, 80.0, 200.0, 100.0], target_page=1)
        result = resolve_arrows([arrow], [match], [], target, _profile())
        assert result[0].head_match_method == "unresolved"

    def test_unresolved_empty_head_text(self, tmp_path):
        target = _make_target_pdf(tmp_path / "t.pdf", [("Yes", (60.0, 95.0))])
        arrow = _arrow(head_text="")
        match = _match()
        result = resolve_arrows([arrow], [match], [], target, _profile())
        assert result[0].head_match_method == "unresolved"

    def test_unresolved_when_tail_annotation_id_is_none(self, tmp_path):
        target = _make_target_pdf(tmp_path / "t.pdf", [("Yes", (60.0, 95.0))])
        arrow = _arrow(tail_annotation_id=None)
        match = _match()
        result = resolve_arrows([arrow], [match], [], target, _profile())
        assert result[0].head_match_method == "unresolved"

    def test_unresolved_when_parent_field_id_none(self, tmp_path):
        """Parent MatchRecord has field_id=None (unmatched annotation) ->
        unresolved. resolve_arrows must still find the match record (D2
        permissive-on-status), but a null field_id is itself disqualifying."""
        target = _make_target_pdf(tmp_path / "t.pdf", [("Yes", (60.0, 95.0))])
        arrow = _arrow(head_text="Yes")
        match = MatchRecord(
            annotation_id="annot-1", field_id=None, match_type="unmatched",
            confidence=0.0, target_rect=[50.0, 80.0, 200.0, 100.0],
            target_page=1, status="re-pairing",
        )
        result = resolve_arrows([arrow], [match], [], target, _profile())
        assert result[0].head_match_method == "unresolved"


class TestPermissiveOnStatus:
    """D2: resolve_arrows runs at Phase 4 against final matches — it must not
    filter parent matches by status. Phase 4's write gate (placed_rects) is
    the actual enforcement point, not resolve_arrows."""

    @pytest.mark.parametrize("status", ["pending", "approved", "re-pairing"])
    def test_resolves_regardless_of_parent_match_status(self, tmp_path, status):
        target = _make_target_pdf(tmp_path / "t.pdf", [("Yes", (60.0, 95.0))])
        arrow = _arrow(head_text="Yes")
        match = _match(status=status, target_rect=[50.0, 80.0, 200.0, 100.0])
        result = resolve_arrows([arrow], [match], [], target, _profile())
        assert result[0].head_match_method == "fuzzy_in_field"


class TestFanOut:
    def test_two_arrows_one_parent_resolve_independently(self, tmp_path):
        target = _make_target_pdf(
            tmp_path / "t.pdf", [("Yes", (60.0, 95.0)), ("No", (60.0, 115.0))]
        )
        arrow_yes = _arrow(arrow_id="a-yes", head_text="Yes")
        arrow_no = _arrow(arrow_id="a-no", head_text="No")
        match = _match(target_rect=[40.0, 80.0, 200.0, 130.0], target_page=1)
        result = resolve_arrows([arrow_yes, arrow_no], [match], [], target, _profile())

        by_id = {r.arrow_id: r for r in result}
        assert by_id["a-yes"].head_match_method == "fuzzy_in_field"
        assert by_id["a-no"].head_match_method == "fuzzy_in_field"
        assert by_id["a-yes"].head_target_rect != by_id["a-no"].head_target_rect


class TestDuplicateParentIdGuard:
    def test_duplicate_annotation_id_in_matches_yields_unresolved_with_qc(self, tmp_path):
        """Plan D8: if persisted matches contain duplicate annotation_ids,
        affected arrows resolve unresolved rather than silently using the
        last one."""
        target = _make_target_pdf(tmp_path / "t.pdf", [("Yes", (60.0, 95.0))])
        arrow = _arrow(tail_annotation_id="annot-1", head_text="Yes")
        match1 = _match(annotation_id="annot-1", field_id="field-1", target_page=1,
                         target_rect=[50.0, 80.0, 200.0, 100.0])
        match2 = _match(annotation_id="annot-1", field_id="field-2", target_page=1,
                         target_rect=[50.0, 80.0, 200.0, 100.0])
        result = resolve_arrows([arrow], [match1, match2], [], target, _profile())
        assert result[0].head_match_method == "unresolved"
        assert result[0].skip_reason == "duplicate_parent_annotation_id"


class TestInvalidTargetPage:
    def test_target_page_out_of_range_treated_as_no_blocks(self, tmp_path):
        target = _make_target_pdf(tmp_path / "t.pdf", [("Yes", (60.0, 95.0))])
        arrow = _arrow(head_text="Yes")
        match = _match(target_page=99)  # PDF has only 1 page
        result = resolve_arrows([arrow], [match], [], target, _profile())
        assert result[0].head_match_method == "unresolved"


class TestNoProximityFieldPass:
    def test_arrow_head_never_resolves_via_proximity_field(self, tmp_path):
        """Plan D1: Pass B is removed entirely. Even when a FieldRecord sits
        exactly at the arrowhead vertex and text search fails, resolution
        must be unresolved, never a proximity_field match (that literal
        value no longer even exists on ArrowMatch)."""
        target = _make_target_pdf(tmp_path / "t.pdf", [])  # no text at all
        arrow = _arrow(head_text="Yes", head_vertex=(15.0, 15.0))
        match = _match(target_rect=[50.0, 80.0, 200.0, 100.0], target_page=1)
        field = FieldRecord(
            id="field-close", page=1, label="Yes",
            rect=[10.0, 10.0, 20.0, 20.0], field_type="text_field",
        )
        result = resolve_arrows([arrow], [match], [field], target, _profile())
        assert result[0].head_match_method == "unresolved"


class TestMultiPage:
    def test_resolves_against_correct_target_page(self, tmp_path):
        target = _make_multi_page_target_pdf(
            tmp_path / "t.pdf",
            [
                [("Wrong Page Text", (60.0, 95.0))],
                [("Yes", (60.0, 95.0))],
            ],
        )
        arrow = _arrow(head_text="Yes")
        match = _match(target_page=2, target_rect=[50.0, 80.0, 200.0, 100.0])
        result = resolve_arrows([arrow], [match], [], target, _profile())
        assert result[0].target_page == 2
        assert result[0].head_match_method == "fuzzy_in_field"
