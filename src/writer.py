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


def _resolve_text_style(
    annot: AnnotationRecord,
    profile: Profile,
) -> tuple[str, float, tuple[float, float, float]]:
    """Return (fontname, fontsize, text_color) per SDTM guideline.

    Font sizes are read from profile.style_defaults:
    - domain_label_font_size for domain_label category
    - font_size for all other categories

    - domain_label:    Helvetica Bold (hebo), domain_label_font_size, black
    - cross_reference: Helvetica Regular (helv), font_size, #00FFFF
    - all others:      Helvetica Regular (helv), font_size, black
    """
    sd = profile.style_defaults
    if annot.category == "domain_label":
        return "hebo", sd.domain_label_font_size, (0.0, 0.0, 0.0)
    if annot.category == "cross_reference":
        return "helv", sd.font_size, (0.0, 1.0, 1.0)
    return "helv", sd.font_size, (0.0, 0.0, 0.0)


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


def _apply_bold_font(
    doc: fitz.Document,
    page: fitz.Page,
    annot: fitz.Annot,
    fontsize: float,
) -> None:
    """Patch the annotation's DA and AP stream to use Helvetica-Bold.

    PyMuPDF's add_freetext_annot always writes /Helv in both the DA string
    and the AP stream regardless of the fontname argument.  Four steps are
    all required to produce bold that survives viewer interaction:

    1. Register "Helvetica-Bold" (standard PDF Base-14 name) in page resources.
    2. Rewrite /DA to reference /Helvetica-Bold — this is what viewers read
       when they regenerate the AP stream on user interaction (click/edit).
    3. In the AP stream content, replace /Helv with /Helvetica-Bold so the
       initial render is also bold (viewers use the AP stream for display).
    4. Add /Helvetica-Bold to the AP stream's own /Resources/Font dict so
       the viewer can resolve the name inside the self-contained Form XObject.

    Using the standard PDF name "Helvetica-Bold" (not the PyMuPDF alias
    "hebo") is the critical invariant.  Viewers resolve /Helvetica-Bold as a
    known Base-14 font when regenerating the AP stream; /hebo is unknown to
    all viewers and causes silent fallback to regular Helvetica on touch.

    Must be called after a.update() so the DA rewrite is not overwritten.
    """
    # 1. Register Helvetica-Bold by its standard PDF Base-14 name and get its xref.
    #    Using the standard name (not the PyMuPDF alias "hebo") is critical: when
    #    a viewer regenerates the AP stream on user interaction it reads /DA and
    #    must resolve the font name.  Viewers understand "Helvetica-Bold" as a
    #    Base-14 standard font; "hebo" is a PyMuPDF-internal alias unknown to any
    #    viewer, causing silent fallback to regular Helvetica on touch.
    page.insert_font(fontname="Helvetica-Bold")
    hb_xref = next(
        f[0] for f in page.get_fonts() if f[4] == "Helvetica-Bold"
    )

    # 2. Rewrite the DA string with the standard PDF font name.
    da_str = f"0 0 0 rg /Helvetica-Bold {fontsize} Tf"
    doc.xref_set_key(annot.xref, "DA", f"({da_str})")

    # 3. Patch the AP stream content: /Helv → /Helvetica-Bold.
    n_num = int(doc.xref_get_key(annot.xref, "AP/N")[1].split()[0])
    stream = doc.xref_stream(n_num)
    patched = re.sub(rb"/Helv\b", b"/Helvetica-Bold", stream)
    doc.update_stream(n_num, patched)

    # 4. Register /Helvetica-Bold in the AP stream's own /Resources/Font dict.
    doc.xref_set_key(n_num, "Resources/Font/Helvetica-Bold", f"{hb_xref} 0 R")


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
    fontname, fontsize, text_color = _resolve_text_style(annot, profile)
    fill_src = annot.style.fill_color
    fill = (fill_src[0], fill_src[1], fill_src[2]) if fill_src and len(fill_src) >= 3 else _FALLBACK_FILL
    style = annot.style
    rect = fitz.Rect(target_rect)

    a = page.add_freetext_annot(
        rect=rect,
        text=annot.content,
        fontsize=fontsize,
        fontname="helv",  # PyMuPDF only supports helv here; bold patched below
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
    if fontname == "hebo":
        _apply_bold_font(doc, page, a, fontsize)
