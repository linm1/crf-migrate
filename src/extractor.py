"""Phase 1: Extract SDTM annotations from source aCRF PDF.

Uses PyMuPDF (fitz) for annotation extraction and the configured rule engine
for classification, form name extraction, and visit detection.
"""
import re
import uuid
from pathlib import Path

import fitz  # PyMuPDF

from src.models import AnnotationRecord, StyleInfo
from src.pdf_utils import find_nearest_label, get_text_blocks, make_clean_page
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

    Delegates to pdf_utils.find_nearest_label.  Converts fitz.Rect to list[float]
    so the shared utility remains free of fitz dependencies.

    Args:
        annot_rect: PyMuPDF annotation rectangle.
        profile: Active profile providing anchor_text_config settings.
        text_blocks: Annotation-free text blocks for the current page.
        exclude_patterns: Pre-compiled patterns from RuleEngine.anchor_exclude_patterns.
            When None, patterns are compiled from profile on each call (legacy path).
    """
    config = profile.anchor_text_config
    if exclude_patterns is None:
        exclude_patterns = [
            re.compile(p, re.IGNORECASE) for p in config.exclude_patterns
        ]
    marker_rect = [annot_rect.x0, annot_rect.y0, annot_rect.x1, annot_rect.y1]
    return find_nearest_label(
        marker_rect,
        text_blocks,
        config.left_column_tolerance_px,
        exclude_patterns=exclude_patterns,
    )
