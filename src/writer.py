"""Phase 4: Write approved SDTM annotations to the target blank CRF PDF.

Takes matches.json (Phase 3 output) and produces output_acrf.pdf with all
approved/modified annotations placed, plus a qc_report dict summarising
what was written, skipped, and left unmatched.
"""
from pathlib import Path

import re

import fitz  # PyMuPDF

from src.arrow_geometry import clamp_to_page, edge_midpoint_from_direction, hybrid_endpoint_placement
from src.models import AnnotationRecord, ArrowMatch, ArrowRecord, MatchRecord
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

# Trailing bold/italic/oblique style token, for the *display* family name used
# only in /RC and /DS (e.g. "Arial,BoldItalic" -> "Arial"). Never affects the
# Base-14 family bucket below.
_STYLE_SUFFIX_RE = re.compile(r"(?i)[,\-\s]+(bold\s*italic|bold\s*oblique|italic|oblique|bold)\s*$")


def _detect_family(raw: str) -> str:
    """Bucket an arbitrary font name into a Base-14 family: times/courier/helvetica."""
    if _FAMILY_TIMES.search(raw):
        return "times"
    if _FAMILY_COURIER.search(raw):
        return "courier"
    return "helvetica"


def _display_family(raw: str) -> str:
    """Strip a trailing style token for /RC and /DS, e.g. "Arial,BoldItalic" -> "Arial"."""
    cleaned = _STYLE_SUFFIX_RE.sub("", raw).strip()
    return cleaned or raw


def _normalise_font_name(raw: str) -> tuple[str, str, bool, bool]:
    """Map an arbitrary font name to (pymupdf_alias, pdf_standard_name, is_bold, is_italic).

    Detects bold/italic from the name, determines the font family,
    and returns the closest Base-14 equivalent.
    """
    is_bold = bool(_BOLD_RE.search(raw))
    is_italic = bool(_ITALIC_RE.search(raw))
    family = _detect_family(raw)
    alias, pdf_name = _BASE14_MAP[(family, is_bold, is_italic)]
    return alias, pdf_name, is_bold, is_italic


def _resolve_text_style(
    annot: AnnotationRecord,
    profile: Profile,
) -> tuple[str, str, float, tuple[float, float, float], bool, bool, str]:
    """Return (pymupdf_alias, pdf_standard_name, fontsize, text_color, is_bold,
    is_italic, display_family).

    display_family is the clean, pre-normalisation family name (e.g. "Arial")
    used only for /RC + /DS (Kofax resize-triggered AP regeneration reads font
    weight/style/family from there) — it never changes pdf_standard_name, the
    Base-14 name used for AP-stream/DA rendering.

    When profile.style_defaults.use_source_style is True:
      - font weight/style/size/color come from the source annotation's StyleInfo
      - font name is normalised to the closest Base-14 equivalent

    When False (default — current behaviour):
      - category-driven rules: domain_label uses bold, cross_reference uses cyan, etc.
      - family bucket (Base-14 rendering) and display_family (/RC + /DS) both
        come from profile.style_defaults.font
    """
    sd = profile.style_defaults

    if sd.use_source_style:
        alias, pdf_name, is_bold, is_italic = _normalise_font_name(annot.style.font)
        fontsize = annot.style.font_size
        tc = annot.style.text_color
        text_color = (tc[0], tc[1], tc[2]) if len(tc) >= 3 else (0.0, 0.0, 0.0)
        return alias, pdf_name, fontsize, text_color, is_bold, is_italic, _display_family(annot.style.font)

    display_family = _display_family(sd.font)
    family_bucket = _detect_family(sd.font)

    if annot.category == "domain_label":
        alias, pdf_name = _BASE14_MAP[(family_bucket, True, False)]
        return alias, pdf_name, sd.domain_label_font_size, (0.0, 0.0, 0.0), True, False, display_family
    if annot.category == "cross_reference":
        alias, pdf_name = _BASE14_MAP[(family_bucket, False, False)]
        return alias, pdf_name, sd.font_size, (0.0, 1.0, 1.0), False, False, display_family
    alias, pdf_name = _BASE14_MAP[(family_bucket, False, False)]
    return alias, pdf_name, sd.font_size, (0.0, 0.0, 0.0), False, False, display_family


