"""Phase 3: Match source annotations to target CRF fields.

Four cascading passes:
  1. Exact   — same form_name + identical anchor_text/label (case-insensitive)
  2. Fuzzy same-form — rapidfuzz token_sort_ratio within same form
  3. Fuzzy cross-form — rapidfuzz token_sort_ratio across all forms
  4. Position fallback — coordinate scaling; domain_label uses absolute position

Passes 2 and 3 use bipartite (optimal) matching via scipy.optimize.linear_sum_assignment
when scipy is available, falling back to greedy iteration otherwise.
"""
from __future__ import annotations

import warnings

import numpy as np
from rapidfuzz import fuzz

from src.models import AnnotationRecord, FieldRecord, MatchRecord
from src.profile_models import Profile

try:
    from scipy.optimize import linear_sum_assignment as _lsa
    _SCIPY_AVAILABLE = True
except (ImportError, TypeError):
    _SCIPY_AVAILABLE = False

_norm = lambda s: s.strip().lower()  # noqa: E731


def _apply_anchor_offset(
    annot_rect: list[float],
    anchor_rect: list[float],
    field_rect: list[float],
) -> list[float]:
    """Compute target_rect by replicating the source offset between an annotation
    and its anchor text label onto the target field label position.

    The annotation's width and height are preserved from the source.

    Args:
        annot_rect:  Source annotation bounding box [x0, y0, x1, y1].
        anchor_rect: Source anchor text label bounding box [x0, y0, x1, y1].
        field_rect:  Target field label bounding box [x0, y0, x1, y1].

    Returns:
        Target annotation bounding box [x0, y0, x1, y1].
    """
    dx = annot_rect[0] - anchor_rect[0]
    dy = annot_rect[1] - anchor_rect[1]
    w = annot_rect[2] - annot_rect[0]
    h = annot_rect[3] - annot_rect[1]
    x0 = field_rect[0] + dx
    y0 = field_rect[1] + dy
    return [x0, y0, x0 + w, y0 + h]


def _is_oob(rect: list[float], page_w: float, page_h: float) -> bool:
    """Return True if rect falls outside [0, page_w] x [0, page_h].

    Skipped when page_w or page_h is 0 (unknown dimensions).
    """
    if page_w <= 0 or page_h <= 0:
        return False
    return rect[0] < 0 or rect[1] < 0 or rect[2] > page_w or rect[3] > page_h


def _clamp_to_page(
    rect: list[float], page_w: float, page_h: float
) -> tuple[list[float], bool]:
    """Clamp rect to [0, page_w] x [0, page_h]. Returns (clamped_rect, was_adjusted)."""
    if page_w <= 0 or page_h <= 0:
        return rect, False
    x0 = max(0.0, min(rect[0], page_w))
    y0 = max(0.0, min(rect[1], page_h))
    x1 = max(0.0, min(rect[2], page_w))
    y1 = max(0.0, min(rect[3], page_h))
    clamped = [x0, y0, x1, y1]
    was_adjusted = bool(x0 != rect[0] or y0 != rect[1] or x1 != rect[2] or y1 != rect[3])
    return clamped, was_adjusted


def _apply_placement_guard(
    target_rect: list[float],
    matched_field: "FieldRecord",
    all_fields: list["FieldRecord"],
) -> tuple[list[float], bool]:
    """Apply OOB fallback then clamp, returning (final_rect, was_adjusted).

    If target_rect is out-of-bounds:
      1. Find all fields on the same page with the same label (case-insensitive).
      2. Use the leftmost one (smallest rect[0]) as the target rect directly.
    Then always apply _clamp_to_page as a final safety net.
    """
    page_w = matched_field.page_width
    page_h = matched_field.page_height
    adjusted = False

    if _is_oob(target_rect, page_w, page_h):
        peers = [
            f for f in all_fields
            if f.page == matched_field.page
            and _norm(f.label) == _norm(matched_field.label)
            and f.id != matched_field.id
        ]
        if peers:
            leftmost = min(peers, key=lambda f: f.rect[0])
            target_rect = list(leftmost.rect)
            adjusted = True

    clamped, clamp_fired = _clamp_to_page(target_rect, page_w, page_h)
    return clamped, adjusted or clamp_fired


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_scaled_rect(
    rect: list[float],
    source_dims: tuple[float, float],
    target_dims: tuple[float, float],
) -> list[float]:
    """Scale a rect from source page coordinates to target page coordinates."""
    sx = target_dims[0] / source_dims[0]
    sy = target_dims[1] / source_dims[1]
    return [rect[0] * sx, rect[1] * sy, rect[2] * sx, rect[3] * sy]


