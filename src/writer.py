"""Phase 4: Write approved SDTM annotations to the target blank CRF PDF.

Takes matches.json (Phase 3 output) and produces output_acrf.pdf with all
approved/modified annotations placed, plus a qc_report dict summarising
what was written, skipped, and left unmatched.
"""
from pathlib import Path

import re

import fitz  # PyMuPDF

from src.models import AnnotationRecord, MatchRecord
from src.profile_models import Profile

_FALLBACK_FILL: tuple[float, float, float] = (0.75, 1.0, 1.0)  # cyan

# ---------------------------------------------------------------------------
# Font name normalisation — map arbitrary source font names to Base-14
# ---------------------------------------------------------------------------

# Font family detection patterns
_FAMILY_TIMES = re.compile(r"(?i)(times|tiro|tiit|tibo|tibi)")
_FAMILY_COURIER = re.compile(r"(?i)(courier|cour|coit|cobo|cobi)")
# Anything else (Arial, Helvetica, unknown) → Helvetica family

# Base-14 lookup: (family, bold, italic) → (pymupdf_alias, pdf_standard_name)
_BASE14_MAP: dict[tuple[str, bool, bool], tuple[str, str]] = {
    ("helvetica", False, False): ("helv", "Helvetica"),
    ("helvetica", False, True):  ("heit", "Helvetica-Oblique"),
    ("helvetica", True, False):  ("hebo", "Helvetica-Bold"),
    ("helvetica", True, True):   ("hebi", "Helvetica-BoldOblique"),
    ("times", False, False):     ("tiro", "Times-Roman"),
    ("times", False, True):      ("tiit", "Times-Italic"),
    ("times", True, False):      ("tibo", "Times-Bold"),
    ("times", True, True):       ("tibi", "Times-BoldItalic"),
    ("courier", False, False):   ("cour", "Courier"),
    ("courier", False, True):    ("coit", "Courier-Oblique"),
    ("courier", True, False):    ("cobo", "Courier-Bold"),
    ("courier", True, True):     ("cobi", "Courier-BoldOblique"),
}

_BOLD_RE = re.compile(r"(?i)(bold|hebo|hebi|cobo|cobi|tibo|tibi)")
_ITALIC_RE = re.compile(r"(?i)(italic|oblique|heit|hebi|coit|cobi|tiit|tibi)")


def _normalise_font_name(raw: str) -> tuple[str, str, bool, bool]:
    """Map an arbitrary font name to (pymupdf_alias, pdf_standard_name, is_bold, is_italic).

    Detects bold/italic from the name, determines the font family,
    and returns the closest Base-14 equivalent.
    """
    is_bold = bool(_BOLD_RE.search(raw))
    is_italic = bool(_ITALIC_RE.search(raw))

    if _FAMILY_TIMES.search(raw):
        family = "times"
    elif _FAMILY_COURIER.search(raw):
        family = "courier"
    else:
        family = "helvetica"

    alias, pdf_name = _BASE14_MAP[(family, is_bold, is_italic)]
    return alias, pdf_name, is_bold, is_italic


