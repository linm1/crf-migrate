"""Phase 1: Extract SDTM annotations from source aCRF PDF.

Uses PyMuPDF (fitz) for annotation extraction and the configured rule engine
for classification, form name extraction, and visit detection.
"""
import re
import uuid
from pathlib import Path

import fitz  # PyMuPDF

from src.models import AnnotationRecord, StyleInfo
from src.pdf_utils import get_text_blocks, make_clean_page
from src.profile_models import Profile
from src.rule_engine import RuleEngine, TextBlock

# FreeText annotation subtype value in PyMuPDF
_FREETEXT_SUBTYPE = "FreeText"


def extract_annotations(
    pdf_path: Path,
    profile: Profile,
    rule_engine: RuleEngine,
) -> list[AnnotationRecord]:
    """Extract and classify all SDTM annotations from a source aCRF PDF.

    Opens pdf_path, iterates every page, and processes each annotation through
    the annotation filter and rule engine.  Returns a list of AnnotationRecord,
    excluding annotations that the rule engine classifies as '_exclude' or that
    the annotation_filter rejects.
    """
    doc = fitz.open(str(pdf_path))
    try:
        records: list[AnnotationRecord] = []
        for page_index in range(doc.page_count):
            page = doc[page_index]
            page_num = page_index + 1
            page_records = _process_page(page, page_num, profile, rule_engine)
            records.extend(page_records)
    finally:
        doc.close()
    return records


def get_page_text_blocks(pdf_path: Path, page_num: int) -> list[TextBlock]:
    """Return annotation-free text blocks for one page of a PDF.

    Opens pdf_path, creates a temporary annotation-free copy of the requested
    page, and returns the extracted text blocks. Intended for the UI to preview
    the clean CRF text used for form-name and anchor-text extraction.

    Args:
        pdf_path: Path to the source PDF.
        page_num: 1-based page number.
    """
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_num - 1]
        temp_doc, clean_page = make_clean_page(page)
        try:
            return get_text_blocks(clean_page)
        finally:
            temp_doc.close()
    finally:
        doc.close()


def _make_clean_page(page: fitz.Page) -> tuple[fitz.Document, fitz.Page]:
    """Delegate to pdf_utils.make_clean_page (kept for backwards compatibility)."""
    return make_clean_page(page)


def _process_page(
    page: fitz.Page,
    page_num: int,
    profile: Profile,
    rule_engine: RuleEngine,
) -> list[AnnotationRecord]:
    """Process all annotations on a single page.

    Creates a temporary annotation-free copy of the page for text extraction
    so that SDTM annotation content never pollutes form-name, visit, or
    anchor-text extraction.  Annotations are then processed from the original
    (unmodified) page.
    """
    temp_doc, clean_page = _make_clean_page(page)
    try:
        text_blocks = _get_text_blocks(clean_page)
    finally:
        temp_doc.close()
    page_text = " ".join(b["text"] for b in text_blocks)
    form_name = rule_engine.extract_form_name(text_blocks, page_height=page.rect.height)
    visit = rule_engine.extract_visit(page_text)

    records: list[AnnotationRecord] = []
    for annot in page.annots():
        record = _process_annotation(
            annot, page_num, form_name, visit, profile, rule_engine, text_blocks
        )
        if record is not None:
            records.append(record)
    return records


def _process_annotation(
    annot: fitz.Annot,
    page_num: int,
    form_name: str,
    visit: str,
    profile: Profile,
    rule_engine: RuleEngine,
    text_blocks: list[TextBlock],
) -> AnnotationRecord | None:
    """Process a single annotation; returns None if it should be excluded.

    Applies annotation_filter first (subtype, empty content), then classifies
    via the rule engine.  Annotations whose category is '_exclude' are dropped.
    """
    info = annot.info
    # annot.type is a tuple like (2, 'FreeText') or (0, 'Text')
    subtype = annot.type[1] if annot.type else ""

    # --- annotation_filter: include_types ---
    af = profile.annotation_filter
    if af.include_types and subtype not in af.include_types:
        return None

    content = info.get("content", "") or ""

    # --- annotation_filter: exclude_empty / min_content_length ---
    if af.exclude_empty and len(content) < af.min_content_length:
        return None

    subject = info.get("subject", "") or ""
    style = _parse_style(annot, profile)
    rect = list(annot.rect)  # [x0, y0, x1, y1]
    rotation = _safe_rotation(annot)

    # --- classification ---
    category, matched_rule = rule_engine.classify(content, subject)
    if category == "_exclude":
        return None

    anchor_text = _extract_anchor_text(
        annot.rect, profile, text_blocks,
        exclude_patterns=rule_engine.anchor_exclude_patterns,
    )

    return AnnotationRecord(
        id=str(uuid.uuid4()),
        page=page_num,
        content=content,
        domain=subject,
        category=category,
        matched_rule=matched_rule,
        rect=rect,
        anchor_text=anchor_text,
        form_name=form_name,
        visit=visit,
        style=style,
        rotation=rotation,
    )


