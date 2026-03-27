"""Tests for field_parser.py — field extraction and _collect_section_headers."""
import uuid
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_engine():
    from src.profile_loader import load_profile
    from src.rule_engine import RuleEngine
    profile = load_profile(Path("profiles/cdisc_standard.yaml"))
    return RuleEngine(profile), profile


class TestEmptyFormNameSkip:
    """Pages with no recognizable form name should produce zero FieldRecords."""

    def test_codelist_page_returns_no_fields(self, tmp_path):
        """A PDF page where all candidate blocks are excluded produces no fields.

        All text on the page matches exclude_patterns so extract_form_name returns
        empty string and _process_page returns [] immediately.
        """
        import fitz
        from src.field_parser import extract_fields
        from src.profile_loader import load_profile
        from src.rule_engine import RuleEngine

        profile = load_profile(Path("profiles/cdisc_standard.yaml"))
        engine = RuleEngine(profile)

        # Build a page where every left-column block is excluded:
        # - "Codelist: ABNRMAL" matches ^Codelist\s*:
        # - "CDISC" matches ^CDISC$
        # Both blocks excluded → extract_form_name returns "" → _process_page returns []
        doc = fitz.open()
        page = doc.new_page(width=595, height=841)
        page.insert_text((78, 80), "Codelist: ABNRMAL", fontsize=7.3)
        page.insert_text((78, 100), "CDISC", fontsize=7.3)
        pdf_path = tmp_path / "codelist.pdf"
        doc.save(str(pdf_path))
        doc.close()

        fields = extract_fields(pdf_path, profile, engine)
        assert fields == [], f"Expected no fields, got {len(fields)}: {fields}"


class TestFormNameFromTopLeftField:
    """form_name must equal the label of the topmost-leftmost FieldRecord on the page.

    Regression: previously, form_name was derived from raw text blocks via
    extract_form_name() which was fragile and could return values from prior pages.
    Now form_name is taken directly from the extracted field with smallest (y0, x0).
    """

    def test_form_name_is_label_of_top_left_field(self, tmp_path):
        """form_name equals the label of the topmost-leftmost extracted field."""
        import fitz
        from src.field_parser import extract_fields
        from src.profile_loader import load_profile
        from src.rule_engine import RuleEngine

        profile = load_profile(Path("profiles/cdisc_standard.yaml"))
        engine = RuleEngine(profile)

        doc = fitz.open()
        page = doc.new_page(width=595, height=841)

        # Top-left text block — this should become the form_name
        page.insert_text((50, 40), "Coagulation (Local)", fontsize=12)

        # A field label + marker lower on the page
        page.insert_text((50, 120), "aPTT", fontsize=9)
        page.insert_text((200, 120), "DD/MON/YYYY", fontsize=9)

        pdf_path = tmp_path / "coag.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_fields(pdf_path, profile, engine)
        assert records, "Expected at least one field record"
        for r in records:
            assert r.form_name == "Coagulation (Local)", (
                f"Expected form_name='Coagulation (Local)', got '{r.form_name}'"
            )

    def test_form_name_independent_per_page(self, tmp_path):
        """Each page independently derives its own form_name from its top-left field.

        Regression: form_name from page N must NOT bleed into page N+1.
        """
        import fitz
        from src.field_parser import extract_fields
        from src.profile_loader import load_profile
        from src.rule_engine import RuleEngine

        profile = load_profile(Path("profiles/cdisc_standard.yaml"))
        engine = RuleEngine(profile)

        doc = fitz.open()

        # Page 1: Coagulation (Local)
        p1 = doc.new_page(width=595, height=841)
        p1.insert_text((50, 40), "Coagulation (Local)", fontsize=12)
        p1.insert_text((50, 120), "aPTT", fontsize=9)
        p1.insert_text((200, 120), "DD/MON/YYYY", fontsize=9)

        # Page 2: Hematology — different form, no mention of Coagulation
        p2 = doc.new_page(width=595, height=841)
        p2.insert_text((50, 40), "Hematology", fontsize=12)
        p2.insert_text((50, 120), "WBC", fontsize=9)
        p2.insert_text((200, 120), "DD/MON/YYYY", fontsize=9)

        pdf_path = tmp_path / "two_forms.pdf"
        doc.save(str(pdf_path))
        doc.close()

        records = extract_fields(pdf_path, profile, engine)

        page1_records = [r for r in records if r.page == 1]
        page2_records = [r for r in records if r.page == 2]

        assert page1_records, "Expected records on page 1"
        assert page2_records, "Expected records on page 2"

        for r in page1_records:
            assert r.form_name == "Coagulation (Local)", (
                f"Page 1: expected 'Coagulation (Local)', got '{r.form_name}'"
            )
        for r in page2_records:
            assert r.form_name == "Hematology", (
                f"Page 2: expected 'Hematology', got '{r.form_name}' — bled from page 1"
            )