def _resolve_text_style(
    annot: AnnotationRecord,
    profile: Profile,
) -> tuple[str, str, float, tuple[float, float, float], bool, bool]:
    """Return (pymupdf_alias, pdf_standard_name, fontsize, text_color, is_bold, is_italic).

    When profile.style_defaults.use_source_style is True:
      - font weight/style/size/color come from the source annotation's StyleInfo
      - font name is normalised to the closest Base-14 equivalent

    When False (default — current behaviour):
      - category-driven rules: domain_label uses bold, cross_reference uses cyan, etc.
    """
    sd = profile.style_defaults

    if sd.use_source_style:
        alias, pdf_name, is_bold, is_italic = _normalise_font_name(annot.style.font)
        fontsize = annot.style.font_size
        tc = annot.style.text_color
        text_color = (tc[0], tc[1], tc[2]) if len(tc) >= 3 else (0.0, 0.0, 0.0)
        return alias, pdf_name, fontsize, text_color, is_bold, is_italic

    if annot.category == "domain_label":
        return "hebo", "Helvetica-Bold", sd.domain_label_font_size, (0.0, 0.0, 0.0), True, False
    if annot.category == "cross_reference":
        return "helv", "Helvetica", sd.font_size, (0.0, 1.0, 1.0), False, False
    return "helv", "Helvetica", sd.font_size, (0.0, 0.0, 0.0), False, False


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

    for match in matches:
        if match.status == "approved":
            annot = annot_by_id.get(match.annotation_id)
            if annot is None:
                skipped_ids.append(match.annotation_id)
                continue
            page_index = match.target_page - 1
            if match.target_page <= 0 or page_index >= doc.page_count:
                skipped_ids.append(match.annotation_id)
                continue
            page = doc[page_index]
            _write_single_annotation(page, match.target_rect, annot, profile, doc)
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
        "re_pairing_annotation_ids": [
            m.annotation_id for m in matches if m.status == "re-pairing"
        ],
        "placement_adjusted_ids": [
            m.annotation_id for m in matches if m.placement_adjusted
        ],
    }


def _apply_font_style(
    doc: fitz.Document,
    page: fitz.Page,
    annot: fitz.Annot,
    fontsize: float,
    pdf_font_name: str,
    text_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> None:
    """Patch a FreeText annotation to use a specific Base-14 font variant.

    4-step pattern (documented in CLAUDE.md):
    1. Register font by standard PDF Base-14 name on the page.
    2. Rewrite /DA to reference the standard name.
    3. Patch AP stream content: /Helv → /FontName.
    4. Register font in the AP stream's own /Resources/Font dict.

    Must be called AFTER a.update() — update() overwrites /DA.
    """
    page.insert_font(fontname=pdf_font_name)
    font_xref = next(f[0] for f in page.get_fonts() if f[4] == pdf_font_name)

    r, g, b = text_color
    da_str = f"{r} {g} {b} rg /{pdf_font_name} {fontsize} Tf"
    doc.xref_set_key(annot.xref, "DA", f"({da_str})")

    n_num = int(doc.xref_get_key(annot.xref, "AP/N")[1].split()[0])
    stream = doc.xref_stream(n_num)
    patched = re.sub(rb"/Helv\b", f"/{pdf_font_name}".encode(), stream)
    doc.update_stream(n_num, patched)

    doc.xref_set_key(n_num, f"Resources/Font/{pdf_font_name}", f"{font_xref} 0 R")


def _write_single_annotation(
    page: fitz.Page,
    target_rect: list[float],
    annot: AnnotationRecord,
    profile: Profile,
    doc: fitz.Document,
) -> None:
    """Add a FreeText annotation to the given page at target_rect.

    Font, size, and text color follow SDTM guideline rules (category-driven).
    Fill/background color is taken directly from the source annotation's
    fill_color, falling back to cyan only when absent.
    Border width and dash pattern are preserved from the source annotation.

    IMPORTANT: Never call xref_set_key on /C after update().  The /C key is
    the fill color for FreeText annotations (PDF spec ISO 32000 Table 177).
    update(fill_color=...) sets /C correctly; overwriting it breaks both the
    fill color and the border color on viewer re-render.
    """
    alias, pdf_name, fontsize, text_color, is_bold, is_italic = _resolve_text_style(annot, profile)
    fill_src = annot.style.fill_color
    fill = (fill_src[0], fill_src[1], fill_src[2]) if fill_src and len(fill_src) >= 3 else _FALLBACK_FILL
    style = annot.style
    rect = fitz.Rect(target_rect)

    a = page.add_freetext_annot(
        rect=rect,
        text=annot.content,
        fontsize=fontsize,
        fontname="helv",  # PyMuPDF only supports helv here; bold/italic patched below
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
    if pdf_name != "Helvetica":
        _apply_font_style(doc, page, a, fontsize, pdf_name, text_color)
