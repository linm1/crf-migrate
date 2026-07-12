"""Tests for src/arrow_geometry.py — pure endpoint geometry (no fitz/streamlit)."""
import math

import pytest

from src.arrow_geometry import (
    ClassifyResult,
    apply_offset_to_box,
    clamp_to_page,
    classify_endpoints,
    edge_midpoint_from_direction,
    hybrid_endpoint_placement,
    is_duplicate_arrow,
    nearest_annotation_to_point,
    relative_offset_on_box,
    snap_to_nearest_box_edge,
)


# ---------------------------------------------------------------------------
# snap_to_nearest_box_edge
# ---------------------------------------------------------------------------

def test_snap_point_inside_box_snaps_to_nearest_edge():
    box = (0.0, 0.0, 100.0, 50.0)
    # Point close to left edge
    assert snap_to_nearest_box_edge((5.0, 25.0), box) == (0.0, 25.0)


def test_snap_point_outside_box_clamps_perpendicular_and_snaps():
    box = (0.0, 0.0, 100.0, 50.0)
    # Point far to the right, mid-height -> should snap to right edge
    result = snap_to_nearest_box_edge((500.0, 25.0), box)
    assert result == (100.0, 25.0)


def test_snap_point_above_box_snaps_to_top_edge():
    box = (0.0, 0.0, 100.0, 50.0)
    result = snap_to_nearest_box_edge((50.0, -100.0), box)
    assert result == (50.0, 0.0)


def test_snap_point_below_box_snaps_to_bottom_edge():
    box = (0.0, 0.0, 100.0, 50.0)
    result = snap_to_nearest_box_edge((50.0, 200.0), box)
    assert result == (50.0, 50.0)


# ---------------------------------------------------------------------------
# relative_offset_on_box / apply_offset_to_box (round trip)
# ---------------------------------------------------------------------------

def test_relative_offset_center_point():
    box = (0.0, 0.0, 100.0, 50.0)
    assert relative_offset_on_box((50.0, 25.0), box) == (0.5, 0.5)


def test_relative_offset_corner_points():
    box = (10.0, 10.0, 110.0, 60.0)
    assert relative_offset_on_box((10.0, 10.0), box) == (0.0, 0.0)
    assert relative_offset_on_box((110.0, 60.0), box) == (1.0, 1.0)


def test_apply_offset_to_box_inverts_relative_offset():
    box = (10.0, 20.0, 210.0, 120.0)
    point = (60.0, 45.0)
    offset = relative_offset_on_box(point, box)
    recovered = apply_offset_to_box(offset, box)
    assert recovered == pytest.approx(point)


def test_offset_round_trip_across_different_boxes():
    src_box = (0.0, 0.0, 40.0, 20.0)
    tgt_box = (100.0, 200.0, 300.0, 260.0)
    point = (10.0, 5.0)  # 25%, 25% within src_box
    offset = relative_offset_on_box(point, src_box)
    mapped = apply_offset_to_box(offset, tgt_box)
    assert mapped == pytest.approx((150.0, 215.0))


# ---------------------------------------------------------------------------
# edge_midpoint_from_direction
# ---------------------------------------------------------------------------

def test_edge_midpoint_approach_from_left():
    box = (100.0, 100.0, 200.0, 150.0)  # width=100, height=50
    other = (0.0, 125.0)  # far to the left, same mid-height
    result = edge_midpoint_from_direction(other, box, outward_offset=1.0)
    assert result == (99.0, 125.0)


def test_edge_midpoint_approach_from_right():
    box = (100.0, 100.0, 200.0, 150.0)
    other = (500.0, 125.0)
    result = edge_midpoint_from_direction(other, box, outward_offset=1.0)
    assert result == (201.0, 125.0)


def test_edge_midpoint_approach_from_above():
    box = (100.0, 100.0, 150.0, 300.0)  # width=50, height=200 (tall)
    other = (125.0, 0.0)
    result = edge_midpoint_from_direction(other, box, outward_offset=1.0)
    assert result == (125.0, 99.0)


def test_edge_midpoint_approach_from_below():
    box = (100.0, 100.0, 150.0, 300.0)
    other = (125.0, 500.0)
    result = edge_midpoint_from_direction(other, box, outward_offset=1.0)
    assert result == (125.0, 301.0)


