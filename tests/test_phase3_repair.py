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
    # dx = 550 - 50 = 500, x0 = 60 + 500 = 560, x1 = 660 → clamped to 595
    annot = _make_annot(rect=[550.0, 200.0, 650.0, 220.0],
                        anchor_rect=[50.0, 200.0, 150.0, 215.0])
    field = _make_field()
    result = compute_target_rect(annot, field, [field])
    assert result[0] == pytest.approx(560.0)   # x0 in-bounds
    assert result[1] == pytest.approx(300.0)   # y0 unchanged
    assert result[2] == pytest.approx(595.0)   # x1 clamped to page_width
    assert result[3] == pytest.approx(320.0)   # y1 unchanged
    assert result[2] >= result[0]              # geometrically valid


def test_compute_target_rect_peer_fallback():
    """When OOB and a same-label peer exists, peer rect is used instead of clamping."""
    # Put the annotation so far right that offset pushes rect out of bounds
    annot = _make_annot(rect=[580.0, 200.0, 680.0, 220.0],
                        anchor_rect=[50.0, 200.0, 150.0, 215.0])
    field = _make_field(id="f1", rect=[60.0, 300.0, 200.0, 315.0])
    # Peer: same page, same label, clearly in bounds
    peer = _make_field(id="f2", rect=[10.0, 400.0, 150.0, 415.0])
    result = compute_target_rect(annot, field, [field, peer])
    # dx = 580 - 50 = 530, x0 = 60 + 530 = 590 → OOB (> page_width 595 after x1=690)
    # peer fallback: leftmost peer (f2 has x0=10 < f1 x0=60 but f1 is the matched field)
    # _apply_placement_guard finds peers with same label on same page (f2), uses leftmost
    assert result == pytest.approx([10.0, 400.0, 150.0, 415.0])


from ui.phase3_review import _compute_predicted_confidence


def test_predicted_confidence_exact_label():
    """Identical anchor_text and field label → score 1.0."""
    annot = _make_annot(anchor_text="Adverse Event Term", visit="Week 1")
    field = _make_field(label="Adverse Event Term", visit="Week 1")
    score = _compute_predicted_confidence(annot, field, visit_boost=5.0)
    assert score == pytest.approx(1.0)


def test_predicted_confidence_no_visit_boost():
    """Visit mismatch → no boost, but raw=100 still gives 1.0."""
    annot = _make_annot(anchor_text="Adverse Event Term", visit="Week 1")
    field = _make_field(label="Adverse Event Term", visit="Week 2")
    score = _compute_predicted_confidence(annot, field, visit_boost=5.0)
    assert score == pytest.approx(1.0)


def test_predicted_confidence_partial_match():
    """Partial label match returns score < 1.0."""
    annot = _make_annot(anchor_text="Systolic BP", visit="")
    field = _make_field(label="Systolic Blood Pressure", visit="")
    score = _compute_predicted_confidence(annot, field, visit_boost=5.0)
    assert 0.5 < score < 1.0


def test_predicted_confidence_capped_at_1():
    """Score + large visit_boost is capped at 1.0."""
    annot = _make_annot(anchor_text="Systolic BP", visit="Week 1")
    field = _make_field(label="Systolic BP", visit="Week 1")
    score = _compute_predicted_confidence(annot, field, visit_boost=50.0)
    assert score == pytest.approx(1.0)


from ui.phase3_review import _field_display_label


def test_field_display_label_with_field():
    field = FieldRecord(
        id="f1", page=3, label="Systolic BP",
        form_name="Vital Signs", visit="", rect=[0, 0, 1, 1],
        field_type="text_field", page_width=595.0, page_height=842.0,
    )
    assert _field_display_label(field) == "Systolic BP  ·  Vital Signs  ·  p.3"


def test_field_display_label_none():
    assert _field_display_label(None) == "—"


def test_field_type_badge_colors():
    """Each field_type maps to the Phase 2 color system."""
    from ui.phase3_review import _FIELD_TYPE_BADGE
    assert _FIELD_TYPE_BADGE["text_field"]     == ("TF", "#EEF2FF", "#C7D2FE", "#4F46E5")
    assert _FIELD_TYPE_BADGE["checkbox"]       == ("CB", "#FEF9C3", "#FDE047", "#A16207")
    assert _FIELD_TYPE_BADGE["date_field"]     == ("DF", "#F0FDF4", "#BBF7D0", "#16A34A")
    assert _FIELD_TYPE_BADGE["table_row"]      == ("TR", "#F4EFEA", "#D4CEC8", "#6B7280")
    assert _FIELD_TYPE_BADGE["section_header"] == ("SH", "#F4EFEA", "#D4CEC8", "#383838")


from ui.phase3_review import _is_repair_eligible


def test_repair_eligible_fuzzy():
    assert _is_repair_eligible("fuzzy") is True


def test_repair_eligible_position_only():
    assert _is_repair_eligible("position_only") is True


def test_repair_eligible_unmatched():
    assert _is_repair_eligible("unmatched") is True


def test_repair_eligible_manual():
    assert _is_repair_eligible("manual") is True


def test_repair_not_eligible_exact():
    assert _is_repair_eligible("exact") is False
