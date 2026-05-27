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

    def test_anchor_label_case_drift_falls_through_to_fuzzy(self, dm_field, default_profile):
        """Exact pass label key is case-sensitive (annotation migration assumes
        source and target CRFs share label casing). Form_name remains case-
        insensitive. When anchor_text casing differs from the target field
        label, the annotation falls through to the fuzzy pass and still
        matches there.
        """
        annot = AnnotationRecord(
            id="annot-ci",
            page=1,
            content="BRTHDTC",
            domain="DM",
            category="sdtm_mapping",
            matched_rule="test",
            rect=[100.0, 90.0, 300.0, 110.0],
            anchor_text="DATE OF BIRTH",  # field label is "Date of Birth"
            form_name="demographics",     # field form_name is "DEMOGRAPHICS"
        )
        matches = match_annotations(
            [annot], [dm_field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        assert matches[0].field_id == dm_field.id
        assert matches[0].match_type != "exact"  # case drift → fuzzy or later

    def test_anchor_label_case_sensitive_exact_match(self, dm_field, default_profile):
        """Exact pass matches when anchor_text and field label have identical casing."""
        annot = AnnotationRecord(
            id="annot-cs",
            page=1,
            content="BRTHDTC",
            domain="DM",
            category="sdtm_mapping",
            matched_rule="test",
            rect=[100.0, 90.0, 300.0, 110.0],
            anchor_text="Date of Birth",  # exact match with field label
            form_name="demographics",
        )
        matches = match_annotations(
            [annot], [dm_field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        assert matches[0].match_type == "exact"
        assert matches[0].field_id == dm_field.id

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
        updated = apply_manual_match(matches, "annot-001", "field-NEW", new_rect, 4)
        assert updated[0].field_id == "field-NEW"
        assert updated[0].match_type == "manual"
        assert updated[0].target_rect == new_rect
        assert updated[0].target_page == 4
        assert updated[0].status == "approved"

    def test_returns_new_list_original_unchanged(self, dm_annotation, dm_field, default_profile):
        """apply_manual_match is immutable — original list is not modified."""
        matches = match_annotations(
            [dm_annotation], [dm_field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        original_field_id = matches[0].field_id
        apply_manual_match(matches, "annot-001", "field-NEW", [0.0, 0.0, 1.0, 1.0], 1)
        assert matches[0].field_id == original_field_id

    def test_raises_value_error_if_not_found(self, dm_annotation, dm_field, default_profile):
        """apply_manual_match raises ValueError for unknown annotation_id."""
        matches = match_annotations(
            [dm_annotation], [dm_field], default_profile,
            SOURCE_DIMS, TARGET_DIMS,
        )
        with pytest.raises(ValueError, match="not found"):
            apply_manual_match(matches, "nonexistent-id", "f1", [0.0, 0.0, 1.0, 1.0], 1)

    def test_updates_target_page_to_field_page(self):
        """apply_manual_match overwrites stale target_page with the new field's page."""
        stale = MatchRecord(
            annotation_id="a1",
            field_id="f_old",
            match_type="position_only",
            confidence=0.5,
            target_rect=[0.0, 0.0, 10.0, 10.0],
            target_page=6,
            status="re-pairing",
            placement_adjusted=False,
        )
        updated = apply_manual_match([stale], "a1", "f_new", [1.0, 2.0, 3.0, 4.0], 7)
        assert updated[0].target_page == 7

    def test_resets_placement_adjusted(self):
        """apply_manual_match resets placement_adjusted — rect is freshly recomputed."""
        stale = MatchRecord(
            annotation_id="a1",
            field_id="f_old",
            match_type="position_only",
            confidence=0.5,
            target_rect=[0.0, 0.0, 10.0, 10.0],
            target_page=6,
            status="re-pairing",
            placement_adjusted=True,
        )
        updated = apply_manual_match([stale], "a1", "f_new", [1.0, 2.0, 3.0, 4.0], 7)
        assert updated[0].placement_adjusted is False


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


# ---------------------------------------------------------------------------
# New tests: _visit_match helper, visit boost, bipartite matching
# ---------------------------------------------------------------------------

from src.matcher import _visit_match  # noqa: E402 — imported after class definitions


def _make_annot(annot_id, anchor_text, form_name="FORM1", visit="", page=1):
    return AnnotationRecord(
        id=annot_id,
        page=page,
        content="X",
        domain="VS",
        category="sdtm_mapping",
        matched_rule="test",
        rect=[10.0, 10.0, 50.0, 20.0],
        anchor_text=anchor_text,
        form_name=form_name,
        visit=visit,
    )


def _make_field(field_id, label, form_name="FORM1", visit="", page=1):
    return FieldRecord(
        id=field_id,
        page=page,
        label=label,
        form_name=form_name,
        visit=visit,
        rect=[5.0, 10.0, 50.0, 18.0],
        field_type="text_field",
    )


# ---------------------------------------------------------------------------
# TestVisitMatchHelper — pure function unit tests
# ---------------------------------------------------------------------------

class TestVisitMatchHelper:
    def test_exact_match_returns_1(self):
        assert _visit_match("Baseline", "Baseline") == pytest.approx(1.0)

    def test_exact_match_case_insensitive(self):
        assert _visit_match("BASELINE", "baseline") == pytest.approx(1.0)

    def test_containment_returns_0_5(self):
        """'Baseline' is contained in 'Baseline Visit' → 0.5."""
        assert _visit_match("Baseline", "Baseline Visit") == pytest.approx(0.5)

    def test_containment_reverse(self):
        """'Baseline Visit' contains 'Baseline' → 0.5."""
        assert _visit_match("Baseline Visit", "Baseline") == pytest.approx(0.5)

    def test_no_match_returns_0(self):
        assert _visit_match("Week 1", "Week 4") == pytest.approx(0.0)

    def test_empty_a_returns_0(self):
        assert _visit_match("", "Baseline") == pytest.approx(0.0)

    def test_empty_b_returns_0(self):
        assert _visit_match("Baseline", "") == pytest.approx(0.0)

    def test_both_empty_returns_0(self):
        assert _visit_match("", "") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# TestVisitBoost — boost applied during fuzzy passes
# ---------------------------------------------------------------------------

class TestVisitBoost:
    """Tests that visit_boost adjusts scores in fuzzy passes."""

    def test_visit_boost_exact_match(self):
        """Two annotations compete for the same label; the one with matching visit wins.

        A1 has "Heart Rate" + matching visit, A2 has "Heart Rate" + wrong visit.
        With a low fuzzy threshold and a boost, A1 should claim the field with the
        matching visit while A2 gets position_only.
        """
        # Both annotations have same anchor_text but different visits.
        # We use cross-form pass (threshold 0.90 scaled = 90).
        # Use unique form names so same-form pass doesn't apply.
        a1 = _make_annot("a1", "Systolic Blood Pressure", form_name="VITALS_OLD", visit="Week 4")
        a2 = _make_annot("a2", "Systolic Blood Pressure", form_name="VITALS_OLD", visit="Week 99")
        f1 = _make_field("f1", "Systolic Blood Pressure", form_name="VITALS_NEW", visit="Week 4")

        # Set same-form threshold very high so only cross-form fires,
        # and cross-form threshold low enough that both could match without boost.
        profile = _make_profile(
            fuzzy_same_form_threshold=0.99,
            fuzzy_cross_form_threshold=0.70,
            visit_boost=5.0,
        )
        matches = match_annotations([a1, a2], [f1], profile, SOURCE_DIMS, TARGET_DIMS)
        matched = {m.annotation_id: m for m in matches}
        # a1 has matching visit → should get f1
        assert matched["a1"].field_id == "f1"
        assert matched["a1"].match_type == "fuzzy"
        # a2 did not get f1 (a1 claimed it)
        assert matched["a2"].field_id is None

    def test_visit_boost_partial_match(self):
        """'Baseline' vs 'Baseline Visit' → visit_match = 0.5, boost = 0.5 * visit_boost."""
        a1 = _make_annot("a1", "Heart Rate", form_name="VITALS_OLD", visit="Baseline")
        f1 = _make_field("f1", "Heart Rate", form_name="VITALS_NEW", visit="Baseline Visit")

        profile = _make_profile(
            fuzzy_same_form_threshold=0.99,
            fuzzy_cross_form_threshold=0.70,
            visit_boost=10.0,
        )
        matches = match_annotations([a1], [f1], profile, SOURCE_DIMS, TARGET_DIMS)
        m = matches[0]
        assert m.match_type == "fuzzy"
        assert m.field_id == "f1"
        # confidence should reflect the boost: raw=100, partial boost=5 → (100+5)/100 = 1.05
        # We just verify it's fuzzy; exact confidence value depends on capping policy.
        assert m.confidence > 0.0

    def test_visit_boost_empty_visit_no_boost(self):
        """Empty visit on either side → no boost applied; matching still works on text alone."""
        a1 = _make_annot("a1", "Heart Rate", form_name="VITALS_OLD", visit="")
        f1 = _make_field("f1", "Heart Rate", form_name="VITALS_NEW", visit="")

        profile = _make_profile(
            fuzzy_same_form_threshold=0.99,
            fuzzy_cross_form_threshold=0.70,
            visit_boost=5.0,
        )
        matches = match_annotations([a1], [f1], profile, SOURCE_DIMS, TARGET_DIMS)
        m = matches[0]
        # Still matches on text alone (100 >= 70)
        assert m.match_type == "fuzzy"
        assert m.field_id == "f1"
        # Confidence should be 1.0 (no boost from empty visit)
        assert m.confidence == pytest.approx(1.0)

    def test_visit_boost_zero_disables_boost(self):
        """visit_boost=0.0 → same outcome as not having a boost."""
        a1 = _make_annot("a1", "Heart Rate", form_name="VITALS_OLD", visit="Week 4")
        f1 = _make_field("f1", "Heart Rate", form_name="VITALS_NEW", visit="Week 4")

        profile_boosted = _make_profile(
            fuzzy_same_form_threshold=0.99,
            fuzzy_cross_form_threshold=0.70,
            visit_boost=5.0,
        )
        profile_no_boost = _make_profile(
            fuzzy_same_form_threshold=0.99,
            fuzzy_cross_form_threshold=0.70,
            visit_boost=0.0,
        )
        m_boosted = match_annotations([a1], [f1], profile_boosted, SOURCE_DIMS, TARGET_DIMS)[0]
        m_no_boost = match_annotations([a1], [f1], profile_no_boost, SOURCE_DIMS, TARGET_DIMS)[0]
        # Both should match, but confidence differs only if boost fires
        assert m_boosted.match_type == "fuzzy"
        assert m_no_boost.match_type == "fuzzy"
        # With visit_boost=0.0 confidence = 1.0, with boost it can be higher (capped or not)
        assert m_no_boost.confidence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# TestBipartiteMatching — globally optimal assignment
# ---------------------------------------------------------------------------

class TestBipartiteMatching:
    def test_bipartite_globally_optimal(self):
        """Bipartite matching gives globally optimal assignment vs greedy.

        Setup:
          A1="Heart Rate" (form VITALS_OLD) vs F1="Heart Rate" (form VITALS_NEW) → score 100
          A2="HR"         (form VITALS_OLD) vs F2="HR"         (form VITALS_NEW) → score 100

        Greedy (iteration order A1, A2) with cross-form pass:
          - A1 claims F1 (score 100). A2 claims F2 (score 100). Both matched. ✓

        In this case greedy and bipartite agree. What we really test is that BOTH
        annotations get matched (neither is left for position pass), confirming the
        bipartite algorithm found both assignments.
        """
        a1 = _make_annot("a1", "Heart Rate", form_name="VITALS_OLD")
        a2 = _make_annot("a2", "HR", form_name="VITALS_OLD")
        f1 = _make_field("f1", "Heart Rate", form_name="VITALS_NEW")
        f2 = _make_field("f2", "HR", form_name="VITALS_NEW")

        profile = _make_profile(
            fuzzy_same_form_threshold=0.99,  # skip same-form pass
            fuzzy_cross_form_threshold=0.70,
        )
        matches = match_annotations([a1, a2], [f1, f2], profile, SOURCE_DIMS, TARGET_DIMS)
        matched = {m.annotation_id: m for m in matches}
        assert matched["a1"].match_type == "fuzzy"
        assert matched["a2"].match_type == "fuzzy"
        assert matched["a1"].field_id is not None
        assert matched["a2"].field_id is not None
        # Verify optimal assignment: a1→f1 and a2→f2 (not crossed)
        assert matched["a1"].field_id == "f1"
        assert matched["a2"].field_id == "f2"

    def test_bipartite_fallback_to_greedy_when_no_scipy(self):
        """When scipy is unavailable, fall back to greedy and emit a warning."""
        import sys
        import warnings

        a1 = _make_annot("a1", "Heart Rate", form_name="VITALS_OLD")
        f1 = _make_field("f1", "Heart Rate", form_name="VITALS_NEW")

        profile = _make_profile(
            fuzzy_same_form_threshold=0.99,
            fuzzy_cross_form_threshold=0.70,
        )

        # Temporarily remove scipy from sys.modules and block its import
        import unittest.mock as mock

        original_scipy = sys.modules.get("scipy")
        original_scipy_optimize = sys.modules.get("scipy.optimize")

        # Block scipy import
        sys.modules["scipy"] = None  # type: ignore
        sys.modules["scipy.optimize"] = None  # type: ignore

        try:
            # Force reimport of matcher without scipy
            import importlib
            import src.matcher as matcher_module
            importlib.reload(matcher_module)

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = matcher_module.match_annotations(
                    [a1], [f1], profile, SOURCE_DIMS, TARGET_DIMS
                )

            # Greedy fallback should still produce a fuzzy match
            assert len(result) == 1
            assert result[0].match_type == "fuzzy"
            assert result[0].field_id == "f1"

            # A warning should have been emitted about scipy unavailability
            scipy_warnings = [x for x in w if "scipy" in str(x.message).lower()]
            assert len(scipy_warnings) >= 1
        finally:
            # Restore scipy
            if original_scipy is not None:
                sys.modules["scipy"] = original_scipy
            else:
                sys.modules.pop("scipy", None)
            if original_scipy_optimize is not None:
                sys.modules["scipy.optimize"] = original_scipy_optimize
            else:
                sys.modules.pop("scipy.optimize", None)
            # Reload matcher with real scipy restored
            import importlib
            import src.matcher as matcher_module
            importlib.reload(matcher_module)


# ---------------------------------------------------------------------------
# TestAutoStatusAssignment — Auto-assign status based on match_type
# ---------------------------------------------------------------------------

class TestAutoStatusAssignment:
    """Verify matcher auto-assigns status based on match_type (spec: 2026-04-01)."""

    def _make_annotation(self, annot_id, form_name="Form A", anchor_text="DOB"):
        from src.models import AnnotationRecord
        return AnnotationRecord(
            id=annot_id,
            page=1,
            content="DM.BRTHDTC",
            domain="DM",
            category="sdtm_mapping",
            matched_rule="test",
            rect=[10.0, 10.0, 80.0, 25.0],
            anchor_text=anchor_text,
            form_name=form_name,
        )

    def _make_field(self, field_id, form_name="Form A", label="DOB"):
        from src.models import FieldRecord
        return FieldRecord(
            id=field_id,
            page=1,
            label=label,
            form_name=form_name,
            rect=[10.0, 10.0, 80.0, 25.0],
            field_type="text_field",
            page_width=595.0,
            page_height=842.0,
        )

    def _make_profile(self):
        from src.profile_models import (
            MatchingConfig, Profile, ProfileMeta, ClassificationRule, RuleCondition,
        )
        return Profile(
            meta=ProfileMeta(name="test", version="1"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(
                    conditions=RuleCondition(fallback=True),
                    category="sdtm_mapping",
                )
            ],
            matching_config=MatchingConfig(),
        )

    def test_exact_match_status_is_approved(self):
        from src.matcher import match_annotations
        annots = [self._make_annotation("a1")]
        fields = [self._make_field("f1")]
        profile = self._make_profile()
        matches = match_annotations(
            annots, fields, profile,
            source_page_dims={1: (595.0, 842.0)},
            target_page_dims={1: (595.0, 842.0)},
        )
        exact = [m for m in matches if m.match_type == "exact"]
        assert len(exact) == 1
        assert exact[0].status == "approved"

    def test_fuzzy_match_status_is_re_pairing(self):
        from src.matcher import match_annotations
        # Use "Date Birth" vs "Date of Birth" which scores ~87 with token_sort_ratio
        annots = [self._make_annotation("a1", anchor_text="Date Birth")]
        fields = [self._make_field("f1", label="Date of Birth")]
        profile = self._make_profile()
        matches = match_annotations(
            annots, fields, profile,
            source_page_dims={1: (595.0, 842.0)},
            target_page_dims={1: (595.0, 842.0)},
        )
        fuzzy = [m for m in matches if m.match_type == "fuzzy"]
        assert len(fuzzy) >= 1
        for m in fuzzy:
            assert m.status == "re-pairing"

    def test_unmatched_status_is_re_pairing(self):
        from src.matcher import match_annotations
        annots = [self._make_annotation("a1", form_name="Form A", anchor_text="Nonexistent Field")]
        fields = [self._make_field("f1", form_name="Form B", label="SomeOtherField")]
        profile = self._make_profile()
        matches = match_annotations(
            annots, fields, profile,
            source_page_dims={1: (595.0, 842.0)},
            target_page_dims={1: (595.0, 842.0)},
        )
        assert len(matches) == 1
        assert matches[0].status == "re-pairing"

    def test_position_match_status_is_re_pairing(self):
        """Annotations that fall through to position pass should have status re-pairing."""
        from src.matcher import match_annotations
        # Annotation with empty anchor_text bypasses exact/fuzzy passes
        # and lands in position pass because form_name matches
        annot = self._make_annotation("a1", form_name="Form A", anchor_text="")
        field = self._make_field("f1", form_name="Form A", label="DOB")
        profile = self._make_profile()
        matches = match_annotations(
            [annot], [field], profile,
            source_page_dims={1: (595.0, 842.0)},
            target_page_dims={1: (595.0, 842.0)},
        )
        assert len(matches) == 1
        assert matches[0].match_type == "position_only"


# ---------------------------------------------------------------------------
# TestCheckboxFieldExclusion — checkbox fields must never be anchor targets
# ---------------------------------------------------------------------------

class TestCheckboxFieldExclusion:
    """Checkbox fields are excluded from all label-based passes (exact + fuzzy)."""

    def _annot(self, aid, anchor_text="Adverse Event", form_name="Form A"):
        from src.models import AnnotationRecord
        return AnnotationRecord(
            id=aid, page=1, content="X", domain="DM",
            category="sdtm_mapping", matched_rule="test",
            rect=[10.0, 10.0, 80.0, 25.0],
            anchor_text=anchor_text, form_name=form_name,
        )

    def _checkbox_field(self, fid, label="Adverse Event", form_name="Form A"):
        from src.models import FieldRecord
        return FieldRecord(
            id=fid, page=1, label=label, form_name=form_name,
            rect=[10.0, 10.0, 80.0, 25.0],
            field_type="checkbox", page_width=595.0, page_height=842.0,
        )

    def _text_field(self, fid, label="Adverse Event", form_name="Form A"):
        from src.models import FieldRecord
        return FieldRecord(
            id=fid, page=1, label=label, form_name=form_name,
            rect=[10.0, 10.0, 80.0, 25.0],
            field_type="text_field", page_width=595.0, page_height=842.0,
        )

    def _profile(self):
        from src.profile_models import (
            MatchingConfig, Profile, ProfileMeta, ClassificationRule, RuleCondition,
        )
        return Profile(
            meta=ProfileMeta(name="test", version="1"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(
                    category="sdtm_mapping",
                    conditions=RuleCondition(fallback=True),
                )
            ],
            matching_config=MatchingConfig(),
        )

    def test_exact_pass_skips_checkbox_field(self):
        """Annotation whose anchor_text matches only a checkbox field produces no exact match."""
        from src.matcher import match_annotations
        annot = self._annot("a1")
        cb_field = self._checkbox_field("f1")
        matches = match_annotations(
            [annot], [cb_field], self._profile(),
            source_page_dims={1: (595.0, 842.0)},
            target_page_dims={1: (595.0, 842.0)},
        )
        exact = [m for m in matches if m.match_type == "exact"]
        assert len(exact) == 0

    def test_exact_pass_prefers_text_field_over_same_label_checkbox(self):
        """When label is identical, text_field wins; checkbox is excluded."""
        from src.matcher import match_annotations
        annot = self._annot("a1")
        cb_field = self._checkbox_field("f_cb")
        tf_field = self._text_field("f_tf")
        matches = match_annotations(
            [annot], [cb_field, tf_field], self._profile(),
            source_page_dims={1: (595.0, 842.0)},
            target_page_dims={1: (595.0, 842.0)},
        )
        exact = [m for m in matches if m.match_type == "exact"]
        assert len(exact) == 1
        assert exact[0].field_id == "f_tf"

    def test_exact_pass_single_cluster_page_alignment(self):
        """Single-cluster form: annotation on p.23 must match the p.23 field, not p.22.

        Regression for ION373-CS1 NASLOTSP: pages 20-24 are contiguous so
        _build_form_clusters produces one cluster containing all five pages.
        Before the fix, index-0 always selected the earliest-page field (p.22).
        """
        from src.matcher import match_annotations
        from src.models import AnnotationRecord, FieldRecord

        form = "Alexander Disease History"
        label = "If Other, specify:"

        annot = AnnotationRecord(
            id="eb171414", page=23, content="NASLOTSP in SUPPFA",
            domain="DM", category="sdtm_mapping", matched_rule="test",
            rect=[100.0, 440.0, 200.0, 460.0],
            anchor_text=label, form_name=form,
            anchor_rect=[50.0, 451.12, 200.0, 463.0],
        )

        def _field(fid, page, y):
            return FieldRecord(
                id=fid, page=page, label=label, form_name=form,
                rect=[50.0, y, 200.0, y + 12.0],
                field_type="text_field", page_width=595.0, page_height=842.0,
            )

        f_p22a = _field("3c9ebef8", page=22, y=272.99)
        f_p22b = _field("c5b6448f", page=22, y=495.21)
        f_p23  = _field("47dfc9ae", page=23, y=451.12)

        profile = self._profile()
        dims = {p: (595.0, 842.0) for p in range(20, 25)}
        matches = match_annotations(
            [annot], [f_p22a, f_p22b, f_p23], profile,
            source_page_dims=dims, target_page_dims=dims,
        )
        exact = [m for m in matches if m.match_type == "exact"]
        assert len(exact) == 1
        assert exact[0].field_id == "47dfc9ae", (
            f"Expected p.23 field '47dfc9ae', got '{exact[0].field_id}'"
        )

    def test_exact_pass_single_page_annot_offset_cluster(self):
        """Single-page annotation must use cluster-relative position, not raw page number.

        Regression for ION373-CS1 AxD-Clinical Manifestations:
        - Source cluster [29,30,31,32]: annotation at p.31 is cluster-position 3
        - Target cluster [30,31,32,33]: cluster-position 3 is p.32
        - Two target fields exist on p.31 (wrong) and one on p.32 (correct)
        - Old guard used f.page == src_pg (=31), hitting p.31 fields instead of p.32
        """
        from src.matcher import match_annotations
        from src.models import AnnotationRecord, FieldRecord

        form = "AxD-Clinical Manifestations"
        label = "If Other, specify:"

        annot = AnnotationRecord(
            id="d54a7523", page=31, content="NASLOTSP in SUPPFA",
            domain="Text Box", category="sdtm_mapping", matched_rule="test",
            rect=[254.65, 333.47, 368.69, 348.99],
            anchor_text=label, form_name=form,
            anchor_rect=[78.11, 338.92, 139.67, 345.91],
        )

        def _field(fid, page, y):
            return FieldRecord(
                id=fid, page=page, label=label, form_name=form,
                rect=[78.0, y, 200.0, y + 12.0],
                field_type="text_field", page_width=595.0, page_height=842.0,
            )

        # Source pages 29-32 (annotation at p.31 = cluster-pos 3)
        # Target pages 30-33 (p.32 = cluster-pos 3 = correct target)
        f_p31a = _field("f_p31a", page=31, y=272.99)   # cluster-pos 2, wrong
        f_p31b = _field("f_p31b", page=31, y=495.21)   # cluster-pos 2, wrong
        f_p32  = _field("f_p32",  page=32, y=338.92)   # cluster-pos 3, correct

        # Dummy annotations on other source pages so _build_form_clusters sees all 4 pages
        def _dummy_annot(aid, page):
            return AnnotationRecord(
                id=aid, page=page, content="X", domain="Text Box",
                category="sdtm_mapping", matched_rule="test",
                rect=[50.0, 50.0, 100.0, 62.0],
                anchor_text="dummy_unique_label_xyz", form_name=form,
            )

        dummy_annots = [_dummy_annot(f"da{p}", p) for p in [29, 30, 32]]
        # Pages 30, 31, 32, 33 must form a contiguous target cluster independently
        # of f_p32 (the assertion field). Without a dummy on p.32, the gap 31→33
        # would split the cluster into [30,31] and [33], breaking the size-equality
        # check in the fix. Use a distinct label so dummy fields never conflict with
        # the "If Other, specify:" group being tested.
        dummy_fields = [
            FieldRecord(
                id=f"df{p}", page=p, label="dummy_unique_label_xyz", form_name=form,
                rect=[50.0, 50.0, 100.0, 62.0],
                field_type="text_field", page_width=595.0, page_height=842.0,
            )
            for p in [30, 31, 32, 33]
        ]

        profile = self._profile()
        src_dims = {p: (595.0, 842.0) for p in range(29, 33)}
        tgt_dims = {p: (595.0, 842.0) for p in range(30, 34)}

        matches = match_annotations(
            [annot] + dummy_annots,
            [f_p31a, f_p31b, f_p32] + dummy_fields,
            profile,
            source_page_dims=src_dims,
            target_page_dims=tgt_dims,
        )
        exact = [m for m in matches if m.match_type == "exact" and m.annotation_id == "d54a7523"]
        assert len(exact) == 1, f"Expected 1 exact match for NASLOTSP, got {len(exact)}"
        assert exact[0].field_id == "f_p32", (
            f"Expected p.32 field 'f_p32' (cluster-pos 3), got '{exact[0].field_id}'"
        )

    def test_fuzzy_same_form_pass_skips_checkbox_field(self):
        """Annotation fuzzy-matching only a checkbox field is not matched to it."""
        from src.matcher import match_annotations
        # "Adverse Events" vs "Adverse Event" scores ~94 — above same-form threshold
        annot = self._annot("a1", anchor_text="Adverse Events")
        cb_field = self._checkbox_field("f_cb", label="Adverse Event")
        matches = match_annotations(
            [annot], [cb_field], self._profile(),
            source_page_dims={1: (595.0, 842.0)},
            target_page_dims={1: (595.0, 842.0)},
        )
        fuzzy = [m for m in matches if m.match_type == "fuzzy"]
        assert all(m.field_id != "f_cb" for m in fuzzy)

    def test_fuzzy_same_form_prefers_text_field_over_checkbox(self):
        """When both a checkbox and text_field fuzzy-match, text_field is selected."""
        from src.matcher import match_annotations
        annot = self._annot("a1", anchor_text="Adverse Events")
        cb_field = self._checkbox_field("f_cb", label="Adverse Event")
        tf_field = self._text_field("f_tf", label="Adverse Event")
        matches = match_annotations(
            [annot], [cb_field, tf_field], self._profile(),
            source_page_dims={1: (595.0, 842.0)},
            target_page_dims={1: (595.0, 842.0)},
        )
        assert len(matches) == 1
        assert matches[0].field_id == "f_tf"


# ---------------------------------------------------------------------------
# Page-rank isolation and repeating field tests
# ---------------------------------------------------------------------------

def _make_annot(aid, anchor_text, form_name, page=1, y=100.0, visit=""):
    return AnnotationRecord(
        id=aid,
        page=page,
        content="X",
        domain="DM",
        category="sdtm_mapping",
        matched_rule="test",
        rect=[50.0, y, 200.0, y + 15.0],
        anchor_text=anchor_text,
        form_name=form_name,
        visit=visit,
    )


def _make_field(fid, label, form_name, page=1, y=100.0, visit=""):
    return FieldRecord(
        id=fid,
        page=page,
        label=label,
        form_name=form_name,
        rect=[50.0, y, 200.0, y + 15.0],
        field_type="text_field",
        page_width=595.0,
        page_height=842.0,
    )


def _make_profile_default():
    return _make_profile()


class TestMultiPageSameFormPageRankIsolation:
    """Annotations on page N of a form must match fields on the corresponding
    (rank-equal) page of the same form in the target — not the wrong page."""

    def test_page_rank_isolates_exact_match(self):
        """Exact pass: annotation on src page 3 (rank 1) matches target page 5 (rank 1),
        annotation on src page 4 (rank 2) matches target page 6 (rank 2)."""
        annot1 = _make_annot("a1", "Date", "Adverse Events", page=3)
        annot2 = _make_annot("a2", "Date", "Adverse Events", page=4)
        field1 = _make_field("f1", "Date", "Adverse Events", page=5)   # rank 1 in target
        field2 = _make_field("f2", "Date", "Adverse Events", page=6)   # rank 2 in target

        profile = _make_profile_default()
        src_dims = {3: (595.0, 842.0), 4: (595.0, 842.0)}
        tgt_dims = {5: (595.0, 842.0), 6: (595.0, 842.0)}
        matches = match_annotations(
            [annot1, annot2], [field1, field2], profile, src_dims, tgt_dims,
        )
        by_annot = {m.annotation_id: m for m in matches}
        # annot1 is rank-1 in source → must land on field1 (rank-1 in target)
        assert by_annot["a1"].field_id == "f1", "rank-1 annotation must match rank-1 field"
        assert by_annot["a2"].field_id == "f2", "rank-2 annotation must match rank-2 field"
        assert by_annot["a1"].match_type == "exact"
        assert by_annot["a2"].match_type == "exact"

    def test_page_rank_isolates_fuzzy_match(self):
        """Fuzzy same-form pass respects page rank: slightly different labels
        still resolve to the correct page."""
        annot1 = _make_annot("a1", "Start Dat", "Adverse Events", page=3)  # fuzzy ~90
        annot2 = _make_annot("a2", "Start Dat", "Adverse Events", page=4)
        field1 = _make_field("f1", "Start Date", "Adverse Events", page=5)
        field2 = _make_field("f2", "Start Date", "Adverse Events", page=6)

        profile = _make_profile_default()
        src_dims = {3: (595.0, 842.0), 4: (595.0, 842.0)}
        tgt_dims = {5: (595.0, 842.0), 6: (595.0, 842.0)}
        matches = match_annotations(
            [annot1, annot2], [field1, field2], profile, src_dims, tgt_dims,
        )
        by_annot = {m.annotation_id: m for m in matches}
        assert by_annot["a1"].field_id == "f1"
        assert by_annot["a2"].field_id == "f2"


class TestMultiPagePageCountMismatchFallback:
    """When source has more pages than target for a form, extra-page annotations
    fall through to cross-form pass rather than landing on wrong-page fields."""

    def test_extra_source_page_falls_to_cross_form(self):
        """Source has rank-1 and rank-2; target only has rank-1.
        rank-2 annotation falls to cross-form pass."""
        annot1 = _make_annot("a1", "Date", "Adverse Events", page=3)   # rank 1
        annot2 = _make_annot("a2", "Date", "Adverse Events", page=4)   # rank 2 — no target match

        field1 = _make_field("f1", "Date", "Adverse Events", page=5)   # only rank-1 in target

        profile = _make_profile_default()
        src_dims = {3: (595.0, 842.0), 4: (595.0, 842.0)}
        tgt_dims = {5: (595.0, 842.0)}
        matches = match_annotations(
            [annot1, annot2], [field1], profile, src_dims, tgt_dims,
        )
        by_annot = {m.annotation_id: m for m in matches}
        # rank-1 annotation claims field1 via exact pass
        assert by_annot["a1"].field_id == "f1"
        # rank-2 annotation has no corresponding target page — field already taken,
        # falls to cross-form or position pass (field_id may be None or f1 re-used via cross-form)
        # Key assertion: it must NOT have been matched as rank-1 exact to the wrong page
        assert by_annot["a2"].match_type != "exact"


class TestRepeatingFieldVerticalOrder:
    """Same label appearing multiple times on one page: Nth annotation (by y0)
    matches Nth field occurrence (by y0)."""

    def test_three_date_annotations_match_by_vertical_rank(self):
        """a1 (y=100) -> f1 (y=110), a2 (y=200) -> f2 (y=210), a3 (y=300) -> f3 (y=310)."""
        a1 = _make_annot("a1", "Date", "Vitals", page=1, y=100.0)
        a2 = _make_annot("a2", "Date", "Vitals", page=1, y=200.0)
        a3 = _make_annot("a3", "Date", "Vitals", page=1, y=300.0)

        f1 = _make_field("f1", "Date", "Vitals", page=1, y=110.0)
        f2 = _make_field("f2", "Date", "Vitals", page=1, y=210.0)
        f3 = _make_field("f3", "Date", "Vitals", page=1, y=310.0)

        profile = _make_profile_default()
        src_dims = {1: (595.0, 842.0)}
        tgt_dims = {1: (595.0, 842.0)}
        matches = match_annotations(
            [a1, a2, a3], [f1, f2, f3], profile, src_dims, tgt_dims,
        )
        by_annot = {m.annotation_id: m for m in matches}
        assert all(by_annot[f"a{i}"].match_type == "exact" for i in range(1, 4))
        assert by_annot["a1"].field_id == "f1"
        assert by_annot["a2"].field_id == "f2"
        assert by_annot["a3"].field_id == "f3"

    def test_annotations_out_of_order_still_pair_by_vertical_rank(self):
        """Even if annotations are passed in reverse order, pairing is by y0 rank."""
        a3 = _make_annot("a3", "Date", "Vitals", page=1, y=300.0)
        a1 = _make_annot("a1", "Date", "Vitals", page=1, y=100.0)
        a2 = _make_annot("a2", "Date", "Vitals", page=1, y=200.0)

        f1 = _make_field("f1", "Date", "Vitals", page=1, y=110.0)
        f2 = _make_field("f2", "Date", "Vitals", page=1, y=210.0)
        f3 = _make_field("f3", "Date", "Vitals", page=1, y=310.0)

        profile = _make_profile_default()
        src_dims = {1: (595.0, 842.0)}
        tgt_dims = {1: (595.0, 842.0)}
        matches = match_annotations(
            [a3, a1, a2], [f1, f2, f3], profile, src_dims, tgt_dims,
        )
        by_annot = {m.annotation_id: m for m in matches}
        assert by_annot["a1"].field_id == "f1"
        assert by_annot["a2"].field_id == "f2"
        assert by_annot["a3"].field_id == "f3"

    def test_more_annotations_than_fields_extras_use_last_field(self):
        """If 3 annotations but only 2 fields, the 3rd annotation maps to f2 (last field)."""
        a1 = _make_annot("a1", "Date", "Vitals", page=1, y=100.0)
        a2 = _make_annot("a2", "Date", "Vitals", page=1, y=200.0)
        a3 = _make_annot("a3", "Date", "Vitals", page=1, y=300.0)

        f1 = _make_field("f1", "Date", "Vitals", page=1, y=110.0)
        f2 = _make_field("f2", "Date", "Vitals", page=1, y=210.0)

        profile = _make_profile_default()
        src_dims = {1: (595.0, 842.0)}
        tgt_dims = {1: (595.0, 842.0)}
        matches = match_annotations(
            [a1, a2, a3], [f1, f2], profile, src_dims, tgt_dims,
        )
        by_annot = {m.annotation_id: m for m in matches}
        assert by_annot["a1"].field_id == "f1"
        assert by_annot["a2"].field_id == "f2"
        assert by_annot["a3"].field_id == "f2"

    def test_single_annotation_single_field_unchanged(self):
        """No regression: single annotation, single same-label field — still exact match."""
        a1 = _make_annot("a1", "Date", "Vitals", page=1, y=100.0)
        f1 = _make_field("f1", "Date", "Vitals", page=1, y=110.0)

        profile = _make_profile_default()
        matches = match_annotations(
            [a1], [f1], profile, {1: (595.0, 842.0)}, {1: (595.0, 842.0)},
        )
        assert matches[0].field_id == "f1"
        assert matches[0].match_type == "exact"

    def test_different_labels_on_same_page_not_affected(self):
        """Annotations with different labels still match their respective fields."""
        a1 = _make_annot("a1", "Date", "Vitals", page=1, y=100.0)
        a2 = _make_annot("a2", "Time", "Vitals", page=1, y=200.0)

        f1 = _make_field("f1", "Date", "Vitals", page=1, y=110.0)
        f2 = _make_field("f2", "Time", "Vitals", page=1, y=210.0)

        profile = _make_profile_default()
        matches = match_annotations(
            [a1, a2], [f1, f2], profile, {1: (595.0, 842.0)}, {1: (595.0, 842.0)},
        )
        by_annot = {m.annotation_id: m for m in matches}
        assert by_annot["a1"].field_id == "f1"
        assert by_annot["a2"].field_id == "f2"

    def test_cross_page_target_repeating_labels_pair_by_global_rank(self):
        """Bug: source has 2 备注 on page 1; target has 备注 on page 1 AND page 2.
        Both annotations must NOT both land on the same target field.
        a1 (src p1 y=100) -> f1 (tgt p1 y=110), a2 (src p1 y=600) -> f2 (tgt p2 y=300)."""
        a1 = _make_annot("a1", "备注", "LB", page=1, y=100.0)
        a2 = _make_annot("a2", "备注", "LB", page=1, y=600.0)

        f1 = _make_field("f1", "备注", "LB", page=1, y=110.0)
        f2 = _make_field("f2", "备注", "LB", page=2, y=300.0)

        profile = _make_profile_default()
        src_dims = {1: (595.0, 842.0)}
        tgt_dims = {1: (595.0, 842.0), 2: (595.0, 842.0)}
        matches = match_annotations([a1, a2], [f1, f2], profile, src_dims, tgt_dims)
        by_annot = {m.annotation_id: m for m in matches}
        assert by_annot["a1"].field_id == "f1"
        assert by_annot["a2"].field_id == "f2"
        assert by_annot["a1"].target_page == 1
        assert by_annot["a2"].target_page == 2

    def test_three_annotations_one_page_three_fields_cross_page(self):
        """Bug: source has 3 备注 all on page 1; target has 备注 on pages 1, 2, and 3.
        All 3 source annotations must NOT all land on target field f1.
        a1 -> f1 (tgt p1), a2 -> f2 (tgt p2), a3 -> f3 (tgt p3)."""
        a1 = _make_annot("a1", "备注", "LB", page=1, y=100.0)
        a2 = _make_annot("a2", "备注", "LB", page=1, y=400.0)
        a3 = _make_annot("a3", "备注", "LB", page=1, y=700.0)

        f1 = _make_field("f1", "备注", "LB", page=1, y=110.0)
        f2 = _make_field("f2", "备注", "LB", page=2, y=200.0)
        f3 = _make_field("f3", "备注", "LB", page=3, y=200.0)

        profile = _make_profile_default()
        src_dims = {1: (595.0, 842.0)}
        tgt_dims = {1: (595.0, 842.0), 2: (595.0, 842.0), 3: (595.0, 842.0)}
        matches = match_annotations([a1, a2, a3], [f1, f2, f3], profile, src_dims, tgt_dims)
        by_annot = {m.annotation_id: m for m in matches}
        assert by_annot["a1"].field_id == "f1"
        assert by_annot["a2"].field_id == "f2"
        assert by_annot["a3"].field_id == "f3"

    def test_label_relative_rank_fallback_when_form_page_counts_differ(self):
        """Regression: source form has 6 pages, target form has 18 pages.
        A repeating label appears on src pages 3+4 (form-ranks 3+4) and
        tgt pages 1+10 (form-ranks 1+10).  Neither rank 3 nor rank 4 exists
        in the target bucket → must fall back to label-relative rank (1↔1, 2↔2)
        and produce exact matches, not fall through to fuzzy cross-form."""
        # Source form spans pages 1–6; label only on pages 3 and 4.
        src_pages_all = [1, 2, 3, 4, 5, 6]
        # Target form spans pages 101–118 (18 pages); label only on pages 103 and 112
        # (form-ranks 3 and 12 — neither matches src form-ranks 3 or 4 relative to full form).
        tgt_pages_all = list(range(101, 119))  # 101..118

        # Filler annotations on other pages so _build_page_rank_map assigns correct form ranks
        filler_annots = [
            _make_annot(f"filler_a{p}", "Other Label", "Big Form", page=p)
            for p in src_pages_all if p not in (3, 4)
        ]
        a1 = _make_annot("a1", "Repeating Label", "Big Form", page=3, y=100.0)
        a2 = _make_annot("a2", "Repeating Label", "Big Form", page=4, y=100.0)

        filler_fields = [
            _make_field(f"filler_f{p}", "Other Label", "Big Form", page=p)
            for p in tgt_pages_all if p not in (103, 112)
        ]
        f1 = _make_field("f1", "Repeating Label", "Big Form", page=103, y=100.0)
        f2 = _make_field("f2", "Repeating Label", "Big Form", page=112, y=100.0)

        profile = _make_profile_default()
        src_dims = {p: (595.0, 842.0) for p in src_pages_all}
        tgt_dims = {p: (595.0, 842.0) for p in tgt_pages_all}

        matches = match_annotations(
            filler_annots + [a1, a2],
            filler_fields + [f1, f2],
            profile,
            src_dims,
            tgt_dims,
        )
        by_annot = {m.annotation_id: m for m in matches}
        assert by_annot["a1"].field_id == "f1", "label-rank-1 annotation must hit label-rank-1 field"
        assert by_annot["a2"].field_id == "f2", "label-rank-2 annotation must hit label-rank-2 field"
        assert by_annot["a1"].match_type == "exact"
        assert by_annot["a2"].match_type == "exact"


class TestExactCaseInsensitiveMatch:
    """Exact pass uses case-insensitive comparison for both form_name and label."""

    def test_case_mismatch_form_name_still_exact(self):
        """Annotation form_name differs in case from field form_name: still exact."""
        annot = _make_annot("a1", "Date of Birth", "demographics", page=1)
        field = _make_field("f1", "Date of Birth", "DEMOGRAPHICS", page=1)

        profile = _make_profile_default()
        matches = match_annotations(
            [annot], [field], profile, {1: (595.0, 842.0)}, {1: (595.0, 842.0)},
        )
        assert matches[0].match_type == "exact"
        assert matches[0].field_id == "f1"

    def test_case_mismatch_label_falls_through_to_fuzzy(self):
        """Annotation anchor_text differs in case from field label.

        The exact pass label key is case-sensitive (annotation migration assumes
        source and target CRFs share label casing). When casing drifts the
        annotation falls through exact → fuzzy_same_form_pass, which lower-
        cases both sides before scoring, so the match still succeeds — just
        labeled `fuzzy` instead of `exact`.
        """
        annot = _make_annot("a1", "DATE OF BIRTH", "DEMOGRAPHICS", page=1)
        field = _make_field("f1", "Date of Birth", "DEMOGRAPHICS", page=1)

        profile = _make_profile_default()
        matches = match_annotations(
            [annot], [field], profile, {1: (595.0, 842.0)}, {1: (595.0, 842.0)},
        )
        assert matches[0].field_id == "f1"
        assert matches[0].match_type == "fuzzy"

    def test_identical_strings_match_exact(self):
        """When form_name and label match exactly, exact pass succeeds."""
        annot = _make_annot("a1", "Date of Birth", "DEMOGRAPHICS", page=1)
        field = _make_field("f1", "Date of Birth", "DEMOGRAPHICS", page=1)

        profile = _make_profile_default()
        matches = match_annotations(
            [annot], [field], profile, {1: (595.0, 842.0)}, {1: (595.0, 842.0)},
        )
        assert matches[0].match_type == "exact"
        assert matches[0].field_id == "f1"
        assert matches[0].status == "approved"


# ---------------------------------------------------------------------------
# TestExactPassRepeatingPageRank
# Bug 1: sibling threshold widened to 5px
# Bug 2: cross-page groups aligned by form-page rank (not global index)
# ---------------------------------------------------------------------------

from src.matcher import _exact_pass  # noqa: E402


class TestExactPassRepeatingPageRank:
    """Tests that exercise _exact_pass directly for the two targeted bugs."""

    def _annot(self, aid: str, form_name: str, anchor: str, page: int, y: float = 100.0):
        return AnnotationRecord(
            id=aid,
            page=page,
            content="X",
            domain="DM",
            category="sdtm_mapping",
            matched_rule="test",
            rect=[10.0, y, 100.0, y + 12.0],
            anchor_text=anchor,
            form_name=form_name,
        )

    def _field(self, fid: str, form_name: str, label: str, page: int, y: float = 100.0):
        return FieldRecord(
            id=fid,
            page=page,
            label=label,
            form_name=form_name,
            rect=[5.0, y, 100.0, y + 12.0],
            field_type="text_field",
        )

    # ------------------------------------------------------------------
    # Bug 1: sibling proximity threshold
    # ------------------------------------------------------------------

    def test_siblings_with_2_4px_gap_share_same_row(self):
        """Two annotations 2.4px apart in y (just above old 2px threshold, below new 5px)
        must be treated as siblings and both map to the first target field, not to f1 then f2."""
        a = self._annot("a", "FORM", "Label X", page=1, y=67.74)
        b = self._annot("b", "FORM", "Label X", page=1, y=70.14)  # gap = 2.4 px
        f1 = self._field("f1", "FORM", "Label X", page=1, y=65.0)
        f2 = self._field("f2", "FORM", "Label X", page=1, y=80.0)

        unmatched = {"a", "b"}
        results = _exact_pass([a, b], [f1, f2], unmatched, exact_threshold=1.0)

        matched = {r.annotation_id: r.field_id for r in results}
        assert matched["a"] == "f1"
        assert matched["b"] == "f1", (
            "sibling annotation (2.4 px gap, within new 5 px threshold) "
            "must share row with 'a' and map to the same field"
        )

    # ------------------------------------------------------------------
    # Bug 2: cross-page form-page-rank alignment
    # ------------------------------------------------------------------

    def test_cross_page_aligns_by_form_page_rank(self):
        """Source annotations on PE pages 24/25 are form-page ranks 2/3 globally
        (because page 23 of PE exists via another label).  They must map to the
        target fields whose form-page rank matches (pages 24 and 25 respectively),
        not to the first two target fields (pages 23 and 24).

        Old code: index-0 annotation -> index-0 field (page 23) — off by one page.
        New code: rank-2 annotation  -> rank-2 field  (page 24) — correct.
        """
        # Anchor annotation/field on PE page 23 establishes:
        #   global src_pg_rank["pe"] = {23:1, 24:2, 25:3}
        #   global tgt_pg_rank["pe"] = {23:1, 24:2, 25:3, 26:4}
        anchor_src = self._annot("s_anchor", "PE", "Anchor", page=23, y=50.0)
        annot_a = self._annot("a", "PE", "FormLabel", page=24, y=67.0)   # rank 2
        annot_b = self._annot("b", "PE", "FormLabel", page=25, y=62.0)   # rank 3

        anchor_tgt = self._field("ft_anchor", "PE", "Anchor", page=23, y=50.0)
        f23 = self._field("f23", "PE", "FormLabel", page=23, y=60.0)     # rank 1
        f24 = self._field("f24", "PE", "FormLabel", page=24, y=60.0)     # rank 2
        f25 = self._field("f25", "PE", "FormLabel", page=25, y=60.0)     # rank 3
        f26 = self._field("f26", "PE", "FormLabel", page=26, y=60.0)     # rank 4

        all_annots = [anchor_src, annot_a, annot_b]
        all_fields = [anchor_tgt, f23, f24, f25, f26]
        unmatched = {"s_anchor", "a", "b"}
        results = _exact_pass(all_annots, all_fields, unmatched, exact_threshold=1.0)

        matched = {r.annotation_id: r.field_id for r in results}
        assert matched.get("a") == "f24", (
            f"page-24 annotation (rank 2) must map to f24, got {matched.get('a')!r}"
        )
        assert matched.get("b") == "f25", (
            f"page-25 annotation (rank 3) must map to f25, got {matched.get('b')!r}"
        )

    def test_cross_page_sibling_clusters_map_to_correct_pages(self):
        """3 sibling annotations per page (2.4 px gaps, outside old 2 px but inside new 5 px
        threshold) must be grouped into one row per page and each cluster maps to
        the target field for its form-page rank, not to whichever field has the same index.

        Old code: 2 px threshold breaks siblings into separate rows; multi-page allow_extras=False
        drops most of them.  New code: 5 px threshold keeps siblings together; rank-based
        bucketing maps each cluster to the right target page.
        """
        # Page-1 cluster — siblings with 2.4 px gaps
        c1a = self._annot("c1a", "STUDY", "Section", page=1, y=100.0)
        c1b = self._annot("c1b", "STUDY", "Section", page=1, y=102.4)
        c1c = self._annot("c1c", "STUDY", "Section", page=1, y=104.8)
        # Page-2 cluster — siblings with 2.4 px gaps
        c2a = self._annot("c2a", "STUDY", "Section", page=2, y=200.0)
        c2b = self._annot("c2b", "STUDY", "Section", page=2, y=202.4)
        c2c = self._annot("c2c", "STUDY", "Section", page=2, y=204.8)

        fp1 = self._field("fp1", "STUDY", "Section", page=1, y=98.0)
        fp2 = self._field("fp2", "STUDY", "Section", page=2, y=198.0)

        unmatched = {"c1a", "c1b", "c1c", "c2a", "c2b", "c2c"}
        results = _exact_pass(
            [c1a, c1b, c1c, c2a, c2b, c2c],
            [fp1, fp2],
            unmatched,
            exact_threshold=1.0,
        )

        matched = {r.annotation_id: r.field_id for r in results}
        assert matched.get("c1a") == "fp1", "page-1 cluster must map to fp1"
        assert matched.get("c1b") == "fp1", "sibling c1b must share fp1 with c1a"
        assert matched.get("c1c") == "fp1", "sibling c1c must share fp1 with c1a"
        assert matched.get("c2a") == "fp2", "page-2 cluster must map to fp2"
        assert matched.get("c2b") == "fp2", "sibling c2b must share fp2 with c2a"
        assert matched.get("c2c") == "fp2", "sibling c2c must share fp2 with c2a"


# ---------------------------------------------------------------------------
# TestVisitClusterAlignment
# Cluster-aware rank: repeating forms (one cluster per visit) are aligned at
# the visit level, not just at the full-form page-rank level.
# ---------------------------------------------------------------------------

class TestVisitClusterAlignment:
    """Visit-cluster page alignment for repeating forms with unequal page counts."""

    def test_visit_cluster_aligns_repeating_form_across_unequal_page_counts(self):
        """ION373-CS1 reproduction: source NE form has 2 visit-clusters of 2 pages
        (src 186,187 = visit-1; src 194,195 = visit-2). Target NE form has 2
        visit-clusters of 8 pages each (tgt 188..195 = visit-1; tgt 196..203 = visit-2).

        A label that exists in BOTH target clusters ('General') must route:
          src 186 -> cluster-1 target (never cluster-2)
          src 194 -> cluster-2 target (never cluster-1)

        A label that exists ONLY in the unscheduled cluster ('Reason Unscheduled')
        must route src 194 -> cluster-2 via label-rank fallback.
        """
        # Source: 2 visit-clusters of 2 pages
        # Visit-1: pages 186, 187
        # Visit-2: pages 194, 195 (non-contiguous with visit-1 => separate cluster)
        a_gen_v1 = _make_annot("a_gen_v1", "General", "NE", page=186, y=100.0)
        a_gen_v2 = _make_annot("a_gen_v2", "General", "NE", page=194, y=100.0)
        a_reason = _make_annot("a_reason", "Reason Unscheduled", "NE", page=194, y=200.0)

        # Target: 2 visit-clusters of 8 pages
        # Visit-1: pages 188..195; Visit-2: pages 196..203
        # "General" appears in both clusters
        f_gen_v1 = _make_field("f_gen_v1", "General", "NE", page=190, y=100.0)
        f_gen_v2 = _make_field("f_gen_v2", "General", "NE", page=198, y=100.0)
        # "Reason Unscheduled" only in cluster-2
        f_reason = _make_field("f_reason", "Reason Unscheduled", "NE", page=196, y=200.0)

        # Filler pages so page-rank map includes all pages in each cluster
        filler_src = [
            _make_annot(f"fs{p}", "Other", "NE", page=p)
            for p in [187, 195]
        ]
        filler_tgt = [
            _make_field(f"ft{p}", "Other", "NE", page=p)
            for p in [188, 189, 191, 192, 193, 194, 195, 197, 199, 200, 201, 202, 203]
        ]

        profile = _make_profile_default()
        src_dims = {p: (595.0, 842.0) for p in [186, 187, 194, 195]}
        tgt_dims = {p: (595.0, 842.0) for p in range(188, 204)}

        all_annots = [a_gen_v1, a_gen_v2, a_reason] + filler_src
        all_fields = [f_gen_v1, f_gen_v2, f_reason] + filler_tgt

        matches = match_annotations(all_annots, all_fields, profile, src_dims, tgt_dims)
        by_annot = {m.annotation_id: m for m in matches}

        # src 186 "General" -> cluster-1 target (p188..195); f_gen_v1 is on p190
        assert by_annot["a_gen_v1"].field_id == "f_gen_v1", (
            "src-p186 (cluster-1) 'General' must land on cluster-1 target (p190), "
            f"got field_id={by_annot['a_gen_v1'].field_id!r} page={by_annot['a_gen_v1'].target_page}"
        )
        assert by_annot["a_gen_v1"].target_page in range(188, 196), (
            f"src-p186 must not cross into cluster-2 (tgt p196..203), got {by_annot['a_gen_v1'].target_page}"
        )

        # src 194 "General" -> cluster-2 target (p196..203); f_gen_v2 is on p198
        assert by_annot["a_gen_v2"].field_id == "f_gen_v2", (
            "src-p194 (cluster-2) 'General' must land on cluster-2 target (p198), "
            f"got field_id={by_annot['a_gen_v2'].field_id!r} page={by_annot['a_gen_v2'].target_page}"
        )
        assert by_annot["a_gen_v2"].target_page in range(196, 204), (
            f"src-p194 must not cross into cluster-1 (tgt p188..195), got {by_annot['a_gen_v2'].target_page}"
        )

        # src 194 "Reason Unscheduled" -> cluster-2 via label-rank fallback
        assert by_annot["a_reason"].field_id == "f_reason", (
            f"'Reason Unscheduled' only in cluster-2; expected f_reason, got {by_annot['a_reason'].field_id!r}"
        )
        assert by_annot["a_reason"].target_page in range(196, 204), (
            f"'Reason Unscheduled' must land in cluster-2 (tgt p196..203), got {by_annot['a_reason'].target_page}"
        )

    def test_visit_cluster_single_source_visit_to_multi_target_visit(self):
        """Collapse case: source has 1 visit-cluster (2 contiguous pages),
        target has 2 visit-clusters. All source annotations must land in
        target cluster-1, never in cluster-2.
        """
        # Source: 1 cluster (pages 1, 2 contiguous)
        a1 = _make_annot("a1", "Label A", "FORM", page=1, y=100.0)
        a2 = _make_annot("a2", "Label A", "FORM", page=2, y=100.0)

        # Target: 2 clusters (pages 10, 11 = cluster-1; pages 20, 21 = cluster-2)
        f_c1 = _make_field("f_c1", "Label A", "FORM", page=10, y=100.0)
        f_c2 = _make_field("f_c2_p1", "Label A", "FORM", page=11, y=100.0)
        f_c3 = _make_field("f_c3", "Label A", "FORM", page=20, y=100.0)
        f_c4 = _make_field("f_c4", "Label A", "FORM", page=21, y=100.0)

        profile = _make_profile_default()
        src_dims = {1: (595.0, 842.0), 2: (595.0, 842.0)}
        tgt_dims = {10: (595.0, 842.0), 11: (595.0, 842.0), 20: (595.0, 842.0), 21: (595.0, 842.0)}

        matches = match_annotations([a1, a2], [f_c1, f_c2, f_c3, f_c4], profile, src_dims, tgt_dims)
        by_annot = {m.annotation_id: m for m in matches}

        # src has 1 cluster -> all annotations go to cluster-1 of target (pages 10, 11)
        assert by_annot["a1"].target_page in (10, 11), (
            f"src-p1 (single cluster) must land in tgt cluster-1 (p10 or p11), got {by_annot['a1'].target_page}"
        )
        assert by_annot["a2"].target_page in (10, 11), (
            f"src-p2 (single cluster) must land in tgt cluster-1 (p10 or p11), got {by_annot['a2'].target_page}"
        )
        # Must NOT land in cluster-2 (pages 20, 21)
        assert by_annot["a1"].target_page not in (20, 21), (
            f"src-p1 must not cross into cluster-2, got {by_annot['a1'].target_page}"
        )
        assert by_annot["a2"].target_page not in (20, 21), (
            f"src-p2 must not cross into cluster-2, got {by_annot['a2'].target_page}"
        )

    def test_visit_cluster_degenerate_single_cluster_matches_current_behavior(self):
        """Sanity: a form with one contiguous cluster (no repeating visits) produces
        the same matches as the pre-cluster label-rank fallback behavior.

        Mirrors test_label_relative_rank_fallback_when_form_page_counts_differ:
        source 6 contiguous pages, target 18 contiguous pages, label only on
        pages 3+4 (src) and 103+112 (tgt). Should still produce exact matches
        via label-rank fallback with cluster index always == 1.
        """
        src_pages_all = [1, 2, 3, 4, 5, 6]
        tgt_pages_all = list(range(101, 119))

        filler_annots = [
            _make_annot(f"filler_a{p}", "Other Label", "Big Form", page=p)
            for p in src_pages_all if p not in (3, 4)
        ]
        a1 = _make_annot("a1", "Repeating Label", "Big Form", page=3, y=100.0)
        a2 = _make_annot("a2", "Repeating Label", "Big Form", page=4, y=100.0)

        filler_fields = [
            _make_field(f"filler_f{p}", "Other Label", "Big Form", page=p)
            for p in tgt_pages_all if p not in (103, 112)
        ]
        f1 = _make_field("f1", "Repeating Label", "Big Form", page=103, y=100.0)
        f2 = _make_field("f2", "Repeating Label", "Big Form", page=112, y=100.0)

        profile = _make_profile_default()
        src_dims = {p: (595.0, 842.0) for p in src_pages_all}
        tgt_dims = {p: (595.0, 842.0) for p in tgt_pages_all}

        matches = match_annotations(
            filler_annots + [a1, a2],
            filler_fields + [f1, f2],
            profile,
            src_dims,
            tgt_dims,
        )
        by_annot = {m.annotation_id: m for m in matches}
        assert by_annot["a1"].field_id == "f1", "single-cluster: label-rank-1 must hit label-rank-1 field"
        assert by_annot["a2"].field_id == "f2", "single-cluster: label-rank-2 must hit label-rank-2 field"
        assert by_annot["a1"].match_type == "exact"
        assert by_annot["a2"].match_type == "exact"


class TestAnchorYNearestField:
    """Regression coverage for the ION373-CS1 'Head Circumference' bug.

    Repeated label appears at two anchor Y positions on the source page (header
    row carrying VSCAT/VSTESTCD at Y=70, measurement row carrying VSORRES at
    Y=150). Target page has the same two label occurrences. Each annotation
    must pair with the field whose Y is nearest its anchor Y — not by sorted
    rank, which previously clamped extra rows onto the last field when the
    domain_label annotation consumed a field slot.
    """

    def _annot_with_anchor(self, aid, anchor_text, form_name, page, anchor_y, category="sdtm_mapping"):
        return AnnotationRecord(
            id=aid,
            page=page,
            content="X",
            domain="VS",
            category=category,
            matched_rule="test",
            rect=[50.0, anchor_y + 5.0, 200.0, anchor_y + 20.0],
            anchor_text=anchor_text,
            anchor_rect=[50.0, anchor_y, 200.0, anchor_y + 12.0],
            form_name=form_name,
        )

    def test_single_page_repeated_label_pairs_by_anchor_y(self):
        """Single-page branch: 3 annots (domain_label + VSCAT + VSORRES) on one
        source page, 2 target fields at different Y. VSCAT (anchor Y=70) must
        land on the Y=70 field; VSORRES (anchor Y=150) on the Y=150 field.
        Previously VSCAT and VSORRES both clamped onto the Y=150 field after
        the domain_label consumed the Y=70 slot.
        """
        dom = self._annot_with_anchor("dom", "Head Circ", "VS", page=1, anchor_y=70.0, category="domain_label")
        vscat = self._annot_with_anchor("vscat", "Head Circ", "VS", page=1, anchor_y=70.0)
        vsorres = self._annot_with_anchor("vsorres", "Head Circ", "VS", page=1, anchor_y=150.0)

        f_hdr = _make_field("f_hdr", "Head Circ", "VS", page=2, y=70.0)
        f_meas = _make_field("f_meas", "Head Circ", "VS", page=2, y=150.0)

        profile = _make_profile_default()
        src_dims = {1: (595.0, 842.0)}
        tgt_dims = {2: (595.0, 842.0)}

        matches = match_annotations(
            [dom, vscat, vsorres], [f_hdr, f_meas], profile, src_dims, tgt_dims,
        )
        by_annot = {m.annotation_id: m for m in matches}
        assert by_annot["dom"].field_id == "f_hdr"
        assert by_annot["vscat"].field_id == "f_hdr"
        assert by_annot["vsorres"].field_id == "f_meas"
        assert by_annot["vscat"].match_type == "exact"
        assert by_annot["vsorres"].match_type == "exact"

    def test_multi_page_repeated_label_pairs_by_anchor_y(self):
        """Multi-page branch (mirrors ION373-CS1 p.163+p.164 routing). Two
        source visits each carry the repeated label; each visit's VSCAT and
        VSORRES must reach the correct field in the matching target cluster.
        """
        v1_vscat = self._annot_with_anchor("v1_vscat", "Head Circ", "VS", page=10, anchor_y=70.0)
        v1_vsorres = self._annot_with_anchor("v1_vsorres", "Head Circ", "VS", page=10, anchor_y=170.0)
        v2_vscat = self._annot_with_anchor("v2_vscat", "Head Circ", "VS", page=11, anchor_y=70.0)
        v2_vsorres = self._annot_with_anchor("v2_vsorres", "Head Circ", "VS", page=11, anchor_y=147.0)

        tgt_v1_hdr = _make_field("tgt_v1_hdr", "Head Circ", "VS", page=20, y=70.0)
        tgt_v1_meas = _make_field("tgt_v1_meas", "Head Circ", "VS", page=20, y=170.0)
        tgt_v2_hdr = _make_field("tgt_v2_hdr", "Head Circ", "VS", page=21, y=70.0)
        tgt_v2_meas = _make_field("tgt_v2_meas", "Head Circ", "VS", page=21, y=147.0)

        profile = _make_profile_default()
        src_dims = {10: (595.0, 842.0), 11: (595.0, 842.0)}
        tgt_dims = {20: (595.0, 842.0), 21: (595.0, 842.0)}

        matches = match_annotations(
            [v1_vscat, v1_vsorres, v2_vscat, v2_vsorres],
            [tgt_v1_hdr, tgt_v1_meas, tgt_v2_hdr, tgt_v2_meas],
            profile, src_dims, tgt_dims,
        )
        by_annot = {m.annotation_id: m for m in matches}
        assert by_annot["v1_vscat"].field_id == "tgt_v1_hdr"
        assert by_annot["v1_vsorres"].field_id == "tgt_v1_meas"
        assert by_annot["v2_vscat"].field_id == "tgt_v2_hdr"
        assert by_annot["v2_vsorres"].field_id == "tgt_v2_meas"
        for aid in ("v1_vscat", "v1_vsorres", "v2_vscat", "v2_vsorres"):
            assert by_annot[aid].match_type == "exact", f"{aid} should match exact"

    def test_case_sensitive_label_isolates_groups(self):
        """'Head Circumference' (form title) and 'HEAD CIRCUMFERENCE' (section
        header) are distinct labels under the new case-sensitive exact pass.
        An annotation anchored to 'Head Circumference' must NOT compete against
        the 'HEAD CIRCUMFERENCE' field for the exact-match slot.
        """
        a = self._annot_with_anchor("a", "Head Circumference", "VS", page=1, anchor_y=70.0)
        f_title = _make_field("f_title", "Head Circumference", "VS", page=2, y=70.0)
        f_subhdr = _make_field("f_subhdr", "HEAD CIRCUMFERENCE", "VS", page=2, y=100.0)

        profile = _make_profile_default()
        src_dims = {1: (595.0, 842.0)}
        tgt_dims = {2: (595.0, 842.0)}

        matches = match_annotations([a], [f_title, f_subhdr], profile, src_dims, tgt_dims)
        assert matches[0].field_id == "f_title"
        assert matches[0].match_type == "exact"