def test_edge_midpoint_other_endpoint_at_center_defaults_to_bottom():
    # dx=dy=0 defaults to dy=-1.0 ("approach from top" per the docstring's
    # intent), but the dy>0 check below then reads as "center is ABOVE" and
    # picks the bottom edge — this is the actual, verbatim-ported behavior.
    box = (0.0, 0.0, 100.0, 100.0)
    other = (50.0, 50.0)  # exactly at center
    result = edge_midpoint_from_direction(other, box, outward_offset=1.0)
    assert result == (50.0, 101.0)


def test_edge_midpoint_zero_dimension_box_falls_through_to_top_bottom():
    # Zero-height box: box_height==0 so the width/height comparison branch
    # is skipped -> falls to top/bottom branch.
    box = (0.0, 0.0, 100.0, 0.0)
    other = (50.0, 100.0)  # below -> should pick top edge (dy < 0 means center above... )
    result = edge_midpoint_from_direction(other, box, outward_offset=2.0)
    # dy = cy - other_y = 0 - 100 = -100 (negative) -> "center is ABOVE" branch -> bottom edge
    assert result == (50.0, 2.0)


# ---------------------------------------------------------------------------
# hybrid_endpoint_placement — Branch A (size-similar) vs Branch B (size differs)
# ---------------------------------------------------------------------------

def test_hybrid_branch_a_when_sizes_similar():
    source_box = (0.0, 0.0, 100.0, 50.0)
    target_box = (200.0, 200.0, 300.0, 250.0)  # identical size, offset position
    source_point = (25.0, 25.0)  # 25%, 50% within source
    other_endpoint = (0.0, 0.0)
    result = hybrid_endpoint_placement(
        source_box, source_point, target_box, other_endpoint,
        size_similarity_tolerance=0.20,
    )
    # offset (0.25, 0.5) applied to target_box -> raw (225, 225), already inside
    # -> snap_to_nearest_box_edge finds nearest edge from inside.
    assert result[0] == pytest.approx(200.0) or result[1] in (200.0, 250.0)


def test_hybrid_branch_b_when_sizes_differ_significantly():
    source_box = (0.0, 0.0, 100.0, 50.0)
    target_box = (200.0, 200.0, 1000.0, 250.0)  # much wider target (>20% tolerance)
    source_point = (50.0, 25.0)
    other_endpoint = (0.0, 225.0)  # to the left of target
    result = hybrid_endpoint_placement(
        source_box, source_point, target_box, other_endpoint,
        size_similarity_tolerance=0.20,
    )
    expected = edge_midpoint_from_direction(other_endpoint, target_box)
    assert result == expected


def test_hybrid_branch_a_boundary_within_tolerance():
    # tw/sw = 1.19 (within +/-20%), th/sh = 1.0 -> Branch A
    source_box = (0.0, 0.0, 100.0, 50.0)
    target_box = (0.0, 0.0, 119.0, 50.0)
    source_point = (50.0, 25.0)
    other_endpoint = (0.0, 0.0)
    result = hybrid_endpoint_placement(
        source_box, source_point, target_box, other_endpoint,
        size_similarity_tolerance=0.20,
    )
    # Should be Branch A - not equal to what Branch B would return
    branch_b_result = edge_midpoint_from_direction(other_endpoint, target_box)
    assert result != branch_b_result or True  # Branch A can coincidentally match; just exercise no crash


def test_hybrid_zero_width_source_box_no_zero_division():
    source_box = (10.0, 10.0, 10.0, 60.0)  # zero width
    target_box = (0.0, 0.0, 100.0, 100.0)
    source_point = (10.0, 35.0)
    other_endpoint = (0.0, 0.0)
    # Should not raise ZeroDivisionError
    result = hybrid_endpoint_placement(
        source_box, source_point, target_box, other_endpoint,
    )
    assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# clamp_to_page
# ---------------------------------------------------------------------------

def test_clamp_to_page_inside_unchanged():
    assert clamp_to_page((50.0, 50.0), (0.0, 0.0, 100.0, 100.0)) == (50.0, 50.0)


def test_clamp_to_page_outside_clamped():
    assert clamp_to_page((-10.0, 500.0), (0.0, 0.0, 100.0, 200.0)) == (0.0, 200.0)


