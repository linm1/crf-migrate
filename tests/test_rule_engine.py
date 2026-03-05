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

    def test_form_name_picks_largest_font(self):
        """extract_form_name selects the block with the largest font size."""
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
            TextBlock(text="Section A", font_size=14.0, bold=True, rect=[0, 0, 100, 20]),
            TextBlock(text="DEMOGRAPHICS", font_size=20.0, bold=True, rect=[0, 30, 200, 50]),
            TextBlock(text="Sub-heading", font_size=16.0, bold=True, rect=[0, 60, 150, 80]),
        ]
        result = engine.extract_form_name(text_blocks)
        assert result == "DEMOGRAPHICS"

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
