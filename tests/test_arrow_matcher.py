"""Tests for src/matcher.py::resolve_arrows — Phase 4 arrow head resolution.

resolve_arrows runs at Phase 4 (Generate) time against the final approved
MatchRecord list, not at Phase 3 — see plan D2. No Pass B "proximity_field"
(plan D1): fuzzy_in_field -> fuzzy_on_page (only when zero fields on the
target page) -> unresolved.
"""
from pathlib import Path

import fitz
import pytest

from src.matcher import (
    _rect_scale_mismatch,
    _resolve_transformed_proximity,
    resolve_arrows,
)
from src.models import AnnotationRecord, ArrowRecord, ArrowStyle, FieldRecord, MatchRecord
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


def _annot(annotation_id="annot-1", rect=None) -> AnnotationRecord:
    """A parent AnnotationRecord — required for candidate C (transformed
    proximity), which maps head_vertex through the parent's *source* rect
    (this record) into the placed target_rect (the MatchRecord). Without it
    C cannot run (D1: never compare raw source coords to target rects)."""
    return AnnotationRecord(
        id=annotation_id,
        page=1,
        content="AEYN",
        domain="AE",
        category="sdtm_mapping",
        matched_rule="test",
        rect=rect or [100.0, 100.0, 150.0, 120.0],
    )


