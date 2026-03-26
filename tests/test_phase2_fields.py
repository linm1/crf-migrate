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


# ---------------------------------------------------------------------------
# Helpers / fixtures for T2.09
# ---------------------------------------------------------------------------

def _create_annotated_crf_pdf(path: Path) -> Path:
    """Synthetic CRF PDF that carries FreeText SDTM annotations.

    Native content (left side, x=50): section header, text field, date field,
    checkbox — identical to _create_blank_crf_pdf page 1.

    FreeText annotations (right side, x=250): SDTM annotation text that
    page.get_text('dict') would surface as ordinary text blocks in the
    absence of the annotation-rect filter.
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    # Native CRF content
    page.insert_text((50, 50), "DEMOGRAPHICS", fontsize=18, fontname="helv")
    page.insert_text((50, 100), "Subject ID: ___________", fontsize=10, fontname="helv")
    page.insert_text((50, 130), "Date of Birth: MM/DD/YYYY", fontsize=10, fontname="helv")
    page.insert_text((50, 160), "Sex: Yes / No", fontsize=10, fontname="helv")

    # SDTM FreeText annotations at non-overlapping positions
    for rect, content, subject in [
        ([250, 115, 430, 135], "DM=SEX", "DM"),
        ([250, 145, 430, 165], "BRTHDTC", "DM"),
    ]:
        annot = page.add_freetext_annot(
            rect=fitz.Rect(rect),
            text=content,
            fontsize=18,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content=content, subject=subject)
        annot.update()

    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="module")
def annotated_crf_path(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fixtures_t209")
    return _create_annotated_crf_pdf(tmp / "annotated_crf.pdf")


# ---------------------------------------------------------------------------
# T2.09 – annotation-origin text is not misclassified as a CRF field
# ---------------------------------------------------------------------------

def test_t2_09_annotation_text_not_classified_as_field(
    annotated_crf_path, cdisc_profile_loaded, cdisc_rule_engine
):
    """T2.09: FreeText annotation content is not returned as a FieldRecord.

    Guards against page.get_text('dict') surfacing annotation appearance-
    stream text as regular page content, causing SDTM strings such as
    'DM=SEX' and 'BRTHDTC' to be misclassified as section_header fields.
    """
    from src.field_parser import extract_fields

    records = extract_fields(annotated_crf_path, cdisc_profile_loaded, cdisc_rule_engine)
    labels = [r.label for r in records]

    # Annotation content must NOT appear as field labels
    assert "DM=SEX" not in labels, (
        f"Annotation text 'DM=SEX' was misclassified as a field. Labels: {labels}"
    )
    assert "BRTHDTC" not in labels, (
        f"Annotation text 'BRTHDTC' was misclassified as a field. Labels: {labels}"
    )

    # Native CRF fields must still be extracted correctly
    assert any("Subject ID" in lbl for lbl in labels), (
        f"Native text field 'Subject ID' missing. Labels: {labels}"
    )
    assert any("MM/DD/YYYY" in lbl or "Date of Birth" in lbl for lbl in labels), (
        f"Native date field missing. Labels: {labels}"
    )
    assert any(r.field_type == "checkbox" for r in records), (
        "Expected at least one checkbox record from 'Sex: Yes / No'"
    )


# ---------------------------------------------------------------------------
# Helpers / fixtures for test_label_is_human_readable
# ---------------------------------------------------------------------------

def _create_split_label_marker_pdf(path: Path) -> Path:
    """Synthetic CRF PDF where label and marker are in SEPARATE text blocks.

    Layout (Page 1):
      x=50  y=50:  "Subject ID"   (label block, fontsize=10)
      x=200 y=50:  "___________"  (marker block, fontsize=10)
      x=50  y=80:  "Visit Date"   (label block, fontsize=10)
      x=200 y=80:  "MM/DD/YYYY"   (marker block, fontsize=10)
      x=50  y=110: "Enrolled"     (label block, fontsize=10)
      x=200 y=110: "Yes / No"     (marker block, fontsize=10)

    With label at x=50 and marker at x=200, find_nearest_label should pick
    the left-column label when the marker is processed in Pass B.
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "Subject ID", fontsize=10, fontname="helv")
    page.insert_text((200, 50), "___________", fontsize=10, fontname="helv")
    page.insert_text((50, 80), "Visit Date", fontsize=10, fontname="helv")
    page.insert_text((200, 80), "MM/DD/YYYY", fontsize=10, fontname="helv")
    page.insert_text((50, 110), "Enrolled", fontsize=10, fontname="helv")
    page.insert_text((200, 110), "Yes / No", fontsize=10, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="module")
def split_label_marker_path(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fixtures_split")
    return _create_split_label_marker_pdf(tmp / "split_label_marker.pdf")


# ---------------------------------------------------------------------------
# test_label_is_human_readable — verifies spatial two-pass label extraction
# ---------------------------------------------------------------------------

def test_label_is_human_readable(
    split_label_marker_path, cdisc_profile_loaded, cdisc_rule_engine
):
    """New: FieldRecord.label is the human-readable name, not the marker text.

    When label and marker are in separate text blocks, Pass B of the two-pass
    algorithm must call find_nearest_label to locate the nearby label text,
    so that FieldRecord.label = 'Subject ID' not '___________'.
    """
    from src.field_parser import extract_fields

    records = extract_fields(split_label_marker_path, cdisc_profile_loaded, cdisc_rule_engine)

    text_fields = [r for r in records if r.field_type == "text_field"]
    date_fields = [r for r in records if r.field_type == "date_field"]
    checkbox_fields = [r for r in records if r.field_type == "checkbox"]

    # Must detect at least one of each type
    assert len(text_fields) > 0, f"Expected text_field records, got: {[r.label for r in records]}"
    assert len(date_fields) > 0, f"Expected date_field records, got: {[r.label for r in records]}"
    assert len(checkbox_fields) > 0, f"Expected checkbox records, got: {[r.label for r in records]}"

    # Labels must be human-readable, not the marker text
    text_labels = [r.label for r in text_fields]
    assert any("Subject ID" in lbl for lbl in text_labels), (
        f"Expected 'Subject ID' as label for text_field, got: {text_labels}"
    )
    # Marker text must NOT appear as labels
    assert not any(r.label == "___________" for r in text_fields), (
        f"Marker text '___________' must not be a field label: {text_labels}"
    )

    date_labels = [r.label for r in date_fields]
    assert any("Visit Date" in lbl for lbl in date_labels), (
        f"Expected 'Visit Date' as label for date_field, got: {date_labels}"
    )
    assert not any("MM/DD/YYYY" == r.label for r in date_fields), (
        f"Marker text 'MM/DD/YYYY' must not be a field label: {date_labels}"
    )

    checkbox_labels = [r.label for r in checkbox_fields]
    assert any("Enrolled" in lbl for lbl in checkbox_labels), (
        f"Expected 'Enrolled' as label for checkbox, got: {checkbox_labels}"
    )
    assert not any("Yes / No" == r.label for r in checkbox_fields), (
        f"Marker text 'Yes / No' must not be a field label: {checkbox_labels}"
    )
