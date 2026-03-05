"""Tests for src/matcher.py — Phase 3 matching (T3.01–T3.10) and session match I/O."""
import json
import pytest
from pathlib import Path

from src.models import AnnotationRecord, FieldRecord, MatchRecord
from src.profile_models import (
    Profile, ProfileMeta, ClassificationRule, RuleCondition,
    MatchingConfig,
)
from src.matcher import (
    match_annotations,
    apply_manual_match,
    batch_approve_exact,
)
from src.session import Session
from src.csv_handler import export_matches_csv, import_matches_csv

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SOURCE_DIMS = {1: (595.0, 842.0)}
TARGET_DIMS = {1: (595.0, 842.0)}
TARGET_DIMS_SCALED = {1: (612.0, 792.0)}


def _make_profile(**matching_overrides) -> Profile:
    config = MatchingConfig(**matching_overrides)
    return Profile(
        meta=ProfileMeta(name="test"),
        domain_codes=["DM", "VS"],
        classification_rules=[
            ClassificationRule(
                conditions=RuleCondition(fallback=True),
                category="sdtm_mapping",
            )
        ],
        matching_config=config,
    )


@pytest.fixture
def default_profile() -> Profile:
    return _make_profile()


@pytest.fixture
def dm_annotation() -> AnnotationRecord:
    return AnnotationRecord(
        id="annot-001",
        page=1,
        content="BRTHDTC",
        domain="DM",
        category="sdtm_mapping",
        matched_rule="test",
        rect=[100.0, 90.0, 300.0, 110.0],
        anchor_text="Date of Birth",
        form_name="DEMOGRAPHICS",
    )


@pytest.fixture
def dm_field() -> FieldRecord:
    return FieldRecord(
        id="field-001",
        page=1,
        label="Date of Birth",
        form_name="DEMOGRAPHICS",
        rect=[50.0, 90.0, 200.0, 105.0],
        field_type="date_field",
    )


# ---------------------------------------------------------------------------
# T3.09 — MatchRecord JSON round-trip (field_id=None case)
# ---------------------------------------------------------------------------

class TestMatchRecordRoundTrip:
    def test_field_id_none_round_trips(self):
        """MatchRecord with field_id=None serializes and deserializes correctly."""
        record = MatchRecord(
            annotation_id="annot-999",
            field_id=None,
            match_type="unmatched",
            confidence=0.0,
            target_rect=[10.0, 20.0, 30.0, 40.0],
        )
        data = record.model_dump()
        assert data["field_id"] is None
        restored = MatchRecord.model_validate(data)
        assert restored.field_id is None
        assert restored.annotation_id == "annot-999"

    def test_field_id_present_round_trips(self):
        """MatchRecord with a field_id serializes and deserializes correctly."""
        record = MatchRecord(
            annotation_id="annot-001",
            field_id="field-001",
            match_type="exact",
            confidence=1.0,
            target_rect=[50.0, 90.0, 200.0, 105.0],
        )
        restored = MatchRecord.model_validate(record.model_dump())
        assert restored.field_id == "field-001"
        assert restored.match_type == "exact"


# ---------------------------------------------------------------------------
# Session save_matches / load_matches
# ---------------------------------------------------------------------------

class TestSessionMatchSaveLoad:
    def test_save_matches_creates_file(self, tmp_path):
        """save_matches writes matches.json to workspace."""
        session = Session(tmp_path)
        records = [
            MatchRecord(
                annotation_id="a1",
                field_id="f1",
                match_type="exact",
                confidence=1.0,
                target_rect=[0.0, 0.0, 100.0, 20.0],
            )
        ]
        path = session.save_matches(records)
        assert path.exists()
        assert path.name == "matches.json"

    def test_save_load_round_trip(self, tmp_path):
        """save_matches then load_matches preserves all data including field_id=None."""
        session = Session(tmp_path)
        records = [
            MatchRecord(
                annotation_id="a1",
                field_id="f1",
                match_type="exact",
                confidence=1.0,
                target_rect=[1.0, 2.0, 3.0, 4.0],
                status="approved",
            ),
            MatchRecord(
                annotation_id="a2",
                field_id=None,
                match_type="unmatched",
                confidence=0.0,
                target_rect=[5.0, 6.0, 7.0, 8.0],
            ),
        ]
        session.save_matches(records)
        loaded = session.load_matches()
        assert len(loaded) == 2
        assert loaded[0].annotation_id == "a1"
        assert loaded[0].field_id == "f1"
        assert loaded[0].status == "approved"
        assert loaded[1].annotation_id == "a2"
        assert loaded[1].field_id is None

    def test_load_matches_missing_file_raises(self, tmp_path):
        """load_matches raises FileNotFoundError when matches.json does not exist."""
        session = Session(tmp_path)
        with pytest.raises(FileNotFoundError, match="matches.json"):
            session.load_matches()