def write_annotations(
    target_pdf_path: Path,
    output_pdf_path: Path,
    matches: list[MatchRecord],
    annotations: list[AnnotationRecord],
    profile: Profile,
    arrows: list[ArrowRecord] | None = None,
    arrow_matches: list[ArrowMatch] | None = None,
) -> dict:
    """Write approved annotations to target PDF. Returns qc_report dict.

    arrows / arrow_matches are additive, trailing, default-None parameters —
    existing positional call sites (tests, ui/phase4_review.py before this
    feature) keep working unmodified. When provided, resolved arrow/line
    connectors are written in a separate pass after the FreeText loop below
    and before the existing /CL integrity assertion and doc.save() call —
    neither of which changes.
    """
    annot_by_id: dict[str, AnnotationRecord] = {a.id: a for a in annotations}

    doc = fitz.open(str(target_pdf_path))

    # Pre-existing malformed /CL pre-pass: pre-annotated target PDFs (e.g. from
    # a prior tool) can already carry FreeText annots with the same spec-malformed
    # empty /CL [ ] + no /IT defect that _strip_cl() fixes for pipeline-written
    # annots below. The final /CL integrity assertion sweeps every annot on the
    # page (not just pipeline-written ones), so those pre-existing offenders must
    # be cleaned here or the assertion aborts Phase 4 even with matches=[].
    # Well-formed callouts (/CL + /IT /FreeTextCallout) are legitimate and left
    # intact. Only the Callout intent legitimizes /CL — any other /IT value
    # (e.g. /FreeTextTypeWriter) alongside /CL is just as malformed as no /IT.
    preexisting_cl_stripped = 0
    preexisting_cl_warned: list[int] = []
    for page in doc:
        for a in page.annots(types=[fitz.PDF_ANNOT_FREE_TEXT]):
            if doc.xref_get_key(a.xref, "CL")[0] == "null":
                continue
            if doc.xref_get_key(a.xref, "IT") == ("name", "/FreeTextCallout"):
                continue  # well-formed callout — leave intact
            if _strip_cl(doc, a) == 1:
                preexisting_cl_stripped += 1
            else:
                preexisting_cl_warned.append(a.xref)

    written_ids: list[str] = []
    skipped_ids: list[str] = []
    placed_rects: dict[str, list[float]] = {}

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
            placed_rects[match.annotation_id] = match.target_rect
        else:
            skipped_ids.append(match.annotation_id)

    arrow_report = _write_arrows(
        doc, arrows or [], arrow_matches or [], annot_by_id, placed_rects, profile
    )

    for page in doc:
        for annot in page.annots():
            if doc.xref_get_key(annot.xref, "CL")[0] == "null":
                continue
            if doc.xref_get_key(annot.xref, "IT") == ("name", "/FreeTextCallout"):
                continue  # well-formed callout — exempt
            if annot.xref in preexisting_cl_warned:
                continue  # ambiguous pre-existing /CL — already surfaced in qc_report
            raise RuntimeError(f"annotation xref {annot.xref} still has /CL after stripping")

    doc.save(str(output_pdf_path), garbage=4, deflate=True)
    doc.close()

    report = build_qc_report(matches, written_ids, skipped_ids)
    report.update(arrow_report)
    report["preexisting_cl_stripped"] = preexisting_cl_stripped
    report["preexisting_cl_warned_xrefs"] = preexisting_cl_warned
    return report


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


def _patch_ap_border_color(
    doc: fitz.Document,
    annot: fitz.Annot,
    border_color: tuple[float, float, float],
) -> None:
    """Patch the AP stream RG operator to set the FreeText border stroke color.

    PyMuPDF 1.26.x raises ValueError when border_color is passed to update()
    for non-richtext FreeText annotations.  The AP stream approach is the only
    reliable path: PyMuPDF encodes the border stroke as ``r g b RG`` in the AP
    Form XObject.  We replace the existing RG triplet with the desired values.

    Must be called AFTER a.update() — update() regenerates the AP stream.
    """
    r, g, b = border_color
    n_key = doc.xref_get_key(annot.xref, "AP/N")
    if n_key[0] == "null":
        return
    n_num = int(n_key[1].split()[0])
    stream = doc.xref_stream(n_num)

    # AP stream encodes fill as ``R G B rg`` and border stroke as ``R G B RG``.
    # Replace only the first RG occurrence (the border color line).
    def _fmt(v: float) -> str:
        # Format float: strip trailing zeros for compactness, match PyMuPDF style
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return s if s else "0"

    replacement = f"{_fmt(r)} {_fmt(g)} {_fmt(b)} RG".encode()
    patched = re.sub(rb"[\d.][\d. ]* RG\b", replacement, stream, count=1)
    doc.update_stream(n_num, patched)


