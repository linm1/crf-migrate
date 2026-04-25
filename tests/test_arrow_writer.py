"""Tests for arrow writing in Phase 4 (writer.py) — Task 6 Phase B.

5 tests covering:
  - Resolved arrow produces a Line annotation on the correct page
  - Style (stroke_color, border width, line_ends) is preserved on roundtrip
  - Unresolved arrow is skipped (no Line annotation added)
  - QC report counts arrows_written / arrows_skipped correctly
  - Backward compatibility when arrow_matches / arrows are not passed
"""
from __future__ import annotations

import uuid
from pathlib import Path

import fitz
import pytest

from src.models import (
    AnnotationRecord,
    ArrowMatch,
    ArrowRecord,
    ArrowStyle,
    MatchRecord,
    StyleInfo,
)
from src.profile_models import (
    ClassificationRule,
    Profile,
    ProfileMeta,
    RuleCondition,
    StyleDefaults,
)
from src.writer import write_annotations
from tests.fixtures.create_arrow_fixtures import create_arrow_output_target_pdf

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile() -> Profile:
    return Profile(
        meta=ProfileMeta(name="test"),
        domain_codes=["DM", "AE"],
        classification_rules=[
            ClassificationRule(
                conditions=RuleCondition(fallback=True),
                category="sdtm_mapping",
            )
        ],
    )


def _make_annotation(annot_id: str = "annot-001", page: int = 1) -> AnnotationRecord:
    return AnnotationRecord(
        id=annot_id,
        page=page,
        content="BRTHDTC",
        domain="DM",
        category="sdtm_mapping",
        matched_rule="test",
        rect=[50.0, 80.0, 200.0, 100.0],
        style=StyleInfo(),
    )


def _make_match(
    annot_id: str = "annot-001",
    status: str = "approved",
    target_page: int = 1,
    target_rect: list[float] | None = None,
) -> MatchRecord:
    return MatchRecord(
        annotation_id=annot_id,
        field_id="field-001",
        match_type="exact",
        confidence=1.0,
        target_rect=target_rect or [50.0, 80.0, 200.0, 100.0],
        target_page=target_page,
        status=status,
    )


def _make_arrow_style() -> ArrowStyle:
    return ArrowStyle(
        stroke_color=(1.0, 0.0, 0.0),
        width=2.0,
        dashes=[],
        line_ends=(4, 0),
        opacity=1.0,
    )


def _make_arrow_record(arrow_id: str = "arrow-001") -> ArrowRecord:
    return ArrowRecord(
        arrow_id=arrow_id,
        source_page=0,
        tail_vertex=(200.0, 90.0),
        head_vertex=(210.0, 62.0),
        head_source_rect=(190.0, 55.0, 260.0, 75.0),
        tail_annotation_id="annot-001",
        head_text="Yes",
        head_search_hint=(210.0, 62.0),
        style=_make_arrow_style(),
    )


