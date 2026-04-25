"""Tests for src/arrow_geometry.py — no fitz dependency."""
from __future__ import annotations

import math
import pytest

from src.arrow_geometry import (
    apply_offset_to_box,
    clamp_to_page,
    edge_midpoint_from_direction,
    head_tail_from_line_ends,
    hybrid_endpoint_placement,
    relative_offset_on_box,
    snap_to_nearest_box_edge,
)


# ===========================================================================
# head_tail_from_line_ends
# ===========================================================================

class TestHeadTailFromLineEnds:
    V0 = (10.0, 20.0)
    V1 = (100.0, 200.0)
    VERTICES = [V0, V1]

    def test_first_vertex_is_head_when_le0_nonzero(self):
        result = head_tail_from_line_ends(self.VERTICES, (4, 0))
        assert result == (self.V0, self.V1)

    def test_second_vertex_is_head_when_le1_nonzero(self):
        result = head_tail_from_line_ends(self.VERTICES, (0, 4))
        assert result == (self.V1, self.V0)

    def test_both_nonzero_returns_none(self):
        result = head_tail_from_line_ends(self.VERTICES, (4, 4))
        assert result is None

    def test_both_zero_returns_none(self):
        result = head_tail_from_line_ends(self.VERTICES, (0, 0))
        assert result is None

    def test_large_style_code_treated_as_nonzero(self):
        """Any non-zero value, including large codes, marks an arrowhead."""
        result = head_tail_from_line_ends(self.VERTICES, (9, 0))
        assert result == (self.V0, self.V1)

    def test_accepts_tuple_vertices(self):
        result = head_tail_from_line_ends((self.V0, self.V1), (0, 2))
        assert result == (self.V1, self.V0)


# ===========================================================================
# snap_to_nearest_box_edge
# ===========================================================================

BOX = (10.0, 20.0, 110.0, 80.0)   # width=100, height=60


class TestSnapToNearestBoxEdge:
    def test_point_near_left_edge_inside(self):
        pt = (15.0, 50.0)   # 5 from left, 35 from right, 30 from top, 30 from bottom
        result = snap_to_nearest_box_edge(pt, BOX)
        assert result == (10.0, 50.0)

    def test_point_near_right_edge_inside(self):
        pt = (105.0, 50.0)  # 5 from right, 95 from left
        result = snap_to_nearest_box_edge(pt, BOX)
        assert result == (110.0, 50.0)

    def test_point_near_top_edge_inside(self):
        pt = (60.0, 23.0)   # 3 from top, 57 from bottom
        result = snap_to_nearest_box_edge(pt, BOX)
        assert result == (60.0, 20.0)

    def test_point_near_bottom_edge_inside(self):
        pt = (60.0, 76.0)   # 4 from bottom, 56 from top
        result = snap_to_nearest_box_edge(pt, BOX)
        assert result == (60.0, 80.0)

    def test_point_already_on_left_edge(self):
        pt = (10.0, 50.0)
        result = snap_to_nearest_box_edge(pt, BOX)
        assert result == (10.0, 50.0)

    def test_point_already_on_top_edge(self):
        pt = (60.0, 20.0)
        result = snap_to_nearest_box_edge(pt, BOX)
        assert result == (60.0, 20.0)

    def test_point_outside_left(self):
        pt = (0.0, 50.0)    # 10 from left edge, outside
        result = snap_to_nearest_box_edge(pt, BOX)
        assert result == (10.0, 50.0)

    def test_point_outside_right(self):
        pt = (200.0, 50.0)
        result = snap_to_nearest_box_edge(pt, BOX)
        assert result == (110.0, 50.0)

    def test_point_outside_top(self):
        pt = (60.0, 0.0)
        result = snap_to_nearest_box_edge(pt, BOX)
        assert result == (60.0, 20.0)

    def test_point_outside_bottom(self):
        pt = (60.0, 200.0)
        result = snap_to_nearest_box_edge(pt, BOX)
        assert result == (60.0, 80.0)

    def test_y_clamped_to_box_when_near_corner(self):
        """Point near corner: 2D nearest is top-edge point, not left-edge corner."""
        pt = (12.0, 5.0)    # 2 from left line, 15 from top line
        result = snap_to_nearest_box_edge(pt, BOX)
        # Nearest point on left edge = (10, 20), dist² = 4+225 = 229
        # Nearest point on top edge  = (12, 20), dist² = 0+225 = 225  ← closer
        assert result == (12.0, 20.0)


