"""Tests for ui/profile_editor.py — form name tab widgets.

Streamlit is only installed in the project venv, not in the system Python used
for running tests. We stub out the entire `streamlit` module before importing
ui.profile_editor so the tests can run without a Streamlit installation.
"""
import sys
from unittest.mock import MagicMock, patch
import pytest


def _make_st_mock(
    checkbox_return=False,
    number_input_return=None,
    text_input_return="",
    selectbox_return="largest_bold_text",
):
    """Return a mock streamlit module with sensible defaults."""
    st = MagicMock()
    st.checkbox.return_value = checkbox_return
    st.number_input.return_value = number_input_return if number_input_return is not None else 0.0
    st.text_input.return_value = text_input_return
    st.selectbox.return_value = selectbox_return
    st.button.return_value = False
    return st


@pytest.fixture(autouse=True)
def stub_streamlit():
    """Insert a fake streamlit into sys.modules before any test, remove after."""
    fake_st = _make_st_mock()
    sys.modules.setdefault("streamlit", fake_st)
    # Force ui.profile_editor to be re-importable with our stub
    sys.modules.pop("ui.profile_editor", None)
    yield fake_st
    sys.modules.pop("ui.profile_editor", None)


class TestFormNameTabTopRegionFraction:
    def test_top_region_fraction_written_when_checkbox_enabled(self):
        """When checkbox is checked and number_input returns 0.30, draft gets 0.30."""
        st = _make_st_mock(checkbox_return=True, number_input_return=0.30)
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_form_name_tab
        draft = {"form_name_rules": {"top_region_fraction": 0.30}}
        _render_form_name_tab(draft)
        assert draft["form_name_rules"]["top_region_fraction"] == 0.30

    def test_top_region_fraction_none_when_checkbox_disabled(self):
        """When checkbox is unchecked, draft top_region_fraction is None."""
        st = _make_st_mock(checkbox_return=False)
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_form_name_tab
        draft = {"form_name_rules": {}}
        _render_form_name_tab(draft)
        assert draft["form_name_rules"].get("top_region_fraction") is None

    def test_checkbox_initialised_true_when_fraction_set(self):
        """Checkbox is initialised as True when top_region_fraction is already set."""
        st = _make_st_mock(checkbox_return=True, number_input_return=0.25)
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_form_name_tab
        draft = {"form_name_rules": {"top_region_fraction": 0.25}}
        _render_form_name_tab(draft)
        # Verify checkbox was called with value=True
        checkbox_call_kwargs = st.checkbox.call_args
        assert checkbox_call_kwargs is not None
        args, kwargs = checkbox_call_kwargs
        assert kwargs.get("value") is True or (len(args) > 1 and args[1] is True)


class TestFormNameTabLabelPrefix:
    def test_label_prefix_written_from_text_input(self):
        """text_input value 'Form:' is written to draft label_prefix."""
        st = _make_st_mock(text_input_return="Form:")
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_form_name_tab
        draft = {"form_name_rules": {}}
        _render_form_name_tab(draft)
        assert draft["form_name_rules"]["label_prefix"] == "Form:"

    def test_label_prefix_none_when_text_input_empty(self):
        """Empty text_input maps to None in draft (disables the feature)."""
        st = _make_st_mock(text_input_return="")
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_form_name_tab
        draft = {"form_name_rules": {}}
        _render_form_name_tab(draft)
        assert draft["form_name_rules"]["label_prefix"] is None

    def test_label_prefix_initialised_from_draft(self):
        """text_input is initialised with existing label_prefix value."""
        st = _make_st_mock(text_input_return="Form:")
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_form_name_tab
        draft = {"form_name_rules": {"label_prefix": "Form:"}}
        _render_form_name_tab(draft)
        # Verify text_input was called with value="Form:" for the Label Prefix field
        text_input_calls = st.text_input.call_args_list
        label_prefix_call = next(
            (c for c in text_input_calls if "Label Prefix" in str(c)), None
        )
        assert label_prefix_call is not None


