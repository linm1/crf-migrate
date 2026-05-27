"""Phase 3: Match source annotations to target CRF fields.

Four cascading passes:
  1. Exact   — same form_name + identical anchor_text/label (case- and whitespace-sensitive)
  2. Fuzzy same-form — rapidfuzz token_sort_ratio within same form and page rank
  3. Fuzzy cross-form — rapidfuzz token_sort_ratio across all forms
  4. Position fallback — coordinate scaling; domain_label uses absolute position

Passes 2 and 3 use bipartite (optimal) matching via scipy.optimize.linear_sum_assignment
when scipy is available, falling back to greedy iteration otherwise.
"""
from __future__ import annotations

import warnings
from collections import defaultdict

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


def _build_page_rank_map(records: list, key_fn) -> dict:
    """Return {norm_form_name: {page_number: page_rank}} with 1-based ranks.

    Page rank is the relative order in which a form appears across pages,
    sorted by ascending page number. Computed independently per record list
    (annotations or fields). Records must have .form_name and .page attributes.
    """
    form_pages: dict[str, list[int]] = {}
    for r in records:
        nf = key_fn(r)
        if nf not in form_pages:
            form_pages[nf] = []
        if r.page not in form_pages[nf]:
            form_pages[nf].append(r.page)
    return {
        nf: {pg: rank + 1 for rank, pg in enumerate(sorted(pages))}
        for nf, pages in form_pages.items()
    }


def _build_form_clusters(records: list, key_fn) -> dict[str, list[list[int]]]:
    """Return {norm_form_name: list_of_clusters} where each cluster is a sorted
    list of contiguous page numbers belonging to that form.

    A page joins the current cluster when its number is exactly last_page + 1;
    otherwise a new cluster starts. Strict adjacency captures the structural
    signal that non-contiguous pages belong to different visits.
    """
    form_pages: dict[str, list[int]] = {}
    for r in records:
        nf = key_fn(r)
        if nf not in form_pages:
            form_pages[nf] = []
        if r.page not in form_pages[nf]:
            form_pages[nf].append(r.page)

    result: dict[str, list[list[int]]] = {}
    for nf, pages in form_pages.items():
        sorted_pages = sorted(pages)
        clusters: list[list[int]] = []
        for pg in sorted_pages:
            if clusters and pg == clusters[-1][-1] + 1:
                clusters[-1].append(pg)
            else:
                clusters.append([pg])
        result[nf] = clusters
    return result


def _merge_form_clusters(
    derived: dict[str, list[list[int]]],
    override: dict[str, list[list[int]]] | None,
) -> dict[str, list[list[int]]]:
    """Apply TOC-derived overrides on top of contiguity-derived clusters.

    For each form in override, intersect the override page ranges with the
    pages already present in derived[form] to produce the same-form cluster
    list restricted to pages that actually carry records. Empty override
    clusters are dropped. Forms absent from override keep their derived
    clustering unchanged.
    """
    if not override:
        return derived
    merged = dict(derived)
    for nf, ov_clusters in override.items():
        derived_pages = {pg for cl in derived.get(nf, []) for pg in cl}
        if not derived_pages:
            continue
        new_clusters: list[list[int]] = []
        for cl in ov_clusters:
            filtered = sorted(p for p in cl if p in derived_pages)
            if filtered:
                new_clusters.append(filtered)
        if new_clusters:
            merged[nf] = new_clusters
    return merged


