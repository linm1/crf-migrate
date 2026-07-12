"""Tests for src/writer.py's arrow/line connector writing pass.

write_annotations()'s arrows/arrow_matches params are additive, trailing,
default-None — existing positional call sites (FreeText-only) must keep
working unmodified. This file focuses on the arrow write pass; the
pre-existing FreeText/Kofax contract tests live in tests/test_writer.py and
must still pass unmodified (verified separately, not duplicated here).
"""
from pathlib import Path

import fitz
import pytest

from src.models import AnnotationRecord, ArrowMatch, ArrowRecord, ArrowStyle, MatchRecord, StyleInfo
from src.profile_models import (
    AnnotationFilter,
    AnchorTextConfig,
    ClassificationRule,
    FormNameConfig,
    MatchingConfig,
    Profile,
    ProfileMeta,
    RuleCondition,
    StyleDefaults,
    VisitRule,
)
from src.writer import write_annotations, _write_arrows, _write_single_arrow


# ---------------------------------------------------------------------------
# Helpers (mirrors tests/test_writer.py's helper shapes)
# ---------------------------------------------------------------------------

def _make_profile(**arrow_overrides) -> Profile:
    profile = Profile(
        meta=ProfileMeta(name="test"),
        domain_codes=["DM", "AE"],
        classification_rules=[
            ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
        ],
    )
    if arrow_overrides:
        profile = profile.model_copy(
            update={"arrows": profile.arrows.model_copy(update=arrow_overrides)}
        )
    return profile


def make_annotation(annot_id: str = "annot-001", page: int = 1) -> AnnotationRecord:
    return AnnotationRecord(
        id=annot_id,
        page=page,
        content="BRTHDTC",
        domain="DM",
        category="sdtm_mapping",
        matched_rule="test",
        rect=[100.0, 90.0, 300.0, 110.0],
        style=StyleInfo(),
    )


def make_match(
    annot_id: str = "annot-001",
    status: str = "approved",
    target_rect: list[float] | None = None,
    target_page: int = 1,
) -> MatchRecord:
    return MatchRecord(
        annotation_id=annot_id,
        field_id="field-001",
        match_type="exact",
        confidence=1.0,
        target_rect=target_rect or [50.0, 80.0, 250.0, 100.0],
        target_page=target_page,
        status=status,
    )


def make_target_pdf(path: Path, n_pages: int = 2) -> Path:
    doc = fitz.open()
    for _ in range(n_pages):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


def make_arrow_style(**kwargs) -> ArrowStyle:
    defaults = dict(
        stroke_color=(1.0, 0.0, 0.0), width=1.0, dashes=[],
        tail_line_end=0, head_line_end=4,
    )
    defaults.update(kwargs)
    return ArrowStyle(**defaults)


def make_arrow(
    arrow_id: str = "arrow-1",
    tail_annotation_id: str = "annot-001",
    tail_vertex=(200.0, 60.0),
    head_vertex=(290.0, 62.0),
    head_source_rect=None,
    style: ArrowStyle | None = None,
) -> ArrowRecord:
    return ArrowRecord(
        arrow_id=arrow_id,
        source_page=1,
        tail_vertex=tail_vertex,
        head_vertex=head_vertex,
        head_source_rect=head_source_rect,
        tail_annotation_id=tail_annotation_id,
        head_text="Yes",
        style=style or make_arrow_style(),
    )


def _all_annots(doc: fitz.Document) -> list:
    """Return every annotation across all pages of doc.

    NOTE: a bare `[a for p in doc for a in p.annots()]` list comprehension (or
    even a helper that builds a local `pages` list and returns only the
    annots) yields annot handles that raise "annotation not bound to any
    page" once accessed — the `page` object each annot handle references
    gets garbage-collected as soon as nothing else holds a reference to it,
    even though the annot handle itself is still alive. Stashing the page
    list on `doc` (which every caller already keeps alive for the duration
    of its assertions) keeps every annot handle valid for as long as `doc`
    itself is open.
    """
    pages = list(doc)
    result = []
    for page in pages:
        result.extend(page.annots())
    doc._arrow_test_pages_keepalive = pages  # noqa: SLF001 — test-only lifetime hack
    return result


