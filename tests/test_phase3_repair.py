# tests/test_phase3_repair.py
"""Tests for Phase 3 inline re-pair helpers."""
import pytest
from src.matcher import compute_target_rect
from src.models import AnnotationRecord, FieldRecord


def _make_annot(**kwargs) -> AnnotationRecord:
    defaults = dict(
        id="a1", page=1, content="AETERM", domain="AE", category="sdtm_mapping",
        matched_rule="r1", rect=[100.0, 200.0, 200.0, 220.0],
        anchor_text="Adverse Event Term", anchor_rect=[50.0, 200.0, 150.0, 215.0],
        form_name="Adverse Events", visit="",
    )
    defaults.update(kwargs)
    return AnnotationRecord(**defaults)


def _make_field(**kwargs) -> FieldRecord:
    defaults = dict(
        id="f1", page=1, label="Adverse Event Term",
        form_name="Adverse Events", visit="", rect=[60.0, 300.0, 200.0, 315.0],
        field_type="text_field", page_width=595.0, page_height=842.0,
    )
    defaults.update(kwargs)
    return FieldRecord(**defaults)


def test_compute_target_rect_with_anchor():
    """Offset from anchor is replicated onto target field rect."""
    annot = _make_annot()
    field = _make_field()
    result = compute_target_rect(annot, field, [field])
    # dx = 100 - 50 = 50, dy = 200 - 200 = 0, w = 100, h = 20
    # x0 = 60 + 50 = 110, y0 = 300 + 0 = 300
    assert result[0] == pytest.approx(110.0)
    assert result[1] == pytest.approx(300.0)
    assert result[2] == pytest.approx(210.0)
    assert result[3] == pytest.approx(320.0)


def test_compute_target_rect_no_anchor():
    """Falls back to field.rect when annotation has no anchor_rect."""
    annot = _make_annot(anchor_rect=None)
    field = _make_field()
    result = compute_target_rect(annot, field, [field])
    assert result == pytest.approx([60.0, 300.0, 200.0, 315.0])


def test_compute_target_rect_clamped():
    """Out-of-bounds result is clamped to page dimensions."""
    annot = _make_annot(rect=[550.0, 200.0, 650.0, 220.0],
                        anchor_rect=[50.0, 200.0, 150.0, 215.0])
    field = _make_field()
    result = compute_target_rect(annot, field, [field])
    assert result[2] <= 595.0  # clamped to page_width