class TestRenderListRow:
    """Tests for the _render_list_row helper."""

    def _make_tab_mock(self, button_return=False):
        st = _make_st_mock()
        st.button.return_value = button_return
        # container returns a context-manager mock
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        st.container.return_value = ctx
        # columns returns list of context-manager mocks
        def fake_columns(spec, **kw):
            n = spec if isinstance(spec, int) else len(spec)
            cols = []
            for _ in range(n):
                c = MagicMock()
                c.__enter__ = MagicMock(return_value=c)
                c.__exit__ = MagicMock(return_value=False)
                cols.append(c)
            return cols
        st.columns.side_effect = fake_columns
        return st

    def test_calls_content_fn_and_returns_false_when_not_deleted(self):
        """Helper renders content_fn and returns False when delete not clicked."""
        st = self._make_tab_mock(button_return=False)
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_list_row
        calls = []
        result = _render_list_row(0, lambda: calls.append(1), del_key="del_test_0")
        assert result is False
        assert calls == [1]

    def test_returns_true_when_delete_clicked(self):
        """Helper returns True when delete button is clicked."""
        st = self._make_tab_mock(button_return=True)
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_list_row
        result = _render_list_row(0, lambda: None, del_key="del_test_0")
        assert result is True

    def test_container_keyed_with_index(self):
        """Helper creates a container keyed with the row index."""
        st = self._make_tab_mock()
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_list_row
        _render_list_row(3, lambda: None, del_key="del_test_3")
        st.container.assert_called_once_with(key="list_row_row_3")


class TestFormNameExcludePatternsDelete:
    """Tests for exclude-patterns delete using _render_list_row."""

    def _make_tab_mock(self, button_key_that_deletes=None):
        st = _make_st_mock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        st.container.return_value = ctx
        def fake_columns(spec, **kw):
            n = spec if isinstance(spec, int) else len(spec)
            cols = []
            for _ in range(n):
                c = MagicMock()
                c.__enter__ = MagicMock(return_value=c)
                c.__exit__ = MagicMock(return_value=False)
                cols.append(c)
            return cols
        st.columns.side_effect = fake_columns
        st.button.side_effect = lambda label, key=None, **kw: key == button_key_that_deletes
        return st

    def test_delete_first_pattern_removes_it(self):
        """Clicking delete on row 0 removes first exclude pattern."""
        st = self._make_tab_mock(button_key_that_deletes="del_row_fnr_0")
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_form_name_tab
        draft = {"form_name_rules": {"exclude_patterns": ["DRAFT", "TEST"]}}
        _render_form_name_tab(draft)
        assert draft["form_name_rules"]["exclude_patterns"] == ["TEST"]

    def test_delete_second_pattern_removes_it(self):
        """Clicking delete on row 1 removes second exclude pattern."""
        st = self._make_tab_mock(button_key_that_deletes="del_row_fnr_1")
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_form_name_tab
        draft = {"form_name_rules": {"exclude_patterns": ["DRAFT", "TEST"]}}
        _render_form_name_tab(draft)
        assert draft["form_name_rules"]["exclude_patterns"] == ["DRAFT"]

    def test_no_delete_preserves_all_patterns(self):
        """When no delete button is clicked all patterns are preserved."""
        patterns_data = ["DRAFT", "TEST"]
        st = self._make_tab_mock(button_key_that_deletes=None)
        # Return the original pat value by matching the widget key index
        def text_input_side_effect(label, value="", key=None, **kw):
            return value  # echo back the value= arg (the original pattern)
        st.text_input.side_effect = text_input_side_effect
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_form_name_tab
        draft = {"form_name_rules": {"exclude_patterns": list(patterns_data)}}
        _render_form_name_tab(draft)
        assert draft["form_name_rules"]["exclude_patterns"] == ["DRAFT", "TEST"]

    def test_add_exclude_pattern_calls_rerun(self):
        """Clicking '+ Add Exclude Pattern' appends empty string and calls st.rerun()."""
        st = self._make_tab_mock(button_key_that_deletes=None)
        # Make the add button return True (simulate click)
        st.button.side_effect = lambda label, key=None, **kw: key == "add_exclude_pat"
        # Echo back the initial pattern value when text_input is called with value=
        def text_input_side_effect(label, value="", key=None, **kw):
            return value
        st.text_input.side_effect = text_input_side_effect
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)
        from ui.profile_editor import _render_form_name_tab
        draft = {"form_name_rules": {"exclude_patterns": ["DRAFT"]}}
        _render_form_name_tab(draft)
        st.rerun.assert_called_once()
        assert draft["form_name_rules"]["exclude_patterns"] == ["DRAFT", ""]


