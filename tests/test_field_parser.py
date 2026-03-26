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
        )
        assert len(records) == 1
        r = records[0]
        assert r.page == 3
        assert r.form_name == "Adverse Events"
        assert r.visit == "Baseline"
        assert r.field_type == "section_header"
        assert r.label == "My Section"