def _make_arrow_match(
    arrow_id: str = "arrow-001",
    method: str = "fuzzy_in_field",
    head_target_rect: tuple[float, float, float, float] | None = (200.0, 55.0, 260.0, 75.0),
    target_page: int = 1,
) -> ArrowMatch:
    return ArrowMatch(
        arrow_id=arrow_id,
        target_page=target_page,
        target_field_id="field-001",
        head_target_rect=head_target_rect,
        head_match_method=method,
        head_confidence=0.9,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_write_resolved_arrow_appears_in_output(tmp_path: Path) -> None:
    """A resolved ArrowMatch produces a Line annotation on the target page."""
    target_pdf = create_arrow_output_target_pdf(tmp_path / "target.pdf")
    output_pdf = tmp_path / "output.pdf"
    profile = _make_profile()

    annot = _make_annotation()
    match = _make_match()
    arrow = _make_arrow_record()
    arm = _make_arrow_match()

    write_annotations(
        target_pdf_path=target_pdf,
        output_pdf_path=output_pdf,
        matches=[match],
        annotations=[annot],
        profile=profile,
        arrow_matches=[arm],
        arrows=[arrow],
    )

    doc = fitz.open(str(output_pdf))
    try:
        page = doc[0]
        line_annots = [a for a in page.annots() if a.type[0] == 3]  # type 3 = Line
        assert len(line_annots) == 1, "Expected exactly 1 Line annotation on page 1"
    finally:
        doc.close()


def test_write_arrow_style_preserved(tmp_path: Path) -> None:
    """Arrow stroke_color, border width, and line_ends are preserved on roundtrip."""
    target_pdf = create_arrow_output_target_pdf(tmp_path / "target.pdf")
    output_pdf = tmp_path / "output.pdf"
    profile = _make_profile()

    annot = _make_annotation()
    match = _make_match()
    arrow = _make_arrow_record()
    arm = _make_arrow_match()

    write_annotations(
        target_pdf_path=target_pdf,
        output_pdf_path=output_pdf,
        matches=[match],
        annotations=[annot],
        profile=profile,
        arrow_matches=[arm],
        arrows=[arrow],
    )

    doc = fitz.open(str(output_pdf))
    try:
        page = doc[0]
        line_annots = [a for a in page.annots() if a.type[0] == 3]
        assert len(line_annots) == 1

        a = line_annots[0]
        # stroke_color
        stroke = a.colors.get("stroke") or []
        assert len(stroke) >= 3
        assert stroke[0] == pytest.approx(1.0, abs=0.05)
        assert stroke[1] == pytest.approx(0.0, abs=0.05)
        assert stroke[2] == pytest.approx(0.0, abs=0.05)

        # border width
        border = a.border or {}
        assert border.get("width") == pytest.approx(2.0, abs=0.1)

        # line_ends
        le = a.line_ends
        assert le is not None
        assert int(le[0]) == 4  # open arrow at p1
        assert int(le[1]) == 0  # none at p2
    finally:
        doc.close()


def test_unresolved_arrow_skipped(tmp_path: Path) -> None:
    """An ArrowMatch with method='unresolved' and no head_target_rect is not written."""
    target_pdf = create_arrow_output_target_pdf(tmp_path / "target.pdf")
    output_pdf = tmp_path / "output.pdf"
    profile = _make_profile()

    annot = _make_annotation()
    match = _make_match()
    arrow = _make_arrow_record()
    arm = _make_arrow_match(method="unresolved", head_target_rect=None)

    write_annotations(
        target_pdf_path=target_pdf,
        output_pdf_path=output_pdf,
        matches=[match],
        annotations=[annot],
        profile=profile,
        arrow_matches=[arm],
        arrows=[arrow],
    )

    doc = fitz.open(str(output_pdf))
    try:
        page = doc[0]
        line_annots = [a for a in page.annots() if a.type[0] == 3]
        assert len(line_annots) == 0, "Unresolved arrow must not produce a Line annotation"
    finally:
        doc.close()


def test_arrow_qc_counts_in_report(tmp_path: Path) -> None:
    """QC report contains arrows_written and arrows_skipped with correct counts."""
    target_pdf = create_arrow_output_target_pdf(tmp_path / "target.pdf")
    output_pdf = tmp_path / "output.pdf"
    profile = _make_profile()

    annot1 = _make_annotation("annot-001")
    annot2 = _make_annotation("annot-002")
    annot3 = _make_annotation("annot-003")
    match1 = _make_match("annot-001")
    match2 = _make_match("annot-002")
    match3 = _make_match("annot-003")

    arrow1 = _make_arrow_record("arrow-001")
    arrow2 = ArrowRecord(
        arrow_id="arrow-002",
        source_page=0,
        tail_vertex=(200.0, 90.0),
        head_vertex=(210.0, 62.0),
        head_source_rect=(190.0, 55.0, 260.0, 75.0),
        tail_annotation_id="annot-002",
        head_text="Yes",
        head_search_hint=(210.0, 62.0),
        style=_make_arrow_style(),
    )
    arrow3 = ArrowRecord(
        arrow_id="arrow-003",
        source_page=0,
        tail_vertex=(200.0, 90.0),
        head_vertex=(210.0, 62.0),
        head_source_rect=None,
        tail_annotation_id="annot-003",
        head_text="",
        head_search_hint=(210.0, 62.0),
        style=_make_arrow_style(),
    )

    arm1 = _make_arrow_match("arrow-001", method="fuzzy_in_field")
    arm2 = _make_arrow_match("arrow-002", method="fuzzy_on_page")
    arm3 = _make_arrow_match("arrow-003", method="unresolved", head_target_rect=None)

    report = write_annotations(
        target_pdf_path=target_pdf,
        output_pdf_path=output_pdf,
        matches=[match1, match2, match3],
        annotations=[annot1, annot2, annot3],
        profile=profile,
        arrow_matches=[arm1, arm2, arm3],
        arrows=[arrow1, arrow2, arrow3],
    )

    assert report["arrows_written"] == 2
    assert report["arrows_skipped"] == 1


def test_no_arrows_provided_backward_compat(tmp_path: Path) -> None:
    """write_annotations without arrow_matches/arrows runs without error."""
    target_pdf = create_arrow_output_target_pdf(tmp_path / "target.pdf")
    output_pdf = tmp_path / "output.pdf"
    profile = _make_profile()

    annot = _make_annotation()
    match = _make_match()

    report = write_annotations(
        target_pdf_path=target_pdf,
        output_pdf_path=output_pdf,
        matches=[match],
        annotations=[annot],
        profile=profile,
    )

    assert isinstance(report, dict)
    assert "written" in report
    assert report["arrows_written"] == 0
    assert report["arrows_skipped"] == 0