# ---------------------------------------------------------------------------
# T3.01 — Exact match
# ---------------------------------------------------------------------------

class TestExactMatch:
    def test_exact_match_same_form_and_label(self, dm_annotation, dm_field, default_profile):
        """Exact match: same form_name + identical anchor_text/label (case-insensitive)."""
        matches = match_annotations(
            [dm_annotation], [dm_field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        assert len(matches) == 1
        m = matches[0]
        assert m.annotation_id == "annot-001"
        assert m.field_id == "field-001"
        assert m.match_type == "exact"
        assert m.confidence == pytest.approx(1.0)
        assert m.target_rect == pytest.approx([50.0, 90.0, 200.0, 105.0])

    def test_exact_match_case_insensitive(self, dm_field, default_profile):
        """Exact match is case-insensitive for both form_name and anchor_text."""
        annot = AnnotationRecord(
            id="annot-ci",
            page=1,
            content="BRTHDTC",
            domain="DM",
            category="sdtm_mapping",
            matched_rule="test",
            rect=[100.0, 90.0, 300.0, 110.0],
            anchor_text="DATE OF BIRTH",
            form_name="demographics",
        )
        matches = match_annotations(
            [annot], [dm_field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        assert matches[0].match_type == "exact"

    def test_empty_annotations_returns_empty(self, dm_field, default_profile):
        """Empty annotation list returns empty result."""
        result = match_annotations([], [dm_field], default_profile, SOURCE_DIMS, TARGET_DIMS)
        assert result == []


# ---------------------------------------------------------------------------
# T3.03 — Unmatched: no field with matching form_name
# ---------------------------------------------------------------------------

class TestUnmatched:
    def test_no_matching_form_returns_unmatched(self, default_profile):
        """Annotation whose form_name is absent from all fields → match_type='unmatched'.

        Uses a unique anchor_text that won't fuzzy-match any field label,
        ensuring it falls all the way through to 'unmatched'.
        """
        annot = AnnotationRecord(
            id="annot-unm",
            page=1,
            content="BRTHDTC",
            domain="DM",
            category="sdtm_mapping",
            matched_rule="test",
            rect=[100.0, 90.0, 300.0, 110.0],
            anchor_text="QQQQ ZZZZ XXXX",  # won't fuzzy-match any label
            form_name="DEMOGRAPHICS",
        )
        field = FieldRecord(
            id="field-other",
            page=1,
            label="AAAA BBBB CCCC",  # completely different text
            form_name="VITALS",       # different form_name → position pass → unmatched
            rect=[50.0, 90.0, 200.0, 105.0],
            field_type="date_field",
        )
        matches = match_annotations(
            [annot], [field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        assert len(matches) == 1
        m = matches[0]
        assert m.match_type == "unmatched"
        assert m.field_id is None
        assert m.confidence == 0.0

    def test_empty_fields_returns_unmatched(self, dm_annotation, default_profile):
        """Annotation with no fields available → match_type='unmatched'."""
        matches = match_annotations(
            [dm_annotation], [], default_profile, SOURCE_DIMS, TARGET_DIMS
        )
        assert len(matches) == 1
        assert matches[0].match_type == "unmatched"


# ---------------------------------------------------------------------------
# T3.02 — Fuzzy threshold: just below vs. above
# ---------------------------------------------------------------------------

class TestFuzzyThreshold:
    def test_score_above_threshold_gives_fuzzy_match(self, default_profile):
        """Score >= fuzzy_same_form_threshold (0.80) → match_type='fuzzy'.

        'Date Birth' vs 'Date of Birth' scores ~87 with token_sort_ratio (above 80).
        """
        annot = AnnotationRecord(
            id="annot-fz1",
            page=1,
            content="VSTESTCD",
            domain="VS",
            category="sdtm_mapping",
            matched_rule="test",
            rect=[10.0, 10.0, 50.0, 20.0],
            anchor_text="Date Birth",       # token_sort_ratio vs "Date of Birth" ≈ 87
            form_name="DEMOGRAPHICS",
        )
        field = FieldRecord(
            id="field-fz1",
            page=1,
            label="Date of Birth",
            form_name="DEMOGRAPHICS",
            rect=[5.0, 10.0, 50.0, 18.0],
            field_type="date_field",
        )
        matches = match_annotations(
            [annot], [field], default_profile, SOURCE_DIMS, TARGET_DIMS,
        )
        assert matches[0].match_type == "fuzzy"
        assert matches[0].field_id == "field-fz1"

    def test_score_below_threshold_does_not_fuzzy_match(self, default_profile):
        """Score < fuzzy_same_form_threshold → no fuzzy match (falls to position/unmatched)."""
        # "AAAA" vs "ZZZZ" → token_sort_ratio will be 0
        annot = AnnotationRecord(
            id="annot-fz2",
            page=1,
            content="X",
            domain="DM",
            category="sdtm_mapping",
            matched_rule="test",
            rect=[10.0, 10.0, 50.0, 20.0],
            anchor_text="AAAA BBBB CCCC",
            form_name="DEMOGRAPHICS",
        )
        field = FieldRecord(
            id="field-fz2",
            page=1,
            label="ZZZZ YYYY XXXX",
            form_name="DEMOGRAPHICS",
            rect=[5.0, 10.0, 50.0, 18.0],
            field_type="text_field",
        )
        matches = match_annotations(
            [annot], [field], default_profile, SOURCE_DIMS, TARGET_DIMS,
        )
        # Should fall through to position_only (same form exists)
        assert matches[0].match_type != "exact"
        assert matches[0].match_type != "fuzzy"


# ---------------------------------------------------------------------------
# T3.04 — Position fallback: empty anchor_text + matching form_name
# ---------------------------------------------------------------------------

class TestPositionFallback:
    def test_empty_anchor_text_position_only(self, default_profile):
        """Empty anchor_text skips fuzzy passes and uses position_only."""
        annot = AnnotationRecord(
            id="annot-pos1",
            page=1,
            content="DOMAIN",
            domain="DM",
            category="sdtm_mapping",
            matched_rule="test",
            rect=[10.0, 10.0, 50.0, 20.0],
            anchor_text="",
            form_name="DEMOGRAPHICS",
        )
        field = FieldRecord(
            id="field-pos1",
            page=1,
            label="Date of Birth",
            form_name="DEMOGRAPHICS",
            rect=[5.0, 10.0, 50.0, 18.0],
            field_type="date_field",
        )
        matches = match_annotations(
            [annot], [field], default_profile, SOURCE_DIMS, TARGET_DIMS,
        )
        assert matches[0].match_type == "position_only"
        assert matches[0].field_id is None


# ---------------------------------------------------------------------------
# T3.06 — Scaling: different source and target page dimensions
# ---------------------------------------------------------------------------

class TestScaling:
    def test_scaled_rect_coordinates(self, default_profile):
        """Position fallback scales rect from source (595×842) to target (612×792)."""
        annot = AnnotationRecord(
            id="annot-scale",
            page=1,
            content="X",
            domain="DM",
            category="sdtm_mapping",
            matched_rule="test",
            rect=[100.0, 200.0, 300.0, 220.0],
            anchor_text="",
            form_name="DEMOGRAPHICS",
        )
        field = FieldRecord(
            id="field-scale",
            page=1,
            label="Something",
            form_name="DEMOGRAPHICS",
            rect=[0.0, 0.0, 10.0, 10.0],
            field_type="text_field",
        )
        matches = match_annotations(
            [annot], [field], default_profile,
            SOURCE_DIMS, TARGET_DIMS_SCALED,
        )
        m = matches[0]
        sx = 612.0 / 595.0
        sy = 792.0 / 842.0
        assert m.target_rect[0] == pytest.approx(100.0 * sx, abs=0.01)
        assert m.target_rect[1] == pytest.approx(200.0 * sy, abs=0.01)
        assert m.target_rect[2] == pytest.approx(300.0 * sx, abs=0.01)
        assert m.target_rect[3] == pytest.approx(220.0 * sy, abs=0.01)


# ---------------------------------------------------------------------------
# T3.05 — Domain label: absolute rect (no scaling)
# ---------------------------------------------------------------------------

class TestDomainLabel:
    def test_domain_label_uses_absolute_rect(self, default_profile):
        """category='domain_label' → target_rect == annotation.rect (no scaling)."""
        annot = AnnotationRecord(
            id="annot-dl",
            page=1,
            content="DM",
            domain="DM",
            category="domain_label",
            matched_rule="test",
            rect=[10.0, 10.0, 50.0, 20.0],
            anchor_text="",
            form_name="DEMOGRAPHICS",
        )
        field = FieldRecord(
            id="field-dl",
            page=1,
            label="Any Field",
            form_name="DEMOGRAPHICS",
            rect=[5.0, 10.0, 50.0, 18.0],
            field_type="text_field",
        )
        matches = match_annotations(
            [annot], [field], default_profile,
            SOURCE_DIMS, TARGET_DIMS_SCALED,  # different dims — should be ignored
        )
        m = matches[0]
        assert m.match_type == "position_only"
        assert m.target_rect == pytest.approx([10.0, 10.0, 50.0, 20.0], abs=0.001)


# ---------------------------------------------------------------------------
# T3.07 — apply_manual_match: immutability + field_id override
# ---------------------------------------------------------------------------

class TestApplyManualMatch:
    def test_updates_target_annotation(self, dm_annotation, dm_field, default_profile):
        """apply_manual_match updates the correct record."""
        matches = match_annotations(
            [dm_annotation], [dm_field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        new_rect = [1.0, 2.0, 3.0, 4.0]
        updated = apply_manual_match(matches, "annot-001", "field-NEW", new_rect)
        assert updated[0].field_id == "field-NEW"
        assert updated[0].match_type == "manual"
        assert updated[0].target_rect == new_rect
        assert updated[0].status == "approved"

    def test_returns_new_list_original_unchanged(self, dm_annotation, dm_field, default_profile):
        """apply_manual_match is immutable — original list is not modified."""
        matches = match_annotations(
            [dm_annotation], [dm_field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        original_field_id = matches[0].field_id
        apply_manual_match(matches, "annot-001", "field-NEW", [0.0, 0.0, 1.0, 1.0])
        assert matches[0].field_id == original_field_id

    def test_raises_value_error_if_not_found(self, dm_annotation, dm_field, default_profile):
        """apply_manual_match raises ValueError for unknown annotation_id."""
        matches = match_annotations(
            [dm_annotation], [dm_field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        with pytest.raises(ValueError, match="not found"):
            apply_manual_match(matches, "nonexistent-id", "f1", [0.0, 0.0, 1.0, 1.0])


# ---------------------------------------------------------------------------
# T3.08 — batch_approve_exact: only exact records get approved
# ---------------------------------------------------------------------------

class TestBatchApproveExact:
    def test_exact_records_get_approved(self, dm_annotation, dm_field, default_profile):
        """batch_approve_exact sets status='approved' for exact matches only."""
        matches = match_annotations(
            [dm_annotation], [dm_field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        assert matches[0].match_type == "exact"
        approved = batch_approve_exact(matches)
        assert approved[0].status == "approved"

    def test_non_exact_records_unchanged(self, default_profile):
        """batch_approve_exact leaves non-exact records at 'pending'."""
        records = [
            MatchRecord(
                annotation_id="a1",
                field_id=None,
                match_type="position_only",
                confidence=0.5,
                target_rect=[0.0, 0.0, 10.0, 10.0],
                status="pending",
            ),
            MatchRecord(
                annotation_id="a2",
                field_id="f2",
                match_type="exact",
                confidence=1.0,
                target_rect=[0.0, 0.0, 10.0, 10.0],
                status="pending",
            ),
        ]
        approved = batch_approve_exact(records)
        assert approved[0].status == "pending"
        assert approved[1].status == "approved"

    def test_returns_new_list_original_unchanged(self, default_profile):
        """batch_approve_exact is immutable — original list is not modified."""
        records = [
            MatchRecord(
                annotation_id="a1",
                field_id="f1",
                match_type="exact",
                confidence=1.0,
                target_rect=[0.0, 0.0, 10.0, 10.0],
                status="pending",
            )
        ]
        batch_approve_exact(records)
        assert records[0].status == "pending"


# ---------------------------------------------------------------------------
# T3.10 — CSV round-trip including field_id=None preservation
# ---------------------------------------------------------------------------

class TestCSVRoundTrip:
    def test_export_import_preserves_data(self, tmp_path):
        """CSV export then import preserves all match record fields."""
        records = [
            MatchRecord(
                annotation_id="a1",
                field_id="f1",
                match_type="exact",
                confidence=1.0,
                target_rect=[10.0, 20.0, 30.0, 40.0],
                status="approved",
                user_notes="checked",
            ),
            MatchRecord(
                annotation_id="a2",
                field_id=None,
                match_type="unmatched",
                confidence=0.0,
                target_rect=[5.0, 6.0, 7.0, 8.0],
            ),
        ]
        csv_path = tmp_path / "matches.csv"
        export_matches_csv(records, csv_path)
        updated, flagged = import_matches_csv(csv_path, records)
        assert len(updated) == 2
        assert len(flagged) == 0
        assert updated[0].field_id == "f1"
        assert updated[0].status == "approved"
        assert updated[0].user_notes == "checked"

    def test_field_id_none_preserved_through_csv(self, tmp_path):
        """field_id=None must NOT become the string 'None' after CSV round-trip."""
        records = [
            MatchRecord(
                annotation_id="a-none",
                field_id=None,
                match_type="position_only",
                confidence=0.5,
                target_rect=[0.0, 0.0, 100.0, 100.0],
            )
        ]
        csv_path = tmp_path / "matches.csv"
        export_matches_csv(records, csv_path)
        updated, _ = import_matches_csv(csv_path, records)
        assert updated[0].field_id is None

    def test_flagged_returns_missing_annotation_ids(self, tmp_path):
        """import_matches_csv flags existing records absent from the CSV."""
        records = [
            MatchRecord(
                annotation_id="a1",
                field_id="f1",
                match_type="exact",
                confidence=1.0,
                target_rect=[0.0, 0.0, 10.0, 10.0],
            ),
            MatchRecord(
                annotation_id="a2",
                field_id=None,
                match_type="unmatched",
                confidence=0.0,
                target_rect=[0.0, 0.0, 10.0, 10.0],
            ),
        ]
        # Export only a1
        csv_path = tmp_path / "matches_partial.csv"
        export_matches_csv(records[:1], csv_path)
        _, flagged = import_matches_csv(csv_path, records)
        assert "a2" in flagged

    def test_target_rect_preserved_as_list(self, tmp_path):
        """target_rect is deserialized back as a list of floats, not a string."""
        records = [
            MatchRecord(
                annotation_id="a1",
                field_id="f1",
                match_type="exact",
                confidence=1.0,
                target_rect=[11.1, 22.2, 33.3, 44.4],
            )
        ]
        csv_path = tmp_path / "matches.csv"
        export_matches_csv(records, csv_path)
        updated, _ = import_matches_csv(csv_path, records)
        assert isinstance(updated[0].target_rect, list)
        assert updated[0].target_rect == pytest.approx([11.1, 22.2, 33.3, 44.4], abs=0.001)