class TestDomainCodesTab:
    def _make_tab_mock(self, **kwargs):
        st = _make_st_mock(**kwargs)
        def fake_columns(spec, **kw):
            n = spec if isinstance(spec, int) else len(spec)
            return [MagicMock() for _ in range(n)]
        st.columns.side_effect = fake_columns
        return st

    def test_delete_removes_correct_code_and_sorts(self):
        """Clicking del_code_2 removes code at sorted index 2 and result is sorted.

        Codes are sorted before rendering: ["AE", "CM", "DM", "VS"].
        Index 2 = "DM", so ["AE", "CM", "VS"] remain.
        """
        st = self._make_tab_mock()
        st.button.side_effect = lambda label, key=None, **kw: key == "del_code_2"
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)

        from ui.profile_editor import _render_domain_codes_tab

        draft = {"domain_codes": ["DM", "AE", "VS", "CM"]}
        _render_domain_codes_tab(draft)

        # Sorted input: ["AE", "CM", "DM", "VS"]; index 2 = "DM" removed
        assert draft["domain_codes"] == ["AE", "CM", "VS"]

    def test_add_new_code_sorts_result(self):
        """Adding a new code inserts it in alphabetical order."""
        st = self._make_tab_mock(text_input_return="LB")
        st.button.side_effect = lambda label, key=None, **kw: key == "add_domain_code"
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)

        from ui.profile_editor import _render_domain_codes_tab

        draft = {"domain_codes": ["DM", "AE"]}
        _render_domain_codes_tab(draft)

        assert draft["domain_codes"] == ["AE", "DM", "LB"]

    def test_add_new_code_deduplicates(self):
        """Adding a code that already exists does not duplicate it."""
        st = self._make_tab_mock(text_input_return="dm")
        st.button.side_effect = lambda label, key=None, **kw: key == "add_domain_code"
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.profile_editor", None)

        from ui.profile_editor import _render_domain_codes_tab

        draft = {"domain_codes": ["DM", "AE"]}
        _render_domain_codes_tab(draft)

        assert draft["domain_codes"] == ["DM", "AE"]


class TestSaveProfile:
    """Tests for _save_profile — verifies stale fields are stripped on save."""

    def test_stale_anchor_text_exclude_patterns_not_written_to_disk(self, tmp_path):
        """_save_profile serialises profile.model_dump(), not the raw draft.

        A draft containing a stale ``anchor_text_config.exclude_patterns`` key
        (present in pre-beaf116 YAML files but removed from AnchorTextConfig)
        must NOT be written back to disk.  The fix is to pass
        ``profile.model_dump()`` to ``yaml.dump()`` instead of the raw draft.
        """
        import yaml
        from pathlib import Path

        # Build a minimal valid draft that includes the stale field
        draft = {
            "meta": {"name": "test", "version": "1.0", "parent": None},
            "domain_codes": ["DM"],
            "classification_rules": [],
            "form_name_rules": {
                "strategy": "largest_bold_text",
                "min_font_size": 12.0,
                "exclude_patterns": [],
                "top_region_fraction": None,
                "label_prefix": None,
            },
            "visit_rules": [],
            "anchor_text_config": {
                "radius_px": 100.0,
                "prefer_direction": ["left", "above"],
                "left_column_tolerance_px": 50.0,
                # stale field that should be stripped by model_dump()
                "exclude_patterns": ["DRAFT", "OBSOLETE"],
            },
            "annotation_filter": {
                "min_width": 10.0,
                "min_height": 10.0,
                "require_text": True,
            },
            "matching_config": {
                "exact_threshold": 1.0,
                "fuzzy_same_form_threshold": 0.8,
                "fuzzy_cross_form_threshold": 0.9,
                "position_fallback_confidence": 0.5,
            },
            "style_defaults": {
                "font_size": 18.0,
                "fill_color": [0.75, 1.0, 1.0],
                "text_color": [0.0, 0.0, 0.0],
                "border_width": 1.0,
            },
        }

        profiles_dir = tmp_path

        # Patch st, load_profile, and RuleEngine so _save_profile can run
        fake_st = _make_st_mock()
        sys.modules["streamlit"] = fake_st
        sys.modules.pop("ui.profile_editor", None)

        from unittest.mock import patch, MagicMock
        from src.profile_models import Profile

        fake_profile = Profile.model_validate(draft)

        with patch("ui.profile_editor.load_profile", return_value=fake_profile), \
             patch("ui.profile_editor.RuleEngine", return_value=MagicMock()):
            from ui.profile_editor import _save_profile
            _save_profile(profiles_dir, "test", draft)

        saved_path = profiles_dir / "test.yaml"
        assert saved_path.exists(), "Profile file was not written"

        saved = yaml.safe_load(saved_path.read_text(encoding="utf-8"))
        anchor_cfg = saved.get("anchor_text_config", {})
        assert "exclude_patterns" not in anchor_cfg, (
            f"Stale 'exclude_patterns' was written to disk: {anchor_cfg}"
        )
