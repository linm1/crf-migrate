"""Tests for profile_loader.py — TR.01, TR.02, TR.03, TR.14, TR.15."""
import pytest
from pathlib import Path
from pydantic import ValidationError
import yaml
import tempfile
import os

from src.profile_loader import load_profile, list_profiles, _deep_merge, _resolve_inheritance
from src.profile_models import Profile

PROFILES_DIR = Path(__file__).parent.parent / "profiles"
CDISC_PROFILE_PATH = PROFILES_DIR / "cdisc_standard.yaml"


# TR.01: Load valid YAML profile into Profile model
class TestLoadValidProfile:
    def test_load_cdisc_standard(self):
        """TR.01: Load valid YAML profile into Profile model."""
        profile = load_profile(CDISC_PROFILE_PATH)
        assert isinstance(profile, Profile)
        assert profile.meta.name == "CDISC Standard"
        assert len(profile.domain_codes) >= 21
        assert len(profile.classification_rules) >= 9
        assert len(profile.visit_rules) >= 5

    def test_profile_has_all_sections(self):
        """TR.01: All profile sections are populated."""
        profile = load_profile(CDISC_PROFILE_PATH)
        assert profile.meta is not None
        assert profile.domain_codes is not None
        assert profile.classification_rules is not None
        assert profile.form_name_rules is not None
        assert profile.anchor_text_config is not None
        assert profile.annotation_filter is not None
        assert profile.matching_config is not None
        assert profile.style_defaults is not None

    def test_profile_matching_config_defaults(self):
        """TR.01: Matching config has correct thresholds."""
        profile = load_profile(CDISC_PROFILE_PATH)
        assert profile.matching_config.exact_threshold == 1.0
        assert profile.matching_config.fuzzy_same_form_threshold == 0.80
        assert profile.matching_config.fuzzy_cross_form_threshold == 0.90
        assert profile.matching_config.position_fallback_confidence == 0.50


# TR.02: Reject profile with invalid regex
class TestInvalidRegex:
    def test_invalid_regex_in_classification_rule(self, tmp_path):
        """TR.02: Reject profile with invalid regex in classification rules."""
        bad_profile = {
            "meta": {"name": "Bad", "version": "1.0"},
            "domain_codes": ["DM"],
            "classification_rules": [
                {"conditions": {"regex": "[invalid"}, "category": "note"}
            ],
        }
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.dump(bad_profile))
        with pytest.raises((ValidationError, ValueError)):
            load_profile(path)

    def test_invalid_regex_in_visit_rules(self, tmp_path):
        """TR.02: Reject profile with invalid regex in visit rules."""
        bad_profile = {
            "meta": {"name": "Bad", "version": "1.0"},
            "domain_codes": ["DM"],
            "classification_rules": [
                {"conditions": {"fallback": True}, "category": "sdtm_mapping"}
            ],
            "visit_rules": [{"regex": "[bad", "value": "Test"}],
        }
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.dump(bad_profile))
        with pytest.raises((ValidationError, ValueError)):
            load_profile(path)


# TR.03: Reject profile with unknown condition type
class TestUnknownConditionType:
    def test_unknown_condition_field(self, tmp_path):
        """TR.03: Reject profile with unknown condition type."""
        bad_profile = {
            "meta": {"name": "Bad", "version": "1.0"},
            "domain_codes": ["DM"],
            "classification_rules": [
                {"conditions": {"foobar": True}, "category": "note"}
            ],
        }
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.dump(bad_profile))
        with pytest.raises((ValidationError, ValueError)):
            load_profile(path)


# TR.14: Profile inheritance — child overrides parent
class TestProfileInheritance:
    def test_child_overrides_parent_visit_rules(self, tmp_path):
        """TR.14: Child visit_rules completely replace parent visit_rules."""
        parent = {
            "meta": {"name": "Parent", "version": "1.0"},
            "domain_codes": ["DM", "AE"],
            "classification_rules": [
                {"conditions": {"fallback": True}, "category": "sdtm_mapping"}
            ],
            "visit_rules": [{"regex": "Screening", "value": "Screening"}],
        }
        child = {
            "meta": {"name": "Child", "version": "1.0", "parent": "parent"},
            "visit_rules": [{"regex": "V(\\d+)", "value": "Visit {1}"}],
        }
        (tmp_path / "parent.yaml").write_text(yaml.dump(parent))
        (tmp_path / "child.yaml").write_text(yaml.dump(child))

        profile = load_profile(tmp_path / "child.yaml", tmp_path)
        # Child visit_rules replaces parent (default _replace behavior)
        assert len(profile.visit_rules) == 1
        assert profile.visit_rules[0].regex == "V(\\d+)"

    def test_child_inherits_domain_codes(self, tmp_path):
        """TR.14: Child inherits domain_codes from parent when not specified."""
        parent = {
            "meta": {"name": "Parent", "version": "1.0"},
            "domain_codes": ["DM", "AE"],
            "classification_rules": [
                {"conditions": {"fallback": True}, "category": "sdtm_mapping"}
            ],
        }
        child = {
            "meta": {"name": "Child", "version": "1.0", "parent": "parent"},
        }
        (tmp_path / "parent.yaml").write_text(yaml.dump(parent))
        (tmp_path / "child.yaml").write_text(yaml.dump(child))

        profile = load_profile(tmp_path / "child.yaml", tmp_path)
        assert "DM" in profile.domain_codes
        assert "AE" in profile.domain_codes