def make_arrow_match(
    arrow_id: str = "arrow-1",
    target_page: int | None = 1,
    target_field_id: str | None = "field-001",
    head_target_rect=(210.0, 55.0, 260.0, 70.0),
    head_match_method: str = "fuzzy_in_field",
    head_confidence: float = 0.9,
) -> ArrowMatch:
    return ArrowMatch(
        arrow_id=arrow_id,
        target_page=target_page,
        target_field_id=target_field_id,
        head_target_rect=head_target_rect,
        head_match_method=head_match_method,
        head_confidence=head_confidence,
    )


# ---------------------------------------------------------------------------
# The most important test in the plan: pending-parent skipped with
# parent_not_written (defect-#2 regression test — D5, no vertex fallback)
# ---------------------------------------------------------------------------

class TestPendingParentSkipped:
    def test_pending_parent_skipped_with_parent_not_written(self, tmp_path):
        """If the parent annotation's match is NOT approved (e.g. still
        pending), its rect is never added to placed_rects, so any arrow
        anchored to it must be skipped with parent_not_written — there is
        no raw-tail-vertex fallback (this is the exact defect the reference
        branch had: it fell back to drawing from the arrow's raw source
        vertex, producing a dangling arrow anchored to nothing on the
        target page)."""
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        pending_match = make_match(status="pending")  # NOT approved
        arrow = make_arrow()
        arrow_match = make_arrow_match()
        profile = _make_profile()

        qc_report = write_annotations(
            target, output, [pending_match], [annot], profile,
            arrows=[arrow], arrow_matches=[arrow_match],
        )

        assert qc_report["arrows_written"] == 0
        assert qc_report["arrows_skipped"] == 1
        assert qc_report["arrow_skipped_ids"] == [
            {"arrow_id": "arrow-1", "reason": "parent_not_written"}
        ]

        # No Line annotation should have been written to the output PDF.
        doc = fitz.open(str(output))
        line_annots = [a for a in _all_annots(doc) if a.type[0] == 3]
        doc.close()
        assert line_annots == []


class TestApprovedParentPageOutOfRange:
    def test_approved_parent_but_target_page_out_of_range_skipped(self, tmp_path):
        target = make_target_pdf(tmp_path / "target.pdf", n_pages=1)
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved", target_page=1)
        arrow = make_arrow()
        arrow_match = make_arrow_match(target_page=99)  # out of range
        profile = _make_profile()

        qc_report = write_annotations(
            target, output, [match], [annot], profile,
            arrows=[arrow], arrow_matches=[arrow_match],
        )

        assert qc_report["arrows_written"] == 0
        assert qc_report["arrow_skipped_ids"] == [
            {"arrow_id": "arrow-1", "reason": "invalid_target_page"}
        ]

    def test_unresolved_arrow_match_skipped_with_head_unresolved(self, tmp_path):
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved")
        arrow = make_arrow()
        arrow_match = make_arrow_match(
            target_page=None, target_field_id=None, head_target_rect=None,
            head_match_method="unresolved", head_confidence=0.0,
        )
        profile = _make_profile()

        qc_report = write_annotations(
            target, output, [match], [annot], profile,
            arrows=[arrow], arrow_matches=[arrow_match],
        )
        assert qc_report["arrow_skipped_ids"] == [
            {"arrow_id": "arrow-1", "reason": "head_unresolved"}
        ]

    def test_target_page_none_with_otherwise_resolved_method_skipped(self, tmp_path):
        """Defensive: a resolved head_match_method paired with target_page=
        None (should not normally co-occur, since resolve_arrows always sets
        both together, but _write_single_arrow must not assume that
        invariant) is skipped with invalid_target_page rather than raising."""
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved")
        arrow = make_arrow()
        arrow_match = make_arrow_match(
            target_page=None, head_match_method="fuzzy_in_field", head_confidence=0.9,
        )
        profile = _make_profile()

        qc_report = write_annotations(
            target, output, [match], [annot], profile,
            arrows=[arrow], arrow_matches=[arrow_match],
        )
        assert qc_report["arrow_skipped_ids"] == [
            {"arrow_id": "arrow-1", "reason": "invalid_target_page"}
        ]