def _visit_match(a: str, b: str) -> float:
    """Return a visit similarity score between two visit label strings.

    Returns:
        1.0  — both non-empty and equal (case-insensitive)
        0.5  — both non-empty and one contains the other (case-insensitive)
        0.0  — either is empty, or no containment relationship
    """
    if not a or not b:
        return 0.0
    a_low, b_low = a.lower(), b.lower()
    if a_low == b_low:
        return 1.0
    if a_low in b_low or b_low in a_low:
        return 0.5
    return 0.0


def _adjusted_score(annot: AnnotationRecord, field: FieldRecord, visit_boost: float) -> float:
    """Return raw fuzzy score (0-100) plus optional visit boost."""
    raw = fuzz.token_sort_ratio(annot.anchor_text, field.label)
    boost = visit_boost * _visit_match(annot.visit, field.visit)
    return raw + boost


def _bipartite_assign(
    annots: list[AnnotationRecord],
    fields: list[FieldRecord],
    score_fn,
    threshold_pct: float,
) -> list[tuple[int, int, float]]:
    """Return (annot_idx, field_idx, score) pairs via optimal bipartite matching.

    Uses scipy.optimize.linear_sum_assignment when available; falls back to
    greedy iteration and emits RuntimeWarning when scipy is absent.
    Cells where score < threshold are zeroed out; if the matrix is all-zero
    the function returns early to avoid spurious zero-score assignments.
    """
    if not annots or not fields:
        return []

    m, n = len(annots), len(fields)
    matrix = np.zeros((m, n), dtype=float)
    for i, annot in enumerate(annots):
        for j, field in enumerate(fields):
            s = score_fn(annot, field)
            if s >= threshold_pct:
                matrix[i, j] = s

    if matrix.max() == 0.0:
        return []

    if _SCIPY_AVAILABLE:
        row_ind, col_ind = _lsa(-matrix)
        return [
            (int(r), int(c), float(matrix[r, c]))
            for r, c in zip(row_ind, col_ind)
            if matrix[r, c] >= threshold_pct
        ]

    # Greedy fallback — emits a warning so callers know scipy is missing
    warnings.warn(
        "scipy is not available; falling back to greedy matching in fuzzy passes.",
        RuntimeWarning,
        stacklevel=4,
    )
    used_fields: set[int] = set()
    results = []
    for i in range(m):
        best_j, best_score = -1, -1.0
        for j in range(n):
            if j not in used_fields and matrix[i, j] > best_score:
                best_score = matrix[i, j]
                best_j = j
        if best_j >= 0 and best_score >= threshold_pct:
            results.append((i, best_j, best_score))
            used_fields.add(best_j)
    return results


def _exact_pass(
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
    unmatched_annot_ids: set[str],
    unmatched_field_ids: set[str],
    exact_threshold: float,
) -> list[MatchRecord]:
    """Pass 1: exact form_name + anchor_text == field label (case-insensitive)."""
    results: list[MatchRecord] = []
    for annot in annotations:
        if annot.id not in unmatched_annot_ids:
            continue
        for field in fields:
            if field.id not in unmatched_field_ids:
                continue
            if (
                _norm(annot.form_name) == _norm(field.form_name)
                and _norm(annot.anchor_text) == _norm(field.label)
                and annot.anchor_text.strip() != ""
            ):
                final_rect, placement_adjusted = _apply_placement_guard(
                    _apply_anchor_offset(list(annot.rect), annot.anchor_rect, list(field.rect))
                    if annot.anchor_rect
                    else list(field.rect),
                    field,
                    fields,
                )
                results.append(MatchRecord(
                    annotation_id=annot.id,
                    field_id=field.id,
                    match_type="exact",
                    confidence=exact_threshold,
                    target_rect=final_rect,
                    placement_adjusted=placement_adjusted,
                ))
                unmatched_annot_ids.discard(annot.id)
                unmatched_field_ids.discard(field.id)
                break
    return results


