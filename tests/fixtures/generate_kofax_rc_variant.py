"""Generate a Kofax diagnostic test PDF with /RC (rich content) and /DS (default style)
added to every FreeText annotation (github.com/linm1/crf-migrate issue #2).

Structural diff against a Kofax-native annotation (see interview-log.md "Round 4") found that
Kofax's own FreeText annotations carry /RC (XHTML with inline per-span font-weight/style/family/
color CSS) and /DS, which writer.py's output never sets. Working hypothesis: Kofax's
regeneration-on-resize reads font weight/style/family from /RC (we provide none, so it falls back
to regular/non-monospace), and reads /DA's color as *border* intent, not text intent (explaining
why border becomes text-colored on resize).

This variant adds /RC + /DS to every annotation, encoding the actual intended font-family,
weight, style, and text color per case — WITHOUT changing /DA's existing semantics (still text
color, still the Base-14 name) — the conservative first test, since swapping /DA's meaning would
directly conflict with Adobe's own regeneration-on-touch behavior (_apply_font_style() depends on
/DA's color = text color for Adobe fidelity).

Produces docs/unknowns/kofax-power-pdf-compat/kofax_test_rc.pdf (gitignored — investigation
scratch). Human test: resize each textbox in Kofax (the isolated trigger from ticket #6) and see
whether bold/italic/font-family AND border color now survive.

Run directly: `python tests/fixtures/generate_kofax_rc_variant.py`
"""
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_kofax_cl_variant import _build_cases, _strip_cl, _write_two_page_pdf  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "docs" / "unknowns" / "kofax-power-pdf-compat"
OUTPUT_PATH = OUT_DIR / "kofax_test_rc.pdf"

# Per-case (font family, is_bold, is_italic) — matches what _build_cases() actually renders,
# derived from the same source used to build the AnnotationRecord.style.font strings there.
_CASE_STYLE = {
    "case-1": ("Helvetica", True, False),   # domain_label -> Helvetica-Bold
    "case-2": ("Helvetica", False, False),  # cross_reference -> Helvetica
    "case-3": ("Helvetica", False, False),  # sdtm_mapping default -> Helvetica
    "case-4": ("Arial", True, False),
    "case-5": ("Arial", False, True),
    "case-6": ("Times New Roman", True, True),
    "case-7": ("Courier New", False, False),
}


def _pdf_string_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_rc(content: str, family: str, size: float, bold: bool, italic: bool,
              color_rgb: tuple[float, float, float]) -> str:
    style_parts = [f"font-size:{size:.2f}pt", f"font-family:'{family}'"]
    if bold:
        style_parts.append("font-weight:bold")
    if italic:
        style_parts.append("font-style:italic")
    r, g, b = (round(c * 255) for c in color_rgb)
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


def _add_rc_ds(pdf_path: Path) -> int:
    doc = fitz.open(str(pdf_path))
    patched = 0
    for page in doc:
        for annot in page.annots():
            content = annot.info.get("content", "")
            # Match by leading case number in the content text ("1:", "2:", etc.).
            case_id = next(
                (cid for cid in _CASE_STYLE if content.startswith(f"{cid.split('-')[1]}:")),
                None,
            )
            if case_id is None:
                continue
            family, bold, italic = _CASE_STYLE[case_id]

            da = doc.xref_get_key(annot.xref, "DA")[1]
            # DA looks like "(r g b rg /FontName size Tf)" — pull the color out for RC.
            inner = da.strip("()")
            parts = inner.split()
            r, g, b = float(parts[0]), float(parts[1]), float(parts[2])
            size = float(parts[-2])

            rc = _build_rc(content, family, size, bold, italic, (r, g, b))
            ds = _build_ds(family, size)
            doc.xref_set_key(annot.xref, "RC", f"({_pdf_string_escape(rc)})")
            doc.xref_set_key(annot.xref, "DS", f"({_pdf_string_escape(ds)})")
            patched += 1

    tmp_path = pdf_path.with_suffix(".tmp.pdf")
    doc.save(str(tmp_path), garbage=4, deflate=True)
    doc.close()
    tmp_path.replace(pdf_path)
    return patched


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    annots, matches, profile = _build_cases()
    _write_two_page_pdf(annots, matches, profile, OUTPUT_PATH)
    # /CL must be stripped first (ticket #4's confirmed drag/resize-lock cause) so this
    # variant is actually draggable/resizable in Kofax at all — otherwise /RC + /DS can
    # never be tested, since resize (the trigger under test) never succeeds in the first
    # place. Stripping before adding /RC/DS also avoids the /CL regex ever running over an
    # object that already contains the (much larger) RC string.
    stripped = _strip_cl(OUTPUT_PATH)
    patched = _add_rc_ds(OUTPUT_PATH)

    raw = OUTPUT_PATH.read_bytes()
    if b"/CL" in raw:
        raise RuntimeError(f"{OUTPUT_PATH} still contains a literal /CL token after stripping")
    if raw.count(b"/RC") < patched:
        raise RuntimeError(f"{OUTPUT_PATH} missing expected /RC entries after patching")

    print(
        f"Wrote {OUTPUT_PATH} ({stripped} annotations had /CL stripped, "
        f"{patched} annotations got /RC + /DS)"
    )


if __name__ == "__main__":
    main()