# TR.15: Profile inheritance — _append merges lists
class TestProfileAppend:
    def test_append_domain_codes(self, tmp_path):
        """TR.15: _append extends parent domain_codes list."""
        parent = {
            "meta": {"name": "Parent", "version": "1.0"},
            "domain_codes": ["DM", "AE"],
            "classification_rules": [
                {"conditions": {"fallback": True}, "category": "sdtm_mapping"}
            ],
        }
        child = {
            "meta": {"name": "Child", "version": "1.0", "parent": "parent"},
            "domain_codes": {"_append": ["TU", "TR", "RS"]},
        }
        (tmp_path / "parent.yaml").write_text(yaml.dump(parent))
        (tmp_path / "child.yaml").write_text(yaml.dump(child))

        profile = load_profile(tmp_path / "child.yaml", tmp_path)
        assert "DM" in profile.domain_codes
        assert "AE" in profile.domain_codes
        assert "TU" in profile.domain_codes
        assert "TR" in profile.domain_codes
        assert "RS" in profile.domain_codes

    def test_circular_inheritance_raises(self, tmp_path):
        """Circular profile inheritance is detected and raises ValueError."""
        profile_a = {
            "meta": {"name": "A", "version": "1.0", "parent": "profile_b"},
            "domain_codes": ["DM"],
            "classification_rules": [
                {"conditions": {"fallback": True}, "category": "sdtm_mapping"}
            ],
        }
        profile_b = {
            "meta": {"name": "B", "version": "1.0", "parent": "profile_a"},
            "domain_codes": ["DM"],
            "classification_rules": [
                {"conditions": {"fallback": True}, "category": "sdtm_mapping"}
            ],
        }
        (tmp_path / "profile_a.yaml").write_text(yaml.dump(profile_a))
        (tmp_path / "profile_b.yaml").write_text(yaml.dump(profile_b))
        with pytest.raises(ValueError, match="Circular"):
            load_profile(tmp_path / "profile_a.yaml", tmp_path)


class TestProfileYamlFormNameConfig:
    """Verify new form_name_rules fields load correctly from YAML."""

    def test_cdisc_profile_top_region_fraction_set(self):
        """cdisc_standard.yaml sets top_region_fraction: 0.35 to restrict form name
        extraction to the top 35% of the true page height, preventing footers from
        being picked up."""
        profiles_dir = Path(__file__).parent.parent / "profiles"
        profile = load_profile(profiles_dir / "cdisc_standard.yaml")
        assert profile.form_name_rules.top_region_fraction == 0.35

    def test_rave_profile_label_prefix_loaded(self):
        """rave_medidata.yaml label_prefix='Form:' is loaded correctly."""
        profiles_dir = Path(__file__).parent.parent / "profiles"
        profile = load_profile(profiles_dir / "rave_medidata.yaml")
        assert profile.form_name_rules.label_prefix == "Form:"

    def test_rave_profile_inherits_top_region_fraction(self):
        """rave_medidata inherits top_region_fraction: 0.35 from cdisc_standard."""
        profiles_dir = Path(__file__).parent.parent / "profiles"
        profile = load_profile(profiles_dir / "rave_medidata.yaml")
        assert profile.form_name_rules.top_region_fraction == 0.35

    def test_rave_profile_label_prefix_extracts_form_name(self):
        """End-to-end: rave profile's label_prefix extracts 'Demographics'
        from a Medidata Rave-style metadata block."""
        from src.rule_engine import RuleEngine, TextBlock
        profiles_dir = Path(__file__).parent.parent / "profiles"
        profile = load_profile(profiles_dir / "rave_medidata.yaml")
        engine = RuleEngine(profile)
        blocks: list[TextBlock] = [
            TextBlock(text="Version 13.0: Complete CRF", font_size=10, bold=True,
                      rect=[50, 20, 400, 35]),
            TextBlock(text="Folder: Screening", font_size=10, bold=True,
                      rect=[50, 40, 300, 55]),
            TextBlock(text="Form: Demographics", font_size=10, bold=True,
                      rect=[50, 60, 300, 75]),
            TextBlock(text="Generated On: 05 Dec 2025 16:52:22", font_size=10, bold=True,
                      rect=[50, 80, 400, 95]),
        ]
        assert engine.extract_form_name(blocks) == "Demographics"


def test_use_source_style_flag_default(tmp_path):
    """use_source_style defaults to False when omitted."""
    yaml_text = """
meta:
  name: Test
domain_codes: [DM]
classification_rules:
  - conditions: {fallback: true}
    category: sdtm_mapping
"""
    p = tmp_path / "test.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    profile = load_profile(p)
    assert profile.style_defaults.use_source_style is False


def test_use_source_style_flag_true(tmp_path):
    """use_source_style=true is correctly loaded."""
    yaml_text = """
meta:
  name: Test
domain_codes: [DM]
classification_rules:
  - conditions: {fallback: true}
    category: sdtm_mapping
style_defaults:
  use_source_style: true
"""
    p = tmp_path / "test.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    profile = load_profile(p)
    assert profile.style_defaults.use_source_style is True


class TestListProfiles:
    def test_list_profiles_returns_yaml_stems(self, tmp_path):
        """list_profiles returns stem names of all .yaml files."""
        (tmp_path / "alpha.yaml").write_text("meta:\n  name: Alpha\n")
        (tmp_path / "beta.yaml").write_text("meta:\n  name: Beta\n")
        result = list_profiles(tmp_path)
        assert "alpha" in result
        assert "beta" in result
