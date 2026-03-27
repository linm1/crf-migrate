"""Phase 4: Write approved SDTM annotations to the target blank CRF PDF.

Takes matches.json (Phase 3 output) and produces output_acrf.pdf with all
approved/modified annotations placed, plus a qc_report dict summarising
what was written, skipped, and left unmatched.
"""
from pathlib import Path

import fitz  # PyMuPDF

from src.models import AnnotationRecord, MatchRecord
from src.profile_models import Profile

_FALLBACK_FILL: tuple[float, float, float] = (0.75, 1.0, 1.0)  # cyan


def _build_domain_color_map(
    annotations: list[AnnotationRecord],
    page_num: int,
) -> dict[str, tuple[float, float, float]]:
    """Build domain→fill_color map using each domain's first-seen source fill color.

    Uses the fill_color from the source annotation directly — no palette
    substitution or validation. Falls back to cyan only when fill_color is
    absent (None or empty).
    """
    domain_color: dict[str, tuple[float, float, float]] = {}
    for annot in annotations:
        if annot.page != page_num:
            continue
        domain = annot.domain
        if domain in domain_color:
            continue
        fill = annot.style.fill_color
        if fill and len(fill) >= 3:
            domain_color[domain] = (fill[0], fill[1], fill[2])
        else:
            domain_color[domain] = _FALLBACK_FILL
    return domain_color


def _resolve_text_style(
    annot: AnnotationRecord,
) -> tuple[str, float, tuple[float, float, float]]:
    """Return (fontname, fontsize, text_color) per SDTM guideline.

    - domain_label:    Arial Bold, 14pt, black
    - cross_reference: Arial Regular, 10pt, #00FFFF
    - all others:      Arial Regular, 10pt, black
    """
    if annot.category == "domain_label":
        return "helv", 14.0, (0.0, 0.0, 0.0)
    if annot.category == "cross_reference":
        return "helv", 10.0, (0.0, 1.0, 1.0)
    return "helv", 10.0, (0.0, 0.0, 0.0)


def write_annotations(
    target_pdf_path: Path,
    output_pdf_path: Path,
    matches: list[MatchRecord],
    annotations: list[AnnotationRecord],
    profile: Profile,
) -> dict:
    """Write approved annotations to target PDF. Returns qc_report dict."""
    annot_by_id: dict[str, AnnotationRecord] = {a.id: a for a in annotations}

    doc = fitz.open(str(target_pdf_path))

    written_ids: list[str] = []
    skipped_ids: list[str] = []

    # Pre-build per-page domain→color maps using all annotations (not just approved),
    # so domain color assignment is stable regardless of approval status.
    pages_needed: set[int] = set()
    for match in matches:
        if match.status in ("approved", "modified"):
            annot = annot_by_id.get(match.annotation_id)
            if annot:
                pages_needed.add(annot.page)

    page_domain_maps: dict[int, dict[str, tuple[float, float, float]]] = {
        page_num: _build_domain_color_map(annotations, page_num)
        for page_num in pages_needed
    }

    for match in matches:
        if match.status in ("approved", "modified"):
            annot = annot_by_id.get(match.annotation_id)
            if annot is None:
                skipped_ids.append(match.annotation_id)
                continue
            page_index = annot.page - 1
            if page_index < 0 or page_index >= doc.page_count:
                skipped_ids.append(match.annotation_id)
                continue
            page = doc[page_index]
            domain_color_map = page_domain_maps.get(annot.page, {})
            _write_single_annotation(page, match.target_rect, annot, domain_color_map)
            written_ids.append(match.annotation_id)
        else:
            skipped_ids.append(match.annotation_id)

    doc.save(str(output_pdf_path))
    doc.close()

    return build_qc_report(matches, written_ids, skipped_ids)


def build_qc_report(
    matches: list[MatchRecord],
    written_ids: list[str],
    skipped_ids: list[str],
) -> dict:
    """Construct the qc_report dict from match results."""
    counts_by_type: dict[str, int] = {}
    for m in matches:
        counts_by_type[m.match_type] = counts_by_type.get(m.match_type, 0) + 1

    return {
        "total_matches": len(matches),
        "written": len(written_ids),
        "skipped": len(skipped_ids),
        "counts_by_match_type": counts_by_type,
        "unmatched_annotation_ids": [
            m.annotation_id for m in matches if m.match_type == "unmatched"
        ],
        "rejected_annotation_ids": [
            m.annotation_id for m in matches if m.status == "rejected"
        ],
    }


def _write_single_annotation(
    page: fitz.Page,
    target_rect: list[float],
    annot: AnnotationRecord,
    domain_color_map: dict[str, tuple[float, float, float]],
) -> None:
    """Add a FreeText annotation to the given page at target_rect.

    Font, size, and text color follow SDTM guideline rules (category-driven).
    Fill/background color is resolved from the source annotation or palette.
    Border width and dash pattern are preserved from the source annotation.

    IMPORTANT: Never call xref_set_key on /C after update().  The /C key is
    the fill color for FreeText annotations (PDF spec ISO 32000 Table 177).
    update(fill_color=...) sets /C correctly; overwriting it breaks both the
    fill color and the border color on viewer re-render.
    """
    fontname, fontsize, text_color = _resolve_text_style(annot)
    fill = domain_color_map.get(annot.domain) or _FALLBACK_FILL
    style = annot.style
    rect = fitz.Rect(target_rect)

    a = page.add_freetext_annot(
        rect=rect,
        text=annot.content,
        fontsize=fontsize,
        fontname=fontname,
        text_color=text_color,
        fill_color=fill,
    )
    # PyMuPDF returns -1.0 for border width when no border is set on the source
    # annotation; clamp to 1.0 so the output always has a visible border.
    border_width = style.border_width if style.border_width > 0 else 1.0
    a.set_border(width=border_width, dashes=style.border_dashes)
    a.set_info(content=annot.content, subject=annot.domain)
    if annot.rotation:
        a.set_rotation(annot.rotation)
    a.update(fill_color=fill, text_color=text_color)
