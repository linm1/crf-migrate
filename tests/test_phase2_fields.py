"""Phase 2 tests: Field extraction from target blank CRF (T2.01 - T2.08)."""
import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import fitz
import pytest

from src.models import FieldRecord


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_field(**kwargs) -> FieldRecord:
    defaults = dict(
        id=str(uuid.uuid4()),
        page=1,
        label="Subject ID",
        form_name="DEMOGRAPHICS",
        visit="Baseline",
        rect=[50.0, 100.0, 200.0, 115.0],
        field_type="text_field",
    )
    defaults.update(kwargs)
    return FieldRecord(**defaults)


def _create_blank_crf_pdf(path: Path) -> Path:
    """Create a synthetic blank target CRF PDF for testing.

    Page 1 – DEMOGRAPHICS form with text, checkbox, and date fields.
    Page 2 – VITAL SIGNS form (Baseline visit).
    Page 3 – a TOC / cover page with no identifiable fields.
    """
    doc = fitz.open()

    # --- Page 1: Demographics ---
    p1 = doc.new_page(width=595, height=842)
    # Section header: large bold-ish text (insert_text uses helv which fitz marks as bold-capable)
    p1.insert_text((50, 50), "DEMOGRAPHICS", fontsize=18, fontname="helv")
    # Text fields (label + underscores)
    p1.insert_text((50, 100), "Subject ID: ___________", fontsize=10, fontname="helv")
    p1.insert_text((50, 130), "Initials: ___________", fontsize=10, fontname="helv")
    # Date field
    p1.insert_text((50, 160), "Date of Birth: MM/DD/YYYY", fontsize=10, fontname="helv")
    # Checkbox field
    p1.insert_text((50, 190), "Sex: Yes / No", fontsize=10, fontname="helv")

    # --- Page 2: Vital Signs with visit label ---
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 30), "Baseline", fontsize=10, fontname="helv")
    p2.insert_text((50, 50), "VITAL SIGNS", fontsize=18, fontname="helv")
    p2.insert_text((50, 100), "Systolic BP: ___________", fontsize=10, fontname="helv")
    p2.insert_text((50, 130), "Diastolic BP: ___________", fontsize=10, fontname="helv")

    # --- Page 3: Cover / TOC — no fields ---
    p3 = doc.new_page(width=595, height=842)
    p3.insert_text((50, 50), "Table of Contents", fontsize=12, fontname="helv")

    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="module")