class TestStyleRoundTrip:
    def test_stroke_color_width_dashes_opacity_round_trip(self, tmp_path):
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved")
        style = make_arrow_style(
            stroke_color=(0.2, 0.4, 0.6), width=2.5, dashes=[3.0, 1.0], opacity=0.5,
        )
        arrow = make_arrow(style=style)
        arrow_match = make_arrow_match()
        profile = _make_profile()

        write_annotations(
            target, output, [match], [annot], profile,
            arrows=[arrow], arrow_matches=[arrow_match],
        )

        doc = fitz.open(str(output))
        line_annots = [a for a in _all_annots(doc) if a.type[0] == 3]
        assert len(line_annots) == 1
        a = line_annots[0]
        stroke = a.colors["stroke"]
        assert stroke == pytest.approx([0.2, 0.4, 0.6], abs=1e-3)
        assert a.border["width"] == pytest.approx(2.5)
        assert tuple(a.border["dashes"]) == (3.0, 1.0)
        assert a.opacity == pytest.approx(0.5, abs=1e-3)
        doc.close()

    def test_line_ends_written_directly_no_swap(self, tmp_path):
        """D4: writer calls set_line_ends(tail_line_end, head_line_end)
        directly with the resolved (not raw-order) codes — no swap logic."""
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved")
        style = make_arrow_style(tail_line_end=0, head_line_end=4)
        arrow = make_arrow(style=style)
        arrow_match = make_arrow_match()
        profile = _make_profile()

        write_annotations(
            target, output, [match], [annot], profile,
            arrows=[arrow], arrow_matches=[arrow_match],
        )

        doc = fitz.open(str(output))
        line_annots = [a for a in _all_annots(doc) if a.type[0] == 3]
        assert line_annots[0].line_ends == (0, 4)
        doc.close()

    def test_double_arrow_4_6_codes_preserved(self, tmp_path):
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved")
        style = make_arrow_style(tail_line_end=4, head_line_end=6)
        arrow = make_arrow(style=style)
        arrow_match = make_arrow_match()
        profile = _make_profile()

        write_annotations(
            target, output, [match], [annot], profile,
            arrows=[arrow], arrow_matches=[arrow_match],
        )

        doc = fitz.open(str(output))
        line_annots = [a for a in _all_annots(doc) if a.type[0] == 3]
        assert line_annots[0].line_ends == (4, 6)
        doc.close()


