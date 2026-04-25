"""Tests for rule_engine.py -- TR.04 through TR.16."""
import pytest
from pydantic import ValidationError
from src.profile_models import (
    Profile, ProfileMeta, ClassificationRule, RuleCondition,
    FormNameConfig, VisitRule, AnchorTextConfig, AnnotationFilter,
    MatchingConfig, StyleDefaults,
)
from src.rule_engine import RuleEngine, TextBlock


def make_profile(rules: list[dict], domain_codes: list[str] | None = None) -> Profile:
    """Helper: build a minimal Profile for testing the rule engine."""
    return Profile(
        meta=ProfileMeta(name="Test"),
        domain_codes=domain_codes or ["DM", "IE", "AE", "VS", "QS"],
        classification_rules=[
            ClassificationRule(conditions=RuleCondition(**r["conditions"]), category=r["category"])
            for r in rules
        ],
    )


def make_cdisc_profile() -> Profile:
    """Build the standard CDISC profile for integration tests."""
    rules = [
        {"conditions": {"max_length": 0}, "category": "_exclude"},
        {"conditions": {"subject_is": "Sticky Note"}, "category": "_exclude"},
        {"conditions": {"subject_is": "Typewritten Text", "max_length": 0}, "category": "_exclude"},
        {"conditions": {"contains": "[NOT SUBMITTED]"}, "category": "not_submitted"},
        {"conditions": {"regex": "^([A-Z]{2,4})=(.+)$", "domain_in": "domain_codes"}, "category": "domain_label"},
        {"conditions": {"multi_line": True}, "category": "note"},
        {"conditions": {"starts_with": "Note:"}, "category": "note"},
        {"conditions": {"contains": "RELREC"}, "category": "note"},
        {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
    ]
    return make_profile(rules, domain_codes=["DM", "IE", "MH", "CM", "AE", "VS", "EG", "PE", "QS", "DS"])


class TestClassifyContains:
    def test_tr04_contains_not_submitted(self):
        """TR.04: Classify content matching 'contains' condition."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        category, _ = engine.classify("[NOT SUBMITTED]", "")
        assert category == "not_submitted"

    def test_contains_case_insensitive(self):
        """contains check is case-insensitive."""
        profile = make_profile([
            {"conditions": {"contains": "RELREC"}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("See RELREC for details", "")
        assert category == "note"

    def test_contains_substring_match(self):
        """contains matches as a substring, not exact equality."""
        profile = make_profile([
            {"conditions": {"contains": "submit"}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("Not submitted yet", "")
        assert category == "note"

    def test_contains_no_match_falls_through(self):
        """contains fails when substring is absent."""
        profile = make_profile([
            {"conditions": {"contains": "RELREC"}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("BRTHDTC", "")
        assert category == "sdtm_mapping"


class TestClassifyRegexDomainIn:
    def test_tr05_domain_label(self):
        """TR.05: Classify content matching 'regex' + 'domain_in'."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        category, _ = engine.classify("DM=Demographics", "")
        assert category == "domain_label"

    def test_domain_not_in_list_falls_through(self):
        """TR.10 (partial): domain_in fails if domain not in list."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        # ZZ is not a domain code
        category, _ = engine.classify("ZZ=Unknown Domain", "")
        # Fallback rule should catch it
        assert category == "sdtm_mapping"

    def test_domain_in_multiple_codes(self):
        """domain_in works for any code in the domain_codes list."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        for code in ["AE", "VS", "CM", "EG"]:
            category, _ = engine.classify(f"{code}=Some Label", "")
            assert category == "domain_label", f"Expected domain_label for code {code}"

    def test_regex_without_domain_in_matches(self):
        """regex alone (without domain_in) matches on pattern only."""
        profile = make_profile([
            {"conditions": {"regex": r"^\d{4}-\d{2}-\d{2}$"}, "category": "date_field"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("2024-01-15", "")
        assert category == "date_field"

    def test_regex_no_match_falls_through(self):
        """regex condition fails when pattern does not match content."""
        profile = make_profile([
            {"conditions": {"regex": r"^\d{4}-\d{2}-\d{2}$"}, "category": "date_field"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("not-a-date", "")
        assert category == "sdtm_mapping"


class TestClassifyStartsWith:
    def test_tr06_starts_with_note(self):
        """TR.06: Classify content matching 'starts_with'."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        category, _ = engine.classify("Note: If abnormal please specify", "")
        assert category == "note"

    def test_starts_with_case_insensitive(self):
        """starts_with is case-insensitive."""
        profile = make_profile([
            {"conditions": {"starts_with": "NOTE:"}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("note: lower case prefix", "")
        assert category == "note"

    def test_starts_with_no_match(self):
        """starts_with fails when content does not begin with prefix."""
        profile = make_profile([
            {"conditions": {"starts_with": "Note:"}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("BRTHDTC", "")
        assert category == "sdtm_mapping"

    def test_starts_with_empty_string_always_matches(self):
        """starts_with empty string matches any content."""
        profile = make_profile([
            {"conditions": {"starts_with": ""}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("Anything at all", "")
        assert category == "note"


class TestClassifyMultiLine:
    def test_tr07_multi_line(self):
        """TR.07: Classify content with carriage-return line breaks as note."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        category, _ = engine.classify("1=Yes\r\n2=No\r\n3=Unknown", "")
        assert category == "note"

    def test_multi_line_with_newline(self):
        """multi_line: True matches content with \\n."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        category, _ = engine.classify("first line\nsecond line", "")
        assert category == "note"

    def test_multi_line_with_carriage_return_only(self):
        """multi_line: True matches content with bare \\r."""
        profile = make_profile([
            {"conditions": {"multi_line": True}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("line one\rline two", "")
        assert category == "note"

    def test_single_line_does_not_match_multi_line(self):
        """multi_line: True does NOT match single-line content."""
        profile = make_profile([
            {"conditions": {"multi_line": True}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("single line content", "")
        assert category == "sdtm_mapping"


class TestClassifyFallback:
    def test_tr08_fallback_sdtm_mapping(self):
        """TR.08: Fallback rule catches unmatched content."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        category, _ = engine.classify("BRTHDTC", "")
        assert category == "sdtm_mapping"

    def test_ultimate_fallback_no_rules_match(self):
        """If no rules match (no fallback rule), returns sdtm_mapping."""
        profile = make_profile([
            {"conditions": {"contains": "NOTE"}, "category": "note"},
            # No fallback rule
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("BRTHDTC", "")
        assert category == "sdtm_mapping"

    def test_fallback_returns_matched_rule_description(self):
        """Fallback rule returns non-empty matched_rule description."""
        profile = make_profile([
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        _, matched = engine.classify("anything", "")
        assert matched
        assert "Rule 1" in matched


class TestRuleOrder:
    def test_tr09_first_match_wins(self):
        """TR.09: Rule order matters -- first matching rule wins."""
        profile = make_profile([
            {"conditions": {"contains": "[NOT SUBMITTED]"}, "category": "not_submitted"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, matched = engine.classify("[NOT SUBMITTED]", "")
        assert category == "not_submitted"
        assert "Rule 1" in matched

    def test_second_rule_wins_when_first_fails(self):
        """Second rule wins when first does not match."""
        profile = make_profile([
            {"conditions": {"contains": "[NOT SUBMITTED]"}, "category": "not_submitted"},
            {"conditions": {"starts_with": "Note:"}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, matched = engine.classify("Note: some note", "")
        assert category == "note"
        assert "Rule 2" in matched

    def test_rule_order_preserved_across_profile_loading(self):
        """Rules are evaluated in the order they appear in the profile."""
        profile = make_profile([
            {"conditions": {"fallback": True}, "category": "first"},
            {"conditions": {"fallback": True}, "category": "second"},
        ])
        engine = RuleEngine(profile)
        category, matched = engine.classify("anything", "")
        assert category == "first"
        assert "Rule 1" in matched


class TestAndLogic:
    def test_tr10_all_conditions_must_match(self):
        """TR.10: AND logic -- all conditions in a rule must match."""
        profile = make_profile([
            {"conditions": {"regex": "^([A-Z]{2,4})=(.+)$", "domain_in": "domain_codes"}, "category": "domain_label"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ], domain_codes=["DM", "AE"])
        engine = RuleEngine(profile)

        # DM is in domain_codes → domain_label
        category, _ = engine.classify("DM=Demographics", "")
        assert category == "domain_label"

        # ZZ not in domain_codes → falls through to fallback
        category, _ = engine.classify("ZZ=Unknown", "")
        assert category == "sdtm_mapping"

    def test_subject_is_and_max_length_both_required(self):
        """AND logic: subject_is + max_length both required."""
        profile = make_profile([
            {"conditions": {"subject_is": "Typewritten Text", "max_length": 0}, "category": "_exclude"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)

        # subject matches but content is non-empty → NOT excluded
        category, _ = engine.classify("some content", "Typewritten Text")
        assert category == "sdtm_mapping"

        # Both match → excluded
        category, _ = engine.classify("", "Typewritten Text")
        assert category == "_exclude"

    def test_contains_and_starts_with_both_required(self):
        """AND logic: contains + starts_with both required to match."""
        profile = make_profile([
            {"conditions": {"contains": "keyword", "starts_with": "Note:"}, "category": "tagged_note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)

        # Only starts_with matches
        category, _ = engine.classify("Note: no keyword here... wait it is here actually", "")
        assert category == "tagged_note"

        # Only contains matches
        category, _ = engine.classify("This has keyword but wrong prefix", "")
        assert category == "sdtm_mapping"

        # Both match
        category, _ = engine.classify("Note: has keyword in it", "")
        assert category == "tagged_note"


class TestExcludeCategory:
    def test_tr11_sticky_note_excluded(self):
        """TR.11: _exclude category returned for sticky notes."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        category, _ = engine.classify("Some note", "Sticky Note")
        assert category == "_exclude"

    def test_empty_content_excluded(self):
        """TR.11: Empty content (max_length: 0) returns _exclude."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        category, _ = engine.classify("", "")
        assert category == "_exclude"

    def test_whitespace_only_not_excluded_by_max_length_zero(self):
        """Whitespace-only content has length > 0, not excluded by max_length: 0."""
        profile = make_profile([
            {"conditions": {"max_length": 0}, "category": "_exclude"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        # Single space has length 1, not excluded by max_length: 0
        category, _ = engine.classify(" ", "")
        assert category == "sdtm_mapping"

    def test_subject_is_case_insensitive(self):
        """subject_is comparison is case-insensitive."""
        profile = make_profile([
            {"conditions": {"subject_is": "sticky note"}, "category": "_exclude"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("content", "Sticky Note")
        assert category == "_exclude"

    def test_max_length_boundary_values(self):
        """max_length boundary: content of exactly max_length passes, one over fails."""
        profile = make_profile([
            {"conditions": {"max_length": 5}, "category": "short"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)

        # Exactly 5 chars → matches
        category, _ = engine.classify("hello", "")
        assert category == "short"

        # 6 chars → does not match
        category, _ = engine.classify("helloo", "")
        assert category == "sdtm_mapping"


class TestMinLength:
    def test_min_length_boundary(self):
        """min_length: content shorter than min fails the rule."""
        profile = make_profile([
            {"conditions": {"min_length": 10}, "category": "long_content"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)

        category, _ = engine.classify("short", "")
        assert category == "sdtm_mapping"

        category, _ = engine.classify("exactly ten!", "")
        assert category == "long_content"


class TestVisitExtraction:
    def test_tr12_visit_with_capture_group(self):
        """TR.12: Visit extraction with capture groups."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            visit_rules=[
                VisitRule(regex=r"Week\s*(\d+)", value="Week {1}"),
                VisitRule(regex=r"Screen(ing)?", value="Screening"),
            ],
        )
        engine = RuleEngine(profile)
        assert engine.extract_visit("Week 24 Assessment") == "Week 24"
        assert engine.extract_visit("Screening Visit") == "Screening"
        assert engine.extract_visit("No visit info") == ""

    def test_visit_no_match_returns_empty(self):
        """extract_visit returns empty string when no visit pattern matches."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            visit_rules=[VisitRule(regex="Screening", value="Screening")],
        )
        engine = RuleEngine(profile)
        assert engine.extract_visit("Regular page with no visit info") == ""

    def test_visit_empty_rules_returns_empty(self):
        """extract_visit returns empty string when visit_rules is empty."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            visit_rules=[],
        )
        engine = RuleEngine(profile)
        assert engine.extract_visit("Week 12") == ""

    def test_visit_first_match_wins(self):
        """extract_visit returns value for first matching rule."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            visit_rules=[
                VisitRule(regex=r"Week\s*(\d+)", value="Week {1}"),
                VisitRule(regex=r"Week", value="Some Week"),  # more general, but comes second
            ],
        )
        engine = RuleEngine(profile)
        assert engine.extract_visit("Week 4 Assessment") == "Week 4"

    def test_visit_no_capture_groups_uses_value_as_is(self):
        """extract_visit returns value verbatim when no capture groups exist."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            visit_rules=[VisitRule(regex=r"Baseline", value="Baseline Visit")],
        )
        engine = RuleEngine(profile)
        assert engine.extract_visit("Baseline Assessment") == "Baseline Visit"

    def test_visit_empty_page_text_returns_empty(self):
        """extract_visit returns empty string for empty page text."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            visit_rules=[VisitRule(regex=r"Week\s*(\d+)", value="Week {1}")],
        )
        engine = RuleEngine(profile)
        assert engine.extract_visit("") == ""


def _engine(form_name_kwargs: dict) -> RuleEngine:
    """Return a RuleEngine with a custom FormNameConfig and minimal other config."""
    profile = Profile(
        meta=ProfileMeta(name="Test"),
        domain_codes=["DM"],
        classification_rules=[
            ClassificationRule(
                conditions=RuleCondition(fallback=True),
                category="sdtm_mapping",
            )
        ],
        form_name_rules=FormNameConfig(**form_name_kwargs),
    )
    return RuleEngine(profile)


class TestFormNameExtraction:
    def test_tr13_form_name_excludes_patterns(self):
        """TR.13: Form name extraction excludes configured patterns."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            form_name_rules=FormNameConfig(
                strategy="largest_bold_text",
                min_font_size=12.0,
                exclude_patterns=["^CDISC$", r"^Study\s+CDISC"],
            ),
        )
        engine = RuleEngine(profile)
        text_blocks = [
            TextBlock(text="CDISC", font_size=14.0, bold=True, rect=[0, 0, 100, 20]),
            TextBlock(text="Study CDISC01", font_size=13.0, bold=True, rect=[0, 30, 200, 50]),
            TextBlock(text="VITAL SIGNS", font_size=16.0, bold=True, rect=[0, 60, 200, 80]),
            TextBlock(text="small text", font_size=8.0, bold=False, rect=[0, 90, 100, 110]),
        ]
        result = engine.extract_form_name(text_blocks)
        assert result == "VITAL SIGNS"

    def test_form_name_respects_min_font_size(self):
        """extract_form_name ignores text below min_font_size."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            form_name_rules=FormNameConfig(strategy="largest_bold_text", min_font_size=12.0),
        )
        engine = RuleEngine(profile)
        text_blocks = [
            TextBlock(text="Large Form Name", font_size=18.0, bold=True, rect=[0, 0, 200, 20]),
            TextBlock(text="Small text", font_size=8.0, bold=True, rect=[0, 30, 100, 50]),
        ]
        result = engine.extract_form_name(text_blocks)
        assert result == "Large Form Name"

    def test_form_name_empty_blocks_returns_empty(self):
        """extract_form_name returns empty string when no text blocks provided."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
        )
        engine = RuleEngine(profile)
        assert engine.extract_form_name([]) == ""

    def test_form_name_all_below_min_font_returns_empty(self):
        """extract_form_name returns empty string when all blocks fall below min_font_size."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            form_name_rules=FormNameConfig(strategy="largest_bold_text", min_font_size=20.0),
        )
        engine = RuleEngine(profile)
        text_blocks = [
            TextBlock(text="Form Name", font_size=16.0, bold=True, rect=[0, 0, 200, 20]),
        ]
        result = engine.extract_form_name(text_blocks)
        assert result == ""

    def test_form_name_all_excluded_by_pattern_returns_empty(self):
        """extract_form_name returns empty string when all candidates match exclude_patterns."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            form_name_rules=FormNameConfig(
                strategy="largest_bold_text",
                min_font_size=12.0,
                exclude_patterns=[".*"],
            ),
        )
        engine = RuleEngine(profile)
        text_blocks = [
            TextBlock(text="VITAL SIGNS", font_size=18.0, bold=True, rect=[0, 0, 200, 20]),
        ]
        result = engine.extract_form_name(text_blocks)
        assert result == ""

    def test_form_name_picks_topmost_qualifying_block(self):
        """extract_form_name selects the topmost (lowest y0) block that passes filters."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            form_name_rules=FormNameConfig(strategy="largest_bold_text", min_font_size=12.0),
        )
        engine = RuleEngine(profile)
        text_blocks = [
            TextBlock(text="Section A", font_size=14.0, bold=True, rect=[0, 0, 100, 20]),     # y0=0 — topmost
            TextBlock(text="DEMOGRAPHICS", font_size=14.0, bold=True, rect=[0, 30, 200, 50]), # y0=30 — same font size
            TextBlock(text="Sub-heading", font_size=14.0, bold=True, rect=[0, 60, 150, 80]),  # y0=60 — same font size
        ]
        result = engine.extract_form_name(text_blocks)
        # Same font size across all blocks → y0 tiebreak: "Section A" is topmost and wins
        assert result == "Section A"

    def test_form_name_strips_whitespace(self):
        """extract_form_name strips leading/trailing whitespace from result."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            form_name_rules=FormNameConfig(strategy="largest_bold_text", min_font_size=12.0),
        )
        engine = RuleEngine(profile)
        text_blocks = [
            TextBlock(text="  DEMOGRAPHICS  ", font_size=18.0, bold=True, rect=[0, 0, 200, 20]),
        ]
        result = engine.extract_form_name(text_blocks)
        assert result == "DEMOGRAPHICS"

    def test_form_name_skips_blank_text_blocks(self):
        """extract_form_name skips text blocks whose text is empty or whitespace."""
        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping")
            ],
            form_name_rules=FormNameConfig(strategy="largest_bold_text", min_font_size=12.0),
        )
        engine = RuleEngine(profile)
        text_blocks = [
            TextBlock(text="   ", font_size=24.0, bold=True, rect=[0, 0, 10, 10]),
            TextBlock(text="", font_size=22.0, bold=True, rect=[0, 20, 10, 30]),
            TextBlock(text="ADVERSE EVENTS", font_size=18.0, bold=True, rect=[0, 40, 200, 60]),
        ]
        result = engine.extract_form_name(text_blocks)
        assert result == "ADVERSE EVENTS"


    def test_form_name_config_accepts_top_region_fraction(self):
        """FormNameConfig validates top_region_fraction as optional float."""
        config = FormNameConfig(top_region_fraction=0.25)
        assert config.top_region_fraction == 0.25

    def test_form_name_config_top_region_fraction_defaults_none(self):
        """top_region_fraction defaults to None (no filtering)."""
        config = FormNameConfig()
        assert config.top_region_fraction is None

    def test_form_name_config_accepts_label_prefix(self):
        """FormNameConfig validates label_prefix as optional string."""
        config = FormNameConfig(label_prefix="Form:")
        assert config.label_prefix == "Form:"

    def test_form_name_config_label_prefix_defaults_none(self):
        """label_prefix defaults to None (strategy-based selection)."""
        config = FormNameConfig()
        assert config.label_prefix is None

    def test_form_name_label_prefix_extracts_value(self):
        """label_prefix='Form:' extracts 'Demographics' from 'Form: Demographics'."""
        engine = _engine({"label_prefix": "Form:"})
        blocks: list[TextBlock] = [
            TextBlock(text="Version 13.0: Complete CRF", font_size=10, bold=True,
                      rect=[50, 30, 400, 45]),
            TextBlock(text="Folder: Screening", font_size=10, bold=True,
                      rect=[50, 50, 300, 65]),
            TextBlock(text="Form: Demographics", font_size=10, bold=True,
                      rect=[50, 70, 300, 85]),
            TextBlock(text="Generated On: 05 Dec 2025 16:52:22", font_size=10, bold=True,
                      rect=[50, 90, 400, 105]),
        ]
        assert engine.extract_form_name(blocks) == "Demographics"

    def test_form_name_label_prefix_case_insensitive(self):
        """label_prefix matching is case-insensitive."""
        engine = _engine({"label_prefix": "form:"})
        blocks: list[TextBlock] = [
            TextBlock(text="FORM: Adverse Events", font_size=10, bold=False,
                      rect=[50, 30, 300, 45]),
        ]
        assert engine.extract_form_name(blocks) == "Adverse Events"

    def test_form_name_label_prefix_no_match_falls_through_to_strategy(self):
        """If no block matches label_prefix, falls through to largest_bold_text."""
        engine = _engine({"label_prefix": "Form:", "min_font_size": 10.0})
        blocks: list[TextBlock] = [
            TextBlock(text="VITAL SIGNS", font_size=18, bold=True,
                      rect=[50, 30, 300, 50]),
            TextBlock(text="Systolic BP: ___", font_size=10, bold=False,
                      rect=[50, 80, 300, 95]),
        ]
        assert engine.extract_form_name(blocks) == "VITAL SIGNS"

    def test_form_name_label_prefix_priority_over_largest_bold(self):
        """label_prefix result takes priority even when a larger block exists."""
        engine = _engine({"label_prefix": "Form:", "min_font_size": 8.0})
        blocks: list[TextBlock] = [
            TextBlock(text="HUGE HEADER", font_size=36, bold=True,
                      rect=[50, 10, 500, 50]),
            TextBlock(text="Form: Actual Form Name", font_size=10, bold=False,
                      rect=[50, 60, 300, 75]),
        ]
        assert engine.extract_form_name(blocks) == "Actual Form Name"

    def test_form_name_label_prefix_strips_whitespace(self):
        """Extracted value is stripped of surrounding whitespace."""
        engine = _engine({"label_prefix": "Form:"})
        blocks: list[TextBlock] = [
            TextBlock(text="Form:   Demographics  ", font_size=10, bold=False,
                      rect=[50, 30, 300, 45]),
        ]
        assert engine.extract_form_name(blocks) == "Demographics"

    def test_form_name_label_prefix_no_blocks_returns_empty(self):
        """label_prefix with empty block list returns empty string."""
        engine = _engine({"label_prefix": "Form:"})
        assert engine.extract_form_name([]) == ""

    def test_form_name_top_region_fraction_excludes_lower_blocks(self):
        """top_region_fraction is ignored; topmost block with same font size wins via y0."""
        # top_region_fraction is no longer used by the algorithm (retained in model only).
        # Block A: y0=50  — topmost, same font size → wins via y0 tiebreak
        # Block B: y0=400 — lower on page, same font size → loses
        engine = _engine({"top_region_fraction": 0.25, "min_font_size": 8.0})
        blocks: list[TextBlock] = [
            TextBlock(text="FORM TITLE", font_size=14, bold=True,
                      rect=[50, 50, 300, 70]),    # y0=50 — topmost
            TextBlock(text="LOWER BODY", font_size=14, bold=True,
                      rect=[50, 400, 300, 800]),  # y0=400 — same font size, lower
        ]
        assert engine.extract_form_name(blocks) == "FORM TITLE"

    def test_form_name_top_region_fraction_none_no_filtering(self):
        """top_region_fraction=None (default) applies no position filter.

        The algorithm ignores top_region_fraction entirely and returns the
        topmost qualifying block when font sizes are equal (y0 tiebreak).
        """
        engine = _engine({"top_region_fraction": None, "min_font_size": 8.0})
        blocks: list[TextBlock] = [
            TextBlock(text="SMALL HEADER", font_size=14, bold=True,
                      rect=[50, 50, 300, 70]),    # y0=50 — topmost, same font size
            TextBlock(text="BIG BODY", font_size=14, bold=True,
                      rect=[50, 500, 300, 525]),  # y0=500 — same font size, lower on page
        ]
        # Same font size → y0 tiebreak: "SMALL HEADER" is topmost and wins
        assert engine.extract_form_name(blocks) == "SMALL HEADER"

    def test_form_name_top_region_fraction_ignored_returns_topmost(self):
        """top_region_fraction is ignored by the new algorithm; topmost block wins.

        Previously this tested that top_region_fraction=0.10 excluded all blocks
        (since both had y0 > cutoff).  Now top_region_fraction has no effect and
        the topmost qualifying block is returned regardless.
        """
        engine = _engine({"top_region_fraction": 0.10, "min_font_size": 8.0})
        blocks: list[TextBlock] = [
            TextBlock(text="HEADER", font_size=18, bold=True,
                      rect=[50, 100, 300, 120]),   # y0=100 — topmost
            TextBlock(text="BODY", font_size=14, bold=False,
                      rect=[50, 300, 300, 500]),   # y0=300 — lower
        ]
        # New algorithm: top_region_fraction ignored → "HEADER" is topmost and qualifies
        assert engine.extract_form_name(blocks) == "HEADER"

    def test_form_name_top_region_fraction_picks_topmost_not_largest(self):
        """With the new algorithm, top_region_fraction is ignored; topmost block wins.

        top_region_fraction has no effect. When all blocks share the same font size,
        the topmost qualifying block is returned via y0 tiebreak.
        """
        engine = _engine({"top_region_fraction": 0.30, "min_font_size": 8.0})
        blocks: list[TextBlock] = [
            TextBlock(text="SMALL TOP", font_size=10, bold=True,
                      rect=[50, 30, 200, 45]),    # y0=30 — topmost, same font size
            TextBlock(text="MID BLOCK", font_size=10, bold=True,
                      rect=[50, 100, 300, 125]),  # y0=100 — same font size, lower
            TextBlock(text="LOW BLOCK", font_size=10, bold=True,
                      rect=[50, 600, 400, 900]),  # y0=600 — same font size, lowest
        ]
        # Same font size across all blocks → y0 tiebreak: "SMALL TOP" is topmost and wins
        assert engine.extract_form_name(blocks) == "SMALL TOP"

    def test_form_name_top_region_fraction_combined_with_exclude_patterns(self):
        """exclude_patterns filters work; top_region_fraction is ignored by algorithm."""
        engine = _engine({
            "top_region_fraction": 0.25,
            "min_font_size": 8.0,
            "exclude_patterns": ["^CDISC$"],
        })
        blocks: list[TextBlock] = [
            TextBlock(text="CDISC", font_size=14, bold=True,
                      rect=[50, 20, 200, 38]),    # excluded by pattern
            TextBlock(text="FORM NAME", font_size=14, bold=True,
                      rect=[50, 50, 250, 68]),    # passes all filters — topmost after exclusion
            TextBlock(text="BODY TEXT", font_size=14, bold=True,
                      rect=[50, 500, 300, 520]),  # same font size, lower — loses on y0
        ]
        assert engine.extract_form_name(blocks) == "FORM NAME"


class TestMatchedRule:
    def test_tr16_matched_rule_populated(self):
        """TR.16: matched_rule field is populated with rule description."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        _, matched_rule = engine.classify("BRTHDTC", "")
        assert matched_rule
        assert len(matched_rule) > 0

    def test_matched_rule_identifies_rule_number(self):
        """matched_rule includes the rule number."""
        profile = make_profile([
            {"conditions": {"contains": "NOTE"}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        _, matched = engine.classify("NOTE here", "")
        assert "Rule 1" in matched

        _, matched = engine.classify("BRTHDTC", "")
        assert "Rule 2" in matched

    def test_matched_rule_describes_condition_type(self):
        """matched_rule description names the condition type that matched."""
        profile = make_profile([
            {"conditions": {"contains": "RELREC"}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        _, matched = engine.classify("See RELREC", "")
        assert "contains" in matched.lower() or "RELREC" in matched

    def test_ultimate_fallback_matched_rule_non_empty(self):
        """Ultimate fallback (no rules at all match) returns non-empty matched_rule."""
        profile = make_profile([
            {"conditions": {"contains": "NOTE"}, "category": "note"},
        ])
        engine = RuleEngine(profile)
        category, matched = engine.classify("BRTHDTC", "")
        assert category == "sdtm_mapping"
        assert matched  # non-empty


class TestDomainInWithoutRegex:
    def test_domain_in_without_regex_returns_false(self):
        """domain_in guard: if regex_match is None (no regex condition), rule fails safely.

        This exercises line 133 -- the defensive branch protecting against a
        profile that specifies domain_in without a regex condition.
        """
        # Build a RuleCondition manually to bypass make_profile helper so we can
        # set domain_in without regex (profile_models allows this combination).
        from src.profile_models import RuleCondition as RC
        cond = RC(domain_in="domain_codes")  # no regex set

        profile = Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(conditions=cond, category="domain_label"),
                ClassificationRule(conditions=RuleCondition(fallback=True), category="sdtm_mapping"),
            ],
        )
        engine = RuleEngine(profile)
        # domain_in rule should fail (no regex → no match), fallback wins
        category, _ = engine.classify("DM=Demographics", "")
        assert category == "sdtm_mapping"


class TestFormNameTopToBottomScan:
    """Tests for the new top-to-bottom scan algorithm in extract_form_name.

    The new algorithm sorts blocks by y0 (ascending) and returns the first
    block that passes min_font_size and does not match any exclude_patterns.
    top_region_fraction is no longer used by the algorithm (retained in model
    for backward compatibility with existing YAML files only).
    """

    def test_form_name_returns_topmost_qualifying_block(self):
        """Returns the topmost (lowest y0) block that passes min_font_size."""
        engine = _engine({"min_font_size": 10.0})
        blocks: list[TextBlock] = [
            TextBlock(text="BOTTOM BLOCK", font_size=14.0, bold=True,
                      rect=[50, 400, 300, 420]),   # y0=400 — lower on page, same font size
            TextBlock(text="TOP BLOCK", font_size=14.0, bold=True,
                      rect=[50, 20, 300, 35]),     # y0=20 — higher on page, same font size
            TextBlock(text="MID BLOCK", font_size=14.0, bold=True,
                      rect=[50, 200, 300, 220]),   # y0=200 — middle, same font size
        ]
        # Same font size across all blocks → y0 tiebreak: "TOP BLOCK" is topmost and wins
        assert engine.extract_form_name(blocks) == "TOP BLOCK"

    def test_form_name_skips_excluded_patterns(self):
        """Blocks matching exclude_patterns are skipped; next qualifying block returned."""
        engine = _engine({
            "min_font_size": 10.0,
            "exclude_patterns": ["^CDISC$", r"^Study\s+\w+$"],
        })
        blocks: list[TextBlock] = [
            TextBlock(text="CDISC", font_size=14.0, bold=True,
                      rect=[50, 10, 200, 25]),     # y0=10 — excluded by pattern
            TextBlock(text="Study Protocol", font_size=12.0, bold=False,
                      rect=[50, 30, 300, 45]),     # y0=30 — excluded by pattern
            TextBlock(text="VITAL SIGNS", font_size=16.0, bold=True,
                      rect=[50, 60, 300, 80]),     # y0=60 — passes
        ]
        assert engine.extract_form_name(blocks) == "VITAL SIGNS"

    def test_form_name_skips_small_font(self):
        """Blocks below min_font_size are skipped even if topmost."""
        engine = _engine({"min_font_size": 12.0})
        blocks: list[TextBlock] = [
            TextBlock(text="tiny header", font_size=8.0, bold=False,
                      rect=[50, 10, 200, 20]),     # y0=10 — too small
            TextBlock(text="small label", font_size=10.0, bold=False,
                      rect=[50, 30, 200, 42]),     # y0=30 — too small
            TextBlock(text="DEMOGRAPHICS", font_size=18.0, bold=True,
                      rect=[50, 60, 300, 80]),     # y0=60 — qualifies
        ]
        assert engine.extract_form_name(blocks) == "DEMOGRAPHICS"

    def test_form_name_returns_empty_when_no_candidates(self):
        """Returns empty string when no blocks pass min_font_size + exclude filters."""
        engine = _engine({
            "min_font_size": 20.0,
            "exclude_patterns": [".*"],  # excludes everything
        })
        blocks: list[TextBlock] = [
            TextBlock(text="SOME BLOCK", font_size=24.0, bold=True,
                      rect=[50, 10, 300, 30]),
        ]
        assert engine.extract_form_name(blocks) == ""

    def test_form_name_top_to_bottom_ignores_font_size_ordering(self):
        """Largest font wins within the same boldness tier — NOT the topmost block.

        Within the same boldness tier, font size is the primary discriminator;
        y0 (position) is only the final tiebreaker when font sizes are equal.
        """
        engine = _engine({"min_font_size": 8.0})
        blocks: list[TextBlock] = [
            TextBlock(text="SMALLER TITLE", font_size=10.0, bold=True,
                      rect=[50, 30, 300, 42]),     # y0=30 — topmost but smaller font
            TextBlock(text="BIGGER HEADING", font_size=36.0, bold=True,
                      rect=[50, 200, 400, 240]),   # y0=200 — lower but larger font
        ]
        # Larger font wins: "BIGGER HEADING" (36pt) beats "SMALLER TITLE" (10pt)
        assert engine.extract_form_name(blocks) == "BIGGER HEADING"

    def test_form_name_skips_blank_blocks_in_scan(self):
        """Empty or whitespace-only blocks are skipped during top-to-bottom scan."""
        engine = _engine({"min_font_size": 8.0})
        blocks: list[TextBlock] = [
            TextBlock(text="   ", font_size=18.0, bold=True,
                      rect=[50, 10, 200, 25]),     # blank — skipped
            TextBlock(text="", font_size=16.0, bold=True,
                      rect=[50, 30, 200, 42]),     # blank — skipped
            TextBlock(text="ADVERSE EVENTS", font_size=14.0, bold=False,
                      rect=[50, 60, 300, 75]),     # qualifies
        ]
        assert engine.extract_form_name(blocks) == "ADVERSE EVENTS"

    def test_form_name_top_region_fraction_is_ignored_by_algorithm(self):
        """top_region_fraction in config does NOT filter blocks (backward compat field only)."""
        engine = _engine({
            "top_region_fraction": 0.05,  # Would exclude almost everything if used
            "min_font_size": 8.0,
        })
        blocks: list[TextBlock] = [
            TextBlock(text="DEEP PAGE TITLE", font_size=14.0, bold=True,
                      rect=[50, 300, 300, 320]),   # y0=300 — would be excluded if fraction used
        ]
        # New algorithm ignores top_region_fraction — should still return this block
        assert engine.extract_form_name(blocks) == "DEEP PAGE TITLE"

    def test_extract_form_name_prefers_bold_over_non_bold_running_header(self):
        """Bold block is preferred over a non-bold running header even when non-bold appears first.

        Regression: previously a non-bold running header ("Coagulation (Local)") at y0=20
        would be returned before the actual bold form title ("Demographics") at y0=40 because
        the scan was purely top-to-bottom without boldness prioritisation.
        """
        engine = _engine({"min_font_size": 8.0, "top_region_fraction": 0.35})
        # page_height=800 → top_cutoff = 0.35 * 800 = 280; both blocks are within cutoff
        blocks: list[TextBlock] = [
            TextBlock(text="Coagulation (Local)", font_size=10.0, bold=False,
                      rect=[0, 20, 200, 30]),   # non-bold, y0=20 — appears first top-to-bottom
            TextBlock(text="Demographics", font_size=10.0, bold=True,
                      rect=[0, 40, 200, 50]),   # bold, y0=40 — appears second
        ]
        # Bold blocks are sorted before non-bold blocks; "Demographics" must win
        assert engine.extract_form_name(blocks, page_height=800) == "Demographics"

    def test_form_name_larger_font_wins_over_smaller_topmost(self):
        """Within the same bold tier, the block with the largest font size wins.

        Even though block A is topmost (lower y0), block B wins because it has
        a larger font size. Font size is the second sort key after boldness.
        """
        engine = _engine({"min_font_size": 8.0})
        blocks: list[TextBlock] = [
            TextBlock(text="SMALL TITLE", font_size=10.0, bold=True,
                      rect=[50, 20, 300, 32]),    # y0=20 — topmost, smaller font
            TextBlock(text="LARGE TITLE", font_size=18.0, bold=True,
                      rect=[50, 60, 400, 80]),    # y0=60 — lower, larger font
        ]
        # Larger font wins: "LARGE TITLE" (18pt) beats "SMALL TITLE" (10pt)
        assert engine.extract_form_name(blocks) == "LARGE TITLE"


class TestAnchorTextLeftColumnAlgorithm:
    """Tests for the new left-column + vertical-distance anchor text algorithm."""

    def _make_profile_with_anchor_config(self, **kwargs) -> "Profile":
        from src.profile_models import AnchorTextConfig
        # exclude_patterns now lives in form_name_rules, not anchor_text_config
        exclude_patterns = kwargs.pop("exclude_patterns", [])
        return Profile(
            meta=ProfileMeta(name="Test"),
            domain_codes=["DM"],
            classification_rules=[
                ClassificationRule(
                    conditions=RuleCondition(fallback=True),
                    category="sdtm_mapping",
                )
            ],
            form_name_rules=FormNameConfig(exclude_patterns=exclude_patterns),
            anchor_text_config=AnchorTextConfig(**kwargs),
        )

    def test_anchor_uses_leftmost_column_only(self):
        """Blocks not in the left column are ignored even if vertically closer."""
        import fitz
        from src.extractor import _extract_anchor_text

        # Left column threshold: min x0 = 50, tolerance = 50 → threshold = 100
        # Block A x0=50 → in left column
        # Block B x0=400 → NOT in left column (even though vertically adjacent)
        profile = self._make_profile_with_anchor_config(
            left_column_tolerance_px=50.0,
            exclude_patterns=[],
        )
        annot_rect = fitz.Rect(50, 100, 250, 120)   # annotation in the middle
        text_blocks = [
            TextBlock(text="Left Label", font_size=10.0, bold=False,
                      rect=[50, 95, 200, 115]),    # x0=50, in left column, vertically adjacent
            TextBlock(text="Right Label", font_size=10.0, bold=False,
                      rect=[400, 98, 550, 118]),   # x0=400, NOT in left column (closer vertically)
        ]
        result, _ = _extract_anchor_text(annot_rect, profile, text_blocks)
        assert result == "Left Label"

    def test_anchor_uses_minimum_vertical_distance(self):
        """Among left-column blocks, returns the one with smallest vertical gap."""
        import fitz
        from src.extractor import _extract_anchor_text

        profile = self._make_profile_with_anchor_config(
            left_column_tolerance_px=50.0,
            exclude_patterns=[],
        )
        annot_rect = fitz.Rect(50, 200, 250, 220)
        text_blocks = [
            TextBlock(text="Far Above", font_size=10.0, bold=False,
                      rect=[50, 10, 200, 30]),     # x0=50, far above — large vertical gap
            TextBlock(text="Close Above", font_size=10.0, bold=False,
                      rect=[50, 180, 200, 198]),   # x0=50, just above annot — small gap
            TextBlock(text="Close Below", font_size=10.0, bold=False,
                      rect=[50, 222, 200, 240]),   # x0=50, just below annot — small gap
        ]
        # Both "Close Above" and "Close Below" are close; "Close Above" has 0 vert gap
        # (annot.y0=200, block y1=198 → overlap: vert_dist = max(0, max(200,180)-min(220,198)) = max(0, 200-198)=2)
        # "Close Below": vert_dist = max(0, max(200,222)-min(220,240)) = max(0, 222-220)=2
        # Tie — result is one of them, not "Far Above"
        result, _ = _extract_anchor_text(annot_rect, profile, text_blocks)
        assert result in ("Close Above", "Close Below")
        assert result != "Far Above"

    def test_anchor_returns_empty_when_no_left_column_blocks(self):
        """Returns empty string when all blocks are right of left_column_tolerance_px.

        To establish a meaningful left-column boundary, the page contains a reference
        left-column block at x0=50 (excluded by pattern), so min_x0=50 and
        left_threshold=100. The two candidate blocks at x0=200 and x0=500 are
        both beyond the threshold and are excluded from consideration.
        """
        import fitz
        from src.extractor import _extract_anchor_text

        profile = self._make_profile_with_anchor_config(
            left_column_tolerance_px=50.0,
            exclude_patterns=["^EXCLUDE_ME$"],
        )
        annot_rect = fitz.Rect(50, 100, 250, 120)
        text_blocks = [
            # Anchor block at x0=50 establishes min_x0=50 → threshold=100
            # but is excluded by pattern
            TextBlock(text="EXCLUDE_ME", font_size=10.0, bold=False,
                      rect=[50, 95, 200, 115]),    # x0=50, excluded by pattern
            # Both candidates are x0 > 100 — outside left column
            TextBlock(text="Right Column", font_size=10.0, bold=False,
                      rect=[200, 95, 400, 115]),   # x0=200 > threshold
            TextBlock(text="Far Right", font_size=10.0, bold=False,
                      rect=[500, 98, 700, 118]),   # x0=500 > threshold
        ]
        result, _ = _extract_anchor_text(annot_rect, profile, text_blocks)
        assert result == ""

    def test_anchor_excludes_patterns(self):
        """Excluded patterns are skipped even if in the left column."""
        import fitz
        from src.extractor import _extract_anchor_text

        profile = self._make_profile_with_anchor_config(
            left_column_tolerance_px=50.0,
            exclude_patterns=["^CDISC$", r"^\s*$"],
        )
        annot_rect = fitz.Rect(50, 100, 250, 120)
        text_blocks = [
            TextBlock(text="CDISC", font_size=10.0, bold=False,
                      rect=[50, 95, 200, 115]),    # x0=50, excluded by pattern
            TextBlock(text="Field Label", font_size=10.0, bold=False,
                      rect=[50, 125, 200, 140]),   # x0=50, passes
        ]
        result, _ = _extract_anchor_text(annot_rect, profile, text_blocks)
        assert result == "Field Label"

    def test_anchor_empty_blocks_returns_empty(self):
        """Returns empty string when text_blocks is empty."""
        import fitz
        from src.extractor import _extract_anchor_text

        profile = self._make_profile_with_anchor_config(
            left_column_tolerance_px=50.0,
            exclude_patterns=[],
        )
        annot_rect = fitz.Rect(50, 100, 250, 120)
        result, _ = _extract_anchor_text(annot_rect, profile, [])
        assert result == ""

    def test_anchor_vertical_overlap_is_zero_distance(self):
        """A block vertically overlapping the annotation has vert_dist=0 (lowest possible)."""
        import fitz
        from src.extractor import _extract_anchor_text

        profile = self._make_profile_with_anchor_config(
            left_column_tolerance_px=50.0,
            exclude_patterns=[],
        )
        annot_rect = fitz.Rect(50, 100, 250, 130)
        text_blocks = [
            TextBlock(text="Overlapping Label", font_size=10.0, bold=False,
                      rect=[50, 95, 200, 135]),    # overlaps annotation vertically → vert_dist=0
            TextBlock(text="Non-overlapping", font_size=10.0, bold=False,
                      rect=[50, 140, 200, 160]),   # below annotation — small gap
        ]
        result, _ = _extract_anchor_text(annot_rect, profile, text_blocks)
        assert result == "Overlapping Label"


class TestEdgeCases:
    def test_unicode_content_contains(self):
        """contains condition works with Unicode content."""
        profile = make_profile([
            {"conditions": {"contains": "visitee"}, "category": "note"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        category, _ = engine.classify("Patient visitee information", "")
        assert category == "note"

    def test_very_long_content(self):
        """classify handles very long content strings without error."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        long_content = "A" * 50_000
        category, matched = engine.classify(long_content, "")
        assert category in {"sdtm_mapping", "note", "_exclude", "not_submitted", "domain_label"}
        assert matched

    def test_classify_null_subject_substituted_empty(self):
        """classify does not raise when subject is an empty string."""
        profile = make_cdisc_profile()
        engine = RuleEngine(profile)
        # Should not raise
        category, _ = engine.classify("BRTHDTC", "")
        assert category == "sdtm_mapping"

    def test_special_regex_chars_in_contains(self):
        """contains condition treats the value as a literal string, not regex."""
        profile = make_profile([
            {"conditions": {"contains": "[NOT SUBMITTED]"}, "category": "not_submitted"},
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"},
        ])
        engine = RuleEngine(profile)
        # Literal brackets must match
        category, _ = engine.classify("Field [NOT SUBMITTED]", "")
        assert category == "not_submitted"

        # Should NOT match when brackets are absent
        category, _ = engine.classify("NOT SUBMITTED", "")
        assert category == "sdtm_mapping"


def _engine_with_anchor(form_name_kwargs: dict, anchor_kwargs: dict | None = None) -> RuleEngine:
    """Return a RuleEngine with custom FormNameConfig and optional AnchorTextConfig."""
    from src.profile_models import AnchorTextConfig
    profile = Profile(
        meta=ProfileMeta(name="Test"),
        domain_codes=["DM"],
        classification_rules=[
            ClassificationRule(
                conditions=RuleCondition(fallback=True),
                category="sdtm_mapping",
            )
        ],
        form_name_rules=FormNameConfig(**form_name_kwargs),
        anchor_text_config=AnchorTextConfig(**(anchor_kwargs or {})),
    )
    return RuleEngine(profile)


class TestFormNameTopLeftBlock:
    """TDD tests for the 'top_left_block' form-name extraction strategy.

    Strategy: among blocks in the leftmost column (x0 <= min_x0 + tolerance),
    sort by y0 ascending and return the first block that passes exclude_patterns.
    min_font_size is deliberately ignored.
    """

    def test_extract_form_name_top_left_block_strategy(self):
        """top_left_block picks the top-left block, not the largest/boldest one.

        Layout:
          - Large center block at x0=200, y0=300  (would win under largest_bold_text)
          - Small top-left block at x0=20, y0=10  (should win under top_left_block)
        left_column_tolerance_px=50 → threshold = min(20,200) + 50 = 70
        Block at x0=20 qualifies (20 <= 70); block at x0=200 does not.
        """
        engine = _engine_with_anchor(
            {"strategy": "top_left_block", "min_font_size": 16.0},
            {"left_column_tolerance_px": 50.0},
        )
        blocks: list[TextBlock] = [
            TextBlock(text="DEMOGRAPHICS", font_size=24.0, bold=True,
                      rect=[200, 300, 500, 330]),  # large block in center
            TextBlock(text="Form Title", font_size=8.0, bold=False,
                      rect=[20, 10, 180, 28]),     # small block top-left
        ]
        result = engine.extract_form_name(blocks)
        assert result == "Form Title"

    def test_extract_form_name_top_left_block_filters_right_column(self):
        """Returns '' when all text blocks are in the right column only.

        min_x0 of all blocks = 350; left_column_tolerance_px = 10.
        left_threshold = 350 + 10 = 360.
        Block A x0=350 → IN left column (350 <= 360) but its text is blank → skipped.
        Block B x0=400 → NOT in left column (400 > 360) → excluded.
        Block C x0=500 → NOT in left column (500 > 360) → excluded.
        No eligible left-column block has non-empty text → returns ''.
        """
        engine = _engine_with_anchor(
            {"strategy": "top_left_block", "min_font_size": 8.0},
            {"left_column_tolerance_px": 10.0},
        )
        # Use x0 values spread far apart so only the leftmost is "in column".
        # The leftmost block has blank text, so nothing gets returned.
        blocks: list[TextBlock] = [
            TextBlock(text="   ", font_size=14.0, bold=False,
                      rect=[350, 10, 360, 20]),   # x0=350 — in left column, blank text
            TextBlock(text="Right Block B", font_size=14.0, bold=True,
                      rect=[400, 10, 550, 30]),   # x0=400 — outside threshold (400 > 360)
            TextBlock(text="Right Block C", font_size=18.0, bold=True,
                      rect=[500, 50, 650, 70]),   # x0=500 — outside threshold
        ]
        result = engine.extract_form_name(blocks)
        assert result == ""

    def test_extract_form_name_top_left_block_respects_exclude_patterns(self):
        """top-left block matching exclude_patterns is skipped; next left-column block returned.

        Two blocks are in the left column:
          y0=10  text="Page Header"  → matches exclude pattern '^Page'
          y0=50  text="VITAL SIGNS"  → passes all filters → returned
        """
        engine = _engine_with_anchor(
            {
                "strategy": "top_left_block",
                "min_font_size": 8.0,
                "exclude_patterns": ["^Page"],
            },
            {"left_column_tolerance_px": 60.0},
        )
        blocks: list[TextBlock] = [
            TextBlock(text="Page Header", font_size=10.0, bold=False,
                      rect=[30, 10, 250, 25]),   # x0=30, y0=10 — excluded by pattern
            TextBlock(text="VITAL SIGNS", font_size=8.0, bold=False,
                      rect=[30, 50, 200, 65]),   # x0=30, y0=50 — should be returned
            TextBlock(text="Body text", font_size=10.0, bold=False,
                      rect=[300, 100, 500, 115]),  # right column — ignored
        ]
        result = engine.extract_form_name(blocks)
        assert result == "VITAL SIGNS"

    def test_extract_form_name_top_left_block_ignores_min_font_size(self):
        """top_left_block does NOT filter by min_font_size (unlike largest_bold_text)."""
        engine = _engine_with_anchor(
            {"strategy": "top_left_block", "min_font_size": 20.0},
            {"left_column_tolerance_px": 50.0},
        )
        # Font size is 6.0 — far below min_font_size=20.0 — but top_left_block ignores it.
        blocks: list[TextBlock] = [
            TextBlock(text="Tiny Form Title", font_size=6.0, bold=False,
                      rect=[20, 5, 200, 18]),
        ]
        result = engine.extract_form_name(blocks)
        assert result == "Tiny Form Title"

    def test_formname_strategy_validator_rejects_unknown(self):
        """FormNameConfig raises ValidationError for an unknown strategy value."""
        from pydantic import ValidationError as PydanticValidationError
        with pytest.raises(PydanticValidationError):
            FormNameConfig(strategy="invalid_strategy")

    def test_formname_strategy_validator_accepts_largest_bold_text(self):
        """FormNameConfig accepts 'largest_bold_text' without error."""
        config = FormNameConfig(strategy="largest_bold_text")
        assert config.strategy == "largest_bold_text"

    def test_formname_strategy_validator_accepts_top_left_block(self):
        """FormNameConfig accepts 'top_left_block' without error."""
        config = FormNameConfig(strategy="top_left_block")
        assert config.strategy == "top_left_block"

    def test_extract_form_name_top_left_block_label_prefix_still_takes_priority(self):
        """label_prefix still takes priority over top_left_block strategy."""
        engine = _engine_with_anchor(
            {
                "strategy": "top_left_block",
                "label_prefix": "Form:",
                "min_font_size": 8.0,
            },
            {"left_column_tolerance_px": 50.0},
        )
        blocks: list[TextBlock] = [
            TextBlock(text="Top Left Block", font_size=10.0, bold=False,
                      rect=[20, 10, 200, 25]),
            TextBlock(text="Form: Actual Name", font_size=10.0, bold=False,
                      rect=[20, 50, 250, 65]),
        ]
        result = engine.extract_form_name(blocks)
        assert result == "Actual Name"

    def test_extract_form_name_top_left_block_top_region_fraction_still_prefilters(self):
        """top_region_fraction pre-filter still restricts candidates before top_left_block."""
        engine = _engine_with_anchor(
            {
                "strategy": "top_left_block",
                "top_region_fraction": 0.20,
                "min_font_size": 8.0,
            },
            {"left_column_tolerance_px": 50.0},
        )
        # page_height=800 → cutoff = 0.20 * 800 = 160
        # Block A y0=10  → in top region AND left column → returned
        # Block B y0=500 → below cutoff → excluded from eligible pool
        blocks: list[TextBlock] = [
            TextBlock(text="In Top Region", font_size=8.0, bold=False,
                      rect=[20, 10, 200, 28]),
            TextBlock(text="Below Cutoff", font_size=8.0, bold=False,
                      rect=[20, 500, 200, 520]),
        ]
        result = engine.extract_form_name(blocks, page_height=800.0)
        assert result == "In Top Region"

    def test_design_prefix_excluded(self):
        """Design: metadata should be skipped; the next valid block is returned."""
        engine = _engine_with_anchor(
            {"strategy": "top_left_block", "min_font_size": 6.5},
            {"left_column_tolerance_px": 50.0},
        )
        blocks: list[TextBlock] = [
            TextBlock(text="Design: 2024-07-26 IA#005_UAT#3", font_size=8.4, bold=False,
                      rect=[206, 77, 400, 90]),
            TextBlock(text="Adverse Events", font_size=7.3, bold=True,
                      rect=[76, 102, 250, 115]),
        ]
        result = engine.extract_form_name(blocks, page_height=841.0)
        assert result == "Adverse Events"


class TestSharedExcludePatterns:
    def test_form_name_and_anchor_excludes_are_same_object(self):
        """_form_name_excludes and _anchor_excludes must be the same object (unified list)."""
        from src.profile_loader import load_profile
        from pathlib import Path
        profile = load_profile(Path("profiles/cdisc_standard.yaml"))
        engine = RuleEngine(profile)
        assert engine._form_name_excludes is engine._anchor_excludes

    def test_form_name_excludes_uses_form_name_patterns(self):
        """_form_name_excludes is compiled from form_name_rules.exclude_patterns."""
        from src.profile_loader import load_profile
        from pathlib import Path
        profile = load_profile(Path("profiles/cdisc_standard.yaml"))
        engine = RuleEngine(profile)
        form_name_pattern_strings = {p.pattern for p in engine._form_name_excludes}
        for raw in profile.form_name_rules.exclude_patterns:
            assert raw in form_name_pattern_strings

    def test_anchor_exclude_patterns_property_returns_anchor_excludes(self):
        """anchor_exclude_patterns property returns _anchor_excludes."""
        from src.profile_loader import load_profile
        from pathlib import Path
        profile = load_profile(Path("profiles/cdisc_standard.yaml"))
        engine = RuleEngine(profile)
        assert engine.anchor_exclude_patterns is engine._anchor_excludes

    def test_form_name_exclude_patterns_property_returns_form_name_excludes(self):
        """form_name_exclude_patterns property returns _form_name_excludes."""
        from src.profile_loader import load_profile
        from pathlib import Path
        profile = load_profile(Path("profiles/cdisc_standard.yaml"))
        engine = RuleEngine(profile)
        assert engine.form_name_exclude_patterns is engine._form_name_excludes

    def test_form_name_and_anchor_exclude_patterns_are_same_object(self):
        """form_name_exclude_patterns and anchor_exclude_patterns return the same object."""
        from src.profile_loader import load_profile
        from pathlib import Path
        profile = load_profile(Path("profiles/cdisc_standard.yaml"))
        engine = RuleEngine(profile)
        assert engine.form_name_exclude_patterns is engine.anchor_exclude_patterns

    def test_form_name_exclude_patterns_is_form_name_excludes(self):
        """form_name_exclude_patterns property returns the same object as _form_name_excludes."""
        from src.profile_loader import load_profile
        from pathlib import Path
        profile = load_profile(Path("profiles/cdisc_standard.yaml"))
        engine = RuleEngine(profile)
        assert engine.form_name_exclude_patterns is engine._form_name_excludes
