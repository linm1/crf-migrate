"""Tests for small CSS helpers used by the Streamlit UI."""

import pytest


def test_build_centered_icon_button_css_preserves_optional_scope() -> None:
    """Optional scope selectors should prefix every generated rule."""
    from ui.style_helpers import build_centered_icon_button_css

    css = build_centered_icon_button_css(
        key_prefixes=["ws_rename_btn_"],
        scope_selector='section[data-testid="stSidebar"]',
        size_px=24,
        font_size_px=12,
    )

    assert 'section[data-testid="stSidebar"] [class*="st-key-ws_rename_btn_"] button' in css
    assert 'section[data-testid="stSidebar"] [class*="st-key-ws_rename_btn_"] button p' in css


def test_build_centered_icon_button_css_includes_centering_rules() -> None:
    """Icon-button CSS should keep glyphs centered within fixed-size buttons."""
    from ui.style_helpers import build_centered_icon_button_css

    css = build_centered_icon_button_css(
        key_prefixes=["ws_rename_btn_", "ws_del_btn_"],
        size_px=24,
        font_size_px=12,
    )

    assert '[class*="st-key-ws_rename_btn_"] button' in css
    assert '[class*="st-key-ws_del_btn_"] button' in css
    assert '[class*="st-key-ws_rename_btn_"] button p' in css
    assert '[class*="st-key-ws_del_btn_"] button p' in css
    assert "> button" not in css
    assert "min-width: 24px !important;" in css
    assert "width: 24px !important;" in css
    assert "height: 24px !important;" in css
    assert "font-size: 12px !important;" in css
    assert "display: flex !important;" in css
    assert "align-items: center !important;" in css
    assert "justify-content: center !important;" in css
    assert "line-height: 1 !important;" in css
    assert "text-transform: none !important;" in css


def test_build_centered_icon_button_css_rejects_empty_prefixes() -> None:
    """The helper should fail fast when there is nothing to target."""
    from ui.style_helpers import build_centered_icon_button_css

    with pytest.raises(ValueError, match="key_prefixes"):
        build_centered_icon_button_css(key_prefixes=[], size_px=24, font_size_px=12)
