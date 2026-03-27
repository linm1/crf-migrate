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

# Sentinel pattern: pages whose top region contains this text are lookup/codelist
# tables, not fillable CRF forms.  They produce no FieldRecords.
_CODELIST_PAGE_RE = re.compile(r"^Codelist\b", re.IGNORECASE)

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
# Maximum vertical distance (px) for Pass B label search — approx 2.5 lines at 12pt
_MAX_LABEL_VERT_PX = 30.0


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

    form_name derivation:
      After all records are built, form_name is set to the label of the
      topmost-leftmost record on this page (min y0, then min x0).  This is
      simpler and more reliable than running extract_form_name() on raw text
      blocks before fields are extracted, which was fragile and susceptible
      to cross-page contamination and font/bold heuristic failures.
    """
    text_blocks = _get_text_blocks(page)

    # Extract page dimensions for field bounds tracking
    page_w = page.rect.width
    page_h = page.rect.height

    # --- Codelist page sentinel: skip lookup-table pages entirely ---
    # Codelist pages start with "Codelist:" / "Codelist View:" in the top region.
    # Even after those blocks are excluded from form_name extraction, the data rows
    # in the same top region (e.g. "SCREENING (SV1)", "DAY 1") would be picked up
    # as spurious form names.  Detect and discard these pages before extraction.
    if page.rect.height > 0:
        top_cutoff = (profile.form_name_rules.top_region_fraction or 0.35) * page.rect.height
        for block in text_blocks:
            if block["rect"][1] <= top_cutoff and _CODELIST_PAGE_RE.search(block["text"]):
                return []

    page_text = " ".join(b["text"] for b in text_blocks)
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

    # Priority order is deliberate: date_field > checkbox > text_field
    # A date placeholder (MM/DD/YYYY) must not be downgraded to a plain text_field.
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

    # Build all records with form_name="" — filled in after all records exist
    headers = _collect_section_headers(
        non_marker_blocks, page_num, form_name="", visit=visit,
        min_header_size=min_header_size, exclude_patterns=exclude_patterns,
        page_width=page_w, page_height=page_h,
    )
    markers = _resolve_marker_labels(
        marker_blocks, non_marker_blocks, page_num, form_name="", visit=visit,
        left_col_tolerance=left_col_tol, exclude_patterns=exclude_patterns,
        page_width=page_w, page_height=page_h,
    )
    records = headers + markers

    if not records:
        return []

    # --- Derive form_name from the topmost-leftmost record on this page ---
    # Using already-extracted field positions is more reliable than running
    # extract_form_name() on raw text blocks before fields exist.
    top_left = min(records, key=lambda r: (r.rect[1], r.rect[0]))
    form_name = top_left.label

    return [r.model_copy(update={"form_name": form_name}) for r in records]


def _collect_section_headers(
    non_marker_blocks: list[TextBlock],
    page_num: int,
    form_name: str,
    visit: str,
    min_header_size: float,
    exclude_patterns: list[re.Pattern[str]],
    page_width: float,
    page_height: float,
) -> list[FieldRecord]:
    """Return a FieldRecord for every non-marker block that qualifies as a section header.

    A block qualifies when it is bold OR has font_size >= min_header_size, AND its
    text does not match any of the exclude_patterns.
    """
    records: list[FieldRecord] = []
    for block in non_marker_blocks:
        # Classify as section header if bold OR large font
        if not block["bold"] and block["font_size"] < min_header_size:
            continue
        text = block["text"].strip()
        if any(p.search(text) for p in exclude_patterns):
            continue
        records.append(
            FieldRecord(
                id=str(uuid.uuid4()),
                page=page_num,
                label=text,
                form_name=form_name,
                visit=visit,
                rect=block["rect"],
                field_type="section_header",
                page_width=page_width,
                page_height=page_height,
            )
        )
    return records


def _resolve_marker_labels(
    marker_blocks: list[tuple[TextBlock, str]],
    non_marker_blocks: list[TextBlock],
    page_num: int,
    form_name: str,
    visit: str,
    left_col_tolerance: float,
    exclude_patterns: list[re.Pattern[str]],
    page_width: float,
    page_height: float,
) -> list[FieldRecord]:
    """Pass B: resolve the nearest human-readable label for each marker block.

    ALL non-marker blocks (including large-font headers) are passed as candidates
    so that field labels of any font size can be found.  A max_vert_distance_px
    cap of _MAX_LABEL_VERT_PX prevents a distant section header from being
    incorrectly used as a field label.  When no nearby label is found, the
    fallback is the marker block's own text (graceful degradation).
    """
    records: list[FieldRecord] = []
    for block, field_type in marker_blocks:
        label, _ = find_nearest_label(
            marker_rect=block["rect"],
            text_blocks=non_marker_blocks,
            left_column_tolerance_px=left_col_tolerance,
            exclude_patterns=exclude_patterns,
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
                page_width=page_width,
                page_height=page_height,
            )
        )
    return records


def _get_text_blocks(page: fitz.Page) -> list[TextBlock]:
    """Extract text blocks, excluding spans inside FreeText annotation bounding boxes.

    Phase 2 target CRFs may carry SDTM FreeText annotations (when the user
    supplies an annotated aCRF as the target reference).  Those annotation
    appearance streams surface as ordinary text blocks via page.get_text(),
    so we must suppress them.

    Critically, we filter ONLY FreeText annotation rects — not AcroForm Widget
    rects.  Filtering all annotation types (the previous behavior) caused label
    text adjacent to AcroForm widget boxes to be silently excluded, making
    find_nearest_label() fall back to wrong or stale labels from earlier pages.
    """
    annot_rects = get_annotation_rects(page, types=["FreeText"])
    return get_text_blocks(page, annot_rects=annot_rects)
