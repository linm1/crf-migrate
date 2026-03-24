"""Shared PDF text extraction utilities used by extractor.py and field_parser.py."""
import fitz  # PyMuPDF

from src.rule_engine import TextBlock


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


def get_annotation_rects(page: fitz.Page) -> list[fitz.Rect]:
    """Return bounding rects for all annotations on the page.

    Used to suppress spans that originate from annotation appearance streams
    rather than native page content. Returns [] on any failure.
    """
    try:
        return [annot.rect for annot in page.annots()]
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
                    bold = bool(flags & 16)  # bit 4 is the bold flag in PDF spec
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