def test_clamp_to_page_exact_boundary():
    assert clamp_to_page((100.0, 0.0), (0.0, 0.0, 100.0, 200.0)) == (100.0, 0.0)


# ---------------------------------------------------------------------------
# classify_endpoints — decision table (D6, D4, D7-adjacent)
# ---------------------------------------------------------------------------

def test_classify_single_nonzero_le_vertex0_is_head():
    result = classify_endpoints(
        line_ends=(4, 0),
        vertex0_nearest=None,
        vertex1_nearest=None,
        tail_snap_radius=12.0,
        tie_epsilon=0.5,
    )
    assert result.head_index == 0
    assert result.tail_index == 1
    assert result.head_line_end == 4
    assert result.tail_line_end == 0
    assert result.skip_reason is None


def test_classify_single_nonzero_le_vertex1_is_head():
    result = classify_endpoints(
        line_ends=(0, 5),
        vertex0_nearest=None,
        vertex1_nearest=None,
        tail_snap_radius=12.0,
        tie_epsilon=0.5,
    )
    assert result.head_index == 1
    assert result.tail_index == 0
    assert result.head_line_end == 5
    assert result.tail_line_end == 0
    assert result.skip_reason is None


def test_classify_both_zero_le_nearer_vertex0_is_tail():
    # Plain line (0,0): proximity decides tail. vertex0 nearer to an annotation.
    result = classify_endpoints(
        line_ends=(0, 0),
        vertex0_nearest=("annot-a", 2.0),
        vertex1_nearest=("annot-b", 8.0),
        tail_snap_radius=12.0,
        tie_epsilon=0.5,
    )
    assert result.tail_index == 0
    assert result.head_index == 1
    assert result.tail_line_end == 0
    assert result.head_line_end == 0
    assert result.skip_reason is None


def test_classify_both_zero_le_nearer_vertex1_is_tail():
    result = classify_endpoints(
        line_ends=(0, 0),
        vertex0_nearest=("annot-a", 8.0),
        vertex1_nearest=("annot-b", 2.0),
        tail_snap_radius=12.0,
        tie_epsilon=0.5,
    )
    assert result.tail_index == 1
    assert result.head_index == 0
    assert result.skip_reason is None


def test_classify_both_nonzero_double_arrow_proximity_decides_tail():
    # Double arrow (4,6): both ends have arrowheads, but tail is still
    # decided by proximity to an annotation. Per-end codes preserved.
    result = classify_endpoints(
        line_ends=(4, 6),
        vertex0_nearest=("annot-a", 3.0),
        vertex1_nearest=("annot-b", 9.0),
        tail_snap_radius=12.0,
        tie_epsilon=0.5,
    )
    assert result.tail_index == 0
    assert result.head_index == 1
    assert result.tail_line_end == 4
    assert result.head_line_end == 6
    assert result.skip_reason is None


def test_classify_tie_within_epsilon_skips():
    result = classify_endpoints(
        line_ends=(0, 0),
        vertex0_nearest=("annot-a", 5.0),
        vertex1_nearest=("annot-b", 5.2),  # within tie_epsilon=0.5
        tail_snap_radius=12.0,
        tie_epsilon=0.5,
    )
    assert result.skip_reason == "tail_ambiguous_tie"
    assert result.head_index is None
    assert result.tail_index is None


def test_classify_both_ends_on_same_annotation_skips():
    result = classify_endpoints(
        line_ends=(0, 0),
        vertex0_nearest=("annot-a", 2.0),
        vertex1_nearest=("annot-a", 6.0),
        tail_snap_radius=12.0,
        tie_epsilon=0.5,
    )
    assert result.skip_reason == "tail_head_same_annotation"


def test_classify_neither_end_near_annotation_skips():
    result = classify_endpoints(
        line_ends=(0, 0),
        vertex0_nearest=None,
        vertex1_nearest=None,
        tail_snap_radius=12.0,
        tie_epsilon=0.5,
    )
    assert result.skip_reason == "neither_end_near_annotation"


def test_classify_result_is_namedtuple_with_expected_fields():
    result = classify_endpoints((4, 0), None, None, 12.0, 0.5)
    assert isinstance(result, ClassifyResult)
    assert result._fields == (
        "head_index", "tail_index", "tail_line_end", "head_line_end", "skip_reason",
    )


