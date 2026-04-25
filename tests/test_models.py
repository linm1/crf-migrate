"""Tests for src/models.py -- AnnotationRecord, FieldRecord, MatchRecord."""
import pytest
import uuid
from pydantic import ValidationError
from src.models import AnnotationRecord, FieldRecord, MatchRecord, StyleInfo


class TestStyleInfo:
    def test_default_values(self):
        style = StyleInfo()
        assert style.font == "Arial"
        assert style.font_size == 10.0
        assert style.text_color == [0.0, 0.0, 0.0]
        assert style.border_color == [0.0, 0.0, 0.0]
        assert style.fill_color is None
        assert style.border_width == 1.0
        assert style.border_dashes is None

    def test_custom_values(self):
        style = StyleInfo(font="Helvetica", font_size=12.0, text_color=[1.0, 0.0, 0.0])
        assert style.font == "Helvetica"
        assert style.font_size == 12.0


class TestStyleInfoFontFlags:
    def test_bold_italic_from_arial_bold_italic(self):
        s = StyleInfo(font="Arial,BoldItalic")
        assert s.is_bold is True
        assert s.is_italic is True

    def test_bold_italic_from_arial_bold_italic_mt(self):
        s = StyleInfo(font="Arial-BoldItalicMT")
        assert s.is_bold is True
        assert s.is_italic is True

    def test_plain_helvetica(self):
        s = StyleInfo(font="Helvetica")
        assert s.is_bold is False
        assert s.is_italic is False

    def test_bold_only(self):
        s = StyleInfo(font="Helvetica-Bold")
        assert s.is_bold is True
        assert s.is_italic is False

    def test_italic_only(self):
        s = StyleInfo(font="Helvetica-Oblique")
        assert s.is_bold is False
        assert s.is_italic is True

    def test_hebo_alias(self):
        s = StyleInfo(font="hebo")
        assert s.is_bold is True
        assert s.is_italic is False


class TestAnnotationRecord:
    def _make_record(self, **kwargs) -> AnnotationRecord:
        defaults = {
            "id": str(uuid.uuid4()),
            "page": 1,
            "content": "BRTHDTC",
            "domain": "DM",
            "category": "sdtm_mapping",
            "matched_rule": "Rule 8: fallback",
            "rect": [100.0, 200.0, 300.0, 220.0],
        }
        defaults.update(kwargs)
        return AnnotationRecord(**defaults)

    def test_create_minimal(self):
        record = self._make_record()
        assert record.content == "BRTHDTC"
        assert record.domain == "DM"
        assert record.category == "sdtm_mapping"

    def test_defaults(self):
        record = self._make_record()
        assert record.anchor_text == ""
        assert record.form_name == ""
        assert record.visit == ""
        assert record.rotation == 0
        assert isinstance(record.style, StyleInfo)

    def test_id_is_string(self):
        record = self._make_record()
        assert isinstance(record.id, str)

    def test_page_is_1_indexed(self):
        record = self._make_record(page=1)
        assert record.page == 1

    def test_rect_has_4_elements(self):
        record = self._make_record()
        assert len(record.rect) == 4

    def test_category_values(self):
        """All valid category values accepted."""
        for cat in ["domain_label", "sdtm_mapping", "not_submitted", "note", "_exclude"]:
            record = self._make_record(category=cat)
            assert record.category == cat

    def test_rotation_values(self):
        record = self._make_record(rotation=90)
        assert record.rotation == 90

    def test_serialization_round_trip(self):
        """model_dump() + model_validate() round-trip preserves all data."""
        original = self._make_record(
            anchor_text="Is age 18-85",
            form_name="ELIGIBILITY CRITERIA",
            visit="Screening",
        )
        data = original.model_dump()
        restored = AnnotationRecord.model_validate(data)
        assert restored.id == original.id
        assert restored.content == original.content
        assert restored.anchor_text == original.anchor_text
        assert restored.form_name == original.form_name
        assert restored.style.font == original.style.font


class TestFieldRecord:
    def _make_field(self, **kwargs) -> FieldRecord:
        defaults = {
            "id": str(uuid.uuid4()),
            "page": 2,
            "label": "Date of Birth",
            "rect": [50.0, 100.0, 250.0, 120.0],
            "field_type": "date_field",
        }
        defaults.update(kwargs)
        return FieldRecord(**defaults)

    def test_create_minimal(self):
        field = self._make_field()
        assert field.label == "Date of Birth"
        assert field.field_type == "date_field"

    def test_defaults(self):
        field = self._make_field()
        assert field.form_name == ""
        assert field.visit == ""

    def test_field_type_values(self):
        """All valid field_type values accepted."""
        for ft in ["text_field", "checkbox", "date_field", "table_row", "section_header"]:
            field = self._make_field(field_type=ft)
            assert field.field_type == ft


class TestMatchRecord:
    def _make_match(self, **kwargs) -> MatchRecord:
        defaults = {
            "annotation_id": str(uuid.uuid4()),
            "field_id": str(uuid.uuid4()),
            "match_type": "exact",
            "confidence": 1.0,
            "target_rect": [100.0, 200.0, 300.0, 220.0],
        }
        defaults.update(kwargs)
        return MatchRecord(**defaults)

    def test_create_minimal(self):
        match = self._make_match()
        assert match.match_type == "exact"
        assert match.confidence == 1.0
        assert match.status == "re-pairing"

    def test_field_id_can_be_none(self):
        """Unmatched annotations have null field_id."""
        match = self._make_match(field_id=None, match_type="unmatched", confidence=0.0)
        assert match.field_id is None

    def test_match_type_values(self):
        for mt in ["exact", "fuzzy", "position_only", "manual", "unmatched", "new"]:
            match = self._make_match(match_type=mt)
            assert match.match_type == mt

    def test_status_values(self):
        for s in ["pending", "approved", "re-pairing"]:
            match = self._make_match(status=s)
            assert match.status == s

    def test_confidence_range(self):
        match = self._make_match(confidence=0.75)
        assert 0.0 <= match.confidence <= 1.0

    def test_default_user_notes_empty(self):
        match = self._make_match()
        assert match.user_notes == ""