# ===========================================================================
# relative_offset_on_box + apply_offset_to_box round-trip
# ===========================================================================

ROUND_TRIP_BOX = (50.0, 100.0, 200.0, 250.0)  # width=150, height=150


def _assert_close(a: tuple, b: tuple, tol: float = 1e-9) -> None:
    assert abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol, f"{a} != {b}"


class TestRelativeOffsetRoundTrip:
    def test_center(self):
        pt = (125.0, 175.0)
        offset = relative_offset_on_box(pt, ROUND_TRIP_BOX)
        back = apply_offset_to_box(offset, ROUND_TRIP_BOX)
        _assert_close(back, pt)

    def test_top_left_corner(self):
        pt = (50.0, 100.0)
        offset = relative_offset_on_box(pt, ROUND_TRIP_BOX)
        assert offset == (0.0, 0.0)
        back = apply_offset_to_box(offset, ROUND_TRIP_BOX)
        _assert_close(back, pt)

    def test_bottom_right_corner(self):
        pt = (200.0, 250.0)
        offset = relative_offset_on_box(pt, ROUND_TRIP_BOX)
        assert offset == (1.0, 1.0)
        back = apply_offset_to_box(offset, ROUND_TRIP_BOX)
        _assert_close(back, pt)

    def test_point_outside_box(self):
        pt = (250.0, 300.0)   # outside right + bottom
        offset = relative_offset_on_box(pt, ROUND_TRIP_BOX)
        assert offset[0] > 1.0 and offset[1] > 1.0
        back = apply_offset_to_box(offset, ROUND_TRIP_BOX)
        _assert_close(back, pt)

    def test_arbitrary_interior_point(self):
        pt = (80.0, 130.0)
        offset = relative_offset_on_box(pt, ROUND_TRIP_BOX)
        back = apply_offset_to_box(offset, ROUND_TRIP_BOX)
        _assert_close(back, pt)


# ===========================================================================
# hybrid_endpoint_placement
# ===========================================================================

def _point_on_box_edge(pt: tuple, box: tuple, tol: float = 1e-6) -> bool:
    """Return True if *pt* lies on one of the four edges of *box*."""
    x, y = pt
    x0, y0, x1, y1 = box
    on_left   = abs(x - x0) < tol and y0 - tol <= y <= y1 + tol
    on_right  = abs(x - x1) < tol and y0 - tol <= y <= y1 + tol
    on_top    = abs(y - y0) < tol and x0 - tol <= x <= x1 + tol
    on_bottom = abs(y - y1) < tol and x0 - tol <= x <= x1 + tol
    return on_left or on_right or on_top or on_bottom


