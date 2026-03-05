"""Phase 3: Match source annotations to target CRF fields.

Four cascading passes:
  1. Exact   — same form_name + identical anchor_text/label (case-insensitive)
  2. Fuzzy same-form — rapidfuzz token_sort_ratio within same form
  3. Fuzzy cross-form — rapidfuzz token_sort_ratio across all forms
  4. Position fallback — coordinate scaling; domain_label uses absolute position

Public API
----------
match_annotations(annotations, fields, profile, source_page_dims, target_page_dims)
    -> list[MatchRecord]

apply_manual_match(matches, annotation_id, field_id, target_rect)
    -> list[MatchRecord]   (immutable — returns new list)

batch_approve_exact(matches)
    -> list[MatchRecord]   (immutable — returns new list)
"""
from __future__ import annotations

from rapidfuzz import fuzz

from src.models import AnnotationRecord, FieldRecord, MatchRecord
from src.profile_models import Profile

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_norm = lambda s: s.strip().lower()  # noqa: E731


def _compute_scaled_rect(
    rect: list[float],
    source_dims: tuple[float, float],
    target_dims: tuple[float, float],
) -> list[float]:
    """Scale a rect from source page coordinates to target page coordinates."""
    sx = target_dims[0] / source_dims[0]
    sy = target_dims[1] / source_dims[1]
    return [rect[0] * sx, rect[1] * sy, rect[2] * sx, rect[3] * sy]


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
                results.append(
                    MatchRecord(
                        annotation_id=annot.id,
                        field_id=field.id,
                        match_type="exact",
                        confidence=exact_threshold,
                        target_rect=list(field.rect),
                    )
                )
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
) -> list[MatchRecord]:
    """Pass 2: fuzzy match within the same form_name."""
    results: list[MatchRecord] = []
    for annot in annotations:
        if annot.id not in unmatched_annot_ids:
            continue
        if annot.anchor_text.strip() == "":
            continue
        candidates = [
            f for f in fields
            if f.id in unmatched_field_ids
            and _norm(f.form_name) == _norm(annot.form_name)
        ]
        best_field, best_score = None, -1.0
        for field in candidates:
            score = fuzz.token_sort_ratio(annot.anchor_text, field.label)
            if score > best_score:
                best_score = score
                best_field = field
        if best_field is not None and best_score >= threshold_pct:
            results.append(
                MatchRecord(
                    annotation_id=annot.id,
                    field_id=best_field.id,
                    match_type="fuzzy",
                    confidence=best_score / 100.0,
                    target_rect=list(best_field.rect),
                )
            )
            unmatched_annot_ids.discard(annot.id)
            unmatched_field_ids.discard(best_field.id)
    return results


def _fuzzy_cross_form_pass(
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
    unmatched_annot_ids: set[str],
    unmatched_field_ids: set[str],
    threshold_pct: float,
) -> list[MatchRecord]:
    """Pass 3: fuzzy match across all remaining fields, ignoring form_name."""
    results: list[MatchRecord] = []
    for annot in annotations:
        if annot.id not in unmatched_annot_ids:
            continue
        if annot.anchor_text.strip() == "":
            continue
        candidates = [f for f in fields if f.id in unmatched_field_ids]
        best_field, best_score = None, -1.0
        for field in candidates:
            score = fuzz.token_sort_ratio(annot.anchor_text, field.label)
            if score > best_score:
                best_score = score
                best_field = field
        if best_field is not None and best_score >= threshold_pct:
            results.append(
                MatchRecord(
                    annotation_id=annot.id,
                    field_id=best_field.id,
                    match_type="fuzzy",
                    confidence=best_score / 100.0,
                    target_rect=list(best_field.rect),
                )
            )
            unmatched_annot_ids.discard(annot.id)
            unmatched_field_ids.discard(best_field.id)
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

        # Domain labels: absolute positioning, no scaling
        if annot.category == "domain_label":
            results.append(
                MatchRecord(
                    annotation_id=annot.id,
                    field_id=None,
                    match_type="position_only",
                    confidence=position_fallback_confidence,
                    target_rect=list(annot.rect),
                )
            )
            unmatched_annot_ids.discard(annot.id)
            continue

        # Check if any field shares the form_name
        if _norm(annot.form_name) in form_names_in_fields:
            src_dims = source_page_dims.get(annot.page, (595.0, 842.0))
            tgt_dims = target_page_dims.get(annot.page, src_dims)
            scaled = _compute_scaled_rect(list(annot.rect), src_dims, tgt_dims)
            results.append(
                MatchRecord(
                    annotation_id=annot.id,
                    field_id=None,
                    match_type="position_only",
                    confidence=position_fallback_confidence,
                    target_rect=scaled,
                )
            )
        else:
            # No matching form at all — truly unmatched
            results.append(
                MatchRecord(
                    annotation_id=annot.id,
                    field_id=None,
                    match_type="unmatched",
                    confidence=0.0,
                    target_rect=list(annot.rect),
                )
            )
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
    unmatched_annot_ids: set[str] = {a.id for a in annotations}
    unmatched_field_ids: set[str] = {f.id for f in fields}

    results: list[MatchRecord] = []

    # Pass 1 — exact
    results += _exact_pass(
        annotations, fields,
        unmatched_annot_ids, unmatched_field_ids,
        config.exact_threshold,
    )

    # Pass 2 — fuzzy same-form
    results += _fuzzy_same_form_pass(
        annotations, fields,
        unmatched_annot_ids, unmatched_field_ids,
        config.fuzzy_same_form_threshold * 100,
    )

    # Pass 3 — fuzzy cross-form
    results += _fuzzy_cross_form_pass(
        annotations, fields,
        unmatched_annot_ids, unmatched_field_ids,
        config.fuzzy_cross_form_threshold * 100,
    )

    # Pass 4 — position fallback
    results += _position_pass(
        annotations, fields,
        unmatched_annot_ids,
        source_page_dims, target_page_dims,
        config.position_fallback_confidence,
    )

    # Sort by original annotation order
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
