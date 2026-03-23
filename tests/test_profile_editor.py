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
        _, kwargs = label_prefix_call
        assert kwargs.get("value") == "Form:"