class TestHybridEndpointPlacement:
    SOURCE_BOX  = (0.0,  0.0,  100.0, 50.0)
    OTHER_EP    = (500.0, 25.0)  # far to the right

    def test_branch_a_same_size_result_on_edge(self):
        """Target box same size → Branch A → exact left-edge midpoint."""
        target_box = (200.0, 0.0, 300.0, 50.0)  # same width=100, height=50
        source_point = (10.0, 25.0)             # near left edge of source
        # offset = (10/100, 25/50) = (0.1, 0.5)
        # raw_pt = (200+10, 0+25) = (210, 25)
        # snap: dist to left=10, right=90, top=25, bottom=25 → left edge
        result = hybrid_endpoint_placement(
            self.SOURCE_BOX, source_point, target_box, self.OTHER_EP
        )
        assert result == (200.0, 25.0), f"Expected (200.0, 25.0), got {result}"

    def test_branch_a_within_tolerance(self):
        """Target 15% wider/taller — still within 20% → Branch A → top edge."""
        target_box = (200.0, 0.0, 315.0, 57.5)  # +15% width and height
        source_point = (50.0, 25.0)
        # offset = (0.5, 0.5); raw_pt = (200+57.5, 0+28.75) = (257.5, 28.75)
        # snap: dist to left=57.5, right=57.5, top=28.75, bottom=28.75 → top wins (first min)
        result = hybrid_endpoint_placement(
            self.SOURCE_BOX, source_point, target_box, self.OTHER_EP
        )
        assert result == (257.5, 0.0), f"Expected (257.5, 0.0), got {result}"

    def test_branch_b_half_size_target(self):
        """Target half the size → Branch B → right edge (other_endpoint is right of center)."""
        target_box = (200.0, 0.0, 250.0, 25.0)  # half width and height
        source_point = (10.0, 25.0)
        # OTHER_EP=(500,25), target center=(225,12.5)
        # dx=225-500=-275 (center left of other_ep) → RIGHT edge
        # dy=12.5-25=-12.5 → horizontal dominates (275*25 > 12.5*50)
        # result = (x1+1, cy) = (251, 12.5)
        result = hybrid_endpoint_placement(
            self.SOURCE_BOX, source_point, target_box, self.OTHER_EP
        )
        assert result == (251.0, 12.5), f"Expected (251.0, 12.5), got {result}"

    def test_branch_b_asymmetric_width(self):
        """Target wider but same height → width ratio fails → Branch B → right edge."""
        # 50% wider fails the width check
        target_box = (200.0, 0.0, 350.0, 50.0)   # width=150, height=50
        source_point = (10.0, 25.0)
        # OTHER_EP=(500,25), target center=(275,25)
        # dx=275-500=-225 (center left of other_ep) → RIGHT edge
        # dy=0 → horizontal dominates
        # result = (x1+1, cy) = (351, 25)
        result = hybrid_endpoint_placement(
            self.SOURCE_BOX, source_point, target_box, self.OTHER_EP
        )
        assert result == (351.0, 25.0), f"Expected (351.0, 25.0), got {result}"

    def test_branch_a_result_is_on_edge_exactly(self):
        """Branch A: source_point at top center → maps to top edge of target."""
        target_box = (200.0, 10.0, 300.0, 60.0)  # same size as SOURCE_BOX
        source_point = (50.0, 0.0)               # top center of source
        # offset = (0.5, 0.0); raw_pt = (250, 10); snap → top edge (dist=0)
        result = hybrid_endpoint_placement(
            self.SOURCE_BOX, source_point, target_box, self.OTHER_EP
        )
        assert result == (250.0, 10.0), f"Expected (250.0, 10.0), got {result}"

    def test_branch_a_preserves_relative_offset_snaps_to_nearest_edge(self):
        """Branch A: source_point on left edge → target left edge at same relative height."""
        source_box = (0.0, 0.0, 100.0, 100.0)
        source_point = (0.0, 50.0)   # left edge, mid-height → offset (0.0, 0.5)
        target_box = (200.0, 200.0, 300.0, 300.0)  # same 100x100
        other_endpoint = (300.0, 250.0)
        # raw_pt = (200+0*100, 200+0.5*100) = (200, 250)
        # snap: dist to left=0 → already on left edge
        result = hybrid_endpoint_placement(source_box, source_point, target_box, other_endpoint)
        assert result == (200.0, 250.0), f"Expected (200.0, 250.0), got {result}"

    def test_branch_b_half_size_attaches_to_left_edge(self):
        """Branch B: other_endpoint left of target → arrow enters LEFT edge."""
        source_box = (0.0, 0.0, 100.0, 100.0)
        source_point = (50.0, 50.0)
        target_box = (200.0, 100.0, 240.0, 140.0)  # 40x40 → fails size check
        other_endpoint = (10.0, 120.0)              # left of target center (220, 120)
        # dx = 220 - 10 = 210 > 0 → LEFT edge: x = x0 - 1 = 199, y = 120
        result = hybrid_endpoint_placement(source_box, source_point, target_box, other_endpoint)
        assert result == (199.0, 120.0), f"Expected (199.0, 120.0), got {result}"

    def test_branch_b_right_edge(self):
        """Branch B: other_endpoint right of target → arrow enters RIGHT edge."""
        source_box = (0.0, 0.0, 100.0, 100.0)
        source_point = (50.0, 50.0)
        target_box = (10.0, 10.0, 50.0, 50.0)   # 40x40 → fails size check
        other_endpoint = (200.0, 30.0)           # right of target center (30, 30)
        # dx = 30 - 200 = -170 < 0 → RIGHT edge: x = x1 + 1 = 51, y = 30
        result = hybrid_endpoint_placement(source_box, source_point, target_box, other_endpoint)
        assert result == (51.0, 30.0), f"Expected (51.0, 30.0), got {result}"

    def test_branch_b_top_edge(self):
        """Branch B: other_endpoint above target → arrow enters TOP edge."""
        source_box = (0.0, 0.0, 100.0, 100.0)
        source_point = (50.0, 50.0)
        target_box = (200.0, 200.0, 240.0, 240.0)  # 40x40 → fails size check
        other_endpoint = (220.0, 10.0)             # above target center (220, 220)
        # dx=0, dy=220-10=210>0 → TOP edge: x=220, y=y0-1=199
        result = hybrid_endpoint_placement(source_box, source_point, target_box, other_endpoint)
        assert result == (220.0, 199.0), f"Expected (220.0, 199.0), got {result}"

    def test_branch_b_bottom_edge(self):
        """Branch B: other_endpoint below target → arrow enters BOTTOM edge."""
        source_box = (0.0, 0.0, 100.0, 100.0)
        source_point = (50.0, 50.0)
        target_box = (200.0, 200.0, 240.0, 240.0)  # 40x40 → fails size check
        other_endpoint = (220.0, 400.0)            # below target center (220, 220)
        # dx=0, dy=220-400=-180<0 → BOTTOM edge: x=220, y=y1+1=241
        result = hybrid_endpoint_placement(source_box, source_point, target_box, other_endpoint)
        assert result == (220.0, 241.0), f"Expected (220.0, 241.0), got {result}"


