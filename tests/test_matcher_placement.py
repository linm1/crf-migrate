"""Tests for OOB placement guards in src/matcher.py and QC report in src/writer.py."""
from __future__ import annotations

import uuid

from src.matcher import _apply_placement_guard
from src.models import AnnotationRecord, FieldRecord, MatchRecord
from src.writer import build_qc_report


def _make_field(
    label: str,
    rect: list[float],
    page: int = 1,
    page_width: float = 600.0,
    page_height: float = 800.0,
    form_name: str = "TestForm",
) -> FieldRecord:
    return FieldRecord(
        id=str(uuid.uuid4()),
        page=page,
        label=label,
        form_name=form_name,
        rect=rect,
        field_type="text_field",
        page_width=page_width,
        page_height=page_height,
    )


def _make_match(annotation_id: str, placement_adjusted: bool) -> MatchRecord:
    return MatchRecord(
        annotation_id=annotation_id,
        field_id=str(uuid.uuid4()),
        match_type="exact",
        confidence=1.0,
        target_rect=[0.0, 0.0, 100.0, 20.0],
        placement_adjusted=placement_adjusted,
    )


def test_oob_right_triggers_leftmost_fallback() -> None:
    """OOB on right edge: fallback uses leftmost same-label peer rect."""
    # Source annotation rect and anchor rect give dx = 500 - 400 = +100
    # Target matched field rect: [450, 200, 530, 220]
    # _apply_anchor_offset -> x0 = 450 + 100 = 550, x1 = 550 + 60 = 610 > 600 (OOB)
    # Peer field (leftmost) rect: [50, 200, 130, 220] — should be used as fallback

    matched_field = _make_field("Question", rect=[450.0, 200.0, 530.0, 220.0])
    peer_field = _make_field("Question", rect=[50.0, 200.0, 130.0, 220.0])
    peer_right = _make_field("Question", rect=[300.0, 200.0, 380.0, 220.0])
    all_fields = [matched_field, peer_field, peer_right]

    # OOB target_rect produced by anchor offset calculation
    target_rect = [550.0, 200.0, 610.0, 220.0]

    final_rect, was_adjusted = _apply_placement_guard(target_rect, matched_field, all_fields)

    assert was_adjusted is True
    assert final_rect == [50.0, 200.0, 130.0, 220.0]


def test_oob_left_triggers_leftmost_fallback() -> None:
    """OOB on left edge: fallback uses leftmost same-label peer (excluding matched_field)."""
    # Source annotation rect [30, 100, 90, 120], anchor rect [150, 100, 230, 120]
    # dx = 30 - 150 = -120
    # Target matched field rect: [80, 200, 160, 220]
    # _apply_anchor_offset -> x0 = 80 + (-120) = -40 -> OOB on left
    # matched_field is excluded; leftmost of remaining peers is peer_leftmost at [80, 200, 160, 220]

    matched_field = _make_field("Item", rect=[80.0, 200.0, 160.0, 220.0])
    peer_field = _make_field("Item", rect=[200.0, 200.0, 280.0, 220.0])
    peer_leftmost = _make_field("Item", rect=[60.0, 200.0, 140.0, 220.0])
    all_fields = [matched_field, peer_field, peer_leftmost]

    # OOB target_rect produced by anchor offset
    target_rect = [-40.0, 200.0, 20.0, 220.0]

    final_rect, was_adjusted = _apply_placement_guard(target_rect, matched_field, all_fields)

    assert was_adjusted is True
    # Leftmost of remaining peers is peer_leftmost at x=60
    assert final_rect == [60.0, 200.0, 140.0, 220.0]


def test_no_peer_falls_back_to_clamp() -> None:
    """OOB rect but no same-label peer: clamp fires instead of peer fallback."""
    # Source annotation rect [520, 100, 580, 120], anchor [420, 100, 500, 120] -> dx = +100
    # Target field rect [480, 200, 560, 220]
    # _apply_anchor_offset -> x0 = 480 + 100 = 580, x1 = 580 + 60 = 640 > 600 (OOB)
    # No other "Unique" field; clamp fires: x1 clamped to 600

    matched_field = _make_field("Unique", rect=[480.0, 200.0, 560.0, 220.0])
    all_fields = [matched_field]  # matched_field excluded in peer search -> no peers

    target_rect = [580.0, 200.0, 640.0, 220.0]

    final_rect, was_adjusted = _apply_placement_guard(target_rect, matched_field, all_fields)

    assert was_adjusted is True
    assert final_rect == [580.0, 200.0, 600.0, 220.0]


def test_in_bounds_no_adjustment() -> None:
    """Valid in-bounds placement: no adjustment expected."""
    # Source annotation rect [100, 100, 160, 120], anchor [200, 100, 280, 120] -> dx = -100
    # Target field rect [300, 200, 380, 220]
    # _apply_anchor_offset -> x0 = 300 - 100 = 200, x1 = 200 + 60 = 260 — in bounds

    matched_field = _make_field("Field", rect=[300.0, 200.0, 380.0, 220.0])
    all_fields = [matched_field]

    target_rect = [200.0, 200.0, 260.0, 220.0]

    final_rect, was_adjusted = _apply_placement_guard(target_rect, matched_field, all_fields)

    assert was_adjusted is False
    assert final_rect == [200.0, 200.0, 260.0, 220.0]


def test_qc_report_includes_adjusted_ids() -> None:
    """build_qc_report returns placement_adjusted_ids for adjusted matches only."""
    id_adjusted_1 = str(uuid.uuid4())
    id_adjusted_2 = str(uuid.uuid4())
    id_not_adjusted = str(uuid.uuid4())

    matches = [
        _make_match(id_adjusted_1, placement_adjusted=True),
        _make_match(id_not_adjusted, placement_adjusted=False),
        _make_match(id_adjusted_2, placement_adjusted=True),
    ]

    report = build_qc_report(matches, written_ids=[], skipped_ids=[])

    adjusted_ids = report["placement_adjusted_ids"]
    assert set(adjusted_ids) == {id_adjusted_1, id_adjusted_2}
    assert id_not_adjusted not in adjusted_ids