class TestGetTextBlocksFreeTextOnlyFilter:
    """Regression: _get_text_blocks must filter only FreeText annotation rects.

    The previous code called get_annotation_rects(page) with no type filter,
    which collected ALL annotation rects including AcroForm Widget boxes.
    Widget rects overlap nearby label text, causing those labels to be silently
    excluded from text_blocks.  find_nearest_label() then fell back to wrong or
    stale labels from earlier pages.

    Fix: filter only FreeText annotation rects — Widget rects are ignored so
    that label text adjacent to form fields is preserved.  FreeText filtering is
    still required to prevent SDTM annotation appearance text from being
    misclassified as CRF fields (see test_t2_09 in test_phase2_fields.py).
    """

    def test_label_not_excluded_by_widget_rect_overlap(self, tmp_path):
        """Label text overlapping a non-FreeText annotation must still be extracted.

        Builds a PDF page where:
          - A FreeText annotation with empty text covers the label "Subject ID"
          - The label text is rendered as native page content
          - A date marker "DD/MON/YYYY" sits to the right

        The annotation here uses type FreeText but carries empty content, so its
        appearance stream produces no rendered text.  The label "Subject ID" is
        native page text that happens to spatially overlap the annotation rect.
        After the fix, the label must still be found and assigned to the field.
        """
        import fitz
        from src.field_parser import extract_fields
        from src.profile_loader import load_profile
        from src.rule_engine import RuleEngine

        profile = load_profile(Path("profiles/cdisc_standard.yaml"))
        engine = RuleEngine(profile)

        doc = fitz.open()
        page = doc.new_page(width=595, height=841)

        # Page title — needed for extract_form_name to succeed
        page.insert_text((50, 50), "Demographics", fontsize=14, fontname="helv")

        # Field label at (50, 120) — width ~80px
        page.insert_text((50, 120), "Subject ID", fontsize=9)

        # Date marker at (200, 120) — to the right of the label
        page.insert_text((200, 120), "DD/MON/YYYY", fontsize=9)

        # Non-FreeText annotation (simulating a Widget/highlight) overlapping the label
        # Use highlight which is not FreeText — its rect must NOT exclude the label
        hl = page.add_highlight_annot(fitz.Rect(45, 113, 130, 127))
        hl.update()

        pdf_path = tmp_path / "widget_overlap.pdf"
        doc.save(str(pdf_path))
        doc.close()

        fields = extract_fields(pdf_path, profile, engine)
        date_fields = [f for f in fields if f.field_type == "date_field"]
        assert date_fields, f"Expected a date_field, got: {fields}"
        assert date_fields[0].label == "Subject ID", (
            f"Expected label='Subject ID', got '{date_fields[0].label}'. "
            "Non-FreeText annotation rect is incorrectly excluding the label."
        )