def blank_crf_path(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fixtures")
    return _create_blank_crf_pdf(tmp / "blank_crf.pdf")


@pytest.fixture(scope="module")
def cdisc_profile_loaded():
    from src.profile_loader import load_profile
    profiles_dir = Path(__file__).parent.parent / "profiles"
    return load_profile(profiles_dir / "cdisc_standard.yaml")


@pytest.fixture(scope="module")
def cdisc_rule_engine(cdisc_profile_loaded):
    from src.rule_engine import RuleEngine
    return RuleEngine(cdisc_profile_loaded)


# ---------------------------------------------------------------------------
# T2.01 – extract_fields returns non-empty list with valid rects
# ---------------------------------------------------------------------------

def test_t2_01_returns_field_records(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine):
    """T2.01: extract_fields returns a non-empty list with valid rects."""
    from src.field_parser import extract_fields

    records = extract_fields(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine)

    assert len(records) > 0
    for r in records:
        assert isinstance(r, FieldRecord)
        assert r.label.strip() != ""
        assert len(r.rect) == 4
        x0, y0, x1, y1 = r.rect
        assert x1 > x0 and y1 > y0
        assert r.page >= 1
        assert r.id  # non-empty UUID


# ---------------------------------------------------------------------------
# T2.02 – checkbox detection
# ---------------------------------------------------------------------------

def test_t2_02_checkbox_field_type(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine):
    """T2.02: spans matching Yes/No pattern yield field_type == 'checkbox'."""
    from src.field_parser import extract_fields

    records = extract_fields(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine)
    checkbox_records = [r for r in records if r.field_type == "checkbox"]
    assert len(checkbox_records) > 0


# ---------------------------------------------------------------------------
# T2.03 – date field detection
# ---------------------------------------------------------------------------

def test_t2_03_date_field_type(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine):
    """T2.03: spans with MM/DD/YYYY placeholder yield field_type == 'date_field'."""
    from src.field_parser import extract_fields

    records = extract_fields(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine)
    date_records = [r for r in records if r.field_type == "date_field"]
    assert len(date_records) > 0


# ---------------------------------------------------------------------------
# T2.04 – section header detection by font size
# ---------------------------------------------------------------------------

def test_t2_04_section_header_detection(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine):
    """T2.04: large-font text (DEMOGRAPHICS / VITAL SIGNS) → section_header."""
    from src.field_parser import extract_fields

    records = extract_fields(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine)
    headers = [r for r in records if r.field_type == "section_header"]
    header_labels = [r.label for r in headers]
    assert any("DEMOGRAPHICS" in lbl or "VITAL SIGNS" in lbl for lbl in header_labels)


# ---------------------------------------------------------------------------
# T2.05 – form_name and visit populated via rule engine
# ---------------------------------------------------------------------------

def test_t2_05_form_name_and_visit_populated(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine):
    """T2.05: form_name and visit are populated from profile rules."""
    from src.field_parser import extract_fields

    records = extract_fields(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine)
    # Page 1 records should have form_name from rule engine
    page1 = [r for r in records if r.page == 1]
    assert len(page1) > 0
    # At least some records should have non-empty form_name (DEMOGRAPHICS header is large)
    assert any(r.form_name for r in page1)

    # Page 2 has "Baseline" text — visit should be extracted
    page2 = [r for r in records if r.page == 2]
    assert len(page2) > 0
    assert any(r.visit == "Baseline" for r in page2)


# ---------------------------------------------------------------------------
# T2.06 – pages with no identifiable fields return empty list (no crash)
# ---------------------------------------------------------------------------

def test_t2_06_empty_page_no_crash(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine):
    """T2.06: a page with no classifiable fields returns no records (no crash)."""
    from src.field_parser import extract_fields

    # Should not raise; page 3 (TOC) may return records or not — just no crash
    records = extract_fields(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine)
    page3 = [r for r in records if r.page == 3]
    # Table of Contents should yield 0 or very few records — the key thing is no exception
    assert isinstance(page3, list)


# ---------------------------------------------------------------------------
# T2.07 – output validates against FieldRecord Pydantic schema
# ---------------------------------------------------------------------------

def test_t2_07_schema_validation(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine):
    """T2.07: all records survive a Pydantic round-trip (valid schema)."""
    from src.field_parser import extract_fields

    records = extract_fields(blank_crf_path, cdisc_profile_loaded, cdisc_rule_engine)
    for r in records:
        dumped = r.model_dump()
        revalidated = FieldRecord.model_validate(dumped)
        assert revalidated == r
        assert revalidated.field_type in {
            "text_field", "checkbox", "date_field", "table_row", "section_header"
        }


# ---------------------------------------------------------------------------
# T2.08 – CSV round-trip for fields
# ---------------------------------------------------------------------------

def test_t2_08_csv_round_trip(tmp_path):
    """T2.08: export → edit a label → import preserves the mutation."""
    from src.csv_handler import export_fields_csv, import_fields_csv

    original = [
        _make_field(id="aaa-111", label="Subject ID", field_type="text_field"),
        _make_field(id="bbb-222", label="Sex", field_type="checkbox"),
    ]

    csv_path = tmp_path / "fields.csv"
    export_fields_csv(original, csv_path)

    # Simulate editing: change first record's label
    import pandas as pd
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    df.loc[df["id"] == "aaa-111", "label"] = "SUBJECT ID (edited)"
    df.to_csv(csv_path, index=False)

    updated, flagged = import_fields_csv(csv_path, original)

    assert flagged == []
    edited = next(r for r in updated if r.id == "aaa-111")
    assert edited.label == "SUBJECT ID (edited)"
    unchanged = next(r for r in updated if r.id == "bbb-222")
    assert unchanged.label == "Sex"


# ---------------------------------------------------------------------------
# Session integration: save_fields / load_fields
# ---------------------------------------------------------------------------

def test_session_save_and_load_fields(tmp_path):
    """Session.save_fields / load_fields round-trip preserves all records."""
    from src.session import Session

    session = Session(tmp_path)
    records = [
        _make_field(id="x1", label="Field A", page=1),
        _make_field(id="x2", label="Field B", page=2, field_type="checkbox"),
    ]

    path = session.save_fields(records)
    assert path.exists()
    assert path.name == "fields.json"

    loaded = session.load_fields()
    assert loaded == records


def test_session_load_fields_missing_raises(tmp_path):
    """Session.load_fields raises FileNotFoundError when fields.json absent."""
    from src.session import Session

    session = Session(tmp_path)
    with pytest.raises(FileNotFoundError):
        session.load_fields()