def _fuzzy_same_form_pass(
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
    unmatched_annot_ids: set[str],
    unmatched_field_ids: set[str],
    threshold_pct: float,
    visit_boost: float,
) -> list[MatchRecord]:
    """Pass 2: bipartite fuzzy match within the same form_name."""
    eligible_annots = [
        a for a in annotations
        if a.id in unmatched_annot_ids and a.anchor_text.strip() != ""
    ]
    results: list[MatchRecord] = []
    form_names = {_norm(a.form_name) for a in eligible_annots}

    for form in form_names:
        grp_annots = [a for a in eligible_annots if _norm(a.form_name) == form]
        grp_fields = [
            f for f in fields
            if f.id in unmatched_field_ids and _norm(f.form_name) == form
        ]

        def _score(a: AnnotationRecord, f: FieldRecord, _b: float = visit_boost) -> float:
            return _adjusted_score(a, f, _b)

        pairs = _bipartite_assign(grp_annots, grp_fields, _score, threshold_pct)
        for ai, fi, score in pairs:
            annot, field = grp_annots[ai], grp_fields[fi]
            raw_rect = (
                _apply_anchor_offset(list(annot.rect), annot.anchor_rect, list(field.rect))
                if annot.anchor_rect
                else list(field.rect)
            )
            final_rect, placement_adjusted = _apply_placement_guard(raw_rect, field, fields)
            results.append(MatchRecord(
                annotation_id=annot.id,
                field_id=field.id,
                match_type="fuzzy",
                confidence=min(score / 100.0, 1.0),
                target_rect=final_rect,
                placement_adjusted=placement_adjusted,
            ))
            unmatched_annot_ids.discard(annot.id)
            unmatched_field_ids.discard(field.id)

    return results


def _fuzzy_cross_form_pass(
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
    unmatched_annot_ids: set[str],
    unmatched_field_ids: set[str],
    threshold_pct: float,
    visit_boost: float,
) -> list[MatchRecord]:
    """Pass 3: bipartite fuzzy match across all remaining fields, ignoring form_name."""
    eligible_annots = [
        a for a in annotations
        if a.id in unmatched_annot_ids and a.anchor_text.strip() != ""
    ]
    eligible_fields = [f for f in fields if f.id in unmatched_field_ids]

    def _score(a: AnnotationRecord, f: FieldRecord, _b: float = visit_boost) -> float:
        return _adjusted_score(a, f, _b)

    pairs = _bipartite_assign(eligible_annots, eligible_fields, _score, threshold_pct)
    results: list[MatchRecord] = []
    for ai, fi, score in pairs:
        annot, field = eligible_annots[ai], eligible_fields[fi]
        raw_rect = (
            _apply_anchor_offset(list(annot.rect), annot.anchor_rect, list(field.rect))
            if annot.anchor_rect
            else list(field.rect)
        )
        final_rect, placement_adjusted = _apply_placement_guard(raw_rect, field, fields)
        results.append(MatchRecord(
            annotation_id=annot.id,
            field_id=field.id,
            match_type="fuzzy",
            confidence=min(score / 100.0, 1.0),
            target_rect=final_rect,
            placement_adjusted=placement_adjusted,
        ))
        unmatched_annot_ids.discard(annot.id)
        unmatched_field_ids.discard(field.id)
    return results