def _safe_rotation(annot: fitz.Annot) -> int:
    """Return annotation rotation as int, defaulting to 0 on any error."""
    try:
        rot = annot.rotation
        return int(rot) if rot is not None else 0
    except Exception:
        return 0


def _parse_style(annot: fitz.Annot, profile: Profile) -> StyleInfo:
    """Extract font and color styling from annotation DA string with profile defaults.

    The DA (default appearance) string for SDTM annotations typically looks
    like: "0 0 0 rg /Arial,BoldItalic 18 Tf".  We parse font name, size, and
    text color from it, then check the annotation's colors dict for the border
    (stroke) color.  Any field that cannot be parsed falls back to the profile's
    style_defaults.
    """
    defaults = profile.style_defaults
    da = annot.info.get("da", "") or ""

    font: str = defaults.font
    font_size: float = defaults.font_size
    text_color: list[float] = list(defaults.text_color)
    border_color: list[float] = list(defaults.border_color)

    # Parse font name and size: "/FontName Size Tf"
    font_match = re.search(r"/(\S+)\s+(\d+(?:\.\d+)?)\s+Tf", da)
    if font_match:
        font = font_match.group(1)
        font_size = float(font_match.group(2))

    # Parse text color from DeviceRGB operator: "r g b rg"
    color_match = re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", da)
    if color_match:
        text_color = [float(color_match.group(i)) for i in range(1, 4)]

    # Border color from annotation stroke entry
    try:
        colors = annot.colors
        stroke = colors.get("stroke") if colors else None
        if stroke:
            border_color = list(float(c) for c in stroke)
    except Exception:
        pass

    return StyleInfo(
        font=font,
        font_size=font_size,
        text_color=text_color,
        border_color=border_color,
    )


def _get_text_blocks(page: fitz.Page) -> list[TextBlock]:
    """Delegate to pdf_utils.get_text_blocks (kept for backwards compatibility).

    Call on a clean page produced by _make_clean_page() so that
    annotation-rendered text has already been removed at the PDF level.
    """
    return get_text_blocks(page)


def _extract_anchor_text(
    annot_rect: fitz.Rect,
    profile: Profile,
    text_blocks: list[TextBlock],
    exclude_patterns: list[re.Pattern[str]] | None = None,
) -> str:
    """Find the anchor text for an annotation using the left-column + vertical-distance algorithm.

    Algorithm:
      1. Compute the left-column threshold: min(block x0) + left_column_tolerance_px.
      2. Filter to blocks whose x0 is within that threshold (left column only).
      3. For each left-column block compute the vertical distance to the annotation:
         vert_dist = max(0, max(annot_y0, block_y0) - min(annot_y1, block_y1))
         (0 when the block vertically overlaps or touches the annotation).
      4. Select the block with the minimum vertical distance; tie-break by
         absolute difference between block center-y and annotation center-y.
      5. Skip blocks matching exclude_patterns.
      6. Return the selected block's text, or empty string when no candidates remain.

    This approach is robust to multi-column CRF layouts where annotations sit
    to the right of field labels: only the leftmost column (where labels live)
    is considered, and vertical proximity determines the best match.

    Args:
        exclude_patterns: Pre-compiled patterns from RuleEngine.anchor_exclude_patterns.
            When None, patterns are compiled from profile on each call (legacy path).
    """
    config = profile.anchor_text_config
    if exclude_patterns is None:
        exclude_patterns = [
            re.compile(p, re.IGNORECASE) for p in config.exclude_patterns
        ]

    if not text_blocks:
        return ""

    # --- 1. Compute left-column threshold ---
    min_x0 = min(b["rect"][0] for b in text_blocks)
    left_threshold = min_x0 + config.left_column_tolerance_px

    annot_y0 = annot_rect.y0
    annot_y1 = annot_rect.y1
    annot_cy = (annot_y0 + annot_y1) / 2.0

    # --- 2-5. Filter to left column, compute vertical distance, apply excludes ---
    candidates: list[tuple[float, float, TextBlock]] = []
    for block in text_blocks:
        if block["rect"][0] > left_threshold:
            continue
        text = block["text"].strip()
        if not text:
            continue
        if any(p.search(text) for p in exclude_patterns):
            continue
        block_y0 = block["rect"][1]
        block_y1 = block["rect"][3]
        vert_dist = max(0.0, max(annot_y0, block_y0) - min(annot_y1, block_y1))
        block_cy = (block_y0 + block_y1) / 2.0
        center_dist = abs(block_cy - annot_cy)
        candidates.append((vert_dist, center_dist, block))

    if not candidates:
        return ""

    # --- 6. Select block with minimum vertical distance (tie-break: center-y distance) ---
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[0][2]["text"].strip()