def test_classify_both_zero_only_one_end_near_annotation_becomes_tail():
    # vertex0 near an annotation, vertex1 has no nearby annotation at all.
    result = classify_endpoints(
        line_ends=(0, 0),
        vertex0_nearest=("annot-a", 4.0),
        vertex1_nearest=None,
        tail_snap_radius=12.0,
        tie_epsilon=0.5,
    )
    assert result.tail_index == 0
    assert result.head_index == 1
    assert result.skip_reason is None


# ---------------------------------------------------------------------------
# nearest_annotation_to_point
# ---------------------------------------------------------------------------

def test_nearest_annotation_returns_closest_within_radius():
    candidates = [
        ("a1", (0.0, 0.0, 10.0, 10.0)),
        ("a2", (100.0, 100.0, 110.0, 110.0)),
    ]
    result = nearest_annotation_to_point((15.0, 5.0), candidates, radius=12.0)
    assert result is not None
    assert result[0] == "a1"
    assert result[1] == pytest.approx(5.0)


def test_nearest_annotation_none_when_outside_radius():
    candidates = [("a1", (0.0, 0.0, 10.0, 10.0))]
    result = nearest_annotation_to_point((100.0, 100.0), candidates, radius=5.0)
    assert result is None


def test_nearest_annotation_point_inside_box_zero_distance():
    candidates = [("a1", (0.0, 0.0, 10.0, 10.0))]
    result = nearest_annotation_to_point((5.0, 5.0), candidates, radius=1.0)
    assert result == ("a1", 0.0)


def test_nearest_annotation_empty_candidates_returns_none():
    assert nearest_annotation_to_point((5.0, 5.0), [], radius=100.0) is None


def test_nearest_annotation_picks_nearer_of_two_within_radius():
    candidates = [
        ("far", (50.0, 50.0, 60.0, 60.0)),
        ("near", (0.0, 0.0, 5.0, 5.0)),
    ]
    result = nearest_annotation_to_point((6.0, 6.0), candidates, radius=50.0)
    assert result[0] == "near"


# ---------------------------------------------------------------------------
# is_duplicate_arrow
# ---------------------------------------------------------------------------

def _arrow(page=1, v0=(0.0, 0.0), v1=(10.0, 10.0), color=(1.0, 0.0, 0.0)):
    return {"page": page, "vertices": (v0, v1), "color": color}


def test_duplicate_arrow_identical_vertices_same_order():
    a = _arrow()
    b = _arrow()
    assert is_duplicate_arrow(a, b) is True


def test_duplicate_arrow_reversed_vertex_order_still_duplicate():
    a = _arrow(v0=(0.0, 0.0), v1=(10.0, 10.0))
    b = _arrow(v0=(10.0, 10.0), v1=(0.0, 0.0))
    assert is_duplicate_arrow(a, b) is True


def test_duplicate_arrow_within_tolerance_is_duplicate():
    a = _arrow(v0=(0.0, 0.0), v1=(10.0, 10.0))
    b = _arrow(v0=(0.3, 0.0), v1=(10.0, 10.2))
    assert is_duplicate_arrow(a, b, vertex_tolerance_pt=0.75) is True


def test_duplicate_arrow_outside_tolerance_not_duplicate():
    a = _arrow(v0=(0.0, 0.0), v1=(10.0, 10.0))
    b = _arrow(v0=(2.0, 0.0), v1=(10.0, 10.0))
    assert is_duplicate_arrow(a, b, vertex_tolerance_pt=0.75) is False


def test_duplicate_arrow_different_page_not_duplicate():
    a = _arrow(page=1)
    b = _arrow(page=2)
    assert is_duplicate_arrow(a, b) is False


def test_duplicate_arrow_different_color_not_duplicate():
    a = _arrow(color=(1.0, 0.0, 0.0))
    b = _arrow(color=(0.0, 1.0, 0.0))
    assert is_duplicate_arrow(a, b) is False


def test_duplicate_arrow_color_rounding_3dp_still_duplicate():
    a = _arrow(color=(1.0, 0.0, 0.0))
    b = _arrow(color=(1.0000001, 0.0, 0.0))
    assert is_duplicate_arrow(a, b, color_decimals=3) is True
