"""Tests for resolve_arrows() in src/matcher.py (Phase 3 arrow resolution)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.matcher import resolve_arrows
from src.models import ArrowMatch, ArrowRecord, ArrowStyle, FieldRecord, MatchRecord
from src.profile_models import (
    ClassificationRule,
    MatchingConfig,
    Profile,
    ProfileMeta,
    RuleCondition,
)
from tests.fixtures.create_arrow_fixtures import create_arrow_target_pdf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STYLE = ArrowStyle(
    stroke_color=(1.0, 0.0, 0.0),
    width=1.0,
    dashes=[],
    line_ends=(4, 0),
    opacity=1.0,
)


def _make_profile(**arrow_overrides) -> Profile:
    profile = Profile(
        meta=ProfileMeta(name="test"),
        domain_codes=["DM"],
        classification_rules=[
            ClassificationRule(
                conditions=RuleCondition(fallback=True),
                category="sdtm_mapping",
            )
        ],
    )
    if arrow_overrides:
        profile = profile.model_copy(
            update={"arrows": profile.arrows.model_copy(update=arrow_overrides)}
        )
    return profile


def _make_arrow(
    arrow_id: str,
    head_text: str,
    tail_annotation_id: str | None,
    source_page: int = 0,
) -> ArrowRecord:
    return ArrowRecord(
        arrow_id=arrow_id,
        source_page=source_page,
        tail_vertex=(200.0, 60.0),
        head_vertex=(210.0, 65.0),
        tail_annotation_id=tail_annotation_id,
        head_text=head_text,
        head_search_hint=(210.0, 65.0),
        style=_STYLE,
    )


def _make_match(annotation_id: str, field_id: str, target_page: int = 1) -> MatchRecord:
    return MatchRecord(
        annotation_id=annotation_id,
        field_id=field_id,
        match_type="exact",
        confidence=1.0,
        target_rect=[200.0, 55.0, 270.0, 75.0],
        target_page=target_page,
        status="approved",
    )


def _make_field(field_id: str, rect: list[float], page: int = 1) -> FieldRecord:
    return FieldRecord(
        id=field_id,
        page=page,
        label="Test Field",
        rect=rect,
        field_type="text_field",
    )


@pytest.fixture(scope="module")
def target_pdf(tmp_path_factory) -> Path:
    """Synthetic target PDF with Yes / No / Unknown text blocks on page 1."""
    p = tmp_path_factory.mktemp("arrow_target") / "target.pdf"
    return create_arrow_target_pdf(p)


@pytest.fixture(scope="module")
def default_profile() -> Profile:
    return _make_profile()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolveArrows:
    def test_fuzzy_in_field_resolves(self, target_pdf, default_profile):
        """Pass A: head_text='Yes' found within inflated field rect."""
        # "Yes" is inserted at (210, 65). Block bbox will be approx
        # [210, 55, 260, 70]. Field rect [200, 55, 270, 75] inflated by 4
        # → [196, 51, 274, 79] which contains the "Yes" block.
        arrow = _make_arrow("a1", "Yes", "annot-1")
        match = _make_match("annot-1", "field-1", target_page=1)
        field = _make_field("field-1", [200.0, 55.0, 270.0, 75.0], page=1)

        results = resolve_arrows(
            [arrow], [match], [field], target_pdf, default_profile
        )

        assert len(results) == 1
        r = results[0]
        assert r.arrow_id == "a1"
        assert r.head_match_method == "fuzzy_in_field"
        assert r.head_confidence >= 0.85
        assert r.target_page == 1
        assert r.target_field_id == "field-1"
        assert r.head_target_rect is not None

    def test_fuzzy_on_page_fallback(self, target_pdf, default_profile):
        """Pass B: head_text='No' found on page but not inside small field rect."""
        # "No" block is at approx [210, 95, 260, 110].
        # Field rect [50, 50, 200, 70] inflated → [46, 46, 204, 74].
        # The "No" block x0=210 > 204 so it's outside the inflated rect.
        arrow = _make_arrow("a2", "No", "annot-2")
        match = _make_match("annot-2", "field-2", target_page=1)
        field = _make_field("field-2", [50.0, 50.0, 200.0, 70.0], page=1)

        results = resolve_arrows(
            [arrow], [match], [field], target_pdf, default_profile
        )

        assert len(results) == 1
        r = results[0]
        assert r.arrow_id == "a2"
        assert r.head_match_method == "fuzzy_on_page"
        assert r.head_confidence >= 0.85

    def test_unresolved_no_parent_match(self, target_pdf, default_profile):
        """Arrow with tail_annotation_id that has no corresponding MatchRecord."""
        arrow = _make_arrow("a3", "Yes", "nonexistent")

        results = resolve_arrows(
            [arrow], [], [], target_pdf, default_profile
        )

        assert len(results) == 1
        r = results[0]
        assert r.arrow_id == "a3"
        assert r.head_match_method == "unresolved"
        assert r.head_target_rect is None
        assert r.head_confidence == pytest.approx(0.0)

    def test_unresolved_tail_none(self, target_pdf, default_profile):
        """Arrow with tail_annotation_id=None is immediately unresolved."""
        arrow = _make_arrow("a4", "Yes", None)

        results = resolve_arrows(
            [arrow], [], [], target_pdf, default_profile
        )

        assert len(results) == 1
        r = results[0]
        assert r.head_match_method == "unresolved"
        assert r.target_page is None

    def test_threshold_from_profile(self, target_pdf):
        """Profile threshold blocks a partial match (head_text 'Ye' vs block 'Yes')."""
        # token_sort_ratio("Ye", "Yes") < 1.0, so threshold=0.99 blocks resolution.
        profile = _make_profile(head_fuzzy_threshold=0.99)

        arrow = _make_arrow("a5", "Ye", "annot-5")
        match = _make_match("annot-5", "field-5", target_page=1)
        field = _make_field("field-5", [200.0, 55.0, 270.0, 75.0], page=1)

        results = resolve_arrows(
            [arrow], [match], [field], target_pdf, profile
        )

        assert results[0].head_match_method == "unresolved"

    def test_multiple_arrows_independent(self, target_pdf, default_profile):
        """Two arrows resolved independently: first to 'Yes', second to 'No'."""
        arrow_yes = _make_arrow("a6", "Yes", "annot-6")
        arrow_no = _make_arrow("a7", "No", "annot-7")

        # Both fields are narrow so neither overlaps the other block.
        # field-6: [200, 55, 270, 75] → "Yes" in field (Pass A)
        # field-7: [50, 50, 200, 70]  → "No" not in field (Pass B)
        match_yes = _make_match("annot-6", "field-6", target_page=1)
        match_no = _make_match("annot-7", "field-7", target_page=1)
        field_yes = _make_field("field-6", [200.0, 55.0, 270.0, 75.0], page=1)
        field_no = _make_field("field-7", [50.0, 50.0, 200.0, 70.0], page=1)

        results = resolve_arrows(
            [arrow_yes, arrow_no],
            [match_yes, match_no],
            [field_yes, field_no],
            target_pdf,
            default_profile,
        )

        assert len(results) == 2
        r_yes = next(r for r in results if r.arrow_id == "a6")
        r_no = next(r for r in results if r.arrow_id == "a7")

        assert r_yes.head_match_method in ("fuzzy_in_field", "fuzzy_on_page")
        assert r_yes.head_confidence >= 0.85
        assert r_no.head_match_method in ("fuzzy_in_field", "fuzzy_on_page")
        assert r_no.head_confidence >= 0.85
