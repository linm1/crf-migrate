"""Phase 1: Extract SDTM annotations from source aCRF PDF.

Uses PyMuPDF (fitz) for annotation extraction and the configured rule engine
for classification, form name extraction, and visit detection.
"""
import re
import uuid
import warnings
from pathlib import Path

import fitz  # PyMuPDF

from src.models import AnnotationRecord, StyleInfo
from src.pdf_utils import find_nearest_label, get_text_blocks, make_clean_page
from src.profile_models import Profile
from src.rule_engine import RuleEngine, TextBlock

# FreeText annotation subtype value in PyMuPDF
_FREETEXT_SUBTYPE = "FreeText"
_DEVICE_RGB_PATTERN = re.compile(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg")
_RC_RGB_COLOR_PATTERN = re.compile(
    r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)",
    re.IGNORECASE,
)
_RC_COLOR_DECL_PATTERN = re.compile(
    r"(?<![-\w])color\s*:\s*(#[0-9a-fA-F]{3}\b|#[0-9a-fA-F]{6}\b|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\))",
    re.IGNORECASE,
)
_RC_STYLE_ATTR_PATTERN = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
_RC_FONT_FAMILY_DECL_PATTERN = re.compile(r"font-family\s*:\s*([^;]+)", re.IGNORECASE)
_RC_FONT_SIZE_DECL_PATTERN = re.compile(r"font-size\s*:\s*(\d+(?:\.\d+)?)pt", re.IGNORECASE)
_RC_FONT_WEIGHT_DECL_PATTERN = re.compile(r"font-weight\s*:\s*([^;\s]+)", re.IGNORECASE)
_RC_FONT_STYLE_DECL_PATTERN = re.compile(r"font-style\s*:\s*([^;\s]+)", re.IGNORECASE)
_DEDUP_CATEGORY_PRIORITY: tuple[str, ...] = (
    "sdtm_mapping",
    "domain_label",
    "cross_reference",
    "note",
)
_DEDUP_HIGH_OVERLAP_IOU = 0.95
_DEDUP_HIGH_OVERLAP_SMALLER_COVERAGE = 0.96


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
    return _dedup_annotations(records)


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
    classify_content = content.strip()
    category, matched_rule = rule_engine.classify(classify_content, subject)
    if category == "_exclude":
        return None

    anchor_text, anchor_rect = _extract_anchor_text(
        annot.rect, profile, text_blocks,
        exclude_patterns=rule_engine.anchor_exclude_patterns,
    )

    return AnnotationRecord(
        id=str(uuid.uuid4()),
        page=page_num,
        content=classify_content,
        domain=subject,
        category=category,
        matched_rule=matched_rule,
        rect=rect,
        anchor_text=anchor_text,
        anchor_rect=anchor_rect,
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


def _rect_area(rect: list[float]) -> float:
    """Return the area of a PDF rect, clamped to zero for invalid dimensions."""
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _rect_intersection_area(left: list[float], right: list[float]) -> float:
    """Return the overlapping area between two rects."""
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _rects_highly_overlap(left: list[float], right: list[float]) -> bool:
    """Return True when two rects are effectively the same annotation footprint."""
    intersection = _rect_intersection_area(left, right)
    if intersection == 0.0:
        return False

    left_area = _rect_area(left)
    right_area = _rect_area(right)
    if left_area == 0.0 or right_area == 0.0:
        return False

    smaller_coverage = intersection / min(left_area, right_area)
    if smaller_coverage >= _DEDUP_HIGH_OVERLAP_SMALLER_COVERAGE:
        return True
    union = left_area + right_area - intersection
    return (intersection / union) >= _DEDUP_HIGH_OVERLAP_IOU


def _warn_duplicate_group(
    page: int,
    detail: str,
    group_size: int,
    winner: AnnotationRecord,
) -> None:
    """Emit a stable duplicate warning for a deduped annotation group."""
    warnings.warn(
        "Duplicate annotations at "
        f"page={page}; {detail}; group_size={group_size}; "
        f"keeping category={winner.category!r} with content={winner.content!r}.",
        UserWarning,
        stacklevel=3,
    )


def _filter_winning_records(
    records: list[AnnotationRecord],
    winning_ids: set[str],
) -> list[AnnotationRecord]:
    """Preserve original extraction order while keeping only chosen winners."""
    return [record for record in records if record.id in winning_ids]


def _overlap_signature(record: AnnotationRecord) -> tuple[int | str, ...]:
    """Return the narrow signature used for overlap-based duplicate detection."""
    return (
        record.page,
        record.content,
        record.category,
        record.domain,
        record.anchor_text,
        record.form_name,
        record.visit,
    )


def _dedup_annotations(records: list[AnnotationRecord]) -> list[AnnotationRecord]:
    """Remove exact and near-identical duplicate annotations."""
    from collections import defaultdict

    def _rect_key(record: AnnotationRecord) -> tuple[int, float, float, float, float]:
        return (
            record.page,
            round(record.rect[0], 2),
            round(record.rect[1], 2),
            round(record.rect[2], 2),
            round(record.rect[3], 2),
        )

    def _category_rank(category: str) -> int:
        try:
            return _DEDUP_CATEGORY_PRIORITY.index(category)
        except ValueError:
            return len(_DEDUP_CATEGORY_PRIORITY)

    def _winner_key(record_index: int, record: AnnotationRecord) -> tuple[int, int, int]:
        return (-len(record.content), _category_rank(record.category), record_index)

    exact_groups: dict[
        tuple[int, float, float, float, float],
        list[tuple[int, AnnotationRecord]],
    ] = defaultdict(list)
    for record_index, record in enumerate(records):
        exact_groups[_rect_key(record)].append((record_index, record))

    winning_ids: set[str] = set()
    for key, group in exact_groups.items():
        if len(group) == 1:
            winning_ids.add(group[0][1].id)
            continue

        _, winner = min(group, key=lambda entry: _winner_key(*entry))
        winning_ids.add(winner.id)
        _warn_duplicate_group(
            page=key[0],
            detail=f"rect=({key[1]}, {key[2]}, {key[3]}, {key[4]})",
            group_size=len(group),
            winner=winner,
        )

    exact_deduped = _filter_winning_records(records, winning_ids)

    overlap_groups: dict[
        tuple[int | str, ...],
        list[tuple[int, AnnotationRecord]],
    ] = defaultdict(list)
    for record_index, record in enumerate(exact_deduped):
        overlap_groups[_overlap_signature(record)].append((record_index, record))

    winning_ids = set()
    for group in overlap_groups.values():
        if len(group) == 1:
            winning_ids.add(group[0][1].id)
            continue

        visited: set[int] = set()
        for start_index in range(len(group)):
            if start_index in visited:
                continue

            stack = [start_index]
            cluster_indices: set[int] = {start_index}
            visited.add(start_index)

            unvisited = [i for i in range(len(group)) if i not in visited]
            while stack:
                current_index = stack.pop()
                current_record = group[current_index][1]
                still_unvisited = []
                for other_index in unvisited:
                    if _rects_highly_overlap(current_record.rect, group[other_index][1].rect):
                        visited.add(other_index)
                        cluster_indices.add(other_index)
                        stack.append(other_index)
                    else:
                        still_unvisited.append(other_index)
                unvisited = still_unvisited

            cluster = [group[index] for index in sorted(cluster_indices)]
            if len(cluster) == 1:
                winning_ids.add(cluster[0][1].id)
                continue

            _, winner = min(cluster, key=lambda entry: _winner_key(*entry))
            winning_ids.add(winner.id)
            _warn_duplicate_group(
                page=winner.page,
                detail="reason='high_overlap'",
                group_size=len(cluster),
                winner=winner,
            )

    return _filter_winning_records(exact_deduped, winning_ids)


def _parse_device_rgb(raw: str) -> list[float] | None:
    """Extract a PDF DeviceRGB color (``r g b rg``) from a content string."""
    color_match = _DEVICE_RGB_PATTERN.search(raw)
    if color_match:
        return [float(color_match.group(i)) for i in range(1, 4)]
    return None


_AP_STROKE_RGB_PATTERN = re.compile(
    r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+RG\b"
)


def _parse_ap_border_color(doc: fitz.Document, annot: fitz.Annot) -> list[float] | None:
    """Extract border stroke color from the AP stream (R G B RG operator).

    PDF graphics operators execute in order; the LAST ``RG`` before paint is the
    active stroke color, so we take the last match rather than the first.
    """
    try:
        ap_ref = doc.xref_get_key(annot.xref, "AP/N")
        if not ap_ref or ap_ref[1] == "null":
            return None
        n_num = int(ap_ref[1].split()[0])
        stream = doc.xref_stream(n_num)
        if not stream:
            return None
        text = stream.decode("latin-1", errors="replace")
        matches = _AP_STROKE_RGB_PATTERN.findall(text)
        if matches:
            r, g, b = matches[-1]
            values = [float(r), float(g), float(b)]
            if all(0.0 <= v <= 1.0 for v in values):
                return values
    except Exception:
        pass
    return None


def _parse_css_color_value(raw_value: str) -> list[float] | None:
    """Parse a CSS color value into normalized RGB floats."""
    value = raw_value.strip()

    if value.startswith("#"):
        hex_value = value[1:]
        if len(hex_value) == 3:
            hex_value = "".join(ch * 2 for ch in hex_value)
        return [int(hex_value[index:index + 2], 16) / 255.0 for index in (0, 2, 4)]

    rgb_match = _RC_RGB_COLOR_PATTERN.fullmatch(value)
    if rgb_match:
        return [
            max(0, min(int(rgb_match.group(i)), 255)) / 255.0
            for i in range(1, 4)
        ]

    return None


def _parse_richtext_color(raw_rc: str) -> list[float] | None:
    """Extract a CSS text color from a FreeText RC XHTML payload."""
    text_color: list[float] | None = None
    for match in _RC_COLOR_DECL_PATTERN.finditer(raw_rc):
        parsed = _parse_css_color_value(match.group(1))
        if parsed is not None:
            text_color = parsed
    return text_color


def _parse_richtext_typography(raw_rc: str) -> tuple[str | None, float | None, bool, bool]:
    """Return (font_family, font_size_pt, is_bold, is_italic) from RC XHTML CSS.

    Reads cascading CSS from <body style="..."> and <span style="..."> elements
    in document order. The LAST declaration wins (CSS cascade, matches existing
    _parse_richtext_color contract).

    Returns (None, None, False, False) when no recognisable typography exists.
    """
    font_family: str | None = None
    font_size: float | None = None
    is_bold: bool = False
    is_italic: bool = False

    for style_match in _RC_STYLE_ATTR_PATTERN.finditer(raw_rc):
        style_text = style_match.group(1)

        family_match = _RC_FONT_FAMILY_DECL_PATTERN.search(style_text)
        if family_match:
            raw_families = family_match.group(1)
            first_family = raw_families.split(",")[0].strip().strip("'\"")
            if first_family:
                font_family = first_family

        size_match = _RC_FONT_SIZE_DECL_PATTERN.search(style_text)
        if size_match:
            font_size = float(size_match.group(1))

        weight_match = _RC_FONT_WEIGHT_DECL_PATTERN.search(style_text)
        if weight_match:
            weight_val = weight_match.group(1).lower()
            if weight_val in ("bold", "bolder"):
                is_bold = True
            elif weight_val in ("normal", "lighter"):
                is_bold = False
            else:
                try:
                    is_bold = int(weight_val) >= 600
                except ValueError:
                    is_bold = False

        fstyle_match = _RC_FONT_STYLE_DECL_PATTERN.search(style_text)
        if fstyle_match:
            fstyle_val = fstyle_match.group(1).lower()
            is_italic = fstyle_val in ("italic", "oblique")

    return font_family, font_size, is_bold, is_italic


def _parse_style(annot: fitz.Annot, profile: Profile) -> StyleInfo:
    """Extract font and color styling from annotation DA string with profile defaults.

    For FreeText annotations in PyMuPDF:
    - The DA (default appearance) string lives in the xref as key "DA", not in
      annot.info["da"] (which is always empty). We read it from the xref directly.
        - Rich-text FreeText annotations can store the visible text color in the "RC"
            XHTML payload while leaving DA at a stale fallback color. Prefer RC color
            when present, then fall back to DA.
    - The box background/fill color is stored in the PDF "C" key and exposed by
      PyMuPDF as annot.colors["stroke"]. annot.colors["fill"] is always empty for
      FreeText annotations and should not be used.
    - Border color per SDTM guideline is always black; border width/dashes come
      from annot.border.
    """
    defaults = profile.style_defaults

    font: str = defaults.font
    font_size: float = defaults.font_size
    text_color: list[float] = list(defaults.text_color)
    fill_color: list[float] | None = list(defaults.fill_color) if defaults.fill_color else None
    border_width: float = 1.0
    border_dashes: list[int] | None = None

    # Read DA string from xref (annot.info["da"] is always empty for FreeText)
    da = ""
    try:
        _, da = annot.parent.parent.xref_get_key(annot.xref, "DA")
    except Exception:
        pass
    if not da or da == "null":
        da = ""

    rc = ""
    try:
        _, rc = annot.parent.parent.xref_get_key(annot.xref, "RC")
    except Exception:
        pass
    if not rc or rc == "null":
        rc = ""

    # Parse font name and size: "/FontName Size Tf"
    font_match = re.search(r"/(\S+)\s+(\d+(?:\.\d+)?)\s+Tf", da)
    if font_match:
        font = font_match.group(1)
        font_size = float(font_match.group(2))

    rc_text_color = _parse_richtext_color(rc)
    if rc_text_color is not None:
        text_color = rc_text_color
    else:
        da_text_color = _parse_device_rgb(da)
        if da_text_color is not None:
            text_color = da_text_color

    rc_family, rc_size, rc_bold, rc_italic = _parse_richtext_typography(rc)
    if rc_family is not None:
        suffix_parts = []
        if rc_bold:
            suffix_parts.append("Bold")
        if rc_italic:
            suffix_parts.append("Italic")
        font = f"{rc_family},{''.join(suffix_parts)}" if suffix_parts else rc_family
    if rc_size is not None:
        font_size = rc_size

    # Fill/background color: for FreeText, PyMuPDF exposes this under
    # annot.colors["stroke"] (PDF "C" key). annot.colors["fill"] is always empty.
    try:
        colors = annot.colors
        if colors:
            stroke = colors.get("stroke")
            if stroke:
                fill_color = list(float(c) for c in stroke)
    except Exception:
        pass

    # Border width and dash pattern
    try:
        border = annot.border
        if border:
            border_width = float(border.get("width") or 1.0)
            border_dashes = border.get("dashes") or None
    except Exception:
        pass

    # Extract real border color from AP stream; fall back to black only if absent
    doc = annot.parent.parent
    ap_border_color = _parse_ap_border_color(doc, annot)
    border_color = ap_border_color if ap_border_color is not None else [0.0, 0.0, 0.0]

    return StyleInfo(
        font=font,
        font_size=font_size,
        text_color=text_color,
        border_color=border_color,
        fill_color=fill_color,
        border_width=border_width,
        border_dashes=border_dashes,
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
) -> tuple[str, list[float] | None]:
    """Find the anchor text for an annotation using the left-column + vertical-distance algorithm.

    Delegates to pdf_utils.find_nearest_label.  Converts fitz.Rect to list[float]
    so the shared utility remains free of fitz dependencies.

    Args:
        annot_rect: PyMuPDF annotation rectangle.
        profile: Active profile providing anchor_text_config settings.
        text_blocks: Annotation-free text blocks for the current page.
        exclude_patterns: Pre-compiled patterns from RuleEngine.anchor_exclude_patterns.
            When None, patterns are compiled from profile on each call (legacy path).

    Returns:
        (anchor_text, anchor_rect) — the label string and its [x0,y0,x1,y1] bounding box,
        or ("", None) when no anchor is found.
    """
    config = profile.anchor_text_config
    if exclude_patterns is None:
        exclude_patterns = [
            re.compile(p, re.IGNORECASE) for p in profile.form_name_rules.exclude_patterns
        ]
    marker_rect = [annot_rect.x0, annot_rect.y0, annot_rect.x1, annot_rect.y1]
    return find_nearest_label(
        marker_rect,
        text_blocks,
        config.left_column_tolerance_px,
        exclude_patterns=exclude_patterns,
    )
