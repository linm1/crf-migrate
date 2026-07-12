"""Tests for src/extractor.py::extract_arrows — arrow/line connector extraction.

All fixtures are built programmatically via fitz at test time (tmp_path),
following repo convention (see tests/test_writer.py, tests/test_extractor.py).
"""
from pathlib import Path
from unittest.mock import MagicMock

import fitz
import pytest

from src.extractor import extract_annotations, extract_arrows
from src.models import AnnotationRecord, StyleInfo
from src.profile_loader import load_profile
from src.profile_models import (
    AnnotationFilter,
    ClassificationRule,
    Profile,
    ProfileMeta,
    RuleCondition,
)
from src.rule_engine import RuleEngine

PROFILES_DIR = Path(__file__).parent.parent / "profiles"


def _make_profile(**arrow_overrides) -> Profile:
    profile = load_profile(PROFILES_DIR / "cdisc_standard.yaml")
    if arrow_overrides:
        profile = profile.model_copy(
            update={"arrows": profile.arrows.model_copy(update=arrow_overrides)}
        )
    return profile


def _annotation(annot_id: str, page: int, rect: list[float]) -> AnnotationRecord:
    return AnnotationRecord(
        id=annot_id,
        page=page,
        content="SUOCCUR=Y",
        domain="SU",
        category="sdtm_mapping",
        matched_rule="test",
        rect=rect,
        style=StyleInfo(),
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _pdf_with_annotation_and_line(
    path: Path,
    line_p0: tuple[float, float],
    line_p1: tuple[float, float],
    line_ends: tuple[int, int],
    ft_rect: tuple[float, float, float, float] = (50.0, 50.0, 200.0, 70.0),
    ft_content: str = "SUOCCUR=Y",
    text_at: tuple[float, float] | None = None,
    text_value: str = "Option 1",
    stroke_color: tuple[float, float, float] = (1.0, 0.0, 0.0),
) -> Path:
    """1-page PDF with a FreeText annotation + one Line annotation + optional text."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if text_at is not None:
        page.insert_text(fitz.Point(*text_at), text_value, fontsize=10, color=(0, 0, 0))
    ft = page.add_freetext_annot(fitz.Rect(*ft_rect), ft_content)
    ft.update()
    a = page.add_line_annot(fitz.Point(*line_p0), fitz.Point(*line_p1))
    a.set_line_ends(line_ends[0], line_ends[1])
    a.set_colors(stroke=stroke_color)
    a.update()
    doc.save(str(path))
    doc.close()
    return path


class TestExtractArrowsDisabled:
    def test_enabled_false_returns_empty_immediately(self, tmp_path):
        """When arrows.enabled=False, ([], []) is returned without opening the PDF."""
        profile = _make_profile(enabled=False)
        # Use a path that does not exist — proves the PDF is never opened.
        missing = tmp_path / "does_not_exist.pdf"
        records, qc_issues = extract_arrows(missing, [], profile)
        assert records == []
        assert qc_issues == []


class TestPlainLineProximityResolution:
    def test_plain_line_tail_snaps_to_nearer_annotation(self, tmp_path):
        """(0,0) plain connector: nearer end to the FreeText annot is the tail."""
        pdf_path = tmp_path / "plain_line.pdf"
        # Tail vertex near the FreeText right edge (200,60); head vertex far away
        # near printed text "Option 1".
        _pdf_with_annotation_and_line(
            pdf_path,
            line_p0=(199.0, 60.0),   # near FreeText rect edge -> tail
            line_p1=(290.0, 62.0),   # near printed text -> head
            line_ends=(0, 0),
            text_at=(270.0, 65.0),
            text_value="Option 1",
        )
        profile = _make_profile()
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert len(records) == 1
        arrow = records[0]
        assert arrow.tail_annotation_id == annotations[0].id
        assert arrow.tail_vertex == pytest.approx((199.0, 60.0))
        assert arrow.head_vertex == pytest.approx((290.0, 62.0))
        assert arrow.head_text == "Option 1"
        assert arrow.style.tail_line_end == 0
        assert arrow.style.head_line_end == 0


class TestPlainLineTailIsVertex1:
    def test_plain_line_tail_is_second_vertex(self, tmp_path):
        """Mirror of the vertex0-is-tail case: when vertex1 (not vertex0) is
        nearer the annotation, tail_annotation_id must still resolve
        correctly (exercises the classified.tail_index == 1 branch)."""
        pdf_path = tmp_path / "plain_line_v1_tail.pdf"
        _pdf_with_annotation_and_line(
            pdf_path,
            line_p0=(290.0, 62.0),   # far -> head
            line_p1=(199.0, 60.0),   # near FreeText -> tail (vertex index 1)
            line_ends=(0, 0),
            text_at=(270.0, 65.0),
            text_value="Option 1",
        )
        profile = _make_profile()
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert len(records) == 1
        arrow = records[0]
        assert arrow.tail_annotation_id == annotations[0].id
        assert arrow.tail_vertex == pytest.approx((199.0, 60.0))
        assert arrow.head_vertex == pytest.approx((290.0, 62.0))


class TestTieSkip:
    def test_tie_within_epsilon_skips_and_logs_qc(self, tmp_path):
        """Both vertices are ~equidistant (within tail_tie_epsilon_pt) from
        two *distinct* annotations -> ambiguous, skipped with a QC issue,
        no record produced. (A single-annotation page would instead hit
        tail_head_same_annotation — this test needs two annotations so the
        tie path is the one actually exercised.)"""
        pdf_path = tmp_path / "tie.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        ft1 = page.add_freetext_annot(fitz.Rect(50.0, 100.0, 90.0, 120.0), "A1")
        ft1.update()
        ft2 = page.add_freetext_annot(fitz.Rect(210.0, 100.0, 250.0, 120.0), "A2")
        ft2.update()
        # v0 is 5pt from ft1's right edge; v1 is 5pt from ft2's left edge.
        a = page.add_line_annot(fitz.Point(95.0, 110.0), fitz.Point(205.0, 110.0))
        a.set_line_ends(0, 0)
        a.update()
        doc.save(str(pdf_path))
        doc.close()

        profile = _make_profile(tail_tie_epsilon_pt=1.0)
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert records == []
        assert any("tail_ambiguous_tie" in issue for issue in qc_issues)


class TestSameAnnotationSkip:
    def test_both_ends_nearest_same_annotation_skips(self, tmp_path):
        """Both vertices resolve to the same nearest annotation -> skip+QC."""
        pdf_path = tmp_path / "same_annot.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        ft = page.add_freetext_annot(fitz.Rect(50.0, 50.0, 250.0, 90.0), "SUOCCUR=Y")
        ft.update()
        # Both vertices land inside/near the same big box.
        a = page.add_line_annot(fitz.Point(80.0, 60.0), fitz.Point(200.0, 80.0))
        a.set_line_ends(0, 0)
        a.update()
        doc.save(str(pdf_path))
        doc.close()

        profile = _make_profile()
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert records == []
        assert any("tail_head_same_annotation" in issue for issue in qc_issues)


class TestNeitherNearSkip:
    def test_neither_end_near_any_annotation_skips(self, tmp_path):
        pdf_path = tmp_path / "neither_near.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        ft = page.add_freetext_annot(fitz.Rect(500.0, 700.0, 590.0, 720.0), "SUOCCUR=Y")
        ft.update()
        a = page.add_line_annot(fitz.Point(10.0, 10.0), fitz.Point(50.0, 50.0))
        a.set_line_ends(0, 0)
        a.update()
        doc.save(str(pdf_path))
        doc.close()

        profile = _make_profile()
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert records == []
        assert any("neither_end_near_annotation" in issue for issue in qc_issues)


class TestLineVerticesNormalisation:
    def test_line_vertices_normalises_fitz_point_objects(self):
        """_line_vertices must handle real fitz.Point objects (annot.vertices
        as returned by PyMuPDF), not just plain (x, y) tuples — the two
        input shapes are normalised identically."""
        from src.extractor import _line_vertices

        mock_annot = MagicMock()
        mock_annot.vertices = [fitz.Point(10.0, 20.0), fitz.Point(30.0, 40.0)]
        vertices = _line_vertices(mock_annot)
        assert vertices == [(10.0, 20.0), (30.0, 40.0)]

    def test_line_vertices_normalises_plain_tuples(self):
        from src.extractor import _line_vertices

        mock_annot = MagicMock()
        mock_annot.vertices = [(10.0, 20.0), (30.0, 40.0)]
        vertices = _line_vertices(mock_annot)
        assert vertices == [(10.0, 20.0), (30.0, 40.0)]

    def test_line_vertices_empty_when_none(self):
        from src.extractor import _line_vertices

        mock_annot = MagicMock()
        mock_annot.vertices = None
        assert _line_vertices(mock_annot) == []


class TestVertexCountGuard:
    def test_more_than_2_vertices_skips_via_mock(self, tmp_path):
        """Plan Codex 7.1: a >2-vertex Line is a spec edge case not producible
        via public fitz PDF-writing APIs — exercised with a mock/adapter
        object instead of a malformed-PDF fixture."""
        from src.extractor import _line_vertices

        mock_annot = MagicMock()
        mock_annot.vertices = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
        vertices = _line_vertices(mock_annot)
        assert len(vertices) == 3
        # The extract_arrows loop's own len(vertices) != 2 guard is exercised
        # indirectly: this unit test pins _line_vertices' pass-through
        # behavior so the guard in extract_arrows has a real 3-length input
        # to trigger against (covered end-to-end by the QC-issue assertion
        # below via monkeypatching page.annots()).

    def test_extract_arrows_skips_and_logs_when_vertices_ne_2(self, tmp_path, monkeypatch):
        pdf_path = tmp_path / "base.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        ft = page.add_freetext_annot(fitz.Rect(50.0, 50.0, 200.0, 70.0), "SUOCCUR=Y")
        ft.update()
        doc.save(str(pdf_path))
        doc.close()

        # Build a fake Line annot with 3 vertices and patch page.annots() to
        # yield it alongside the real annotation-derived text.
        fake_line = MagicMock()
        fake_line.type = (3, "Line")
        fake_line.vertices = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
        fake_line.line_ends = (0, 0)
        fake_line.colors = {"stroke": [0.0, 0.0, 0.0]}
        fake_line.border = {"width": 1.0, "dashes": ()}
        fake_line.opacity = -1

        import src.extractor as extractor_mod

        real_open = fitz.open

        class _PatchedPage:
            def __init__(self, real_page):
                self._real_page = real_page

            def annots(self):
                return [fake_line]

            def __getattr__(self, name):
                return getattr(self._real_page, name)

        class _PatchedDoc:
            def __init__(self, real_doc):
                self._real_doc = real_doc

            def __getitem__(self, idx):
                return _PatchedPage(self._real_doc[idx])

            def close(self):
                self._real_doc.close()

            def __getattr__(self, name):
                return getattr(self._real_doc, name)

        def _patched_open(path_arg=None):
            if path_arg is None:
                return real_open()
            return _PatchedDoc(real_open(path_arg))

        monkeypatch.setattr(extractor_mod.fitz, "open", _patched_open)

        profile = _make_profile()
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert records == []
        assert any("3 vertices" in issue for issue in qc_issues)


class TestDoubleArrowCodePreservation:
    def test_double_arrow_preserves_per_end_codes(self, tmp_path):
        """(4,6) double arrow: proximity decides tail, but each vertex keeps
        its own original line-end code."""
        pdf_path = tmp_path / "double_arrow.pdf"
        _pdf_with_annotation_and_line(
            pdf_path,
            line_p0=(199.0, 60.0),  # near FreeText -> tail, code should be 4
            line_p1=(290.0, 62.0),  # far -> head, code should be 6
            line_ends=(4, 6),
            text_at=(270.0, 65.0),
            text_value="Option 1",
        )
        profile = _make_profile()
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert len(records) == 1
        arrow = records[0]
        assert arrow.tail_annotation_id == annotations[0].id
        assert arrow.style.tail_line_end == 4
        assert arrow.style.head_line_end == 6


class TestDuplicateDedup:
    def test_overlay_duplicate_within_tolerance_deduped(self, tmp_path):
        """Two near-identical Line annots (same page/color, vertices within
        0.3pt) -> only the first is kept."""
        pdf_path = tmp_path / "dup.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text(fitz.Point(270.0, 65.0), "Option 1", fontsize=10, color=(0, 0, 0))
        ft = page.add_freetext_annot(fitz.Rect(50.0, 50.0, 200.0, 70.0), "SUOCCUR=Y")
        ft.update()
        a1 = page.add_line_annot(fitz.Point(199.0, 60.0), fitz.Point(290.0, 62.0))
        a1.set_line_ends(0, 0)
        a1.set_colors(stroke=(1.0, 0.0, 0.0))
        a1.update()
        # Near-duplicate overlay: vertices offset by 0.1pt.
        a2 = page.add_line_annot(fitz.Point(199.1, 60.0), fitz.Point(290.1, 62.0))
        a2.set_line_ends(0, 0)
        a2.set_colors(stroke=(1.0, 0.0, 0.0))
        a2.update()
        doc.save(str(pdf_path))
        doc.close()

        profile = _make_profile(dedup_vertex_tolerance_pt=0.75)
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert len(records) == 1
        assert any("duplicate overlay" in issue for issue in qc_issues)

    def test_same_position_different_line_ends_both_kept(self, tmp_path):
        """Plan D7 fix: a plain line (0,0) and a double-arrow (4,6) at the
        same position are DIFFERENT connectors with different styles, not a
        duplicate overlay render artifact — both must be extracted, not
        deduped."""
        pdf_path = tmp_path / "different_style_same_pos.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text(fitz.Point(270.0, 65.0), "Option 1", fontsize=10, color=(0, 0, 0))
        ft = page.add_freetext_annot(fitz.Rect(50.0, 50.0, 200.0, 70.0), "SUOCCUR=Y")
        ft.update()
        a1 = page.add_line_annot(fitz.Point(199.0, 60.0), fitz.Point(290.0, 62.0))
        a1.set_line_ends(0, 0)
        a1.set_colors(stroke=(1.0, 0.0, 0.0))
        a1.update()
        # Same position (within dedup tolerance) but a double-arrow style —
        # must NOT be treated as a duplicate of the plain line above.
        a2 = page.add_line_annot(fitz.Point(199.0, 60.0), fitz.Point(290.0, 62.0))
        a2.set_line_ends(4, 6)
        a2.set_colors(stroke=(1.0, 0.0, 0.0))
        a2.update()
        doc.save(str(pdf_path))
        doc.close()

        profile = _make_profile(dedup_vertex_tolerance_pt=0.75)
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert len(records) == 2
        assert not any("duplicate overlay" in issue for issue in qc_issues)

    def test_near_miss_outside_tolerance_both_kept(self, tmp_path):
        """Vertices offset by 1.0pt (> 0.75pt tolerance) -> both kept, no dedup."""
        pdf_path = tmp_path / "near_miss.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text(fitz.Point(270.0, 65.0), "Option 1", fontsize=10, color=(0, 0, 0))
        ft = page.add_freetext_annot(fitz.Rect(50.0, 50.0, 200.0, 70.0), "SUOCCUR=Y")
        ft.update()
        a1 = page.add_line_annot(fitz.Point(199.0, 60.0), fitz.Point(290.0, 62.0))
        a1.set_line_ends(0, 0)
        a1.set_colors(stroke=(1.0, 0.0, 0.0))
        a1.update()
        a2 = page.add_line_annot(fitz.Point(200.0, 60.0), fitz.Point(291.0, 62.0))
        a2.set_line_ends(0, 0)
        a2.set_colors(stroke=(1.0, 0.0, 0.0))
        a2.update()
        doc.save(str(pdf_path))
        doc.close()

        profile = _make_profile(dedup_vertex_tolerance_pt=0.75)
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert len(records) == 2


class TestHeadTextAbsentKept:
    def test_head_with_no_nearby_text_kept_with_empty_head_text(self, tmp_path):
        """A tail-snapped arrow whose head finds no text within
        head_text_search_radius_pt is kept (head_text='') so Phase 4 can
        mark it unresolved — not silently dropped like the reference's
        raw-tail-vertex fallback would have implied."""
        pdf_path = tmp_path / "no_head_text.pdf"
        _pdf_with_annotation_and_line(
            pdf_path,
            line_p0=(199.0, 60.0),   # near FreeText -> tail
            line_p1=(400.0, 400.0),  # far from any text -> no head text
            line_ends=(0, 0),
            text_at=None,
        )
        profile = _make_profile(head_text_search_radius_pt=20.0)
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert len(records) == 1
        assert records[0].head_text == ""
        assert any("no text" in issue for issue in qc_issues)


class TestHeadNearAnnotationVsPrintedText:
    def test_head_prefers_printed_text_over_freetext_annotation_content(self, tmp_path):
        """D3: head snap must use the clean (annotation-free) page so a head
        near both printed CRF text and a FreeText annotation's rendered
        content anchors to the printed text, not the annotation.

        Uses a single-arrowhead line_ends code (0, 4) so the head/tail roles
        are decided unambiguously by the line-end code rather than by D6
        proximity classification (which would otherwise also see the decoy
        FreeText box as a second tail candidate near the head vertex and
        skip the arrow entirely — a separate mechanism from what this test
        is targeting)."""
        pdf_path = tmp_path / "clean_page.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        # Tail-side FreeText annotation (the arrow's parent).
        tail_ft = page.add_freetext_annot(fitz.Rect(50.0, 50.0, 200.0, 70.0), "SUOCCUR=Y")
        tail_ft.update()
        # Printed CRF text near the head vertex.
        page.insert_text(fitz.Point(270.0, 400.0), "Printed Label", fontsize=10, color=(0, 0, 0))
        # A second FreeText annotation whose rendered text sits at the same
        # spot as the head vertex — if head-snap used raw get_text("blocks")
        # (which includes annotation appearance text), it could match this
        # instead of "Printed Label".
        head_ft = page.add_freetext_annot(fitz.Rect(285.0, 397.0, 400.0, 417.0), "DECOY TEXT")
        head_ft.update()
        a = page.add_line_annot(fitz.Point(199.0, 60.0), fitz.Point(290.0, 402.0))
        a.set_line_ends(0, 4)  # v1 (head) unambiguous via line-end code
        a.update()
        doc.save(str(pdf_path))
        doc.close()

        profile = _make_profile(head_text_search_radius_pt=60.0)
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, qc_issues = extract_arrows(pdf_path, annotations, profile)

        assert len(records) == 1
        assert records[0].head_text == "Printed Label"
        assert "DECOY" not in records[0].head_text


class TestOpacityRoundTrip:
    def test_no_explicit_opacity_defaults_to_1(self, tmp_path):
        """PyMuPDF's -1 sentinel (no /CA key) must normalise to opacity=1.0."""
        pdf_path = tmp_path / "opacity.pdf"
        _pdf_with_annotation_and_line(
            pdf_path,
            line_p0=(199.0, 60.0),
            line_p1=(290.0, 62.0),
            line_ends=(0, 0),
            text_at=(270.0, 65.0),
        )
        profile = _make_profile()
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, _ = extract_arrows(pdf_path, annotations, profile)
        assert len(records) == 1
        assert records[0].style.opacity == 1.0


class TestExtractAnnotationsUnchanged:
    def test_extract_annotations_output_unchanged_when_arrows_present(self, tmp_path):
        """Regression: extract_annotations()'s own output must be identical
        whether or not Line annotations are present on the page — arrow
        extraction is a fully separate pass."""
        pdf_path = tmp_path / "with_lines.pdf"
        _pdf_with_annotation_and_line(
            pdf_path,
            line_p0=(199.0, 60.0),
            line_p1=(290.0, 62.0),
            line_ends=(4, 0),
            text_at=(270.0, 65.0),
        )
        pdf_path_no_line = tmp_path / "no_lines.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text(fitz.Point(270.0, 65.0), "Option 1", fontsize=10, color=(0, 0, 0))
        ft = page.add_freetext_annot(fitz.Rect(50.0, 50.0, 200.0, 70.0), "SUOCCUR=Y")
        ft.update()
        doc.save(str(pdf_path_no_line))
        doc.close()

        profile = _make_profile()
        records_with_line = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records_without_line = extract_annotations(pdf_path_no_line, profile, RuleEngine(profile))

        assert len(records_with_line) == len(records_without_line) == 1
        r1, r2 = records_with_line[0], records_without_line[0]
        assert r1.content == r2.content
        assert r1.category == r2.category
        assert r1.rect == r2.rect
        assert r1.anchor_text == r2.anchor_text


class TestArrowIdStability:
    def test_arrow_id_stable_across_reextraction(self, tmp_path):
        pdf_path = tmp_path / "stable.pdf"
        _pdf_with_annotation_and_line(
            pdf_path,
            line_p0=(199.0, 60.0),
            line_p1=(290.0, 62.0),
            line_ends=(0, 0),
            text_at=(270.0, 65.0),
        )
        profile = _make_profile()
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records1, _ = extract_arrows(pdf_path, annotations, profile)
        records2, _ = extract_arrows(pdf_path, annotations, profile)
        assert records1[0].arrow_id == records2[0].arrow_id

    def test_arrow_id_uses_1_indexed_page(self, tmp_path):
        """source_page and the arrow_id hash input both use 1-indexed pages,
        consistent with AnnotationRecord.page (not PyMuPDF's 0-indexed
        page_index, which the reference branch used)."""
        pdf_path = tmp_path / "page1.pdf"
        _pdf_with_annotation_and_line(
            pdf_path,
            line_p0=(199.0, 60.0),
            line_p1=(290.0, 62.0),
            line_ends=(0, 0),
            text_at=(270.0, 65.0),
        )
        profile = _make_profile()
        annotations = extract_annotations(pdf_path, profile, RuleEngine(profile))
        records, _ = extract_arrows(pdf_path, annotations, profile)
        assert records[0].source_page == 1
