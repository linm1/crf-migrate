"""End-to-end integration tests for the Arrow Migration Phase B pipeline.

Tests cover:
  1. Full pipeline round-trip: extract → match → resolve → write
  2. Arrows disabled in profile: extract_arrows returns []
  3. Session persistence: save/load ArrowRecord via Session
"""
from __future__ import annotations

import uuid
from pathlib import Path

import fitz
import pytest

from src.extractor import extract_arrows
from src.matcher import resolve_arrows
from src.models import (
    AnnotationRecord,
    ArrowMatch,
    ArrowRecord,
    ArrowStyle,
    FieldRecord,
    MatchRecord,
    StyleInfo,
)
from src.profile_loader import load_profile
from src.profile_models import (
    ClassificationRule,
    Profile,
    ProfileMeta,
    RuleCondition,
)
from src.session import Session
from src.writer import write_annotations
from tests.fixtures.create_arrow_fixtures import create_arrow_source_pdf

PROFILES_DIR = Path(__file__).parent.parent / "profiles"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_STYLE = ArrowStyle(
    stroke_color=(1.0, 0.0, 0.0),
    width=1.0,
    dashes=[],
    line_ends=(4, 0),
    opacity=1.0,
)


def _simple_profile() -> Profile:
    """Minimal profile with arrows enabled and a single fallback rule."""
    return Profile(
        meta=ProfileMeta(name="integration-test"),
        domain_codes=["DM"],
        classification_rules=[
            ClassificationRule(
                conditions=RuleCondition(fallback=True),
                category="sdtm_mapping",
            )
        ],
    )