# ===========================================================================
# clamp_to_page
# ===========================================================================

PAGE = (0.0, 0.0, 595.0, 842.0)  # A4 in points


class TestClampToPage:
    def test_point_inside_page_unchanged(self):
        pt = (200.0, 400.0)
        assert clamp_to_page(pt, PAGE) == pt

    def test_point_at_origin_unchanged(self):
        assert clamp_to_page((0.0, 0.0), PAGE) == (0.0, 0.0)

    def test_point_at_max_corner_unchanged(self):
        assert clamp_to_page((595.0, 842.0), PAGE) == (595.0, 842.0)

    def test_point_outside_right_clamped(self):
        result = clamp_to_page((700.0, 400.0), PAGE)
        assert result == (595.0, 400.0)

    def test_point_outside_left_clamped(self):
        result = clamp_to_page((-50.0, 400.0), PAGE)
        assert result == (0.0, 400.0)

    def test_point_outside_top_clamped(self):
        result = clamp_to_page((200.0, -10.0), PAGE)
        assert result == (200.0, 0.0)

    def test_point_outside_bottom_clamped(self):
        result = clamp_to_page((200.0, 900.0), PAGE)
        assert result == (200.0, 842.0)

    def test_point_outside_both_axes(self):
        result = clamp_to_page((-100.0, 1000.0), PAGE)
        assert result == (0.0, 842.0)