class TestEndpointPlacementBranches:
    def test_branch_a_size_similar_endpoint_placement(self, tmp_path):
        """Source and target annotation boxes are the same size -> Branch A
        (offset-preserve + edge-snap)."""
        annot_by_id = {"annot-001": make_annotation()}
        placed_rects = {"annot-001": [400.0, 400.0, 600.0, 420.0]}  # same size as source rect
        arrow = make_arrow(tail_vertex=(300.0, 410.0))  # midpoint-ish within source rect
        arrow_match = make_arrow_match(head_target_rect=(210.0, 55.0, 260.0, 70.0))
        profile = _make_profile()

        # A freshly-created in-memory fitz.open() document's page is not
        # fully bound to its parent until the doc has been saved/reopened
        # from a real path (add_line_annot raises otherwise on this fitz
        # version) — use a real file, matching how write_annotations() is
        # always invoked in production (against an opened target_pdf_path).
        pdf_path = make_target_pdf(tmp_path / "branch_a.pdf", n_pages=1)
        doc = fitz.open(str(pdf_path))
        written, reason = _write_single_arrow(
            doc, arrow, arrow_match, annot_by_id, placed_rects, profile
        )
        assert written is True
        assert reason is None
        line_annots = [a for a in _all_annots(doc) if a.type[0] == 3]
        assert len(line_annots) == 1
        p1, p2 = line_annots[0].vertices
        # Tail point should land on the target box edge (Branch A / snap).
        assert 400.0 <= p1[0] <= 600.0 or 400.0 <= p2[0] <= 600.0
        doc.close()

    def test_branch_b_size_differs_uses_edge_midpoint(self, tmp_path):
        """Source and target annotation boxes differ in size beyond
        tolerance -> Branch B (edge_midpoint_from_direction)."""
        annot_by_id = {"annot-001": make_annotation()}  # source rect width=200
        placed_rects = {"annot-001": [400.0, 400.0, 1200.0, 420.0]}  # much wider target
        arrow = make_arrow(tail_vertex=(150.0, 100.0), head_vertex=(50.0, 410.0))
        arrow_match = make_arrow_match(head_target_rect=(210.0, 55.0, 260.0, 70.0))
        profile = _make_profile()

        pdf_path = make_target_pdf(tmp_path / "branch_b.pdf", n_pages=1)
        doc = fitz.open(str(pdf_path))
        written, reason = _write_single_arrow(
            doc, arrow, arrow_match, annot_by_id, placed_rects, profile
        )
        assert written is True
        doc.close()

    def test_head_uses_edge_midpoint_when_no_head_source_rect(self, tmp_path):
        """When arrow.head_source_rect is None, head placement falls back to
        edge_midpoint_from_direction (no Branch A/B decision possible)."""
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved")
        arrow = make_arrow(head_source_rect=None)
        arrow_match = make_arrow_match(head_target_rect=(210.0, 55.0, 260.0, 70.0))
        profile = _make_profile()

        qc_report = write_annotations(
            target, output, [match], [annot], profile,
            arrows=[arrow], arrow_matches=[arrow_match],
        )
        assert qc_report["arrows_written"] == 1

    def test_head_uses_hybrid_placement_when_head_source_rect_present(self, tmp_path):
        """When arrow.head_source_rect IS set, head placement goes through
        hybrid_endpoint_placement (Branch A/B) rather than the
        edge_midpoint_from_direction-only fallback."""
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved")
        arrow = make_arrow(head_source_rect=(270.0, 51.0, 320.0, 71.0))
        arrow_match = make_arrow_match(head_target_rect=(210.0, 55.0, 260.0, 70.0))
        profile = _make_profile()

        qc_report = write_annotations(
            target, output, [match], [annot], profile,
            arrows=[arrow], arrow_matches=[arrow_match],
        )
        assert qc_report["arrows_written"] == 1


class TestCombinedFreeTextAndArrowsDocument:
    """Codex 6.3: a combined FreeText+arrows document must preserve the
    existing Kofax /RC//DS contract AND have no /CL bytes after save, across
    all three line_ends variants (0,0)/(4,0)/(4,6)."""

    @pytest.mark.parametrize("tail_le,head_le", [(0, 0), (0, 4), (4, 6)])
    def test_freetext_kofax_contract_and_no_cl_with_arrows_present(self, tmp_path, tail_le, head_le):
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved")
        style = make_arrow_style(tail_line_end=tail_le, head_line_end=head_le)
        arrow = make_arrow(style=style)
        arrow_match = make_arrow_match()
        profile = _make_profile()

        write_annotations(
            target, output, [match], [annot], profile,
            arrows=[arrow], arrow_matches=[arrow_match],
        )

        doc = fitz.open(str(output))
        freetext_annots = [a for a in _all_annots(doc) if a.type[1] == "FreeText"]
        assert len(freetext_annots) == 1
        xref = freetext_annots[0].xref
        rc = doc.xref_get_key(xref, "RC")[0]
        ds = doc.xref_get_key(xref, "DS")[0]
        cl = doc.xref_get_key(xref, "CL")[0]
        assert rc != "null"
        assert ds != "null"
        assert cl == "null"
        doc.close()

        assert b"/CL" not in output.read_bytes()


