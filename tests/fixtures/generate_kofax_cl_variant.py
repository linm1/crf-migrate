"""Generate Kofax diagnostic test PDFs for the annotation-editability investigation
(github.com/linm1/crf-migrate issue #2).

Produces two PDFs under docs/unknowns/kofax-power-pdf-compat/ (gitignored — investigation
scratch, not shipped fixtures):

- kofax_test.pdf         baseline, unmodified writer.py output (7 cases)
- kofax_test_no_cl.pdf   same 7 cases, with /CL (callout-line geometry) stripped from every
                          annotation — isolates the ticket #4 hypothesis that PyMuPDF's
                          unconditional, spec-malformed /CL emission (no matching /IT) is
                          what Kofax interprets as a locked callout.

Run directly: `python tests/fixtures/generate_kofax_cl_variant.py`
"""
import re
from pathlib import Path

import fitz

_CL_LINE_RE = re.compile(r"/CL\s*\[[^\]]*\]\s*\n?")

from src.models import AnnotationRecord, MatchRecord, StyleInfo
from src.profile_models import (
    ClassificationRule,
    Profile,
    ProfileMeta,
    RuleCondition,
    StyleDefaults,
)
from src.writer import write_annotations

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "docs" / "unknowns" / "kofax-power-pdf-compat"


def _build_cases() -> tuple[list[AnnotationRecord], list[MatchRecord], Profile]:
    """Same 7 cases used in the original manual-test round (see kofax-test-checklist.md),
    covering every font/border/fill combination src/writer.py currently produces."""
    annots: list[AnnotationRecord] = []
    matches: list[MatchRecord] = []

    def add(annot_id, page, target_rect, category, content,
            font="Arial", font_size=12.0, text_color=None,
            border_color=None, fill_color=None):
        annots.append(AnnotationRecord(
            id=annot_id, page=page, content=content, domain="DM",
            category=category, matched_rule="manual-test",
            rect=[0, 0, 10, 10],
            style=StyleInfo(
                font=font, font_size=font_size,
                text_color=text_color or [0.0, 0.0, 0.0],
                border_color=border_color or [0.0, 0.0, 0.0],
                fill_color=fill_color,
                border_width=1.5,
            ),
        ))
        matches.append(MatchRecord(
            annotation_id=annot_id, field_id=None, match_type="manual",
            confidence=1.0, target_rect=target_rect, target_page=page,
            status="approved",
        ))

    add("case-1", 1, [50, 60, 350, 90], "domain_label",
        "1: DOMAIN LABEL (Helvetica-Bold, black, expect BOLD to persist after edit)")
    add("case-2", 1, [50, 110, 400, 140], "cross_reference",
        "2: CROSS REFERENCE (Helvetica regular, cyan text)")
    add("case-3", 1, [50, 160, 400, 190], "sdtm_mapping",
        "3: DEFAULT/SDTM (Helvetica regular, black text, RED border via AP patch)",
        border_color=[1.0, 0.0, 0.0])
    add("case-4", 2, [50, 60, 400, 90], "sdtm_mapping",
        "4: source-style BOLD Arial -> Helvetica-Bold; RED border, YELLOW fill",
        font="Arial,Bold", border_color=[1.0, 0.0, 0.0], fill_color=[1.0, 1.0, 0.0])
    add("case-5", 2, [50, 110, 400, 140], "sdtm_mapping",
        "5: source-style ITALIC Arial -> Helvetica-Oblique; GREEN border",
        font="Arial,Italic", text_color=[0.0, 0.0, 1.0], border_color=[0.0, 0.6, 0.0])
    add("case-6", 2, [50, 160, 400, 190], "sdtm_mapping",
        "6: source-style BOLD-ITALIC Times -> Times-BoldItalic; ORANGE border, PINK fill",
        font="Times New Roman,BoldItalic", text_color=[0.4, 0.0, 0.4],
        border_color=[1.0, 0.55, 0.0], fill_color=[1.0, 0.75, 0.8])
    add("case-7", 2, [50, 210, 400, 240], "sdtm_mapping",
        "7: source-style REGULAR Courier -> Courier; BLACK border, no fill",
        font="Courier New", border_color=[0.0, 0.0, 0.0])

    profile = Profile(
        meta=ProfileMeta(name="kofax-manual-test"),
        domain_codes=["DM"],
        classification_rules=[
            ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping"),
        ],
        style_defaults=StyleDefaults(),
    )
    return annots, matches, profile


