"""Pure-function arrow endpoint geometry — no fitz or streamlit imports.

Mirrors the rule_engine.py boundary discipline: no PDF or UI dependencies.
All types use plain Python tuples:
  - point  = tuple[float, float]      i.e. (x, y)
  - box    = tuple[float, float, float, float]  i.e. (x0, y0, x1, y1)
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases (informational only — not enforced at runtime)
# ---------------------------------------------------------------------------
Point = tuple[float, float]
Box = tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# 1. head_tail_from_line_ends
# ---------------------------------------------------------------------------

def head_tail_from_line_ends(
    vertices: list[Point] | tuple[Point, Point],
    line_ends: tuple[int, int],
) -> tuple[Point, Point] | None:
    """Disambiguate head and tail from PyMuPDF line-end style codes.

    Args:
        vertices:  Exactly 2 points [(x0,y0), (x1,y1)].
        line_ends: Pair of style codes as returned by PyMuPDF (annot.line_ends).
                   Non-zero means an arrowhead is drawn at that vertex.

    Returns:
        (head_vertex, tail_vertex) when exactly one end is non-zero, else None.
    """
    v0, v1 = vertices[0], vertices[1]
    le0, le1 = int(line_ends[0]), int(line_ends[1])

    both_nonzero = le0 != 0 and le1 != 0
    both_zero = le0 == 0 and le1 == 0

    if both_nonzero or both_zero:
        logger.warning(
            "head_tail_from_line_ends: ambiguous line_ends %s — returning None",
            line_ends,
        )
        return None

    if le0 != 0:
        return (v0, v1)  # v0 is head
    return (v1, v0)  # v1 is head


# ---------------------------------------------------------------------------
# 2. snap_to_nearest_box_edge
# ---------------------------------------------------------------------------

def snap_to_nearest_box_edge(point: Point, box: Box) -> Point:
    """Snap *point* to the nearest edge of *box*.

    The returned point always lies on one of the four edges (left, right,
    top, bottom).  The perpendicular coordinate is clamped to box bounds.

    Args:
        point: (x, y) — may be inside or outside the box.
        box:   (x0, y0, x1, y1).

    Returns:
        (x, y) on the nearest edge.
    """
    x, y = point
    x0, y0, x1, y1 = box

    # Clamp helpers for the perpendicular coordinate
    cx = max(x0, min(x1, x))
    cy = max(y0, min(y1, y))

    # Candidate nearest points on each of the 4 edge segments
    candidates: list[Point] = [
        (x0, cy),  # left edge
        (x1, cy),  # right edge
        (cx, y0),  # top edge
        (cx, y1),  # bottom edge
    ]

    def _dist2(p: Point) -> float:
        return (p[0] - x) ** 2 + (p[1] - y) ** 2

    return min(candidates, key=_dist2)


# ---------------------------------------------------------------------------
# 3. relative_offset_on_box
# ---------------------------------------------------------------------------

def relative_offset_on_box(point: Point, box: Box) -> Point:
    """Compute relative offset of *point* within *box*.

    Returns (ox, oy) where ox = (x - x0) / width, oy = (y - y0) / height.
    Values may be outside [0, 1] when point is outside box.
    """
    x, y = point
    x0, y0, x1, y1 = box
    width = x1 - x0
    height = y1 - y0
    return ((x - x0) / width, (y - y0) / height)


# ---------------------------------------------------------------------------
# 4. apply_offset_to_box
# ---------------------------------------------------------------------------

def apply_offset_to_box(offset: Point, box: Box) -> Point:
    """Inverse of relative_offset_on_box.

    Returns (x0 + ox * width, y0 + oy * height).
    """
    ox, oy = offset
    x0, y0, x1, y1 = box
    width = x1 - x0
    height = y1 - y0
    return (x0 + ox * width, y0 + oy * height)


# ---------------------------------------------------------------------------
# 5. edge_midpoint_from_direction
# ---------------------------------------------------------------------------

def edge_midpoint_from_direction(
    other_endpoint: Point,
    target_box: Box,
    outward_offset: float = 1.0,
) -> Point:
    """Return the midpoint of the target box edge that faces *other_endpoint*.

    The approach direction is the vector from *other_endpoint* to the center
    of *target_box*.  We select the edge whose inward normal aligns with that
    direction, then return its midpoint displaced 1 pt outward.

    Args:
        other_endpoint:  The tail (or head) of the arrow — the far vertex.
        target_box:      (x0, y0, x1, y1) of the annotation box to approach.
        outward_offset:  How many points to push the result outside the box
                         so the arrowhead visually touches but does not overlap.

    Returns:
        (x, y) on the chosen edge, shifted outward by *outward_offset* pts.
    """
    x0, y0, x1, y1 = target_box
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    box_width = x1 - x0
    box_height = y1 - y0

    dx = cx - other_endpoint[0]
    dy = cy - other_endpoint[1]

    # Avoid zero-division when endpoint is exactly at center
    if dx == 0.0 and dy == 0.0:
        dx = 0.0
        dy = -1.0  # default: approach from top

    # Compare aspect ratios to decide left/right vs top/bottom
    # Use abs so direction sign doesn't matter for the comparison
    adx = abs(dx)
    ady = abs(dy)

    # Wider direction wins: |dx/dy| > width/height  ↔  |dx|*height > |dy|*width
    if box_height > 0 and box_width > 0 and adx * box_height > ady * box_width:
        # Approach via left or right edge
        if dx > 0:
            # center is to the RIGHT of other_endpoint → approach from right edge
            return (x1 + outward_offset, cy)
        else:
            # center is to the LEFT → approach from left edge
            return (x0 - outward_offset, cy)
    else:
        # Approach via top or bottom edge
        if dy > 0:
            # center is BELOW other_endpoint → approach from bottom edge
            return (cx, y1 + outward_offset)
        else:
            # center is ABOVE → approach from top edge
            return (cx, y0 - outward_offset)


# ---------------------------------------------------------------------------
# 6. hybrid_endpoint_placement
# ---------------------------------------------------------------------------

def hybrid_endpoint_placement(
    source_box: Box,
    source_point: Point,
    target_box: Box,
    other_endpoint: Point,
    size_similarity_tolerance: float = 0.20,
) -> Point:
    """Main endpoint placement — Branch A (size-similar) or Branch B (size differs).

    Branch A (boxes are within *size_similarity_tolerance* of each other in
    both width and height):
        1. Compute relative offset of *source_point* within *source_box*.
        2. Apply that offset to *target_box* to get a raw target point.
        3. Snap raw point to the nearest edge of *target_box*.

    Branch B (sizes differ significantly):
        Use edge_midpoint_from_direction(*other_endpoint*, *target_box*).

    Args:
        source_box:               Bounding box of the source annotation.
        source_point:             The original endpoint (head or tail vertex).
        target_box:               Bounding box of the target annotation.
        other_endpoint:           The opposite endpoint (used only in Branch B).
        size_similarity_tolerance: Fractional tolerance (default 0.20 = ±20%).

    Returns:
        (x, y) — the placed endpoint on or near the target box edge.
    """
    tol = size_similarity_tolerance
    sw = (source_box[2] - source_box[0]) or 1.0   # avoid zero-division
    sh = (source_box[3] - source_box[1]) or 1.0
    tw = target_box[2] - target_box[0]
    th = target_box[3] - target_box[1]

    size_ok = (
        (1.0 - tol) <= (tw / sw) <= (1.0 + tol)
        and (1.0 - tol) <= (th / sh) <= (1.0 + tol)
    )

    if size_ok:
        offset = relative_offset_on_box(source_point, source_box)
        raw_pt = apply_offset_to_box(offset, target_box)
        return snap_to_nearest_box_edge(raw_pt, target_box)

    return edge_midpoint_from_direction(other_endpoint, target_box)


# ---------------------------------------------------------------------------
# 7. clamp_to_page
# ---------------------------------------------------------------------------

def clamp_to_page(point: Point, page_rect: Box) -> Point:
    """Clamp *point* so it stays within *page_rect*.

    Args:
        point:     (x, y) to clamp.
        page_rect: (x0, y0, x1, y1) — typically (0, 0, page_width, page_height).

    Returns:
        (x, y) clamped to page bounds.
    """
    x, y = point
    x0, y0, x1, y1 = page_rect
    return (max(x0, min(x1, x)), max(y0, min(y1, y)))
