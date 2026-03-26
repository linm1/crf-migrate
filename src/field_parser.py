"""Phase 2: Extract CRF fields from the target blank CRF PDF.

Uses PyMuPDF (fitz) for text extraction and the configured rule engine for
form name and visit detection.  Field-type heuristics use a two-pass spatial
algorithm: Pass A identifies marker blocks (date/checkbox/text-field patterns);
Pass B resolves the nearest human-readable label for each marker using the
spatial left-column search from pdf_utils.find_nearest_label.
"""
import re
import uuid
from pathlib import Path

import fitz  # PyMuPDF

from src.models import FieldRecord
from src.pdf_utils import find_nearest_label, get_annotation_rects, get_text_blocks
from src.profile_models import Profile
from src.rule_engine import RuleEngine, TextBlock

# Regex patterns for field-type heuristics
_DATE_RE = re.compile(
    r"\b(MM|DD|YYYY|mm|dd|yyyy)\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    re.IGNORECASE,
)
_CHECKBOX_RE = re.compile(
    r"\b(yes|no|y/n)\b|[□☐☑✓✗]",
    re.IGNORECASE,
)
_TEXT_FIELD_RE = re.compile(r"_{3,}")


def extract_fields(
    pdf_path: Path,
    profile: Profile,
    rule_engine: RuleEngine,
) -> list[FieldRecord]:
    """Extract and classify all form fields from a blank target CRF PDF.

    Opens pdf_path, iterates every page, and returns a flat list of
    FieldRecord objects — one per identifiable field span.
    """
    doc = fitz.open(str(pdf_path))
    try:
        records: list[FieldRecord] = []
        for page_index in range(doc.page_count):
            page = doc[page_index]
            page_num = page_index + 1
            page_records = _process_page(page, page_num, profile, rule_engine)
            records.extend(page_records)
    finally:
        doc.close()
    return records


def _process_page(
    page: fitz.Page,
    page_num: int,
    profile: Profile,
    rule_engine: RuleEngine,
) -> list[FieldRecord]:
    """Extract fields from a single page using a two-pass spatial algorithm.

    Pass A — Identify marker blocks:
      A block is a marker if its text matches _DATE_RE, _CHECKBOX_RE, or
      _TEXT_FIELD_RE.  All other blocks are potential label/header blocks.

    Section headers:
      Non-marker blocks with font_size >= min_font_size become section_header
      records (label = the block's own text, unchanged).

    Pass B — Resolve human-readable labels for markers:
      For each marker block, call find_nearest_label against the non-marker
      blocks.  If a label is found, use it; otherwise fall back to block["text"].
    """
    text_blocks = _get_text_blocks(page)
    page_text = " ".join(b["text"] for b in text_blocks)
    form_name = rule_engine.extract_form_name(text_blocks, page_height=page.rect.height)
    visit = rule_engine.extract_visit(page_text) or ""

    min_header_size = profile.form_name_rules.min_font_size
    left_col_tol = profile.anchor_text_config.left_column_tolerance_px
    exclude_patterns = rule_engine.anchor_exclude_patterns

    # --- Pass A: partition blocks into markers vs non-markers ---
    #
    # marker_blocks    : blocks matching a field-type pattern (date/checkbox/text)
    # non_marker_blocks: all other blocks (potential labels and section headers)
    #
    marker_blocks: list[tuple[TextBlock, str]] = []  # (block, field_type)
    non_marker_blocks: list[TextBlock] = []

    for block in text_blocks:
        text = block["text"]
        if _DATE_RE.search(text):
            marker_blocks.append((block, "date_field"))
        elif _CHECKBOX_RE.search(text):
            marker_blocks.append((block, "checkbox"))
        elif _TEXT_FIELD_RE.search(text):
            marker_blocks.append((block, "text_field"))
        else:
            non_marker_blocks.append(block)

    records: list[FieldRecord] = []

    # --- Section headers: non-marker blocks with large font ---
    for block in non_marker_blocks:
        if block["font_size"] >= min_header_size:
            records.append(
                FieldRecord(
                    id=str(uuid.uuid4()),
                    page=page_num,
                    label=block["text"],
                    form_name=form_name,
                    visit=visit,
                    rect=block["rect"],
                    field_type="section_header",
                )
            )

    # --- Pass B: for each marker, find its nearest left-column label ---
    #
    # ALL non-marker blocks (including large-font headers) are passed as candidates
    # so that field labels of any font size can be found.  A max_vert_distance_px
    # cap of 30px (≈2.5 lines of 12pt body text) prevents a distant section header
    # that happens to be the only non-marker block on the page from being
    # incorrectly used as a field label.  When no nearby label is found within
    # the cap, the fallback is the marker block's own text (graceful degradation).
    _MAX_LABEL_VERT_PX = 30.0

    for block, field_type in marker_blocks:
        label = find_nearest_label(
            marker_rect=block["rect"],
            text_blocks=non_marker_blocks,
            left_column_tolerance_px=left_col_tol,
            exclude_patterns=exclude_patterns if exclude_patterns else None,
            max_vert_distance_px=_MAX_LABEL_VERT_PX,
        )
        if not label:
            # Graceful degradation: use the marker text itself
            label = block["text"]

        records.append(
            FieldRecord(
                id=str(uuid.uuid4()),
                page=page_num,
                label=label,
                form_name=form_name,
                visit=visit,
                rect=block["rect"],
                field_type=field_type,
            )
        )

    return records


def _get_text_blocks(page: fitz.Page) -> list[TextBlock]:
    """Extract text blocks, excluding spans inside annotation bounding boxes.

    Delegates to pdf_utils.get_text_blocks with annotation-overlap filtering
    so that FreeText annotation appearance text is not misclassified as CRF
    fields.
    """
    annot_rects = get_annotation_rects(page)
    return get_text_blocks(page, annot_rects=annot_rects)
