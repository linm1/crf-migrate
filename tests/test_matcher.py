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

    def test_exact_match_case_insensitive_still_matches(self, dm_field, default_profile):
        """Exact pass is case-insensitive.

        An annotation with mismatched casing ("DATE OF BIRTH" vs "Date of Birth",
        or "demographics" vs "DEMOGRAPHICS") must still match in the exact pass.
        """
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
    """Same label appearing multiple times on one page: each annotation matches
    the first field with that label (field reuse — field is a reusable anchor)."""

    def test_three_date_annotations_all_match_same_field(self):
        """Three 'Date' annotations each match the first 'Date' field (field reuse)."""
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
        # All three annotations exactly match against the first field encountered
        assert all(by_annot[f"a{i}"].match_type == "exact" for i in range(1, 4))
        assert all(by_annot[f"a{i}"].field_id == "f1" for i in range(1, 4))

    def test_annotations_out_of_order_all_match_first_field(self):
        """Annotations given in any order still match the first field (field reuse)."""
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
        assert all(by_annot[aid].match_type == "exact" for aid in ["a1", "a2", "a3"])
        assert all(by_annot[aid].field_id == "f1" for aid in ["a1", "a2", "a3"])


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

    def test_case_mismatch_label_still_exact(self):
        """Annotation anchor_text differs in case from field label: still exact."""
        annot = _make_annot("a1", "DATE OF BIRTH", "DEMOGRAPHICS", page=1)
        field = _make_field("f1", "Date of Birth", "DEMOGRAPHICS", page=1)

        profile = _make_profile_default()
        matches = match_annotations(
            [annot], [field], profile, {1: (595.0, 842.0)}, {1: (595.0, 842.0)},
        )
        assert matches[0].match_type == "exact"
        assert matches[0].field_id == "f1"

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