# ---------------------------------------------------------------------------
# Kofax Power PDF compatibility — /CL removal, /RC + /DS rich-content styling
# ---------------------------------------------------------------------------
# See CLAUDE.md ("FreeText annotation writing — critical PyMuPDF behavior") for
# why these are required: PyMuPDF's add_freetext_annot() unconditionally emits
# a spec-malformed /CL (callout-line geometry with no matching /IT), which
# Kofax Power PDF interprets as a locked callout, blocking drag/resize
# entirely. Kofax's resize-triggered AP-stream regeneration separately reads
# font weight/style/family from /RC (rich content, XHTML) and /DS (default
# style) rather than /DA — neither of which writer.py sets otherwise.

_CL_LINE_RE = re.compile(r"/CL\s*\[[^\]]*\]\s*\n?")


def _pdf_string_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_rc(
    content: str,
    family: str,
    size: float,
    is_bold: bool,
    is_italic: bool,
    text_color: tuple[float, float, float],
) -> str:
    """XHTML fragment Kofax reads for font weight/style/family on resize-triggered
    AP regeneration. Structurally identical to the validated diagnostic prototype
    (tests/fixtures/generate_kofax_rc_variant.py:_build_rc).
    """
    style_parts = [f"font-size:{size:.2f}pt", f"font-family:'{_xml_escape(family)}'"]
    if is_bold:
        style_parts.append("font-weight:bold")
    if is_italic:
        style_parts.append("font-style:italic")
    r, g, b = (round(c * 255) for c in text_color)
    style_parts.append(f"color:#{r:02X}{g:02X}{b:02X}")
    style = ";".join(style_parts)
    return (
        '<?xml version="1.0" ?> <body xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/" xfa:APIVersion="Acrobat:7.0.0" '
        "xfa:spec=\"2.0.2\" style=\"font-family:'Helvetica'\">"
        f'<p><span style="{style}">{_xml_escape(content)}</span></p></body>'
    )


def _build_ds(family: str, size: float) -> str:
    return f"text-decoration:;font-size:{size:.2f}pt;font-family:'{family}'"


def _write_rc_ds(
    doc: fitz.Document,
    annot: fitz.Annot,
    content: str,
    family: str,
    fontsize: float,
    text_color: tuple[float, float, float],
    is_bold: bool,
    is_italic: bool,
) -> None:
    """Set /RC + /DS unconditionally, so Kofax's resize regeneration reads correct
    font weight/style/family instead of falling back to regular. /RC and /DS are
    top-level annotation-dict keys, untouched by a.update()'s /DA or /AP
    regeneration, so call-order relative to update() doesn't matter — unlike
    _apply_font_style/_patch_ap_border_color.
    """
    rc = _build_rc(content, family, fontsize, is_bold, is_italic, text_color)
    ds = _build_ds(family, fontsize)
    doc.xref_set_key(annot.xref, "RC", f"({_pdf_string_escape(rc)})")
    doc.xref_set_key(annot.xref, "DS", f"({_pdf_string_escape(ds)})")