class TestCollectSectionHeaders:
    def _make_block(self, text, bold, font_size, x0=78, y0=100):
        from src.rule_engine import TextBlock
        return TextBlock(text=text, bold=bold, font_size=font_size, rect=[x0, y0, x0 + 200, y0 + 10])

    def _make_engine(self):
        engine, _ = _load_engine()
        return engine

    def test_bold_block_becomes_section_header(self):
        """Bold block below min_font_size still qualifies as section header."""
        from src.field_parser import _collect_section_headers
        engine = self._make_engine()
        blocks = [self._make_block("Adverse Events", bold=True, font_size=7.3)]
        records = _collect_section_headers(
            blocks, page_num=2, form_name="Adverse Events", visit="",
            min_header_size=8.0, exclude_patterns=engine.anchor_exclude_patterns,
            page_width=595.0, page_height=841.0,
        )
        assert len(records) == 1
        assert records[0].label == "Adverse Events"
        assert records[0].field_type == "section_header"

    def test_footer_text_excluded_from_section_headers(self):
        """Footer-style text is filtered even if font_size is large."""
        from src.field_parser import _collect_section_headers
        engine = self._make_engine()
        blocks = [self._make_block(
            "August 07, 2024 10:53:29     Page 4 of 109", bold=False, font_size=10.0
        )]
        records = _collect_section_headers(
            blocks, page_num=4, form_name="Adverse Events", visit="",
            min_header_size=6.5, exclude_patterns=engine.anchor_exclude_patterns,
            page_width=595.0, page_height=841.0,
        )
        assert records == [], f"Expected no records, got {records}"

    def test_non_bold_small_font_excluded(self):
        """A non-bold block below min_header_size is not a section header."""
        from src.field_parser import _collect_section_headers
        engine = self._make_engine()
        blocks = [self._make_block("plain small text", bold=False, font_size=6.0)]
        records = _collect_section_headers(
            blocks, page_num=1, form_name="TestForm", visit="",
            min_header_size=8.0, exclude_patterns=engine.anchor_exclude_patterns,
            page_width=595.0, page_height=841.0,
        )
        assert records == []

    def test_large_font_non_bold_qualifies(self):
        """A large-font non-bold block qualifies as section header (font alone is sufficient)."""
        from src.field_parser import _collect_section_headers
        engine = self._make_engine()
        blocks = [self._make_block("BIG HEADER", bold=False, font_size=14.0)]
        records = _collect_section_headers(
            blocks, page_num=1, form_name="TestForm", visit="",
            min_header_size=8.0, exclude_patterns=engine.anchor_exclude_patterns,
            page_width=595.0, page_height=841.0,
        )
        assert len(records) == 1
        assert records[0].label == "BIG HEADER"

    def test_empty_blocks_returns_empty_list(self):
        """No blocks yields no section headers."""
        from src.field_parser import _collect_section_headers
        engine = self._make_engine()
        records = _collect_section_headers(
            [], page_num=1, form_name="TestForm", visit="",
            min_header_size=8.0, exclude_patterns=engine.anchor_exclude_patterns,
            page_width=595.0, page_height=841.0,
        )
        assert records == []

    def test_cdisc_text_excluded(self):
        """Block matching ^CDISC$ pattern is excluded from section headers."""
        from src.field_parser import _collect_section_headers
        engine = self._make_engine()
        blocks = [self._make_block("CDISC", bold=True, font_size=10.0)]
        records = _collect_section_headers(
            blocks, page_num=1, form_name="TestForm", visit="",
            min_header_size=8.0, exclude_patterns=engine.anchor_exclude_patterns,
            page_width=595.0, page_height=841.0,
        )
        assert records == []

    def test_page_number_pattern_excluded(self):
        """Block matching \\bPage \\d+ of \\d+\\b is excluded from section headers."""
        from src.field_parser import _collect_section_headers
        engine = self._make_engine()
        blocks = [self._make_block("Page 5 of 120", bold=False, font_size=12.0)]
        records = _collect_section_headers(
            blocks, page_num=5, form_name="TestForm", visit="",
            min_header_size=8.0, exclude_patterns=engine.anchor_exclude_patterns,
            page_width=595.0, page_height=841.0,
        )
        assert records == []

    def test_record_fields_populated_correctly(self):
        """Resulting FieldRecord has correct page, form_name, visit and field_type."""
        from src.field_parser import _collect_section_headers
        engine = self._make_engine()
        blocks = [self._make_block("My Section", bold=True, font_size=7.0)]
        records = _collect_section_headers(
            blocks, page_num=3, form_name="Adverse Events", visit="Baseline",
            min_header_size=8.0, exclude_patterns=engine.anchor_exclude_patterns,
            page_width=595.0, page_height=841.0,
        )
        assert len(records) == 1
        r = records[0]
        assert r.page == 3
        assert r.form_name == "Adverse Events"
        assert r.visit == "Baseline"
        assert r.field_type == "section_header"
        assert r.label == "My Section"
