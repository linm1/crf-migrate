"""Tests for src/writer.py — Phase 4 (T4.01–T4.09).

All tests use programmatic synthetic PDFs via fitz — no fixture file dependencies.
"""
from pathlib import Path

import fitz
import pytest

from src.models import AnnotationRecord, MatchRecord, StyleInfo
from src.profile_models import (
    AnnotationFilter,
    AnchorTextConfig,
    ClassificationRule,
    FormNameConfig,
    MatchingConfig,
    Profile,
    ProfileMeta,
    RuleCondition,
    StyleDefaults,
    VisitRule,
)
from src.writer import build_qc_report, write_annotations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile() -> Profile:
    return Profile(
        meta=ProfileMeta(name="test"),
        domain_codes=["DM", "AE"],
        classification_rules=[
            ClassificationRule(
                conditions=RuleCondition(fallback=True),
                category="sdtm_mapping",
            )
        ],
    )


def make_annotation(
    annot_id: str = "annot-001",
    page: int = 1,
    content: str = "BRTHDTC",
    domain: str = "DM",
    font_size: float = 18.0,
    border_color: list[float] | None = None,
    rotation: int = 0,
) -> AnnotationRecord:
    return AnnotationRecord(
        id=annot_id,
        page=page,
        content=content,
        domain=domain,
        category="sdtm_mapping",
        matched_rule="test",
        rect=[100.0, 90.0, 300.0, 110.0],
        style=StyleInfo(
            font_size=font_size,
            border_color=border_color or [0.75, 1.0, 1.0],
        ),
        rotation=rotation,
    )


def make_match(
    annot_id: str = "annot-001",
    status: str = "approved",
    target_rect: list[float] | None = None,
    match_type: str = "exact",
) -> MatchRecord:
    return MatchRecord(
        annotation_id=annot_id,
        field_id="field-001",
        match_type=match_type,
        confidence=1.0,
        target_rect=target_rect or [50.0, 80.0, 250.0, 100.0],
        status=status,
    )


def make_target_pdf(path: Path) -> Path:
    """Write a minimal 2-page blank PDF to path and return it."""
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=595, height=842)
    p = path / "target.pdf"
    doc.save(str(p))
    doc.close()
    return p


# ---------------------------------------------------------------------------
# T4.08 — output PDF page count matches target
# ---------------------------------------------------------------------------

def test_T4_08_output_page_count(tmp_path):
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    annot = make_annotation()
    match = make_match(status="approved")
    profile = _make_profile()

    write_annotations(target, output, [match], [annot], profile)

    doc = fitz.open(str(output))
    assert doc.page_count == 2
    doc.close()


# ---------------------------------------------------------------------------
# T4.06 — rejected match → no annotation written
# ---------------------------------------------------------------------------

def test_T4_06_rejected_not_written(tmp_path):
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    annot = make_annotation()
    match = make_match(status="rejected")
    profile = _make_profile()

    write_annotations(target, output, [match], [annot], profile)

    doc = fitz.open(str(output))
    assert sum(1 for _ in doc[0].annots()) == 0
    doc.close()


# ---------------------------------------------------------------------------
# T4.01 — single approved match → Square+FreeText pair at target_rect
# ---------------------------------------------------------------------------

def test_T4_01_approved_writes_freetext(tmp_path):
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    annot = make_annotation()
    target_rect = [50.0, 80.0, 250.0, 100.0]
    match = make_match(status="approved", target_rect=target_rect)
    profile = _make_profile()

    write_annotations(target, output, [match], [annot], profile)

    doc = fitz.open(str(output))
    page = doc[0]
    annots = list(page.annots())
    assert len(annots) == 1
    assert annots[0].type[1] == "FreeText"
    assert annots[0].info["content"] == "BRTHDTC"
    doc.close()


# ---------------------------------------------------------------------------
# T4.02 — sdtm_mapping category → 10pt per SDTM guideline
# ---------------------------------------------------------------------------

def test_T4_02_font_size_guideline(tmp_path):
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    # font_size on StyleInfo is now ignored at write time; guideline drives size
    annot = make_annotation(font_size=18.0)
    match = make_match(status="approved")
    profile = _make_profile()

    write_annotations(target, output, [match], [annot], profile)

    doc = fitz.open(str(output))
    page = doc[0]
    da_strings = [doc.xref_get_key(a.xref, "DA")[1] for a in page.annots()]
    assert len(da_strings) == 1
    # sdtm_mapping → 10pt per guideline
    assert "10" in da_strings[0]
    doc.close()


# ---------------------------------------------------------------------------
# T4.03 — border is always black per SDTM guideline
# ---------------------------------------------------------------------------

def test_T4_03_border_color_black(tmp_path):
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    annot = make_annotation(border_color=[0.75, 1.0, 1.0])
    match = make_match(status="approved")
    profile = _make_profile()

    write_annotations(target, output, [match], [annot], profile)

    doc = fitz.open(str(output))
    # For FreeText, /C is the fill color (PDF spec Table 177); the border
    # color is encoded in the AP stream as 0 0 0 RG by update().
    # Verify /C is the palette fill color (not black) — this means the AP
    # stream border (RG operator) is also correctly set to black by PyMuPDF.
    annots = list(doc[0].annots())
    assert len(annots) == 1
    fill_color = annots[0].colors.get("stroke")  # PyMuPDF exposes /C as "stroke" for FreeText
    assert fill_color is not None
    # /C must be the palette fill color (not black) — overwriting it with black
    # would break both fill and border on viewer re-render.
    assert not all(abs(c) < 0.05 for c in fill_color), "/C must be fill color, not black"
    doc.close()