def _field_at(center, label, field_id, half=5.0, page=1) -> FieldRecord:
    cx, cy = center
    return FieldRecord(
        id=field_id, page=page, label=label,
        rect=[cx - half, cy - half, cx + half, cy + half],
        field_type="text_field",
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

    def test_unresolved_when_no_block_matches_even_with_fields_present(self, tmp_path):
        """AHR-2: the guarded fuzzy_on_page (B) pass is *relaxed* — it no
        longer refuses pages that have FieldRecords (the old zero-fields
        gate). Here resolution is unresolved solely because no text block
        fuzzy-matches head_text, NOT because a gate blocked B. (D1 is still
        intact: B matches head_text against target *text*, never source
        coordinates against target rects.)"""
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

    def test_relaxed_b_fires_on_field_rich_page(self, tmp_path):
        """AHR-2 positive relaxed-B case: a page WITH FieldRecords still
        gets a fuzzy_on_page hit when a text block matches head_text and
        Pass A / C did not resolve it. Proves the zero-fields gate is gone."""
        target = _make_target_pdf(
            tmp_path / "t.pdf", [("Yes", (400.0, 700.0))]  # far from parent rect
        )
        arrow = _arrow(head_text="Yes")
        match = _match(target_rect=[50.0, 80.0, 200.0, 100.0], target_page=1)
        field = FieldRecord(
            id="field-1", page=1, label="Some Field",
            rect=[10.0, 10.0, 30.0, 20.0], field_type="text_field",
        )
        # No annotations passed -> C is off, isolating the relaxed-B path.
        result = resolve_arrows([arrow], [match], [field], target, _profile())

        assert result[0].head_match_method == "fuzzy_on_page"

    def test_b_proximity_tiebreak_prefers_block_nearer_parent(self, tmp_path):
        """AHR-2: B keeps its proximity tiebreak — among equally-scoring
        text blocks it prefers the one nearest the parent's placed
        target_rect (never regress to A's no-tiebreak failure mode)."""
        target = _make_target_pdf(
            tmp_path / "t.pdf",
            [("Yes", (250.0, 300.0)), ("Yes", (450.0, 700.0))],  # both outside A's window
        )
        arrow = _arrow(head_text="Yes")
        match = _match(target_rect=[100.0, 100.0, 150.0, 120.0], target_page=1)
        result = resolve_arrows([arrow], [match], [], target, _profile())

        r = result[0]
        assert r.head_match_method == "fuzzy_on_page"
        # The nearer block (y~290) wins over the far one (y~690).
        assert r.head_target_rect is not None and r.head_target_rect[1] < 400.0


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


class TestTransformedProximityC:
    """AHR-2/AHR-3: candidate C — transformed_proximity. Maps the arrow's
    head_vertex through the parent's source-rect -> target-rect translation
    (D1-preserving), then accepts the nearest target field/text block iff ALL
    guards hold (radius, text-compatibility, locality margin, page/rect scale)
    — else falls through to the guarded fuzzy_on_page (B) pass.

    Test geometry convention: the parent source rect and target rect share
    top-left (100, 100), so the mapped point equals head_vertex — head_vertex
    is placed at (400, 400), well outside Pass A's inflated window (~[70, 70,
    180, 150]), so A always misses and C is the pass under test. A single far
    dummy block ("zzz qqq" at (10, 800)) keeps the page's text-block list
    non-empty without ever being C's winner.
    """

    MAPPED = (400.0, 400.0)
    SRC_RECT = [100.0, 100.0, 150.0, 120.0]           # w=50, h=20
    TGT_RECT_SAME = [100.0, 100.0, 150.0, 120.0]       # scale 1.0

    def _c_arrow(self):
        return _arrow(head_text="Yes", head_vertex=self.MAPPED)

    def test_c_accepts_single_block_candidate(self, tmp_path):
        """One matching text block at the mapped point, no fields -> C
        accepts it (runner-up is None -> margin guard passes)."""
        target = _make_target_pdf(tmp_path / "t.pdf", [("Yes", self.MAPPED)])
        match = _match(target_rect=self.TGT_RECT_SAME, target_page=1)
        result = resolve_arrows(
            [self._c_arrow()], [match], [], target, _profile(),
            annotations=[_annot(rect=self.SRC_RECT)],
        )
        r = result[0]
        assert r.head_match_method == "transformed_proximity"
        assert r.head_target_rect is not None
        assert r.head_confidence >= 0.85

    def test_c_accepts_field_over_far_runner_up(self, tmp_path):
        """A field at the mapped point wins over a far dummy block; the
        locality margin is large, so C accepts."""
        target = _make_target_pdf(tmp_path / "t.pdf", [("zzz qqq", (10.0, 800.0))])
        field = _field_at(self.MAPPED, "Yes", "field-hit")
        match = _match(target_rect=self.TGT_RECT_SAME, target_page=1)
        result = resolve_arrows(
            [self._c_arrow()], [match], [field], target, _profile(),
            annotations=[_annot(rect=self.SRC_RECT)],
        )
        assert result[0].head_match_method == "transformed_proximity"

    def test_c_radius_miss_falls_through_to_unresolved(self, tmp_path):
        """Nearest candidate is beyond the radius -> C falls through; B finds
        no matching block -> terminal unresolved with the residual reason."""
        target = _make_target_pdf(tmp_path / "t.pdf", [("zzz qqq", (10.0, 800.0))])
        field = _field_at((500.0, 500.0), "Yes", "field-far")  # ~141pt from mapped
        match = _match(target_rect=self.TGT_RECT_SAME, target_page=1)
        result = resolve_arrows(
            [self._c_arrow()], [match], [field], target, _profile(),
            annotations=[_annot(rect=self.SRC_RECT)],
        )
        r = result[0]
        assert r.head_match_method == "unresolved"
        assert r.skip_reason == "head_outside_radius_and_fuzzy"

    def test_c_text_incompatible_falls_through(self, tmp_path):
        """Nearest candidate is within radius but its text is incompatible
        with head_text -> C's text guard fails -> fall through."""
        target = _make_target_pdf(tmp_path / "t.pdf", [("zzz qqq", (10.0, 800.0))])
        field = _field_at(self.MAPPED, "Zzz Qqq Www", "field-wrongtext")
        match = _match(target_rect=self.TGT_RECT_SAME, target_page=1)
        result = resolve_arrows(
            [self._c_arrow()], [match], [field], target, _profile(),
            annotations=[_annot(rect=self.SRC_RECT)],
        )
        assert result[0].head_match_method == "unresolved"

    def test_c_margin_too_small_falls_through(self, tmp_path):
        """Two equally-plausible fields sit within the locality margin of each
        other -> near-tie -> C's margin guard fails -> fall through."""
        target = _make_target_pdf(tmp_path / "t.pdf", [("zzz qqq", (10.0, 800.0))])
        near = _field_at((400.0, 400.0), "Yes", "field-a")   # dist 0
        tie = _field_at((404.0, 400.0), "Yes", "field-b")    # dist ~4 (< margin 6)
        match = _match(target_rect=self.TGT_RECT_SAME, target_page=1)
        result = resolve_arrows(
            [self._c_arrow()], [match], [near, tie], target, _profile(),
            annotations=[_annot(rect=self.SRC_RECT)],
        )
        assert result[0].head_match_method == "unresolved"

    def test_c_scale_mismatch_skips_transform(self, tmp_path):
        """When the parent's source/target rect scale diverges beyond
        tolerance, C's translation-only transform is untrustworthy and is
        skipped entirely (defers to B) — even with a perfect candidate at the
        mapped point. Real samples are uniform 595x842, so this branch is only
        reachable synthetically."""
        target = _make_target_pdf(tmp_path / "t.pdf", [("zzz qqq", (10.0, 800.0))])
        field = _field_at(self.MAPPED, "Yes", "field-hit")
        scaled_target_rect = [100.0, 100.0, 180.0, 160.0]  # w=80,h=60 vs src 50x20
        match = _match(target_rect=scaled_target_rect, target_page=1)
        result = resolve_arrows(
            [self._c_arrow()], [match], [field], target, _profile(),
            annotations=[_annot(rect=self.SRC_RECT)],
        )
        # C must NOT claim this; B has no matching block -> unresolved.
        assert result[0].head_match_method != "transformed_proximity"
        assert result[0].head_match_method == "unresolved"

    def test_c_requires_annotations(self, tmp_path):
        """Without annotations, C cannot map through the parent source rect
        and is silently disabled — the field at the mapped point is ignored."""
        target = _make_target_pdf(tmp_path / "t.pdf", [("zzz qqq", (10.0, 800.0))])
        field = _field_at(self.MAPPED, "Yes", "field-hit")
        match = _match(target_rect=self.TGT_RECT_SAME, target_page=1)
        result = resolve_arrows(
            [self._c_arrow()], [match], [field], target, _profile(),
        )  # no annotations kwarg
        assert result[0].head_match_method == "unresolved"


class TestTransformedProximityHelperGuards:
    """Direct coverage of C's defensive guards that resolve_arrows' own
    early-exits keep unreachable through the public path."""

    def test_scale_mismatch_false_for_degenerate_source_rect(self):
        """A zero-area source rect can't yield a scale ratio -> treated as
        no-mismatch (C proceeds) rather than dividing by zero."""
        assert _rect_scale_mismatch([10.0, 10.0, 10.0, 10.0], [0.0, 0.0, 50.0, 20.0], 0.05) is False

    def test_transformed_proximity_none_when_no_candidates(self):
        """No fields and no text blocks -> no candidate to rank -> None. (In
        resolve_arrows the `if not blocks` early-exit prevents this, so it is
        exercised only directly.)"""
        arrow = _arrow(head_text="Yes", head_vertex=(400.0, 400.0))
        match = _match(target_rect=[100.0, 100.0, 150.0, 120.0], target_page=1)
        parent = _annot(rect=[100.0, 100.0, 150.0, 120.0])
        assert _resolve_transformed_proximity(arrow, match, parent, [], []) is None