def _strip_cl(doc: fitz.Document, annot: fitz.Annot) -> int:
    """Remove /CL (callout-line geometry) entirely from this annotation's object.

    PyMuPDF's add_freetext_annot() unconditionally emits /CL with no matching /IT
    (intent) — a spec-malformed combination Kofax Power PDF interprets as a locked
    callout, blocking drag/resize entirely (ticket #4, CONFIRMED). Must run BEFORE
    _write_rc_ds() so this regex never runs over an object that already contains
    the much larger /RC string.

    xref_set_key(xref, "CL", "null") is not sufficient — it nulls the value but
    does not remove the key, and stale /CL bytes can survive a plain save. True
    removal requires rewriting the object's raw text via update_object().

    Only applies the rewrite when the regex matches exactly once. A real /CL
    array can't contain "]", so a genuine /CL key always matches; if a match
    count of 2+ occurs (e.g. a pre-existing /RC string that itself contains a
    spurious "/CL [" substring), skip rather than risk corrupting the object.
    Returns the substitution count so callers can distinguish stripped vs.
    warned-and-left-alone.
    """
    text = doc.xref_object(annot.xref)
    new_text, n_subs = _CL_LINE_RE.subn("", text)
    if n_subs == 1:
        doc.update_object(annot.xref, new_text)
    return n_subs


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
    alias, pdf_name, fontsize, text_color, is_bold, is_italic, display_family = _resolve_text_style(annot, profile)
    fill_src = annot.style.fill_color
    fill = (fill_src[0], fill_src[1], fill_src[2]) if fill_src and len(fill_src) >= 3 else _FALLBACK_FILL
    style = annot.style
    bc_src = annot.style.border_color
    border_color = (bc_src[0], bc_src[1], bc_src[2]) if bc_src and len(bc_src) >= 3 else (0.0, 0.0, 0.0)
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
    _patch_ap_border_color(doc, a, border_color)
    if pdf_name != "Helvetica":
        _apply_font_style(doc, page, a, fontsize, pdf_name, text_color)
    _strip_cl(doc, a)
    _write_rc_ds(doc, a, annot.content, display_family, fontsize, text_color, is_bold, is_italic)


# ---------------------------------------------------------------------------
# Arrow/line connector writing (Arrow/Line Connector Migration feature)
# ---------------------------------------------------------------------------
# Additive — nothing above this point changes. Runs after the FreeText loop
# (which populates placed_rects above) and before the /CL integrity
# assertion + doc.save() in write_annotations(), neither of which changes.
#
# Line annotations do not use the FreeText /C-is-fill / AP-stream-patching
# machinery documented in CLAUDE.md — that machinery is specific to
# FreeText's spec-malformed /CL and Kofax's /RC+/DS resize behavior. For a
# Line annot, set_colors(stroke=...) is simply the line's visible color; no
# equivalent gymnastics are needed here.

_ARROW_SKIP_HEAD_UNRESOLVED = "head_unresolved"
_ARROW_SKIP_PARENT_NOT_WRITTEN = "parent_not_written"
_ARROW_SKIP_INVALID_TARGET_PAGE = "invalid_target_page"
_ARROW_SKIP_WRITE_ERROR = "write_error"


def _write_single_arrow(
    doc: fitz.Document,
    arrow: ArrowRecord,
    arrow_match: ArrowMatch,
    annot_by_id: dict[str, AnnotationRecord],
    placed_rects: dict[str, list[float]],
    profile: Profile,
) -> tuple[bool, str | None]:
    """Write one resolved arrow as a Line annotation. Returns (written, skip_reason)."""
    if arrow_match.head_match_method == "unresolved" or arrow_match.head_target_rect is None:
        return False, arrow_match.skip_reason or _ARROW_SKIP_HEAD_UNRESOLVED

    # D5: no raw-tail-vertex fallback. The parent annotation must have been
    # actually written (present in placed_rects) — there is no fallback path.
    if arrow.tail_annotation_id is None or arrow.tail_annotation_id not in placed_rects:
        return False, _ARROW_SKIP_PARENT_NOT_WRITTEN

    if arrow_match.target_page is None:
        return False, _ARROW_SKIP_INVALID_TARGET_PAGE

    page_index = arrow_match.target_page - 1
    if page_index < 0 or page_index >= doc.page_count:
        return False, _ARROW_SKIP_INVALID_TARGET_PAGE

    target_annot_rect = placed_rects[arrow.tail_annotation_id]
    source_annot = annot_by_id.get(arrow.tail_annotation_id)
    source_annot_rect = tuple(source_annot.rect) if source_annot is not None else tuple(target_annot_rect)

    tail_pt = hybrid_endpoint_placement(
        source_box=source_annot_rect,
        source_point=arrow.tail_vertex,
        target_box=tuple(target_annot_rect),
        other_endpoint=arrow.head_vertex,
        size_similarity_tolerance=profile.arrows.size_similarity_tolerance,
    )

    head_target_rect = arrow_match.head_target_rect
    if arrow.head_source_rect is not None:
        head_pt = hybrid_endpoint_placement(
            source_box=arrow.head_source_rect,
            source_point=arrow.head_vertex,
            target_box=head_target_rect,
            other_endpoint=arrow.tail_vertex,
            size_similarity_tolerance=profile.arrows.size_similarity_tolerance,
        )
    else:
        head_pt = edge_midpoint_from_direction(
            other_endpoint=arrow.tail_vertex,
            target_box=head_target_rect,
        )

    page = doc[page_index]
    page_rect = (0.0, 0.0, float(page.rect.width), float(page.rect.height))
    tail_pt = clamp_to_page(tail_pt, page_rect)
    head_pt = clamp_to_page(head_pt, page_rect)

    style = arrow.style
    a = page.add_line_annot(fitz.Point(*tail_pt), fitz.Point(*head_pt))
    a.set_colors(stroke=list(style.stroke_color))
    a.set_border(width=style.width, dashes=list(style.dashes))
    a.set_line_ends(style.tail_line_end, style.head_line_end)
    a.update(opacity=style.opacity)
    _patch_line_dashes(doc, a, style.width, style.dashes)

    return True, None