def _cluster_rank(page: int, clusters: list[list[int]]) -> tuple[int, int]:
    """Return (cluster_idx, page_in_cluster_idx) both 1-based for the given page.

    Returns (0, 0) if page is not found in any cluster.
    """
    for ci, cluster in enumerate(clusters):
        for pi, pg in enumerate(cluster):
            if pg == page:
                return (ci + 1, pi + 1)
    return (0, 0)


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
    annot_rect: list[float] | None = None,
) -> tuple[list[float], bool]:
    """Apply OOB fallback then clamp, returning (final_rect, was_adjusted).

    If target_rect is out-of-bounds:
      1. Find all fields on the same page with the same label (case-insensitive).
      2. Use the leftmost one (smallest rect[0]) as the fallback origin.
      3. If annot_rect is provided, preserve the source annotation's w×h.
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
            fb = list(leftmost.rect)
            if annot_rect is not None:
                w = abs(annot_rect[2] - annot_rect[0])
                h = abs(annot_rect[3] - annot_rect[1])
                target_rect = [fb[0], fb[1], fb[0] + w, fb[1] + h]
            else:
                target_rect = fb
            adjusted = True

    clamped, clamp_fired = _clamp_to_page(target_rect, page_w, page_h)
    return clamped, adjusted or clamp_fired


def _check_dim_guard(
    final_rect: list[float],
    annot_rect: list[float],
    threshold: float = 2.0,
) -> bool:
    """Return True if final rect dimensions deviate more than threshold× from source."""
    src_w = abs(annot_rect[2] - annot_rect[0])
    src_h = abs(annot_rect[3] - annot_rect[1])
    dst_w = abs(final_rect[2] - final_rect[0])
    dst_h = abs(final_rect[3] - final_rect[1])
    if src_w > 0 and (dst_w / src_w > threshold or dst_w / src_w < 1.0 / threshold):
        return True
    if src_h > 0 and (dst_h / src_h > threshold or dst_h / src_h < 1.0 / threshold):
        return True
    return False


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
    """Return raw fuzzy score (0-100) plus optional visit boost.

    Lowercases both sides before scoring: rapidfuzz.token_sort_ratio is
    case-sensitive by default, and the exact pass is now case-sensitive too,
    so fuzzy must absorb any case drift between source anchor_text and
    target field label.
    """
    raw = fuzz.token_sort_ratio(annot.anchor_text.lower(), field.label.lower())
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
    exact_threshold: float,
    src_clusters: dict[str, list[list[int]]] | None = None,
    tgt_clusters: dict[str, list[list[int]]] | None = None,
) -> list[MatchRecord]:
    """Pass 1: exact form_name + anchor_text == field label (case-insensitive).

    For annotations and fields that share the same form and label, the Nth
    annotation (sorted globally by page then y0) is paired with the Nth field
    (sorted globally by page then y0). If annotations outnumber fields and all
    source annotations share one page (same-page repeating rows with a single
    target field), extras are pinned to the last field. Otherwise surplus
    annotations fall through to later passes. Mutates `unmatched_annot_ids`
    in place.

    When src_clusters/tgt_clusters are provided the multi-page branch buckets
    by (cluster_idx, page_in_cluster_idx) so that repeating forms (one cluster
    per visit) are aligned at the visit level, not just at the full-form level.
    """
    results: list[MatchRecord] = []

    # Case-sensitive label key for exact_pass only. Annotation migration assumes
    # source/target CRFs use the same label casing; "Head Circumference" (form
    # title) and "HEAD CIRCUMFERENCE" (sub-header) are distinct SDTM labels that
    # case-insensitive matching incorrectly conflates. form_name stays case-
    # insensitive (vendors vary). If casing drifts the annotation falls through
    # to the fuzzy passes, which remain case-insensitive.
    _label_key = lambda s: s.strip()  # noqa: E731

    # Build (norm_form, label) -> [annotations sorted by (page, y0)]
    annot_groups: dict[tuple[str, str], list[AnnotationRecord]] = {}
    for annot in annotations:
        if annot.id not in unmatched_annot_ids:
            continue
        if not annot.anchor_text.strip():
            continue
        key = (_norm(annot.form_name), _label_key(annot.anchor_text))
        annot_groups.setdefault(key, []).append(annot)
    for key in annot_groups:
        annot_groups[key].sort(key=lambda a: (a.page, a.rect[1]))

    # Build (norm_form, label) -> [fields sorted by (page, y0)]
    # Deduplicate fields that share the same (form, label, page, y_row): when a label
    # row produces both a section_header and a text_field at the same y, keep only the
    # first one encountered after sorting — section_header is the positional anchor used
    # by _apply_anchor_offset, so it is always the correct match target.
    field_groups: dict[tuple[str, str], list[FieldRecord]] = {}
    for field in fields:
        if field.field_type == "checkbox":
            continue
        key = (_norm(field.form_name), _label_key(field.label))
        field_groups.setdefault(key, []).append(field)
    for key in field_groups:
        field_groups[key].sort(key=lambda f: (f.page, f.rect[1], 0 if f.field_type != "checkbox" else 1))
        # Remove duplicate rows: drop any field whose (page, y0) is within 5px of its predecessor
        deduped: list[FieldRecord] = []
        for f in field_groups[key]:
            if not deduped or f.page != deduped[-1].page or abs(f.rect[1] - deduped[-1].rect[1]) > 5.0:
                deduped.append(f)
        field_groups[key] = deduped

    # Fallback to full-form rank maps when cluster maps are not provided (e.g. direct
    # calls from tests that predate the cluster API).
    _src_clusters = src_clusters if src_clusters is not None else _build_form_clusters(
        annotations, lambda a: _norm(a.form_name)
    )
    _tgt_clusters = tgt_clusters if tgt_clusters is not None else _build_form_clusters(
        fields, lambda f: _norm(f.form_name)
    )

    def _emit_match(annot: AnnotationRecord, field: FieldRecord) -> MatchRecord:
        annot_rect = list(annot.rect)
        final_rect, placement_adjusted = _apply_placement_guard(
            _apply_anchor_offset(annot_rect, annot.anchor_rect, list(field.rect))
            if annot.anchor_rect
            else list(field.rect),
            field,
            fields,
            annot_rect=annot_rect,
        )
        placement_adjusted = placement_adjusted or _check_dim_guard(final_rect, annot_rect)
        return MatchRecord(
            annotation_id=annot.id,
            field_id=field.id,
            match_type="exact",
            confidence=exact_threshold,
            target_rect=final_rect,
            target_page=field.page,
            placement_adjusted=placement_adjusted,
            status="approved",
        )

    def _row_y(annot: AnnotationRecord) -> float:
        """Y used for row grouping. Prefers anchor_rect (semantic ground truth —
        the field the source author tied the annotation to). Falls back to the
        annotation's own rect Y when anchor_rect is missing.
        """
        return annot.anchor_rect[1] if annot.anchor_rect else annot.rect[1]

    def _assign_row_indices(annots: list[AnnotationRecord]) -> list[int]:
        """Assign a row index to each annotation.

        Annotations within 5 px of their predecessor on the same page share
        the same row slot (siblings).  A page change or a gap > 5 px increments
        the row counter. Grouping is driven by anchor Y so repeated labels at
        distinct anchor positions on one page (e.g. ION373-CS1 'Head
        Circumference' header at Y=76 + measurement row at Y=147) get
        distinct row indices and pair with distinct target fields.
        """
        # Sort annotations by (page, anchor Y) so row indices follow the
        # source-PDF ordering of distinct anchor positions, not the visually
        # drawn annotation box positions — which can be interleaved (e.g. the
        # domain_label box drawn near the header anchor at Y=76 even though
        # the SDTM mapping box is drawn lower).
        order = sorted(range(len(annots)), key=lambda i: (annots[i].page, _row_y(annots[i])))
        row_for: dict[int, int] = {}
        row_idx = 0
        prev_page: int | None = None
        prev_y: float | None = None
        for i in order:
            a = annots[i]
            y = _row_y(a)
            if prev_page is not None and (
                a.page != prev_page or abs(y - prev_y) > 5.0
            ):
                row_idx += 1
            row_for[i] = row_idx
            prev_page = a.page
            prev_y = y
        return [row_for[i] for i in range(len(annots))]

    def _resolve_tgt_cluster_idx(
        src_ci: int,
        n_src_clusters: int,
        n_tgt_clusters: int,
    ) -> int:
        """Map a source cluster index (1-based) to a target cluster index (1-based).

        Handles mismatched cluster counts per plan §5:
        - src=1, tgt>1: all source goes to first target cluster.
        - src>1, tgt=1: all source clusters collapse onto the lone target cluster.
        - both>1 unequal: pair by index, clip excess to last target cluster.
        """
        if n_src_clusters == 1 or n_tgt_clusters == 1:
            return min(src_ci, n_tgt_clusters)
        # Both > 1: pair by index, clip excess source to last target cluster.
        return min(src_ci, n_tgt_clusters)

    # Pair Nth source row -> Nth target field.
    # When annotations span a single source page the original "extras pin to last field"
    # behavior is preserved.  When annotations span multiple source pages they are first
    # bucketed by (cluster_idx, page_in_cluster_idx) so that source visit cluster N is
    # always paired against target visit cluster N.
    for (norm_form, norm_label), sorted_annots in annot_groups.items():
        sorted_fields = field_groups.get((norm_form, norm_label))
        if not sorted_fields:
            continue

        src_pages = {a.page for a in sorted_annots}

        if len(src_pages) == 1:
            # ── Single-page: restrict candidate fields to the same visit cluster ──
            # Without this restriction a label that exists in multiple target visit
            # clusters always resolves to the first occurrence in document order
            # (cluster 1), even when the annotation belongs to a later source visit.
            form_src_clusters = _src_clusters.get(norm_form, [])
            form_tgt_clusters = _tgt_clusters.get(norm_form, [])
            src_pg = next(iter(src_pages))
            n_src = len(form_src_clusters) if form_src_clusters else 1
            n_tgt = len(form_tgt_clusters) if form_tgt_clusters else 1
            src_ci = _cluster_rank(src_pg, form_src_clusters)[0] if form_src_clusters else 1
            tgt_ci = (
                min(src_ci, n_tgt)
                if (n_src == 1 or n_tgt == 1)
                else min(src_ci, n_tgt)
            )
            if form_tgt_clusters and 1 <= tgt_ci <= len(form_tgt_clusters):
                cluster_pages = set(form_tgt_clusters[tgt_ci - 1])
                cluster_fields = [f for f in sorted_fields if f.page in cluster_pages]
                candidate_fields = cluster_fields or sorted_fields
            else:
                candidate_fields = sorted_fields
            # Within the cluster, prefer fields on the page that corresponds to the
            # source annotation's cluster-relative position.
            # When src and tgt clusters have equal page counts, use cluster-pos mapping:
            #   src p.31 in [29-32] (pos 3) → tgt [30-33][2] = p.32 (not raw p.31).
            # When counts differ, fall back to raw page-number equality so the existing
            # "same-page fields win" behaviour is preserved for mismatched clusters.
            anchor_y = _row_y(sorted_annots[0]) if sorted_annots else None
            tgt_cluster_pages = (
                form_tgt_clusters[tgt_ci - 1]
                if form_tgt_clusters and 1 <= tgt_ci <= len(form_tgt_clusters)
                else []
            )
            src_cluster_pages = (
                form_src_clusters[src_ci - 1]
                if form_src_clusters and 1 <= src_ci <= len(form_src_clusters)
                else []
            )
            if tgt_cluster_pages and src_cluster_pages and len(src_cluster_pages) == len(tgt_cluster_pages):
                src_pi = _cluster_rank(src_pg, form_src_clusters)[1] if form_src_clusters else 1
                # src_pi == 0 means src_pg not in any cluster (cluster override removed it).
                # The 1 <= src_pi guard falls through to src_pg, preserving old behaviour.
                tgt_pi_page = (
                    tgt_cluster_pages[src_pi - 1]
                    if 1 <= src_pi <= len(tgt_cluster_pages)
                    else src_pg
                )
            else:
                tgt_pi_page = src_pg
            page_fields = [f for f in candidate_fields if f.page == tgt_pi_page]
            if page_fields and len(page_fields) >= len(sorted_annots):
                # Enough page-filtered fields for all annotations. Reorder them.
                if len(sorted_annots) == 1:
                    # Single annotation: proximity sort to its anchor Y (existing behaviour).
                    candidate_fields = sorted(
                        page_fields, key=lambda f: abs(f.rect[1] - (anchor_y or 0))
                    )
                else:
                    # Multiple annotations: sort by field Y (document order) so ridx N → field[N].
                    # Proximity-to-a-single-anchor-Y biases all rows toward the same top field.
                    candidate_fields = sorted(page_fields, key=lambda f: f.rect[1])
            annot_row = _assign_row_indices(sorted_annots)
            for annot, ridx in zip(sorted_annots, annot_row):
                field = candidate_fields[min(ridx, len(candidate_fields) - 1)]
                results.append(_emit_match(annot, field))
                unmatched_annot_ids.discard(annot.id)
        else:
            # ── Multi-page: bucket by (cluster_idx, page_in_cluster_idx) ──
            form_src_clusters = _src_clusters.get(norm_form, [])
            form_tgt_clusters = _tgt_clusters.get(norm_form, [])

            n_src = len(form_src_clusters) if form_src_clusters else 1
            n_tgt = len(form_tgt_clusters) if form_tgt_clusters else 1

            annots_by_crank: dict[tuple[int, int], list[AnnotationRecord]] = defaultdict(list)
            for a in sorted_annots:
                crank = _cluster_rank(a.page, form_src_clusters) if form_src_clusters else (1, 0)
                annots_by_crank[crank].append(a)

            fields_by_crank: dict[tuple[int, int], list[FieldRecord]] = defaultdict(list)
            for f in sorted_fields:
                crank = _cluster_rank(f.page, form_tgt_clusters) if form_tgt_clusters else (1, 0)
                fields_by_crank[crank].append(f)

            # Pre-compute per-cluster label-rank maps for the label-rank fallback.
            # For each cluster pair (src_ci → tgt_ci), rank pages that carry this label
            # within that cluster. label-rank-K in src → label-rank-K in tgt.
            src_cluster_label_ranks: dict[int, dict[int, int]] = {}  # src_ci → {page: label_rank}
            tgt_cluster_label_ranks: dict[int, dict[int, int]] = {}  # tgt_ci → {page: label_rank}
            tgt_fields_by_cluster_lrank: dict[int, dict[int, list[FieldRecord]]] = {}  # tgt_ci → {lrank: fields}

            for a in sorted_annots:
                ci = _cluster_rank(a.page, form_src_clusters)[0] if form_src_clusters else 1
                src_cluster_label_ranks.setdefault(ci, {})
            for f in sorted_fields:
                ci = _cluster_rank(f.page, form_tgt_clusters)[0] if form_tgt_clusters else 1
                tgt_cluster_label_ranks.setdefault(ci, {})

            for src_ci_key in src_cluster_label_ranks:
                pages_in_src_ci = sorted({
                    a.page for a in sorted_annots
                    if (_cluster_rank(a.page, form_src_clusters)[0] if form_src_clusters else 1) == src_ci_key
                })
                src_cluster_label_ranks[src_ci_key] = {pg: i + 1 for i, pg in enumerate(pages_in_src_ci)}

            for tgt_ci_key in tgt_cluster_label_ranks:
                pages_in_tgt_ci = sorted({
                    f.page for f in sorted_fields
                    if (_cluster_rank(f.page, form_tgt_clusters)[0] if form_tgt_clusters else 1) == tgt_ci_key
                })
                tgt_cluster_label_ranks[tgt_ci_key] = {pg: i + 1 for i, pg in enumerate(pages_in_tgt_ci)}
                lrank_map: dict[int, list[FieldRecord]] = defaultdict(list)
                for f in sorted_fields:
                    fci = _cluster_rank(f.page, form_tgt_clusters)[0] if form_tgt_clusters else 1
                    if fci == tgt_ci_key:
                        lr = tgt_cluster_label_ranks[tgt_ci_key].get(f.page)
                        if lr is not None:
                            lrank_map[lr].append(f)
                tgt_fields_by_cluster_lrank[tgt_ci_key] = dict(lrank_map)

            def _ordered_pi_candidates(p_idx: int, n_pages: int) -> list[int]:
                """1-based page_in_cluster indices in nearest-first order."""
                candidates: list[int] = []
                lo, hi = p_idx, p_idx
                while lo >= 1 or hi <= n_pages:
                    if lo >= 1:
                        candidates.append(lo)
                    if hi != lo and hi <= n_pages:
                        candidates.append(hi)
                    lo -= 1
                    hi += 1
                return candidates

            # Track which tgt crank positions have already been claimed within this
            # (norm_form, norm_label) group so we never double-assign.
            consumed_tgt_cranks: set[tuple[int, int]] = set()

            for (src_ci, src_pi), crank_annots in sorted(annots_by_crank.items()):
                tgt_ci = _resolve_tgt_cluster_idx(src_ci, n_src, n_tgt)
                tgt_cluster_pages = (
                    form_tgt_clusters[tgt_ci - 1]
                    if form_tgt_clusters and tgt_ci >= 1 and tgt_ci <= len(form_tgt_clusters)
                    else []
                )
                n_tgt_cluster_pages = len(tgt_cluster_pages)

                rank_fields: list[FieldRecord] | None = None

                # 1. Exact + nearest-page within cluster (tracks consumed positions to
                #    prevent double-assignment when label counts differ between src and tgt).
                for candidate_pi in _ordered_pi_candidates(src_pi, n_tgt_cluster_pages):
                    tgt_crank = (tgt_ci, candidate_pi)
                    if tgt_crank in consumed_tgt_cranks:
                        continue
                    bucket = fields_by_crank.get(tgt_crank)
                    if bucket:
                        rank_fields = bucket
                        consumed_tgt_cranks.add(tgt_crank)
                        break

                if not rank_fields:
                    # 2. Per-cluster label-rank fallback: used when the label exists in the
                    #    target cluster at a page position that cannot be reached by nearest-page
                    #    walking (e.g. source 6 pages, target 18 pages — same label on p3/p4 of
                    #    source must map to p103/p112 of target by label-rank, not by page-index).
                    src_pg = crank_annots[0].page
                    lrank = (src_cluster_label_ranks.get(src_ci) or {}).get(src_pg)
                    if lrank is not None and tgt_ci in tgt_fields_by_cluster_lrank:
                        rank_fields = tgt_fields_by_cluster_lrank[tgt_ci].get(lrank)

                if not rank_fields:
                    # Genuinely unmatched; fall through to later passes.
                    continue

                # Row-index pre-pass within this bucket. Row indices are now
                # driven by anchor_rect Y (see _assign_row_indices), so repeated
                # labels at distinct anchor positions on the same page get
                # distinct rows and pair with distinct target fields — fixing
                # ION373-CS1 p.164 where domain_label + VSCAT shared anchor
                # Y=76 (now row 0 → header field) while VSORRES at anchor
                # Y=147 (row 1 → measurement field).
                annot_row = _assign_row_indices(crank_annots)
                for annot, ridx in zip(crank_annots, annot_row):
                    field = rank_fields[min(ridx, len(rank_fields) - 1)]
                    results.append(_emit_match(annot, field))
                    unmatched_annot_ids.discard(annot.id)

    return results


def _fuzzy_same_form_pass(
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
    unmatched_annot_ids: set[str],
    threshold_pct: float,
    visit_boost: float,
    src_rank_map: dict,
    tgt_rank_map: dict,
    src_clusters: dict[str, list[list[int]]] | None = None,
    tgt_clusters: dict[str, list[list[int]]] | None = None,
) -> list[MatchRecord]:
    """Pass 2: bipartite fuzzy match within the same form_name and page rank.

    Groups by (norm_form_name, cluster_idx, page_in_cluster_idx) when cluster maps
    are provided so annotations from visit-cluster N of a form only compete against
    fields from the corresponding visit-cluster. Falls back to (form, page_rank) when
    cluster maps are absent. Annotations whose src_rank has no matching tgt bucket
    fall through to Pass 3.
    """
    eligible_annots = [
        a for a in annotations
        if a.id in unmatched_annot_ids and a.anchor_text.strip() != ""
    ]
    results: list[MatchRecord] = []

    _src_clusters = src_clusters or {}
    _tgt_clusters = tgt_clusters or {}

    if _src_clusters and _tgt_clusters:
        # Cluster-aware grouping: group by (norm_form, tgt_ci) so all annotations
        # from source cluster src_ci compete together against all fields in the
        # corresponding target cluster tgt_ci. Bipartite assignment handles optimal
        # pairing within the cluster without double-assignment.
        def _src_ci(a: AnnotationRecord) -> int:
            form_clusters = _src_clusters.get(_norm(a.form_name), [])
            return _cluster_rank(a.page, form_clusters)[0] if form_clusters else 1

        def _resolve_tgt_ci_fuzzy(src_ci: int, form: str) -> int:
            n_src = len(_src_clusters.get(form, []))
            n_tgt = len(_tgt_clusters.get(form, []))
            if not n_src or not n_tgt:
                return src_ci
            if n_src == 1 or n_tgt == 1:
                return min(src_ci, max(n_tgt, 1))
            return min(src_ci, n_tgt)

        group_keys: set[tuple[str, int]] = set()
        for a in eligible_annots:
            form = _norm(a.form_name)
            group_keys.add((form, _src_ci(a)))

        for (form, src_ci) in group_keys:
            grp_annots = [
                a for a in eligible_annots
                if _norm(a.form_name) == form and _src_ci(a) == src_ci
            ]

            tgt_ci = _resolve_tgt_ci_fuzzy(src_ci, form)
            form_tgt_clusters = _tgt_clusters.get(form, [])
            tgt_cluster_pages = (
                set(form_tgt_clusters[tgt_ci - 1])
                if form_tgt_clusters and tgt_ci >= 1 and tgt_ci <= len(form_tgt_clusters)
                else None
            )

            grp_fields = [
                f for f in fields
                if _norm(f.form_name) == form
                and f.field_type != "checkbox"
                and (
                    tgt_cluster_pages is None
                    or f.page in tgt_cluster_pages
                )
            ]

            if not grp_annots or not grp_fields:
                continue

            def _score(a: AnnotationRecord, f: FieldRecord, _b: float = visit_boost) -> float:
                return _adjusted_score(a, f, _b)

            pairs = _bipartite_assign(grp_annots, grp_fields, _score, threshold_pct)
            for ai, fi, score in pairs:
                annot, field = grp_annots[ai], grp_fields[fi]
                annot_rect = list(annot.rect)
                raw_rect = (
                    _apply_anchor_offset(annot_rect, annot.anchor_rect, list(field.rect))
                    if annot.anchor_rect
                    else list(field.rect)
                )
                final_rect, placement_adjusted = _apply_placement_guard(raw_rect, field, fields, annot_rect=annot_rect)
                placement_adjusted = placement_adjusted or _check_dim_guard(final_rect, annot_rect)
                results.append(MatchRecord(
                    annotation_id=annot.id,
                    field_id=field.id,
                    match_type="fuzzy",
                    confidence=min(score / 100.0, 1.0),
                    target_rect=final_rect,
                    target_page=field.page,
                    placement_adjusted=placement_adjusted,
                    status="re-pairing",
                ))
                unmatched_annot_ids.discard(annot.id)
    else:
        # Legacy path: group by (norm_form_name, page_rank)
        form_rank_keys = {
            (_norm(a.form_name), src_rank_map.get(_norm(a.form_name), {}).get(a.page, 0))
            for a in eligible_annots
        }

        for (form, src_rank) in form_rank_keys:
            grp_annots = [
                a for a in eligible_annots
                if _norm(a.form_name) == form
                and src_rank_map.get(_norm(a.form_name), {}).get(a.page, 0) == src_rank
            ]
            grp_fields = [
                f for f in fields
                if _norm(f.form_name) == form
                and f.field_type != "checkbox"
                and tgt_rank_map.get(_norm(f.form_name), {}).get(f.page, 0) == src_rank
            ]

            def _score(a: AnnotationRecord, f: FieldRecord, _b: float = visit_boost) -> float:
                return _adjusted_score(a, f, _b)

            pairs = _bipartite_assign(grp_annots, grp_fields, _score, threshold_pct)
            for ai, fi, score in pairs:
                annot, field = grp_annots[ai], grp_fields[fi]
                annot_rect = list(annot.rect)
                raw_rect = (
                    _apply_anchor_offset(annot_rect, annot.anchor_rect, list(field.rect))
                    if annot.anchor_rect
                    else list(field.rect)
                )
                final_rect, placement_adjusted = _apply_placement_guard(raw_rect, field, fields, annot_rect=annot_rect)
                placement_adjusted = placement_adjusted or _check_dim_guard(final_rect, annot_rect)
                results.append(MatchRecord(
                    annotation_id=annot.id,
                    field_id=field.id,
                    match_type="fuzzy",
                    confidence=min(score / 100.0, 1.0),
                    target_rect=final_rect,
                    target_page=field.page,
                    placement_adjusted=placement_adjusted,
                    status="re-pairing",
                ))
                unmatched_annot_ids.discard(annot.id)

    return results


def _fuzzy_cross_form_pass(
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
    unmatched_annot_ids: set[str],
    threshold_pct: float,
    visit_boost: float,
) -> list[MatchRecord]:
    """Pass 3: bipartite fuzzy match across all remaining fields, ignoring form_name."""
    eligible_annots = [
        a for a in annotations
        if a.id in unmatched_annot_ids and a.anchor_text.strip() != ""
    ]
    eligible_fields = [f for f in fields if f.field_type != "checkbox"]

    def _score(a: AnnotationRecord, f: FieldRecord, _b: float = visit_boost) -> float:
        return _adjusted_score(a, f, _b)

    pairs = _bipartite_assign(eligible_annots, eligible_fields, _score, threshold_pct)
    results: list[MatchRecord] = []
    for ai, fi, score in pairs:
        annot, field = eligible_annots[ai], eligible_fields[fi]
        annot_rect = list(annot.rect)
        raw_rect = (
            _apply_anchor_offset(annot_rect, annot.anchor_rect, list(field.rect))
            if annot.anchor_rect
            else list(field.rect)
        )
        final_rect, placement_adjusted = _apply_placement_guard(raw_rect, field, fields, annot_rect=annot_rect)
        placement_adjusted = placement_adjusted or _check_dim_guard(final_rect, annot_rect)
        results.append(MatchRecord(
            annotation_id=annot.id,
            field_id=field.id,
            match_type="fuzzy",
            confidence=min(score / 100.0, 1.0),
            target_rect=final_rect,
            target_page=field.page,
            placement_adjusted=placement_adjusted,
            status="re-pairing",
        ))
        unmatched_annot_ids.discard(annot.id)
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
                target_page=annot.page,
                placement_adjusted=placement_adjusted,
                status="re-pairing",
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
                target_page=annot.page,
                placement_adjusted=placement_adjusted,
                status="re-pairing",
            ))
        else:
            results.append(MatchRecord(
                annotation_id=annot.id,
                field_id=None,
                match_type="unmatched",
                confidence=0.0,
                target_rect=list(annot.rect),
                target_page=0,
                status="re-pairing",
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
    source_form_clusters: dict[str, list[list[int]]] | None = None,
    target_form_clusters: dict[str, list[list[int]]] | None = None,
) -> list[MatchRecord]:
    """Match source annotations to target fields via 4 cascading passes.

    Returns a list of MatchRecord sorted by original annotation order.

    source_form_clusters / target_form_clusters override the default
    contiguity-derived visit clustering. Pass TOC-derived clusters (via
    pdf_utils.build_form_clusters_from_toc) when the form's pages are
    contiguous in the PDF but represent multiple visits (only the bookmark
    boundary distinguishes them). Restricted to forms present in records.
    """
    if not annotations:
        return []

    config = profile.matching_config
    visit_boost = config.visit_boost
    unmatched_annot_ids: set[str] = {a.id for a in annotations}
    results: list[MatchRecord] = []

    src_rank_map = _build_page_rank_map(annotations, lambda r: _norm(r.form_name))
    tgt_rank_map = _build_page_rank_map(fields, lambda r: _norm(r.form_name))
    src_clusters = _merge_form_clusters(
        _build_form_clusters(annotations, lambda r: _norm(r.form_name)),
        source_form_clusters,
    )
    tgt_clusters = _merge_form_clusters(
        _build_form_clusters(fields, lambda r: _norm(r.form_name)),
        target_form_clusters,
    )

    results += _exact_pass(
        annotations, fields, unmatched_annot_ids, config.exact_threshold,
        src_clusters=src_clusters, tgt_clusters=tgt_clusters,
    )
    results += _fuzzy_same_form_pass(
        annotations, fields, unmatched_annot_ids,
        config.fuzzy_same_form_threshold * 100, visit_boost,
        src_rank_map, tgt_rank_map,
        src_clusters=src_clusters, tgt_clusters=tgt_clusters,
    )
    results += _fuzzy_cross_form_pass(
        annotations, fields, unmatched_annot_ids,
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
    target_page: int,
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
            "target_page": target_page,
            "status": "approved",
            "placement_adjusted": False,
        }
    )
    return updated


def batch_approve_exact(matches: list[MatchRecord]) -> list[MatchRecord]:
    """Return a new list with all 'exact' matches set to status='approved'."""
    return [
        m.model_copy(update={"status": "approved"}) if m.match_type == "exact" else m
        for m in matches
    ]


def compute_target_rect(
    annot: AnnotationRecord,
    field: FieldRecord,
    all_fields: list[FieldRecord],
) -> list[float]:
    """Compute and guard-apply the target_rect for a manual re-pair.

    Replicates the same logic used by fuzzy passes:
    - If annot has anchor_rect: apply anchor offset onto field.rect
    - Otherwise: use field.rect directly
    - Always apply OOB guard + page clamp via _apply_placement_guard

    Returns the final [x0, y0, x1, y1] rect.
    """
    raw_rect = (
        _apply_anchor_offset(list(annot.rect), annot.anchor_rect, list(field.rect))
        if annot.anchor_rect
        else list(field.rect)
    )
    final_rect, _ = _apply_placement_guard(raw_rect, field, all_fields)
    return final_rect
