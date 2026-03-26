"""Tests for src/extractor.py — T1.01 through T1.11.

T1.12-T1.15 (CSV round-trip) are covered in test_csv_handler.py.
T1.16 (re-classify) requires UI and is out of scope for unit tests.
"""
import re
import uuid
import pytest
from pathlib import Path

from src.models import AnnotationRecord
from src.rule_engine import RuleEngine
from src.extractor import extract_annotations
from src.profile_models import Profile, ProfileMeta, ClassificationRule, RuleCondition


class TestExtractAnnotations:
    def test_t1_01_returns_list_of_annotation_records(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """T1.01: extract_annotations returns a list of AnnotationRecord with non-empty content."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        assert isinstance(records, list)
        assert len(records) > 0
        assert all(isinstance(r, AnnotationRecord) for r in records)
        assert all(r.content for r in records)

    def test_t1_02_sticky_notes_excluded(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """T1.02: annotation_filter excludes non-FreeText annotations (sticky notes)."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        # None of the returned records should be from a Sticky Note
        assert all(r.domain != "Sticky Note" for r in records)
        # Verify by content — "Reviewer note" is the sticky note content
        assert all(r.content != "Reviewer note" for r in records)

    def test_t1_03_different_profiles_different_categories(self, sample_acrf_path, cdisc_profile):
        """T1.03: Different profiles produce different category assignments for edge cases."""
        all_note_profile = Profile(
            meta=ProfileMeta(name="All Note"),
            domain_codes=cdisc_profile.domain_codes,
            classification_rules=[
                ClassificationRule(
                    conditions=RuleCondition(fallback=True),
                    category="note",
                )
            ],
        )
        engine_note = RuleEngine(all_note_profile)
        engine_cdisc = RuleEngine(cdisc_profile)

        records_cdisc = extract_annotations(sample_acrf_path, cdisc_profile, engine_cdisc)
        records_note = extract_annotations(sample_acrf_path, all_note_profile, engine_note)

        # With all-note profile, everything should be "note"
        note_categories = {r.category for r in records_note}
        assert note_categories == {"note"}

        # With cdisc profile, we expect a mix including real categories
        cdisc_categories = {r.category for r in records_cdisc}
        assert "sdtm_mapping" in cdisc_categories or "domain_label" in cdisc_categories

    def test_t1_04_domain_from_subject_field(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """T1.04: Domain is extracted from the PDF annotation Subject field."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        dm_records = [r for r in records if r.domain == "DM"]
        vs_records = [r for r in records if r.domain == "VS"]
        ae_records = [r for r in records if r.domain == "AE"]
        assert len(dm_records) > 0
        assert len(vs_records) > 0
        assert len(ae_records) > 0

    def test_t1_05_extract_styling_from_da_string(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """T1.05: StyleInfo is populated for all annotations."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        assert all(r.style is not None for r in records)
        assert all(r.style.font_size > 0 for r in records)

    def test_t1_08_visit_extracted_via_profile_rules(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """T1.08: visit field populated from page text when a visit rule matches."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        # visit must be a string (empty string when no rule matches)
        assert all(isinstance(r.visit, str) for r in records)

    def test_t1_09_rotation_populated(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """T1.09: rotation field is populated (0 or other integer)."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        assert all(isinstance(r.rotation, int) for r in records)

    def test_t1_10_unique_uuids(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """T1.10: All extracted records have unique UUID4 ids."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        ids = [r.id for r in records]
        assert len(ids) == len(set(ids)), "Duplicate IDs found"
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            re.IGNORECASE,
        )
        for id_ in ids:
            assert uuid_pattern.match(id_), f"Invalid UUID4: {id_!r}"

    def test_t1_11_output_validates_against_schema(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """T1.11: Each record round-trips through AnnotationRecord schema without error."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        for record in records:
            data = record.model_dump()
            restored = AnnotationRecord.model_validate(data)
            assert restored.id == record.id

    def test_form_name_populated(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """form_name is a string on every record (may be empty if extraction rules don't match)."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        assert all(isinstance(r.form_name, str) for r in records)

    def test_rect_has_four_elements(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """rect is a list of exactly 4 floats."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        assert all(len(r.rect) == 4 for r in records)

    def test_page_numbers_are_1_indexed(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """Pages are 1-indexed integers (never 0)."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        assert all(r.page >= 1 for r in records)

    def test_multi_line_classified_as_note(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """Multi-line annotations (code lists) are classified as note by cdisc_standard."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        multi_line = [r for r in records if "\r" in r.content or "\n" in r.content]
        assert len(multi_line) >= 1, "Expected at least one multi-line annotation in fixture"
        for r in multi_line:
            assert r.category == "note", (
                f"Expected category='note' but got {r.category!r} for content: {r.content!r}"
            )

    def test_not_submitted_classified_correctly(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """[NOT SUBMITTED] annotations classified as not_submitted."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        not_submitted = [r for r in records if "[NOT SUBMITTED]" in r.content]
        assert len(not_submitted) >= 1, "Expected at least one [NOT SUBMITTED] annotation in fixture"
        for r in not_submitted:
            assert r.category == "not_submitted"

    def test_domain_label_classified_correctly(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """DM=Demographics-style annotations are classified as domain_label."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        domain_labels = [r for r in records if r.category == "domain_label"]
        assert len(domain_labels) >= 1

    def test_matched_rule_field_populated(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """matched_rule is a non-empty string for every extracted annotation."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        assert all(r.matched_rule for r in records)

    def test_note_starting_with_note_colon(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """Annotations starting with 'Note:' are classified as note."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        note_colon = [r for r in records if r.content.startswith("Note:")]
        assert len(note_colon) >= 1, "Expected at least one 'Note:' annotation in fixture"
        for r in note_colon:
            assert r.category == "note"

    def test_sdtm_mappings_present(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """Plain SDTM variable names (AETERM, BRTHDTC, etc.) are classified as sdtm_mapping."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        sdtm_mappings = [r for r in records if r.category == "sdtm_mapping"]
        assert len(sdtm_mappings) >= 1

    def test_returns_empty_list_for_pdf_with_no_freetext(self, tmp_path, cdisc_profile, cdisc_engine):
        """extract_annotations returns [] when the PDF contains no FreeText annotations."""
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), "No annotations here", fontsize=12, fontname="helv")
        pdf_path = tmp_path / "empty_annots.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert records == []

    def test_page_text_blocks_feed_visit_extraction(self, tmp_path, cdisc_profile, cdisc_engine):
        """When page contains 'Screening', the visit field is 'Screening' on all annotations."""
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), "Screening Visit", fontsize=14, fontname="helv")
        annot = page.add_freetext_annot(
            rect=fitz.Rect([200, 90, 400, 110]),
            text="DMDTC",
            fontsize=18,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content="DMDTC", subject="DM")
        annot.update()
        pdf_path = tmp_path / "screening.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) >= 1
        assert all(r.visit == "Screening" for r in records)

    def test_exclude_empty_annotation_content(self, tmp_path, cdisc_profile, cdisc_engine):
        """Annotations with empty content are excluded when annotation_filter.exclude_empty=True."""
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        annot = page.add_freetext_annot(
            rect=fitz.Rect([200, 90, 400, 110]),
            text="",
            fontsize=18,
            fontname="helv",
            text_color=(0, 0, 0),
        )
        # Force empty content via set_info
        annot.set_info(content="", subject="DM")
        annot.update()
        pdf_path = tmp_path / "empty_content.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        # No records with empty content should survive
        assert all(r.content != "" for r in records)

    def test_each_page_contributes_records(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """Annotations from all three pages appear in the output."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        pages_seen = {r.page for r in records}
        assert 1 in pages_seen
        assert 2 in pages_seen
        assert 3 in pages_seen

    def test_anchor_text_is_string(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """anchor_text field is always a string (may be empty)."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        assert all(isinstance(r.anchor_text, str) for r in records)

    def test_border_color_in_style(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """StyleInfo.border_color is a list of three floats."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        for r in records:
            assert isinstance(r.style.border_color, list)
            assert len(r.style.border_color) == 3
            assert all(isinstance(c, float) for c in r.style.border_color)

    def test_text_color_in_style(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """StyleInfo.text_color is a list of three floats."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        for r in records:
            assert isinstance(r.style.text_color, list)
            assert len(r.style.text_color) == 3

    def test_second_call_returns_same_count(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """extract_annotations is deterministic — two calls return the same number of records."""
        records1 = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        records2 = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        assert len(records1) == len(records2)

    def test_fill_color_extracted_from_freetext(self, tmp_path, cdisc_profile, cdisc_engine):
        """style.fill_color is populated from the annotation's C key (box background).

        PyMuPDF stores the FreeText background color in the PDF 'C' key, exposed as
        annot.colors['stroke']. This test verifies the extractor reads it correctly
        after a PDF round-trip (saving and re-opening is required for PyMuPDF to
        populate annot.colors from the persisted data).
        """
        import fitz
        fill = (0.75, 1.0, 1.0)
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        a = page.add_freetext_annot(
            fitz.Rect(50, 100, 300, 130),
            "DM=Demographics",
            fontsize=18,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=fill,
        )
        a.set_info(content="DM=Demographics", subject="DM")
        a.update()
        pdf_path = tmp_path / "fill_test.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) == 1
        fc = records[0].style.fill_color
        assert fc is not None, "fill_color should not be None after extraction"
        assert len(fc) == 3
        assert abs(fc[0] - fill[0]) < 0.02
        assert abs(fc[1] - fill[1]) < 0.02
        assert abs(fc[2] - fill[2]) < 0.02

    def test_da_string_font_extracted(self, tmp_path, cdisc_profile, cdisc_engine):
        """style.font and font_size are parsed from the DA string via xref lookup.

        annot.info['da'] is always empty for FreeText; the DA lives in the xref.
        """
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        a = page.add_freetext_annot(
            fitz.Rect(50, 50, 300, 80),
            "BRTHDTC",
            fontsize=14,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        a.set_info(content="BRTHDTC", subject="DM")
        a.update()
        pdf_path = tmp_path / "da_test.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) == 1
        assert records[0].style.font_size == 14.0


class TestGetTextBlocksAnnotFiltering:
    """Verify that _make_clean_page + _get_text_blocks cleanly separates SDTM annotation
    text from original CRF page text (form names, field labels, etc.).

    The copy-and-delete approach is used: a temporary annotation-free copy of each page
    is created via _make_clean_page(), then text is extracted from that clean copy.
    This is robust because PyMuPDF's own engine handles the association between
    annotations and their rendered text — no geometric heuristics needed.
    """

    def _make_pdf_with_annot(self, tmp_path, filename="annot_test.pdf"):
        """Create a minimal PDF with CRF text and a FreeText annotation, saved to disk."""
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=400, height=600)
        # Regular CRF page text (simulates a form title)
        page.insert_text((50, 50), "DEMOGRAPHICS", fontsize=18, fontname="helv")
        # FreeText annotation (simulates an SDTM annotation box)
        annot_rect = fitz.Rect(50, 100, 300, 130)
        annot = page.add_freetext_annot(
            annot_rect, "DM=Demographics",
            fontsize=12, fontname="helv",
            text_color=(0, 0, 0), fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content="DM=Demographics", subject="DM")
        annot.update()
        pdf_path = tmp_path / filename
        doc.save(str(pdf_path))
        doc.close()
        return fitz.open(str(pdf_path))

    def test_get_text_blocks_returns_crf_text(self, tmp_path):
        """_get_text_blocks on a raw page includes the original CRF text."""
        from src.extractor import _get_text_blocks
        doc = self._make_pdf_with_annot(tmp_path)
        page = doc[0]
        blocks = _get_text_blocks(page)
        texts = " ".join(b["text"] for b in blocks)
        assert "DEMOGRAPHICS" in texts, f"CRF text missing: {texts!r}"
        doc.close()

    def test_clean_page_excludes_annotation_text(self, tmp_path):
        """After _make_clean_page, FreeText annotation text is absent from the text stream."""
        from src.extractor import _make_clean_page, _get_text_blocks
        doc = self._make_pdf_with_annot(tmp_path)
        page = doc[0]
        temp_doc, clean_page = _make_clean_page(page)
        try:
            blocks = _get_text_blocks(clean_page)
            texts = " ".join(b["text"] for b in blocks)
            assert "DM=Demographics" not in texts, (
                f"Annotation text leaked into clean page blocks: {texts!r}"
            )
        finally:
            temp_doc.close()
        doc.close()

    def test_clean_page_preserves_crf_text(self, tmp_path):
        """After _make_clean_page, original CRF text (form name) is still present."""
        from src.extractor import _make_clean_page, _get_text_blocks
        doc = self._make_pdf_with_annot(tmp_path)
        page = doc[0]
        temp_doc, clean_page = _make_clean_page(page)
        try:
            blocks = _get_text_blocks(clean_page)
            texts = " ".join(b["text"] for b in blocks)
            assert "DEMOGRAPHICS" in texts, (
                f"CRF text was incorrectly removed from clean page: {texts!r}"
            )
        finally:
            temp_doc.close()
        doc.close()

    def test_adjacent_text_preserved_after_cleaning(self, tmp_path):
        """Text geometrically adjacent to (or under) an annotation box is preserved.

        This is the key regression test: 'Adverse Events' sits directly below
        the annotation boxes on the AE page. Previous geometric heuristics
        could incorrectly exclude it; the copy-and-delete approach cannot.
        """
        import fitz
        from src.extractor import _make_clean_page, _get_text_blocks

        doc = fitz.open()
        page = doc.new_page(width=400, height=600)
        # CRF form name text sits near/under the annotation
        page.insert_text((50, 135), "Adverse Events", fontsize=10, fontname="helv")
        # Annotation rect overlaps the y-range of the text
        annot_rect = fitz.Rect(50, 100, 300, 140)
        annot = page.add_freetext_annot(
            annot_rect, "AE=Adverse Events",
            fontsize=12, fontname="helv",
            text_color=(0, 0, 0), fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content="AE=Adverse Events", subject="AE")
        annot.update()
        pdf_path = tmp_path / "adjacent_text_test.pdf"
        doc.save(str(pdf_path))
        doc.close()

        doc2 = fitz.open(str(pdf_path))
        page2 = doc2[0]
        temp_doc, clean_page = _make_clean_page(page2)
        try:
            blocks = _get_text_blocks(clean_page)
            texts = " ".join(b["text"] for b in blocks)
            assert "Adverse Events" in texts, (
                f"'Adverse Events' adjacent to annotation was incorrectly excluded: {texts!r}"
            )
        finally:
            temp_doc.close()
        doc2.close()

    def test_make_clean_page_does_not_mutate_original(self, tmp_path):
        """_make_clean_page leaves the original page and its annotations intact."""
        from src.extractor import _make_clean_page
        doc = self._make_pdf_with_annot(tmp_path)
        page = doc[0]
        original_annot_count = sum(1 for _ in page.annots())
        assert original_annot_count >= 1, "Fixture must have at least one annotation"

        temp_doc, _ = _make_clean_page(page)
        temp_doc.close()

        after_count = sum(1 for _ in page.annots())
        assert after_count == original_annot_count, (
            f"Original page annotations changed: before={original_annot_count}, after={after_count}"
        )
        doc.close()
