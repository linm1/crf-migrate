"""Phase 2: Extract CRF fields from the target blank CRF PDF.

Uses PyMuPDF (fitz) for text extraction and the configured rule engine for
form name and visit detection.  All field-type heuristics are applied in a
fixed priority order; the first match wins.
"""
import re
import uuid
from pathlib import Path

import fitz  # PyMuPDF

from src.models import FieldRecord
from src.pdf_utils import get_annotation_rects, get_text_blocks
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
    """Extract fields from a single page.

    Extracts text blocks once, calls the rule engine for form_name / visit,
    then classifies each span individually.
    """
    text_blocks = _get_text_blocks(page)
    page_text = " ".join(b["text"] for b in text_blocks)
    form_name = rule_engine.extract_form_name(text_blocks, page_height=page.rect.height)
    visit = rule_engine.extract_visit(page_text) or ""

    records: list[FieldRecord] = []
    for block in text_blocks:
        record = _classify_block(block, page_num, form_name, visit, profile)
        if record is not None:
            records.append(record)
    return records


def _get_text_blocks(page: fitz.Page) -> list[TextBlock]:
    """Extract text blocks, excluding spans inside annotation bounding boxes.

    Delegates to pdf_utils.get_text_blocks with annotation-overlap filtering
    so that FreeText annotation appearance text is not misclassified as CRF
    fields.
    """
    annot_rects = get_annotation_rects(page)
    return get_text_blocks(page, annot_rects=annot_rects)


def _classify_block(
    block: TextBlock,
    page_num: int,
    form_name: str,
    visit: str,
    profile: Profile,
) -> FieldRecord | None:
    """Classify a single text block; return None if it doesn't map to a field.

    Heuristics applied in priority order (first match wins):
      1. date_field  — contains a date placeholder (MM/DD/YYYY etc.)
      2. checkbox    — contains Yes/No or checkbox glyph
      3. section_header — bold or large-font text above min_font_size
      4. text_field  — contains 3+ consecutive underscores
      5. Returns None for unclassified spans
    """
    text = block["text"]
    font_size = block["font_size"]
    min_header_size = profile.form_name_rules.min_font_size

    if _DATE_RE.search(text):
        field_type = "date_field"
    elif _CHECKBOX_RE.search(text):
        field_type = "checkbox"
    elif font_size >= min_header_size:
        field_type = "section_header"
    elif _TEXT_FIELD_RE.search(text):
        field_type = "text_field"
    else:
        return None

    return FieldRecord(
        id=str(uuid.uuid4()),
        page=page_num,
        label=text,
        form_name=form_name,
        visit=visit,
        rect=block["rect"],
        field_type=field_type,
    )