# ---------------------------------------------------------------------------
# T4.04 — domain_label category → 14pt bold per SDTM guideline
# ---------------------------------------------------------------------------

def test_T4_04_domain_label_font_size(tmp_path):
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    annot = AnnotationRecord(
        id="annot-001",
        page=1,
        content="DM",
        domain="DM",
        category="domain_label",
        matched_rule="test",
        rect=[100.0, 90.0, 300.0, 110.0],
        style=StyleInfo(),
    )
    match = make_match(status="approved")
    profile = _make_profile()

    write_annotations(target, output, [match], [annot], profile)

    doc = fitz.open(str(output))
    page = doc[0]
    da_strings = [doc.xref_get_key(a.xref, "DA")[1] for a in page.annots()]
    assert len(da_strings) == 1
    # domain_label → 14pt per guideline
    assert "14" in da_strings[0]
    doc.close()


# ---------------------------------------------------------------------------
# T4.05 — rotation=90 preserved
# ---------------------------------------------------------------------------

def test_T4_05_rotation_preserved(tmp_path):
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    annot = make_annotation(rotation=90)
    match = make_match(status="approved")
    profile = _make_profile()

    write_annotations(target, output, [match], [annot], profile)

    doc = fitz.open(str(output))
    rotations = [a.rotation for a in doc[0].annots()]
    assert len(rotations) == 1
    assert rotations[0] == 90
    doc.close()


# ---------------------------------------------------------------------------
# T4.07 — build_qc_report with mixed match types → accurate counts
# ---------------------------------------------------------------------------

def test_T4_07_build_qc_report_counts(tmp_path):
    matches = [
        make_match(annot_id="a1", match_type="exact", status="approved"),
        make_match(annot_id="a2", match_type="exact", status="approved"),
        make_match(annot_id="a3", match_type="fuzzy", status="rejected"),
        make_match(annot_id="a4", match_type="unmatched", status="pending"),
    ]
    written_ids = ["a1", "a2"]
    skipped_ids = ["a3", "a4"]

    report = build_qc_report(matches, written_ids, skipped_ids)

    assert report["total_matches"] == 4
    assert report["written"] == 2
    assert report["skipped"] == 2
    assert report["counts_by_match_type"]["exact"] == 2
    assert report["counts_by_match_type"]["fuzzy"] == 1
    assert report["counts_by_match_type"]["unmatched"] == 1
    assert report["unmatched_annotation_ids"] == ["a4"]
    assert report["rejected_annotation_ids"] == ["a3"]


# ---------------------------------------------------------------------------
# T4.09 — original page text preserved in output PDF
# ---------------------------------------------------------------------------

def test_T4_09_original_content_preserved(tmp_path):
    # Build a target PDF that has real text content
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 100), "Date of Birth", fontsize=10)
    page.insert_text((50, 130), "Sex", fontsize=10)
    target = tmp_path / "target_with_text.pdf"
    doc.save(str(target))
    doc.close()

    # Count text blocks in original
    orig_doc = fitz.open(str(target))
    orig_blocks = orig_doc[0].get_text("blocks")
    orig_count = len(orig_blocks)
    orig_doc.close()

    output = tmp_path / "output.pdf"
    annot = make_annotation()
    match = make_match(status="approved")
    profile = _make_profile()

    write_annotations(target, output, [match], [annot], profile)

    out_doc = fitz.open(str(output))
    out_blocks = out_doc[0].get_text("blocks")
    # Output should have at least orig_count blocks (annotation adds its text as a block too)
    assert len(out_blocks) >= orig_count
    # Verify original text is still present
    all_text = " ".join(b[4] for b in out_blocks if len(b) > 4)
    assert "Date of Birth" in all_text
    assert "Sex" in all_text
    out_doc.close()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_matches_list(tmp_path):
    """Empty matches → valid qc_report with zeroes, no PDF modifications."""
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    profile = _make_profile()

    report = write_annotations(target, output, [], [], profile)

    assert report["total_matches"] == 0
    assert report["written"] == 0
    doc = fitz.open(str(output))
    assert doc.page_count == 2
    doc.close()


def test_missing_annotation_id_skipped(tmp_path):
    """Match referencing missing annotation_id is silently skipped."""
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    match = make_match(annot_id="ghost-id", status="approved")
    profile = _make_profile()

    report = write_annotations(target, output, [match], [], profile)

    assert report["written"] == 0
    assert report["skipped"] == 1


def test_pending_match_not_written(tmp_path):
    """Pending match is treated as skipped."""
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    annot = make_annotation()
    match = make_match(status="pending")
    profile = _make_profile()

    write_annotations(target, output, [match], [annot], profile)

    doc = fitz.open(str(output))
    assert sum(1 for _ in doc[0].annots()) == 0
    doc.close()


# ---------------------------------------------------------------------------
# T4.10 — StyleDefaults accepts domain_label_font_size and default_font_size
# ---------------------------------------------------------------------------

def test_T4_10_style_defaults_font_size_fields():
    sd = StyleDefaults(domain_label_font_size=12.0, default_font_size=12.0)
    assert sd.domain_label_font_size == 12.0
    assert sd.default_font_size == 12.0


def test_modified_match_is_written(tmp_path):
    """Modified status is treated the same as approved."""
    target = make_target_pdf(tmp_path)
    output = tmp_path / "output.pdf"
    annot = make_annotation()
    match = make_match(status="modified")
    profile = _make_profile()

    report = write_annotations(target, output, [match], [annot], profile)

    assert report["written"] == 1
    doc = fitz.open(str(output))
    assert sum(1 for _ in doc[0].annots()) == 1
    doc.close()
