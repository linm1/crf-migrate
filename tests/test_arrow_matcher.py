"""Tests for resolve_arrows() in src/matcher.py (Phase 3 arrow resolution).

Verifies fuzzy text-block matching and 2-D proximity field matching.
"""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from src.matcher import resolve_arrows
from src.models import ArrowMatch, ArrowRecord, ArrowStyle, FieldRecord, MatchRecord
from src.profile_models import (
    ClassificationRule,
    Profile,
    ProfileMeta,
    RuleCondition,
)


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
    head_vertex: tuple[float, float] = (210.0, 65.0),
    source_page: int = 0,
) -> ArrowRecord:
    return ArrowRecord(
        arrow_id=arrow_id,
        source_page=source_page,
        tail_vertex=(50.0, 60.0),
        head_vertex=head_vertex,
        tail_annotation_id=tail_annotation_id,
        head_text=head_text,
        head_search_hint=head_vertex,
        style=_STYLE,
    )


def _make_field(
    field_id: str,
    label: str,
    rect: list[float],
    page: int = 1,
) -> FieldRecord:
    return FieldRecord(
        id=field_id,
        page=page,
        label=label,
        rect=rect,
        field_type="text_field",
        page_width=595.0,
        page_height=842.0,
    )


def _make_match(
    annotation_id: str,
    field_id: str,
    target_page: int = 1,
    target_rect: list[float] | None = None,
) -> MatchRecord:
    return MatchRecord(
        annotation_id=annotation_id,
        field_id=field_id,
        match_type="exact",
        confidence=1.0,
        target_rect=target_rect or [200.0, 55.0, 270.0, 75.0],
        target_page=target_page,
        status="approved",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def target_pdf(tmp_path_factory) -> Path:
    """Single-page target PDF with three labelled text blocks.

    Layout (approximate, PDF points, y from top):
    - "Yes"      near [210, 60, 240, 70]  — close to annotation rect [200,55,270,75]
    - "No"       near [210,100, 240,110]
    - "Option A" near [400,190, 460,200]  — far from annotation rect
    """
    path = tmp_path_factory.mktemp("matcher_fixtures") / "target.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(fitz.Point(210, 70), "Yes", fontsize=10, color=(0, 0, 0))
    page.insert_text(fitz.Point(210, 110), "No", fontsize=10, color=(0, 0, 0))
    page.insert_text(fitz.Point(400, 200), "Option A", fontsize=10, color=(0, 0, 0))
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="module")
def default_profile() -> Profile:
    return _make_profile()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolveArrows:
    def test_fuzzy_in_field_match(self, target_pdf, default_profile):
        """head_text='Yes' resolves against 'Yes' text block near annotation rect."""
        # annotation rect [200,55,270,75] inflated ±30 → [170,25,300,105]
        # "Yes" block ~[210,60,240,70] is inside the inflated rect → Pass A
        arrow = _make_arrow("a1", "Yes", "annot-1")
        match = _make_match("annot-1", "field-1", target_page=1,
                            target_rect=[200.0, 55.0, 270.0, 75.0])

        results = resolve_arrows([arrow], [match], [], target_pdf, default_profile)

        assert len(results) == 1
        r = results[0]
        assert r.arrow_id == "a1"
        assert r.head_match_method in ("fuzzy_in_field", "fuzzy_on_page")
        assert r.target_page == 1
        assert r.target_field_id == "field-1"
        assert r.head_target_rect is not None
        assert r.head_confidence > 0.8

    def test_fuzzy_on_page_when_outside_annot_rect(self, target_pdf, default_profile):
        """head_text='Option A' is far from the annotation rect → Pass B resolves it."""
        arrow = _make_arrow("a2", "Option A", "annot-2",
                            head_vertex=(400.0, 200.0))
        # annotation rect far left; Option A is at x=400
        match = _make_match("annot-2", "field-2", target_page=1,
                            target_rect=[50.0, 55.0, 100.0, 75.0])

        results = resolve_arrows([arrow], [match], [], target_pdf, default_profile)

        r = results[0]
        assert r.head_match_method == "fuzzy_on_page"
        assert r.head_confidence > 0.8
        assert r.head_target_rect is not None

    def test_unresolved_no_parent_match(self, target_pdf, default_profile):
        """tail_annotation_id with no MatchRecord → unresolved."""
        arrow = _make_arrow("a3", "Yes", "nonexistent")

        results = resolve_arrows([arrow], [], [], target_pdf, default_profile)

        assert results[0].head_match_method == "unresolved"
        assert results[0].head_target_rect is None
        assert results[0].head_confidence == pytest.approx(0.0)

    def test_unresolved_tail_none(self, target_pdf, default_profile):
        """tail_annotation_id=None → immediately unresolved."""
        arrow = _make_arrow("a4", "Yes", None)

        results = resolve_arrows([arrow], [], [], target_pdf, default_profile)

        r = results[0]
        assert r.head_match_method == "unresolved"
        assert r.target_page is None

    def test_unresolved_below_threshold(self, target_pdf):
        """head_text with no similar text on page stays unresolved."""
        profile = _make_profile(head_fuzzy_threshold=0.85)
        arrow = _make_arrow("a5", "XYZ_NOMATCH_999", "annot-5")
        match = _make_match("annot-5", "field-5", target_page=1)

        results = resolve_arrows([arrow], [match], [], target_pdf, profile)

        assert results[0].head_match_method == "unresolved"

    def test_result_count_matches_arrow_count(self, target_pdf, default_profile):
        """Returns exactly one ArrowMatch per ArrowRecord."""
        arrows = [
            _make_arrow("b1", "Yes", "annot-b1"),
            _make_arrow("b2", "No", "annot-b2"),
            _make_arrow("b3", "Yes", None),   # tail=None → unresolved
        ]
        matches = [
            _make_match("annot-b1", "field-b1", target_page=1),
            _make_match("annot-b2", "field-b2", target_page=1),
        ]

        results = resolve_arrows(arrows, matches, [], target_pdf, default_profile)

        assert len(results) == 3
        unresolved = [r for r in results if r.head_match_method == "unresolved"]
        resolved = [r for r in results if r.head_match_method != "unresolved"]
        assert len(unresolved) >= 1   # b3 (tail=None) is always unresolved
        assert len(resolved) >= 1     # b1 or b2 should resolve

    def test_proximity_field_selects_nearest_field(self, target_pdf, default_profile):
        """Pass B selects the FieldRecord with the smallest 2-D distance to head_vertex."""
        # head_vertex at (300, 150); field-near is closer than field-far
        # field-near rect [280, 140, 340, 160] → h_dist=0 (hx=300 inside), v_dist=0
        # field-far  rect [50,  50,  100, 70]  → h_dist=200, v_dist=80
        fields = [
            _make_field("field-near", "Near Field", [280.0, 140.0, 340.0, 160.0], page=1),
            _make_field("field-far",  "Far Field",  [50.0,  50.0,  100.0,  70.0], page=1),
        ]
        arrow = _make_arrow("px1", "XYZ_NOMATCH", "annot-px1", head_vertex=(300.0, 150.0))
        # annotation rect far left so Pass A won't fire
        match = _make_match("annot-px1", "field-px1", target_page=1,
                            target_rect=[10.0, 10.0, 30.0, 20.0])

        results = resolve_arrows([arrow], [match], fields, target_pdf, default_profile)

        r = results[0]
        assert r.head_match_method == "proximity_field"
        assert r.head_target_rect == (280.0, 140.0, 340.0, 160.0)
        assert r.head_confidence == pytest.approx(1.0)

    def test_proximity_field_ignores_wrong_page_fields(self, target_pdf, default_profile):
        """Pass B only considers fields on target_page; wrong-page fields are ignored."""
        fields = [
            _make_field("field-p2", "Page2 Field", [300.0, 150.0, 360.0, 170.0], page=2),
        ]
        arrow = _make_arrow("px2", "XYZ_NOMATCH", "annot-px2", head_vertex=(300.0, 150.0))
        match = _make_match("annot-px2", "field-px2", target_page=1,
                            target_rect=[10.0, 10.0, 30.0, 20.0])

        results = resolve_arrows([arrow], [match], fields, target_pdf, default_profile)

        # No fields on page 1 → falls back to fuzzy_on_page (no match) → unresolved
        r = results[0]
        assert r.head_match_method == "unresolved"

