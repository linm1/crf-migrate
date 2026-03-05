"""Phase 1: Extract SDTM annotations from source aCRF PDF.

Uses PyMuPDF (fitz) for annotation extraction and the configured rule engine
for classification, form name extraction, and visit detection.
"""
import re
import uuid
from pathlib import Path

import fitz  # PyMuPDF

from src.models import AnnotationRecord, StyleInfo
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


def _process_page(
    page: fitz.Page,
    page_num: int,
    profile: Profile,
    rule_engine: RuleEngine,
) -> list[AnnotationRecord]:
    """Process all annotations on a single page.

    Extracts text blocks once for the page, derives form_name and visit from
    them, then processes each annotation individually.
    """
    text_blocks = _get_text_blocks(page)
    page_text = " ".join(b["text"] for b in text_blocks)
    form_name = rule_engine.extract_form_name(text_blocks)
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

    anchor_text = _extract_anchor_text(annot.rect, profile, text_blocks)

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
    """Extract all non-empty text spans from a page as TextBlock dicts.

    Uses PyMuPDF's 'dict' text extraction mode to capture per-span font
    metadata.  Silently returns an empty list on extraction failure.
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
                    flags = span.get("flags", 0)
                    bold = bool(flags & 16)  # bit 4 is the bold flag in PDF spec
                    blocks.append(
                        TextBlock(
                            text=text,
                            font_size=span.get("size", 10.0),
                            bold=bold,
                            rect=list(span.get("bbox", [0.0, 0.0, 0.0, 0.0])),
                        )
                    )
    except Exception:
        pass
    return blocks


def _extract_anchor_text(
    annot_rect: fitz.Rect,
    profile: Profile,
    text_blocks: list[TextBlock],
) -> str:
    """Find the nearest non-excluded text block within the configured radius.

    Candidates are ranked by direction preference (prefer_direction list order)
    then by Euclidean distance from the annotation center.  Returns the text of
    the best candidate, or an empty string when no candidate is within radius.
    """
    config = profile.anchor_text_config
    radius = config.radius_px
    exclude_patterns = [
        re.compile(p, re.IGNORECASE) for p in config.exclude_patterns
    ]

    cx = (annot_rect.x0 + annot_rect.x1) / 2.0
    cy = (annot_rect.y0 + annot_rect.y1) / 2.0

    def _score(block: TextBlock) -> tuple[float, float]:
        """Return (direction_penalty, distance) — lower is more preferred."""
        bx = (block["rect"][0] + block["rect"][2]) / 2.0
        by = (block["rect"][1] + block["rect"][3]) / 2.0
        dx = bx - cx
        dy = by - cy
        dist = (dx ** 2 + dy ** 2) ** 0.5

        prefer = config.prefer_direction
        penalty = float(len(prefer)) * 10.0 + 50.0  # default: no preference match

        if "left" in prefer and dx < 0 and abs(dx) > abs(dy):
            penalty = float(prefer.index("left")) * 10.0
        elif "above" in prefer and dy < 0 and abs(dy) >= abs(dx):
            penalty = float(prefer.index("above")) * 10.0
        elif "right" in prefer and dx > 0 and abs(dx) > abs(dy):
            penalty = float(prefer.index("right")) * 10.0
        elif "below" in prefer and dy > 0 and abs(dy) >= abs(dx):
            penalty = float(prefer.index("below")) * 10.0

        return penalty, dist

    candidates: list[TextBlock] = []
    for block in text_blocks:
        bx = (block["rect"][0] + block["rect"][2]) / 2.0
        by = (block["rect"][1] + block["rect"][3]) / 2.0
        dist = ((bx - cx) ** 2 + (by - cy) ** 2) ** 0.5
        if dist > radius:
            continue
        text = block["text"].strip()
        if not text:
            continue
        if any(p.search(text) for p in exclude_patterns):
            continue
        candidates.append(block)

    if not candidates:
        return ""

    candidates.sort(key=_score)
    return candidates[0]["text"].strip()
