"""Pure-function arrow endpoint geometry — no fitz or streamlit imports.

Mirrors the rule_engine.py boundary discipline: no PDF or UI dependencies.
All types use plain Python tuples:
  - point  = tuple[float, float]      i.e. (x, y)
  - box    = tuple[float, float, float, float]  i.e. (x0, y0, x1, y1)

Ported (verbatim geometry math) from feat/arrow-migration:src/arrow_geometry.py:
snap_to_nearest_box_edge, relative_offset_on_box, apply_offset_to_box,
edge_midpoint_from_direction, hybrid_endpoint_placement, clamp_to_page.

head_tail_from_line_ends is replaced by classify_endpoints (see plan D4/D6/D7):
the reference's swap-heuristic-only disambiguation cannot express plain-line
proximity resolution, double-arrow per-end code preservation, or the
tie/same-annotation/no-annotation skip reasons required by this feature.
"""
from __future__ import annotations

from typing import NamedTuple

# ---------------------------------------------------------------------------
# Type aliases (informational only — not enforced at runtime)
# ---------------------------------------------------------------------------
Point = tuple[float, float]
Box = tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# snap_to_nearest_box_edge
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
# relative_offset_on_box
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
# apply_offset_to_box
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
# edge_midpoint_from_direction
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
            # center is to the RIGHT → other_endpoint is left → arrow enters LEFT edge
            return (x0 - outward_offset, cy)
        else:
            # center is to the LEFT → other_endpoint is right → arrow enters RIGHT edge
            return (x1 + outward_offset, cy)
    else:
        # Approach via top or bottom edge
        if dy > 0:
            # center is BELOW → other_endpoint is above → arrow enters TOP edge
            return (cx, y0 - outward_offset)
        else:
            # center is ABOVE → other_endpoint is below → arrow enters BOTTOM edge
            return (cx, y1 + outward_offset)


# ---------------------------------------------------------------------------
# hybrid_endpoint_placement
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
# clamp_to_page
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


# ---------------------------------------------------------------------------
# classify_endpoints — replaces head_tail_from_line_ends (plan D4, D6, D7)
# ---------------------------------------------------------------------------

class ClassifyResult(NamedTuple):
    """Result of classifying which vertex is the tail (annotation-anchored end)
    and which is the head (field-text-anchored end) of a Line annotation.

    head_line_end / tail_line_end are the PyMuPDF line-end style codes for
    each resolved role (not the raw per-vertex order) — the writer calls
    ``set_line_ends(tail_line_end, head_line_end)`` directly with no further
    swapping (plan D4).
    """

    head_index: int | None
    tail_index: int | None
    tail_line_end: int
    head_line_end: int
    skip_reason: str | None  # neither_end_near_annotation | tail_ambiguous_tie | tail_head_same_annotation


def nearest_annotation_to_point(
    point: Point,
    candidates: list[tuple[str, Box]],
    radius: float,
) -> tuple[str, float] | None:
    """Return (annotation_id, distance) of the candidate box nearest *point*.

    Distance is the Euclidean distance from *point* to the nearest edge of
    the box (0.0 when point is inside the box). Only candidates within
    *radius* qualify. Returns None when no candidate qualifies.
    """
    best: tuple[str, float] | None = None
    best_dist = radius
    for annotation_id, box in candidates:
        x0, y0, x1, y1 = box
        cx = max(x0, min(point[0], x1))
        cy = max(y0, min(point[1], y1))
        dist = ((point[0] - cx) ** 2 + (point[1] - cy) ** 2) ** 0.5
        if dist <= best_dist and (best is None or dist < best_dist):
            best = (annotation_id, dist)
            best_dist = dist
    return best


