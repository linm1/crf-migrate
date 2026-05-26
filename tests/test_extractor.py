"""Tests for src/extractor.py — T1.01 through T1.12.

T1.13-T1.15 (CSV round-trip) are covered in test_csv_handler.py.
T1.16 (re-classify) requires UI and is out of scope for unit tests.
"""
import re
import uuid
import warnings
import pytest
from pathlib import Path

from src.models import AnnotationRecord
from src.rule_engine import RuleEngine
from src.extractor import extract_annotations, _ANCHOR_CHECKBOX_RE
from src.pdf_utils import find_nearest_label
from src.profile_models import Profile, ProfileMeta, ClassificationRule, RuleCondition


pytestmark = pytest.mark.filterwarnings(
    "ignore:Duplicate annotations at page=.*:UserWarning"
)


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

    def test_t1_12_dedup_identical_rect(self, tmp_path, cdisc_profile, cdisc_engine):
        """T1.12: Duplicate annotations at the same page and rect are deduped with a warning."""
        import fitz

        expected_rect = [50.0, 50.0, 220.0, 90.0]
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)

        longer = page.add_freetext_annot(
            rect=fitz.Rect(expected_rect),
            text="BRTHDTC",
            fontsize=18,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        longer.set_info(content="BRTHDTC", subject="DM")
        longer.update()

        shorter = page.add_freetext_annot(
            rect=fitz.Rect(expected_rect),
            text="DM",
            fontsize=18,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        shorter.set_info(content="DM", subject="DM")
        shorter.update()

        pdf_path = tmp_path / "duplicate_identical_rect.pdf"
        doc.save(str(pdf_path))
        doc.close()

        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)

        duplicate_user_warnings = [
            warning
            for warning in caught_warnings
            if issubclass(warning.category, UserWarning)
            and "duplicate" in str(warning.message).lower()
        ]

        matching_records = [
            record
            for record in records
            if record.page == 1
            and all(abs(value - expected) < 0.01 for value, expected in zip(record.rect, expected_rect))
        ]

        assert len(matching_records) == 1
        assert matching_records[0].content == "BRTHDTC"
        assert duplicate_user_warnings

    def test_t1_12_dedup_highly_overlapped_rects(self, tmp_path, cdisc_profile, cdisc_engine):
        """T1.12: Near-identical overlapping annotations are deduped with a warning."""
        import fitz

        first_rect = [50.0, 50.0, 220.0, 90.0]
        second_rect = [50.3, 49.8, 219.7, 89.6]
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)

        first = page.add_freetext_annot(
            rect=fitz.Rect(first_rect),
            text="CO (Comments)",
            fontsize=12,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(1.0, 1.0, 0.58824),
        )
        first.set_info(content="CO (Comments)", subject="Text Box")
        first.update()

        second = page.add_freetext_annot(
            rect=fitz.Rect(second_rect),
            text="CO (Comments)",
            fontsize=12,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(1.0, 0.7451, 0.60785),
        )
        second.set_info(content="CO (Comments)", subject="Text Box")
        second.update()

        pdf_path = tmp_path / "duplicate_high_overlap_rects.pdf"
        doc.save(str(pdf_path))
        doc.close()

        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)

        duplicate_user_warnings = [
            warning
            for warning in caught_warnings
            if issubclass(warning.category, UserWarning)
            and "duplicate" in str(warning.message).lower()
        ]

        matching_records = [
            record
            for record in records
            if record.page == 1 and record.content == "CO (Comments)"
        ]

        assert len(matching_records) == 1
        assert duplicate_user_warnings

    def test_t1_12_dedup_session_like_high_overlap_rects(self, tmp_path, cdisc_profile, cdisc_engine):
        """T1.12: Page-13-style near overlays are deduped even when the overlap is not exact."""
        import fitz

        first_rect = [355.1947, 216.1858, 395.8142, 232.4336]
        second_rect = [355.2544, 215.4172, 396.0166, 231.8354]
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)

        first = page.add_freetext_annot(
            rect=fitz.Rect(first_rect),
            text="COVAL",
            fontsize=10,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(1.0, 1.0, 0.58824),
        )
        first.set_info(content="COVAL", subject="Text Box")
        first.update()

        second = page.add_freetext_annot(
            rect=fitz.Rect(second_rect),
            text="COVAL",
            fontsize=10,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(1.0, 0.7451, 0.60785),
        )
        second.set_info(content="COVAL", subject="Text Box")
        second.update()

        pdf_path = tmp_path / "duplicate_session_like_overlap_rects.pdf"
        doc.save(str(pdf_path))
        doc.close()

        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)

        duplicate_user_warnings = [
            warning
            for warning in caught_warnings
            if issubclass(warning.category, UserWarning)
            and "duplicate" in str(warning.message).lower()
        ]

        matching_records = [
            record
            for record in records
            if record.page == 1 and record.content == "COVAL"
        ]

        assert len(matching_records) == 1
        assert duplicate_user_warnings

    def test_t1_12_dedup_no_overlap_same_signature_kept(self, tmp_path, cdisc_profile, cdisc_engine):
        """T1.12: Same-signature annotations at distant rects are NOT deduped (singleton clusters)."""
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)

        # Two annotations: same content/subject, but rects far apart (no overlap)
        for rect, subject in [
            ([50.0, 50.0, 220.0, 70.0], "DM"),
            ([50.0, 700.0, 220.0, 720.0], "DM"),
        ]:
            a = page.add_freetext_annot(
                rect=fitz.Rect(rect),
                text="USUBJID",
                fontsize=12,
                fontname="helv",
                text_color=(0, 0, 0),
                fill_color=(0.75, 1.0, 1.0),
            )
            a.set_info(content="USUBJID", subject=subject)
            a.update()

        pdf_path = tmp_path / "no_overlap_same_sig.pdf"
        doc.save(str(pdf_path))
        doc.close()

        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)

        matching = [r for r in records if r.page == 1 and r.content == "USUBJID"]
        assert len(matching) == 2, (
            f"Distant same-signature annotations must both survive, got {len(matching)}"
        )
        dedup_warnings = [
            w for w in caught_warnings
            if issubclass(w.category, UserWarning) and "duplicate" in str(w.message).lower()
        ]
        assert not dedup_warnings, "No dedup warning expected for non-overlapping annotations"

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

    def test_border_color_not_derived_from_text_color(self, sample_acrf_path, cdisc_profile, cdisc_engine):
        """border_color must come from the AP stream RG operator, not text_color."""
        records = extract_annotations(sample_acrf_path, cdisc_profile, cdisc_engine)
        for r in records:
            # Structural check: border_color is always a 3-float list in [0,1] range.
            assert all(0.0 <= c <= 1.0 for c in r.style.border_color), (
                f"border_color out of [0,1] range: {r.style.border_color}"
            )
            # text_color being red ([1,0,0]) must NOT bleed into border_color
            if r.style.text_color == [1.0, 0.0, 0.0]:
                assert r.style.border_color != [1.0, 0.0, 0.0], (
                    f"Annotation '{r.content}': border_color was set to red — "
                    "border color must come from AP stream, not text_color"
                )

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

    def test_text_color_extracted_from_richtext_rc_when_da_is_black(
        self,
        tmp_path,
        cdisc_profile,
        cdisc_engine,
    ):
        """Rich-text color from RC overrides a stale black DA text color.

        Source aCRFs can store the visible text color in the RC XHTML/CSS while
        leaving /DA at black. The extractor should preserve the rendered source
        text color instead of defaulting to DA's black fallback.
        """
        import fitz

        rc_xml = (
            "<?xml version='1.0'?>"
            "<body xmlns='http://www.w3.org/1999/xhtml'>"
            "<p><span style='color:#FF0000'>[NOT SUBMITTED]</span></p>"
            "</body>"
        )

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        annot = page.add_freetext_annot(
            fitz.Rect(50, 50, 240, 90),
            rc_xml,
            fontsize=10,
            fontname="Helvetica",
            text_color=(1, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
            border_color=(0, 0, 0),
            richtext=True,
        )
        annot.set_info(content="[NOT SUBMITTED]", subject="SV")
        annot.update()

        doc.xref_set_key(annot.xref, "RC", fitz.get_pdf_str(rc_xml))
        doc.xref_set_key(annot.xref, "DA", "(0 0 0 rg /Arial,BoldItalic 10 Tf)")

        pdf_path = tmp_path / "richtext_red.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) == 1
        assert records[0].style.text_color == pytest.approx([1.0, 0.0, 0.0], abs=0.02)

    def test_text_color_uses_last_rc_color_declaration(
        self,
        tmp_path,
        cdisc_profile,
        cdisc_engine,
    ):
        """Later text-color declarations win over earlier defaults in RC XHTML."""
        import fitz

        rc_xml = (
            "<?xml version='1.0'?>"
            "<body xmlns='http://www.w3.org/1999/xhtml' style='color:rgb(0,0,0)'>"
            "<p><span style='background-color:#000000;color:#FF0000'>SVSTDTC/SVENDTC</span></p>"
            "</body>"
        )

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        annot = page.add_freetext_annot(
            fitz.Rect(50, 50, 260, 90),
            rc_xml,
            fontsize=10,
            fontname="Helvetica",
            text_color=(1, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
            border_color=(0, 0, 0),
            richtext=True,
        )
        annot.set_info(content="SVSTDTC/SVENDTC", subject="SV")
        annot.update()

        doc.xref_set_key(annot.xref, "RC", fitz.get_pdf_str(rc_xml))
        doc.xref_set_key(annot.xref, "DA", "(0 0 0 rg /Arial,BoldItalic 10 Tf)")

        pdf_path = tmp_path / "richtext_last_color_wins.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) == 1
        assert records[0].style.text_color == pytest.approx([1.0, 0.0, 0.0], abs=0.02)

    def test_rc_typography_bold_span_overrides_da_font(
        self,
        tmp_path,
        cdisc_profile,
        cdisc_engine,
    ):
        """RC span font-weight:bold and font-family override DA placeholder /Helv 12 Tf."""
        import fitz

        rc_xml = (
            '<?xml version="1.0"?>'
            '<body xmlns="http://www.w3.org/1999/xhtml"'
            ' style="font-size:12.00pt;font-family:\'Times New Roman\'">'
            '<p><span style="font-weight:bold;font-style:normal;font-family:\'Arial\'">'
            "FT (Functional Tests)"
            "</span></p></body>"
        )

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        annot = page.add_freetext_annot(
            fitz.Rect(50, 50, 300, 80),
            "FT (Functional Tests)",
            fontsize=12,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content="FT (Functional Tests)", subject="FT")
        annot.update()
        doc.xref_set_key(annot.xref, "RC", fitz.get_pdf_str(rc_xml))
        # DA size (14pt) differs from RC size (12pt) — proves RC wins over DA
        doc.xref_set_key(annot.xref, "DA", "(0 0 0 rg /Helv 14 Tf)")

        pdf_path = tmp_path / "rc_bold_span.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) == 1
        style = records[0].style
        assert style.font == "Arial,Bold", f"Expected Arial,Bold, got {style.font!r}"
        assert style.font_size == pytest.approx(12.0)

    def test_rc_typography_normal_weight_no_bold_suffix(
        self,
        tmp_path,
        cdisc_profile,
        cdisc_engine,
    ):
        """RC span with font-weight:normal produces font token without Bold suffix."""
        import fitz

        rc_xml = (
            '<?xml version="1.0"?>'
            '<body xmlns="http://www.w3.org/1999/xhtml">'
            '<p><span style="font-weight:normal;font-family:\'Arial\';font-size:10.00pt">'
            "FTCAT = 10-METER WALK/RUN"
            "</span></p></body>"
        )

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        annot = page.add_freetext_annot(
            fitz.Rect(50, 50, 300, 80),
            "FTCAT = 10-METER WALK/RUN",
            fontsize=12,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content="FTCAT = 10-METER WALK/RUN", subject="FT")
        annot.update()
        doc.xref_set_key(annot.xref, "RC", fitz.get_pdf_str(rc_xml))
        doc.xref_set_key(annot.xref, "DA", "(0 0 0 rg /Helv 12 Tf)")

        pdf_path = tmp_path / "rc_normal_weight.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) == 1
        style = records[0].style
        assert style.font == "Arial", f"Expected Arial, got {style.font!r}"
        assert style.font_size == pytest.approx(10.0)

    def test_rc_typography_body_size_span_family_cascade(
        self,
        tmp_path,
        cdisc_profile,
        cdisc_engine,
    ):
        """RC body provides font-size; span overrides font-family — both applied."""
        import fitz

        rc_xml = (
            '<?xml version="1.0"?>'
            '<body xmlns="http://www.w3.org/1999/xhtml"'
            ' style="font-size:10.00pt;font-family:\'Helvetica\'">'
            '<p><span style="font-family:\'Arial\'">'
            "DM (Demographics)"
            "</span></p></body>"
        )

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        annot = page.add_freetext_annot(
            fitz.Rect(50, 50, 300, 80),
            "DM (Demographics)",
            fontsize=12,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content="DM (Demographics)", subject="DM")
        annot.update()
        doc.xref_set_key(annot.xref, "RC", fitz.get_pdf_str(rc_xml))
        doc.xref_set_key(annot.xref, "DA", "(0 0 0 rg /Helv 12 Tf)")

        pdf_path = tmp_path / "rc_body_size_span_family.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) == 1
        style = records[0].style
        # span overrides body for family → Arial; body size → 10pt
        assert style.font == "Arial", f"Expected Arial, got {style.font!r}"
        assert style.font_size == pytest.approx(10.0)

    def test_rc_typography_absent_rc_falls_back_to_da(
        self,
        tmp_path,
        cdisc_profile,
        cdisc_engine,
    ):
        """When no RC is set, font name and size come from DA (existing behavior)."""
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        annot = page.add_freetext_annot(
            fitz.Rect(50, 50, 300, 80),
            "DM (Demographics)",
            fontsize=14,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content="DM (Demographics)", subject="DM")
        annot.update()
        # No RC set — DA is the only source

        pdf_path = tmp_path / "rc_absent_da_fallback.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) == 1
        style = records[0].style
        assert style.font_size == pytest.approx(14.0)

    def test_rc_typography_italic_span(
        self,
        tmp_path,
        cdisc_profile,
        cdisc_engine,
    ):
        """RC span with font-style:italic produces font token with Italic suffix."""
        import fitz

        rc_xml = (
            '<?xml version="1.0"?>'
            '<body xmlns="http://www.w3.org/1999/xhtml">'
            '<p><span style="font-style:italic;font-family:\'Arial\';font-size:10.00pt">'
            "See page 5"
            "</span></p></body>"
        )

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        annot = page.add_freetext_annot(
            fitz.Rect(50, 50, 300, 80),
            "See page 5",
            fontsize=12,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content="See page 5", subject="")
        annot.update()
        doc.xref_set_key(annot.xref, "RC", fitz.get_pdf_str(rc_xml))
        doc.xref_set_key(annot.xref, "DA", "(0 0 0 rg /Helv 12 Tf)")

        pdf_path = tmp_path / "rc_italic_span.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) == 1
        style = records[0].style
        assert style.font == "Arial,Italic", f"Expected Arial,Italic, got {style.font!r}"
        assert style.font_size == pytest.approx(10.0)

    def test_rc_typography_bold_italic_combined(
        self,
        tmp_path,
        cdisc_profile,
        cdisc_engine,
    ):
        """RC span with both font-weight:bold and font-style:italic produces BoldItalic token."""
        import fitz

        rc_xml = (
            '<?xml version="1.0"?>'
            '<body xmlns="http://www.w3.org/1999/xhtml">'
            '<p><span style="font-weight:bold;font-style:italic;font-family:\'Arial\';font-size:10.00pt">'
            "Note: See page 5"
            "</span></p></body>"
        )

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        annot = page.add_freetext_annot(
            fitz.Rect(50, 50, 300, 80),
            "Note: See page 5",
            fontsize=12,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content="Note: See page 5", subject="")
        annot.update()
        doc.xref_set_key(annot.xref, "RC", fitz.get_pdf_str(rc_xml))
        doc.xref_set_key(annot.xref, "DA", "(0 0 0 rg /Helv 12 Tf)")

        pdf_path = tmp_path / "rc_bold_italic.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_annotations(pdf_path, cdisc_profile, cdisc_engine)
        assert len(records) == 1
        style = records[0].style
        assert style.font == "Arial,BoldItalic", f"Expected Arial,BoldItalic, got {style.font!r}"
        assert style.font_size == pytest.approx(10.0)


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


# ---------------------------------------------------------------------------
# TestAnchorCheckboxExclusion — _ANCHOR_CHECKBOX_RE prevents checkbox option
# text from being selected as an annotation anchor.
# ---------------------------------------------------------------------------

def _tb(text: str, x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"text": text, "font_size": 10.0, "bold": False, "rect": [x0, y0, x1, y1]}


class TestAnchorCheckboxExclusion:
    """find_nearest_label skips checkbox option text when _ANCHOR_CHECKBOX_RE is excluded."""

    def _blocks_with_checkbox_nearest(self):
        # Annotation rect is at y=50–65.  "Yes" is at y=50 (same row — very close).
        # "Adverse Event" is at y=30 (slightly farther up).
        return [
            _tb("Yes", 5.0, 50.0, 30.0, 60.0),
            _tb("Adverse Event", 5.0, 30.0, 100.0, 42.0),
        ]

    def test_checkbox_text_skipped_as_anchor(self):
        marker_rect = [35.0, 50.0, 150.0, 65.0]
        blocks = self._blocks_with_checkbox_nearest()
        text, _ = find_nearest_label(
            marker_rect, blocks, left_column_tolerance_px=200.0,
            exclude_patterns=[_ANCHOR_CHECKBOX_RE],
        )
        assert text == "Adverse Event"

    def test_checkbox_symbol_skipped_as_anchor(self):
        marker_rect = [35.0, 50.0, 150.0, 65.0]
        blocks = [
            _tb("☐", 5.0, 50.0, 20.0, 60.0),
            _tb("Subject ID", 5.0, 30.0, 90.0, 42.0),
        ]
        text, _ = find_nearest_label(
            marker_rect, blocks, left_column_tolerance_px=200.0,
            exclude_patterns=[_ANCHOR_CHECKBOX_RE],
        )
        assert text == "Subject ID"

    def test_without_exclusion_checkbox_would_win(self):
        """Control: without the exclude pattern, 'Yes' (nearest) is selected."""
        marker_rect = [35.0, 50.0, 150.0, 65.0]
        blocks = self._blocks_with_checkbox_nearest()
        text, _ = find_nearest_label(
            marker_rect, blocks, left_column_tolerance_px=200.0,
            exclude_patterns=[],
        )
        assert text == "Yes"