def _position_pass(
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
    unmatched_annot_ids: set[str],
    source_page_dims: dict[int, tuple[float, float]],
    target_page_dims: dict[int, tuple[float, float]],
    position_fallback_confidence: float,
) -> list[MatchRecord]:
    """Pass 4: position-based placement. Domain labels use absolute rect."""
    results: list[MatchRecord] = []
    form_names_in_fields = {_norm(f.form_name) for f in fields}

    for annot in annotations:
        if annot.id not in unmatched_annot_ids:
            continue

        if annot.category == "domain_label":
            tgt_dims = target_page_dims.get(annot.page, (0.0, 0.0))
            tgt_w, tgt_h = tgt_dims
            clamped, placement_adjusted = _clamp_to_page(list(annot.rect), tgt_w, tgt_h)
            results.append(MatchRecord(
                annotation_id=annot.id,
                field_id=None,
                match_type="position_only",
                confidence=position_fallback_confidence,
                target_rect=clamped,
                placement_adjusted=placement_adjusted,
            ))
            unmatched_annot_ids.discard(annot.id)
            continue

        if _norm(annot.form_name) in form_names_in_fields:
            src_dims = source_page_dims.get(annot.page, (595.0, 842.0))
            tgt_dims = target_page_dims.get(annot.page, src_dims)
            tgt_w, tgt_h = tgt_dims
            scaled = _compute_scaled_rect(list(annot.rect), src_dims, tgt_dims)
            clamped, placement_adjusted = _clamp_to_page(scaled, tgt_w, tgt_h)
            results.append(MatchRecord(
                annotation_id=annot.id,
                field_id=None,
                match_type="position_only",
                confidence=position_fallback_confidence,
                target_rect=clamped,
                placement_adjusted=placement_adjusted,
            ))
        else:
            results.append(MatchRecord(
                annotation_id=annot.id,
                field_id=None,
                match_type="unmatched",
                confidence=0.0,
                target_rect=list(annot.rect),
            ))
        unmatched_annot_ids.discard(annot.id)

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_annotations(
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
    profile: Profile,
    source_page_dims: dict[int, tuple[float, float]],
    target_page_dims: dict[int, tuple[float, float]],
) -> list[MatchRecord]:
    """Match source annotations to target fields via 4 cascading passes.

    Returns a list of MatchRecord sorted by original annotation order.
    """
    if not annotations:
        return []

    config = profile.matching_config
    visit_boost = config.visit_boost
    unmatched_annot_ids: set[str] = {a.id for a in annotations}
    unmatched_field_ids: set[str] = {f.id for f in fields}
    results: list[MatchRecord] = []

    results += _exact_pass(
        annotations, fields, unmatched_annot_ids, unmatched_field_ids, config.exact_threshold,
    )
    results += _fuzzy_same_form_pass(
        annotations, fields, unmatched_annot_ids, unmatched_field_ids,
        config.fuzzy_same_form_threshold * 100, visit_boost,
    )
    results += _fuzzy_cross_form_pass(
        annotations, fields, unmatched_annot_ids, unmatched_field_ids,
        config.fuzzy_cross_form_threshold * 100, visit_boost,
    )
    results += _position_pass(
        annotations, fields, unmatched_annot_ids,
        source_page_dims, target_page_dims, config.position_fallback_confidence,
    )

    annot_order = {a.id: i for i, a in enumerate(annotations)}
    results.sort(key=lambda r: annot_order.get(r.annotation_id, 0))
    return results


def apply_manual_match(
    matches: list[MatchRecord],
    annotation_id: str,
    field_id: str,
    target_rect: list[float],
) -> list[MatchRecord]:
    """Return a new list with the specified annotation updated to a manual match.

    Raises ValueError if annotation_id is not found in matches.
    """
    idx = next(
        (i for i, m in enumerate(matches) if m.annotation_id == annotation_id),
        None,
    )
    if idx is None:
        raise ValueError(f"annotation_id '{annotation_id}' not found in matches")
    updated = list(matches)
    updated[idx] = matches[idx].model_copy(
        update={
            "field_id": field_id,
            "match_type": "manual",
            "target_rect": target_rect,
            "status": "approved",
        }
    )
    return updated


def batch_approve_exact(matches: list[MatchRecord]) -> list[MatchRecord]:
    """Return a new list with all 'exact' matches set to status='approved'."""
    return [
        m.model_copy(update={"status": "approved"}) if m.match_type == "exact" else m
        for m in matches
    ]