def _make_integration_target_pdf(path: Path) -> Path:
    """Single-page target PDF with 'Option 1' and 'Option 2' text blocks.

    The head_text of the two source arrows is 'Option 1' and 'Option 2'
    (set by create_arrow_source_pdf).  Placing matching text here ensures
    resolve_arrows can fuzzy-match them in Pass B (page-wide search).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Field label area  [50, 50, 200, 70]  — same region as source FreeText
    page.insert_text(fitz.Point(50, 65), "SUOCCUR", fontsize=10, color=(0, 0, 0))
    # Arrow head text blocks — must match head_text values from the source fixture
    page.insert_text(fitz.Point(270, 65), "Option 1", fontsize=10, color=(0, 0, 0))
    page.insert_text(fitz.Point(270, 107), "Option 2", fontsize=10, color=(0, 0, 0))
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Test 1: Full pipeline round-trip
# ---------------------------------------------------------------------------

def test_full_arrow_pipeline_round_trip(tmp_path: Path) -> None:
    """Full pipeline: extract → resolve → write; Line annotations written to output."""

    profile = _simple_profile()
    assert profile.arrows.enabled, "arrows must be enabled in the test profile"

    # ----- Create PDFs -----
    source_pdf = tmp_path / "source.pdf"
    target_pdf = tmp_path / "target.pdf"
    output_pdf = tmp_path / "output.pdf"

    create_arrow_source_pdf(source_pdf)
    _make_integration_target_pdf(target_pdf)

    # ----- Phase 1 — extract arrows -----
    # Use a synthetic AnnotationRecord that matches the FreeText position in
    # the source fixture so that tail-snapping sets tail_annotation_id.
    annot_id = "annot-integration-001"
    synthetic_annot = AnnotationRecord(
        id=annot_id,
        page=1,
        content="SUOCCUR=Y",
        domain="AE",
        category="sdtm_mapping",
        matched_rule="integration-test-fallback",
        rect=[50.0, 50.0, 200.0, 70.0],
    )

    arrows = extract_arrows(source_pdf, [synthetic_annot], profile)

    assert len(arrows) >= 1, "Should extract at least one arrow from the source fixture"
    assert all(a.arrow_id for a in arrows), "Every ArrowRecord must have a non-empty arrow_id"

    # ----- Phase 3 — resolve arrows -----
    # Synthetic field pointing to the same page/region as the annot match
    field_id = "field-integration-001"
    synthetic_field = FieldRecord(
        id=field_id,
        page=1,
        label="SUOCCUR",
        rect=[50.0, 50.0, 200.0, 70.0],
        field_type="text_field",
        page_width=595.0,
        page_height=842.0,
    )

    # Synthetic match: maps synthetic_annot → synthetic_field, approved
    synthetic_match = MatchRecord(
        annotation_id=annot_id,
        field_id=field_id,
        match_type="exact",
        confidence=1.0,
        target_rect=[50.0, 50.0, 200.0, 70.0],
        target_page=1,
        status="approved",
    )

    arrow_matches = resolve_arrows(
        arrows,
        [synthetic_match],
        [synthetic_field],
        target_pdf,
        profile,
    )

    # Every arrow must produce one ArrowMatch (even if unresolved)
    assert len(arrow_matches) == len(arrows), (
        "resolve_arrows must return one ArrowMatch per input arrow"
    )

    resolved = [m for m in arrow_matches if m.head_match_method != "unresolved"]
    assert len(resolved) >= 1, "At least one arrow should resolve to a target text block"

    # ----- Phase 4 — write -----
    result = write_annotations(
        target_pdf,
        output_pdf,
        [synthetic_match],
        [synthetic_annot],
        profile,
        arrow_matches=arrow_matches,
        arrows=arrows,
    )

    # Basic structural assertions on the QC report
    assert output_pdf.exists(), "Output PDF must be created"
    assert "arrows_written" in result
    assert "arrows_skipped" in result
    assert result["arrows_written"] + result["arrows_skipped"] == len(arrow_matches)
    assert result["arrows_written"] >= 1, "At least one resolved arrow must be written"

    # Verify Line annotations (type 3) are present in the output PDF
    doc = fitz.open(str(output_pdf))
    line_annots: list = []
    for page in doc:
        for annot in page.annots():
            if annot.type[0] == 3:   # PDF annotation type 3 = Line
                line_annots.append(annot)
    doc.close()

    assert len(line_annots) >= 1, "Output PDF must contain at least one Line annotation"


# ---------------------------------------------------------------------------
# Test 2: Arrows disabled in profile
# ---------------------------------------------------------------------------

def test_arrows_disabled_in_profile(tmp_path: Path) -> None:
    """When profile.arrows.enabled is False, extract_arrows returns []."""
    profile = _simple_profile()
    profile = profile.model_copy(
        update={"arrows": profile.arrows.model_copy(update={"enabled": False})}
    )

    source_pdf = tmp_path / "source.pdf"
    doc = fitz.open()
    page = doc.new_page()
    # Add a Line annotation so there is something to skip
    a = page.add_line_annot(fitz.Point(100, 100), fitz.Point(200, 200))
    a.set_line_ends(fitz.PDF_ANNOT_LE_OPEN_ARROW, fitz.PDF_ANNOT_LE_NONE)
    a.update()
    doc.save(str(source_pdf))
    doc.close()

    arrows = extract_arrows(source_pdf, [], profile)

    assert arrows == [], "extract_arrows must return [] when arrows are disabled"


# ---------------------------------------------------------------------------
# Test 3: Session persistence for ArrowRecord
# ---------------------------------------------------------------------------

def test_arrow_session_persistence(tmp_path: Path) -> None:
    """ArrowRecord objects survive a save → load round-trip via Session."""
    session = Session(tmp_path)

    arrow = ArrowRecord(
        arrow_id="abc123",
        source_page=0,
        tail_vertex=(10.0, 20.0),
        head_vertex=(100.0, 120.0),
        tail_annotation_id="annot_001",
        head_text="SUBJID",
        head_search_hint=(100.0, 120.0),
        head_source_rect=(90.0, 115.0, 130.0, 125.0),
        style=ArrowStyle(
            stroke_color=(1.0, 0.0, 0.0),
            width=1.0,
            dashes=[],
            line_ends=(0, 1),
        ),
    )

    session.save_arrows([arrow])
    loaded = session.load_arrows()

    assert len(loaded) == 1
    assert loaded[0].arrow_id == "abc123"
    assert loaded[0].head_text == "SUBJID"
    assert loaded[0].tail_annotation_id == "annot_001"
    assert loaded[0].head_source_rect == (90.0, 115.0, 130.0, 125.0)
    assert loaded[0].style.stroke_color == (1.0, 0.0, 0.0)