def classify_endpoints(
    line_ends: tuple[int, int],
    vertex0_nearest: tuple[str, float] | None,
    vertex1_nearest: tuple[str, float] | None,
    tail_snap_radius: float,
    tie_epsilon: float,
) -> ClassifyResult:
    """Classify a 2-vertex Line annotation's head/tail roles.

    Args:
        line_ends: PyMuPDF (le0, le1) style codes for (vertex0, vertex1).
            Non-zero means an arrowhead is drawn at that vertex.
        vertex0_nearest: (annotation_id, distance) of the nearest annotation
            to vertex0, or None if none within tail_snap_radius.
        vertex1_nearest: Same, for vertex1.
        tail_snap_radius: Radius (pt) used when the caller computed
            vertex{0,1}_nearest — informational, not re-checked here (the
            caller is responsible for having applied the radius).
        tie_epsilon: Distance (pt) below which two candidate tail distances
            are considered a tie (skip+QC per plan D6).

    Rules (plan D4 / D6):
        - Exactly one non-zero line-end code -> that vertex is the head,
          unconditionally (classic single arrowhead line).
        - Both codes zero (plain connector) or both non-zero (double arrow):
          disambiguate tail by proximity — the vertex nearer an annotation
          is the tail. Ties within tie_epsilon, both ends nearest the same
          annotation, or neither end near any annotation all skip with a
          QC reason. Double arrows preserve each vertex's original code.
    """
    le0, le1 = int(line_ends[0]), int(line_ends[1])
    both_zero = le0 == 0 and le1 == 0
    both_nonzero = le0 != 0 and le1 != 0

    if not both_zero and not both_nonzero:
        # Exactly one non-zero code: that vertex is the head, unconditionally.
        if le0 != 0:
            return ClassifyResult(
                head_index=0, tail_index=1,
                tail_line_end=le1, head_line_end=le0,
                skip_reason=None,
            )
        return ClassifyResult(
            head_index=1, tail_index=0,
            tail_line_end=le0, head_line_end=le1,
            skip_reason=None,
        )

    # both_zero or both_nonzero: proximity decides tail (D6).
    if vertex0_nearest is None and vertex1_nearest is None:
        return ClassifyResult(
            head_index=None, tail_index=None,
            tail_line_end=0, head_line_end=0,
            skip_reason="neither_end_near_annotation",
        )

    if vertex0_nearest is not None and vertex1_nearest is not None:
        if vertex0_nearest[0] == vertex1_nearest[0]:
            return ClassifyResult(
                head_index=None, tail_index=None,
                tail_line_end=0, head_line_end=0,
                skip_reason="tail_head_same_annotation",
            )
        if abs(vertex0_nearest[1] - vertex1_nearest[1]) <= tie_epsilon:
            return ClassifyResult(
                head_index=None, tail_index=None,
                tail_line_end=0, head_line_end=0,
                skip_reason="tail_ambiguous_tie",
            )
        if vertex0_nearest[1] < vertex1_nearest[1]:
            return ClassifyResult(
                head_index=1, tail_index=0,
                tail_line_end=le0, head_line_end=le1,
                skip_reason=None,
            )
        return ClassifyResult(
            head_index=0, tail_index=1,
            tail_line_end=le1, head_line_end=le0,
            skip_reason=None,
        )

    # Exactly one of the two vertices is near an annotation -> that one is tail.
    if vertex0_nearest is not None:
        return ClassifyResult(
            head_index=1, tail_index=0,
            tail_line_end=le0, head_line_end=le1,
            skip_reason=None,
        )
    return ClassifyResult(
        head_index=0, tail_index=1,
        tail_line_end=le1, head_line_end=le0,
        skip_reason=None,
    )


def is_duplicate_arrow(
    a: dict,
    b: dict,
    vertex_tolerance_pt: float = 0.75,
    color_decimals: int = 3,
) -> bool:
    """Return True when two extracted Line annotations are near-identical
    overlay duplicates (plan D7).

    Args:
        a, b: dicts with keys "page" (int), "vertices" (tuple of two (x,y)
            points), "color" (tuple of 3 floats, 0..1).
        vertex_tolerance_pt: Max per-coordinate distance for two vertices to
            be considered the same point.
        color_decimals: Rounding precision for stroke color comparison.

    Two arrows are duplicates when they share the same page, the same
    (rounded) stroke color, and their vertex pairs match within tolerance —
    checked in both original and reversed vertex order, since a duplicate
    overlay may have been drawn with swapped endpoints.
    """
    if a["page"] != b["page"]:
        return False

    color_a = tuple(round(c, color_decimals) for c in a["color"])
    color_b = tuple(round(c, color_decimals) for c in b["color"])
    if color_a != color_b:
        return False

    def _close(p1: Point, p2: Point) -> bool:
        return abs(p1[0] - p2[0]) <= vertex_tolerance_pt and abs(p1[1] - p2[1]) <= vertex_tolerance_pt

    av0, av1 = a["vertices"]
    bv0, bv1 = b["vertices"]

    same_order = _close(av0, bv0) and _close(av1, bv1)
    reversed_order = _close(av0, bv1) and _close(av1, bv0)
    return same_order or reversed_order