def _patch_line_dashes(
    doc: fitz.Document,
    annot: fitz.Annot,
    width: float,
    dashes: list[float],
) -> None:
    """Patch the /BS dict to include a /D dash array for a Line annotation.

    PyMuPDF 1.26.4's set_border(dashes=...) silently drops the dash array
    for Line annotations — it writes /BS << /W .. /S /S >> with no /D key at
    all (verified: /BS dict inspected immediately after set_border() has no
    /D entry). This patches the dict-level style so annot.border["dashes"]
    round-trips correctly after save/reopen; it does not touch the AP
    stream, so whether Adobe/Kofax render the dash pattern visually is a
    separate, human-verification concern (see
    docs/unknowns/arrow-connector-migration/implementation-notes.md).

    Line-only — never touches the FreeText /BS or AP-stream machinery.
    """
    if not dashes:
        return
    dash_str = " ".join(_fmt_dash(d) for d in dashes)
    bs_value = f"<< /W {_fmt_dash(width)} /S /D /D [{dash_str}] >>"
    doc.xref_set_key(annot.xref, "BS", bs_value)


def _fmt_dash(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _write_arrows(
    doc: fitz.Document,
    arrows: list[ArrowRecord],
    arrow_matches: list[ArrowMatch],
    annot_by_id: dict[str, AnnotationRecord],
    placed_rects: dict[str, list[float]],
    profile: Profile,
) -> dict:
    """Write all resolved arrows as Line annotations. Returns additive QC keys.

    Always returns arrows_total / arrows_written / arrows_skipped /
    arrow_skipped_ids, even when arrows/arrow_matches are empty, so
    build_qc_report's dict shape is stable whether or not this feature is
    used. Each arrow is written inside its own try/except so one bad arrow
    never aborts the whole pass (or the surrounding write_annotations call).
    """
    arrows_by_id = {a.arrow_id: a for a in arrows}
    written = 0
    skipped = 0
    skipped_ids: list[dict] = []

    for arrow_match in arrow_matches:
        arrow = arrows_by_id.get(arrow_match.arrow_id)
        if arrow is None:
            skipped += 1
            skipped_ids.append({"arrow_id": arrow_match.arrow_id, "reason": _ARROW_SKIP_HEAD_UNRESOLVED})
            continue

        try:
            was_written, reason = _write_single_arrow(
                doc, arrow, arrow_match, annot_by_id, placed_rects, profile
            )
        except Exception:
            was_written, reason = False, _ARROW_SKIP_WRITE_ERROR

        if was_written:
            written += 1
        else:
            skipped += 1
            skipped_ids.append({"arrow_id": arrow_match.arrow_id, "reason": reason})

    return {
        "arrows_total": len(arrow_matches),
        "arrows_written": written,
        "arrows_skipped": skipped,
        "arrow_skipped_ids": skipped_ids,
    }
