"""Tests for arrow extraction in Phase 1 (extractor.py)."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from src.extractor import extract_arrows
from src.models import AnnotationRecord, ArrowRecord, StyleInfo
from src.profile_loader import load_profile
from tests.fixtures.create_arrow_fixtures import create_arrow_source_pdf

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def arrow_source_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("arrow_fixtures")
    return create_arrow_source_pdf(tmp / "arrows_source.pdf")


@pytest.fixture(scope="module")
def cdisc_profile():
    return load_profile(Path("profiles/cdisc_standard.yaml"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_annot(rect: list[float], page: int = 1) -> AnnotationRecord:
    return AnnotationRecord(
        id=str(uuid.uuid4()),
        page=page,
        content="SUOCCUR=Y",
        domain="AE",
        category="sdtm_mapping",
        matched_rule="mock",
        rect=rect,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_extracts_two_valid_arrows(arrow_source_pdf: Path, cdisc_profile) -> None:
    """Arrow 3 with ambiguous line_ends=(4,4) is skipped; 2 arrows remain."""
    arrows = extract_arrows(arrow_source_pdf, [], cdisc_profile)
    assert len(arrows) == 2
    assert all(isinstance(a, ArrowRecord) for a in arrows)


def test_head_tail_disambiguation(arrow_source_pdf: Path, cdisc_profile) -> None:
    """line_ends=(4,0) → head is first vertex (index 0)."""
    arrows = extract_arrows(arrow_source_pdf, [], cdisc_profile)
    assert len(arrows) == 2
    # Arrow 1: vertices[(290,62),(200,60)], le=(4,0) → head_vertex=v[0]=(290,62)
    arrow1 = arrows[0]
    assert arrow1.head_vertex == pytest.approx((290.0, 62.0), abs=0.5)
    assert arrow1.tail_vertex == pytest.approx((200.0, 60.0), abs=0.5)
    # Arrow 2: vertices[(290,102),(200,65)], le=(4,0) → head_vertex=v[0]=(290,102)
    arrow2 = arrows[1]
    assert arrow2.head_vertex == pytest.approx((290.0, 102.0), abs=0.5)
    assert arrow2.tail_vertex == pytest.approx((200.0, 65.0), abs=0.5)


def test_head_text_match(arrow_source_pdf: Path, cdisc_profile) -> None:
    """head_text matches 'Option 1' / 'Option 2' for the two valid arrows."""
    arrows = extract_arrows(arrow_source_pdf, [], cdisc_profile)
    assert len(arrows) == 2
    assert "Option 1" in arrows[0].head_text
    assert "Option 2" in arrows[1].head_text


def test_tail_snap_with_annotation(arrow_source_pdf: Path, cdisc_profile) -> None:
    """tail_annotation_id is set when an annotation rect covers the tail vertex."""
    mock = _mock_annot(rect=[50.0, 50.0, 200.0, 70.0], page=1)
    arrows = extract_arrows(arrow_source_pdf, [mock], cdisc_profile)
    assert len(arrows) == 2
    assert arrows[0].tail_annotation_id == mock.id
    assert arrows[1].tail_annotation_id == mock.id


def test_arrow_style_roundtrip(arrow_source_pdf: Path, cdisc_profile) -> None:
    """stroke_color is approximately red (1.0, 0.0, 0.0)."""
    arrows = extract_arrows(arrow_source_pdf, [], cdisc_profile)
    assert len(arrows) == 2
    for arrow in arrows:
        r, g, b = arrow.style.stroke_color
        assert r == pytest.approx(1.0, abs=0.05)
        assert g == pytest.approx(0.0, abs=0.05)
        assert b == pytest.approx(0.0, abs=0.05)


def test_ambiguous_line_ends_skipped(arrow_source_pdf: Path, cdisc_profile) -> None:
    """Arrow 3 with line_ends=(4,4) does not appear in results."""
    qc: list[str] = []
    arrows = extract_arrows(arrow_source_pdf, [], cdisc_profile, qc_issues=qc)
    # Only 2 valid arrows; Arrow 3 is absent
    assert len(arrows) == 2
    # A QC warning was recorded for the ambiguous arrow
    assert any("ambiguous" in msg.lower() for msg in qc)


def test_disabled_profile_returns_empty(arrow_source_pdf: Path, cdisc_profile) -> None:
    """When arrows.enabled=False the function returns an empty list immediately."""
    disabled_profile = cdisc_profile.model_copy(
        update={"arrows": cdisc_profile.arrows.model_copy(update={"enabled": False})}
    )
    arrows = extract_arrows(arrow_source_pdf, [], disabled_profile)
    assert arrows == []


def test_head_source_rect_populated(arrow_source_pdf: Path, cdisc_profile) -> None:
    """head_source_rect is not None for valid arrows that snapped to a text block."""
    arrows = extract_arrows(arrow_source_pdf, [], cdisc_profile)
    assert len(arrows) == 2
    for arrow in arrows:
        assert arrow.head_source_rect is not None, (
            f"Arrow {arrow.arrow_id} should have head_source_rect populated"
        )
        x0, y0, x1, y1 = arrow.head_source_rect
        assert x1 > x0, "head_source_rect width must be positive"
        assert y1 > y0, "head_source_rect height must be positive"
