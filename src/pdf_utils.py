"""Shared PDF text extraction utilities used by extractor.py and field_parser.py."""
import re

import fitz  # PyMuPDF

from src.rule_engine import TextBlock


def find_nearest_label(
    marker_rect: list[float],
    text_blocks: list[TextBlock],
    left_column_tolerance_px: float,
    exclude_patterns: list[re.Pattern[str]] | None = None,
    max_vert_distance_px: float | None = None,
) -> tuple[str, list[float] | None]:
    """Find the nearest left-column label to a marker rectangle.

    Pure function — no PDF or fitz dependency. Accepts marker_rect as a plain
    list[float] so it can be called from field_parser.py without fitz.Rect.

    Algorithm:
      1. Compute left-column threshold: min(block x0) + left_column_tolerance_px.
      2. Filter to blocks whose x0 <= left_threshold (left column only).
      3. For each candidate compute vert_dist = max(0, max(marker_y0, block_y0)
         - min(marker_y1, block_y1)).  Zero when block overlaps annotation.
      4. When max_vert_distance_px is set, discard candidates with
         vert_dist > max_vert_distance_px.
      5. Tie-break by abs(block_center_y - marker_y0) — prefers labels near the top of the annotation.
      6. Skip blocks matching any of exclude_patterns.
      7. Return (stripped text, rect) of best candidate, or ("", None) when none remain.

    Args:
        marker_rect: [x0, y0, x1, y1] bounding box of the annotation/marker.
        text_blocks: Page text blocks, e.g. from get_text_blocks().
        left_column_tolerance_px: Width added to the leftmost x0 to define the
            left-column boundary.  Blocks with x0 beyond that boundary are ignored.
        exclude_patterns: Pre-compiled patterns; matching blocks are skipped.
            Pass None to apply no pattern filtering.
        max_vert_distance_px: When provided, candidates whose vert_dist exceeds
            this value are excluded.  Pass None (default) for no distance cap.

    Returns:
        (text, rect) where text is the label string and rect is its [x0,y0,x1,y1]
        bounding box. Both are ("", None) when no candidate is found.
    """
    if not text_blocks:
        return "", None

    x0, y0, x1, y1 = marker_rect

    min_x0 = min(b["rect"][0] for b in text_blocks)
    left_threshold = min_x0 + left_column_tolerance_px

    candidates: list[tuple[float, float, TextBlock]] = []
    for block in text_blocks:
        if block["rect"][0] > left_threshold:
            continue
        text = block["text"].strip()
        if not text:
            continue
        if exclude_patterns and any(p.search(text) for p in exclude_patterns):
            continue
        block_y0 = block["rect"][1]
        block_y1 = block["rect"][3]
        vert_dist = max(0.0, max(y0, block_y0) - min(y1, block_y1))
        if max_vert_distance_px is not None and vert_dist > max_vert_distance_px:
            continue
        block_cy = (block_y0 + block_y1) / 2.0
        center_dist = abs(block_cy - y0)
        candidates.append((vert_dist, center_dist, block))

    if not candidates:
        return "", None

    candidates.sort(key=lambda t: (t[0], t[1]))
    best = candidates[0][2]
    return best["text"].strip(), list(best["rect"])


def make_clean_page(page: fitz.Page) -> tuple[fitz.Document, fitz.Page]:
    """Create an in-memory single-page copy with all annotations removed.

    PyMuPDF includes FreeText annotation content in the page text stream.
    Deleting annotations from a temporary copy is the only reliable way to
    obtain pure CRF text (form names, field labels) without annotation
    contamination — geometric bbox heuristics are fragile because rendered
    span boundaries do not reliably align with annotation rect boundaries.

    Returns (temp_doc, clean_page). Caller MUST close temp_doc when done.
    The original page and its parent document are never mutated.
    """
    temp_doc = fitz.open()
    temp_doc.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    clean_page = temp_doc[0]
    for annot in list(clean_page.annots()):
        clean_page.delete_annot(annot)
    return temp_doc, clean_page


def get_annotation_rects(
    page: fitz.Page,
    types: list[str] | None = None,
) -> list[fitz.Rect]:
    """Return bounding rects for annotations on the page.

    Used to suppress spans that originate from annotation appearance streams
    rather than native page content. Returns [] on any failure.

    Args:
        page: The page to inspect.
        types: Optional list of annotation subtype strings to include (e.g.
            ``["FreeText"]``).  When None, all annotation types are included.
            Pass ``["FreeText"]`` to exclude AcroForm Widget rects so that
            field labels near widget boundaries are not inadvertently filtered.
    """
    try:
        annots = page.annots()
        if types is not None:
            type_set = {t.lower() for t in types}
            return [
                annot.rect for annot in annots
                if annot.type[1].lower() in type_set
            ]
        return [annot.rect for annot in annots]
    except Exception:
        return []


def span_inside_annotation(
    bbox: list[float],
    annot_rects: list[fitz.Rect],
    threshold: float = 0.3,
) -> bool:
    """Return True if >= threshold of the span's area overlaps any annotation rect.

    FreeText annotation appearance text scores ~1.0 (fully inside its rect).
    Native page text at a different position scores 0.0.
    Uses arithmetic intersection to avoid fitz operator version differences.
    """
    if not annot_rects:
        return False
    x0, y0, x1, y1 = bbox
    span_area = (x1 - x0) * (y1 - y0)
    if span_area <= 0.0:
        return False
    for arect in annot_rects:
        inter_area = (
            max(0.0, min(x1, arect.x1) - max(x0, arect.x0))
            * max(0.0, min(y1, arect.y1) - max(y0, arect.y0))
        )
        if inter_area / span_area >= threshold:
            return True
    return False


def get_text_blocks(
    page: fitz.Page,
    annot_rects: list[fitz.Rect] | None = None,
) -> list[TextBlock]:
    """Extract all non-empty text spans from a page as TextBlock dicts.

    Uses PyMuPDF's 'dict' text extraction mode to capture per-span font
    metadata. Silently returns an empty list on extraction failure.

    Args:
        page: The page to extract text from.
        annot_rects: When provided, spans whose area overlaps any of these
            rects by >= 30% are excluded. Pass the result of
            get_annotation_rects(page) to suppress FreeText annotation
            appearance text that PyMuPDF surfaces as ordinary text blocks.
            When None, no filtering is applied (use on clean pages produced
            by make_clean_page()).
    """
    blocks: list[TextBlock] = []
    try:
        raw = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        for block in raw.get("blocks", []):
            if block.get("type") != 0:  # 0 = text block; 1 = image block
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue
                    bbox = list(span.get("bbox", [0.0, 0.0, 0.0, 0.0]))
                    if annot_rects is not None and span_inside_annotation(bbox, annot_rects):
                        continue
                    flags = span.get("flags", 0)
                    font_name = span.get("font", "")
                    bold = bool(flags & 16) or ("bold" in font_name.lower())
                    blocks.append(
                        TextBlock(
                            text=text,
                            font_size=span.get("size", 10.0),
                            bold=bold,
                            rect=bbox,
                        )
                    )
    except Exception:
        pass
    return blocks