def _write_two_page_pdf(annots, matches, profile, output_path: Path) -> None:
    """Run write_annotations() twice (page 1 category-driven style, page 2 use_source_style)
    against a blank 2-page target, landing both pages in one final PDF."""
    target_pdf = output_path.with_name(output_path.stem + "_target.pdf")
    stage1_pdf = output_path.with_name(output_path.stem + "_stage1.pdf")

    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.new_page(width=612, height=792)
    doc.save(str(target_pdf))
    doc.close()

    page1_annots = [a for a in annots if a.page == 1]
    page1_matches = [m for m in matches if m.target_page == 1]
    write_annotations(target_pdf, stage1_pdf, page1_matches, page1_annots, profile)

    profile2 = profile.model_copy(update={
        "style_defaults": profile.style_defaults.model_copy(update={"use_source_style": True})
    })
    page2_annots = [a for a in annots if a.page == 2]
    page2_matches = [m for m in matches if m.target_page == 2]
    write_annotations(stage1_pdf, output_path, page2_matches, page2_annots, profile2)

    target_pdf.unlink(missing_ok=True)
    stage1_pdf.unlink(missing_ok=True)


def _strip_cl(pdf_path: Path) -> int:
    """Remove /CL (callout-line geometry) from every FreeText annotation, physically —
    not just logically. `xref_set_key(xref, "CL", "null")` does NOT delete the key: it
    writes a literal `/CL null` entry, which is spec-equivalent to absent for a compliant
    parser but is not the "physically absent" control this experiment needs, since Kofax's
    spec-compliance here is exactly what's under test. Also, saveIncr() would leave the
    original /CL array present as raw bytes in the prior incremental-update section
    regardless. Instead: rewrite each annotation's object text with the /CL line removed
    entirely via update_object(), then do a full (non-incremental) save with garbage
    collection so no stale revision data survives either.

    Verifies its own postcondition (raw bytes contain no "/CL" token) rather than
    silently trusting xref_get_keys(), since a PyMuPDF version change could someday stop
    emitting /CL at all and this function would otherwise report false success.
    """
    doc = fitz.open(str(pdf_path))
    patched = 0
    for page in doc:
        for annot in page.annots():
            text = doc.xref_object(annot.xref)
            new_text, n_subs = _CL_LINE_RE.subn("", text)
            if n_subs:
                doc.update_object(annot.xref, new_text)
                patched += 1

    tmp_path = pdf_path.with_suffix(".tmp.pdf")
    doc.save(str(tmp_path), garbage=4, deflate=True)
    doc.close()
    tmp_path.replace(pdf_path)

    raw = pdf_path.read_bytes()
    if b"/CL" in raw:
        raise RuntimeError(
            f"{pdf_path} still contains a literal /CL token after full rewrite — "
            "stripping did not physically remove the callout-line data."
        )
    return patched


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    annots, matches, profile = _build_cases()

    baseline_path = OUT_DIR / "kofax_test.pdf"
    _write_two_page_pdf(annots, matches, profile, baseline_path)
    print(f"Wrote baseline: {baseline_path}")

    variant_path = OUT_DIR / "kofax_test_no_cl.pdf"
    _write_two_page_pdf(annots, matches, profile, variant_path)
    patched = _strip_cl(variant_path)
    print(f"Wrote /CL-stripped variant: {variant_path} ({patched} annotations patched)")


if __name__ == "__main__":
    main()