class TestNoArrowsCall:
    def test_no_arrows_produces_structurally_equivalent_output_and_zeroed_qc(self, tmp_path):
        """Calling write_annotations without arrows/arrow_matches must still
        produce a valid FreeText-only output with zeroed (not missing) arrow
        QC keys — additive, not "byte-identical" (xref layout can vary)."""
        target = make_target_pdf(tmp_path / "target.pdf")
        output_no_arrows = tmp_path / "output_no_arrows.pdf"
        output_with_none_args = tmp_path / "output_explicit_none.pdf"
        annot = make_annotation()
        match = make_match(status="approved")
        profile = _make_profile()

        report_positional = write_annotations(target, output_no_arrows, [match], [annot], profile)
        report_explicit_none = write_annotations(
            target, output_with_none_args, [match], [annot], profile,
            arrows=None, arrow_matches=None,
        )

        for report in (report_positional, report_explicit_none):
            assert report["arrows_total"] == 0
            assert report["arrows_written"] == 0
            assert report["arrows_skipped"] == 0
            assert report["arrow_skipped_ids"] == []
            # Pre-existing keys still present and correctly shaped.
            assert report["written"] == 1
            assert report["skipped"] == 0

        doc1 = fitz.open(str(output_no_arrows))
        doc2 = fitz.open(str(output_with_none_args))
        ft1 = [a for a in _all_annots(doc1) if a.type[1] == "FreeText"]
        ft2 = [a for a in _all_annots(doc2) if a.type[1] == "FreeText"]
        assert len(ft1) == len(ft2) == 1
        assert ft1[0].get_text() == ft2[0].get_text()
        doc1.close()
        doc2.close()


class TestForcedExceptionContained:
    def test_one_bad_arrow_does_not_abort_the_whole_pass(self, tmp_path, monkeypatch):
        """A forced exception while writing one arrow must be caught and
        recorded as write_error, without preventing other arrows (or the
        FreeText annotations) from being written."""
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved")

        good_arrow = make_arrow(arrow_id="good")
        bad_arrow = make_arrow(arrow_id="bad")
        good_match = make_arrow_match(arrow_id="good")
        bad_match = make_arrow_match(arrow_id="bad")
        profile = _make_profile()

        import src.writer as writer_mod

        real_write_single_arrow = writer_mod._write_single_arrow

        def _patched(doc, arrow, arrow_match, annot_by_id, placed_rects, profile):
            if arrow.arrow_id == "bad":
                raise RuntimeError("forced failure")
            return real_write_single_arrow(doc, arrow, arrow_match, annot_by_id, placed_rects, profile)

        monkeypatch.setattr(writer_mod, "_write_single_arrow", _patched)

        qc_report = write_annotations(
            target, output, [match], [annot], profile,
            arrows=[good_arrow, bad_arrow], arrow_matches=[good_match, bad_match],
        )

        assert qc_report["arrows_written"] == 1
        assert qc_report["arrows_skipped"] == 1
        assert {"arrow_id": "bad", "reason": "write_error"} in qc_report["arrow_skipped_ids"]

        # The FreeText annotation must still have been written successfully.
        doc = fitz.open(str(output))
        freetext_annots = [a for a in _all_annots(doc) if a.type[1] == "FreeText"]
        assert len(freetext_annots) == 1
        doc.close()


class TestArrowMissingFromArrowsById:
    def test_arrow_match_with_no_corresponding_arrow_record_skipped(self, tmp_path):
        """An ArrowMatch whose arrow_id has no ArrowRecord counterpart (e.g.
        stale arrow_matches.json against a re-extracted arrows.json) is
        skipped defensively rather than raising."""
        target = make_target_pdf(tmp_path / "target.pdf")
        output = tmp_path / "output.pdf"
        annot = make_annotation()
        match = make_match(status="approved")
        orphan_match = make_arrow_match(arrow_id="ghost-arrow")
        profile = _make_profile()

        qc_report = write_annotations(
            target, output, [match], [annot], profile,
            arrows=[], arrow_matches=[orphan_match],
        )
        assert qc_report["arrows_skipped"] == 1
        assert qc_report["arrows_written"] == 0
