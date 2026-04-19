"""Small CSS helpers for Streamlit UI widgets."""

from __future__ import annotations

from collections.abc import Sequence


def build_centered_icon_button_css(
    *,
    key_prefixes: Sequence[str],
    scope_selector: str = "",
    size_px: int,
    font_size_px: int,
    gap_px: int = 0,
) -> str:
    """Return CSS that centers icon-only Streamlit button labels.

    Streamlit sometimes wraps button content in an extra block element inside the
    keyed container, so this helper intentionally targets descendant buttons and
    their inner ``p`` nodes rather than assuming a direct-child button structure.
    """
    if not key_prefixes:
        raise ValueError("key_prefixes must not be empty")

    scope_prefix = f"{scope_selector} " if scope_selector else ""
    button_selectors = ",\n".join(
        f'{scope_prefix}[class*="st-key-{key_prefix}"] button'
        for key_prefix in key_prefixes
    )
    text_selectors = ",\n".join(
        f'{scope_prefix}[class*="st-key-{key_prefix}"] button p'
        for key_prefix in key_prefixes
    )
    # Optional gap between adjacent icon buttons (applies margin-left to all
    # but the first key in the provided sequence). Default is 0 (no gap).
    gap_rules = ""
    if gap_px and len(key_prefixes) > 1:
        parts: list[str] = []
        for key_prefix in key_prefixes[1:]:
            parts.append(f'{scope_prefix}[class*="st-key-{key_prefix}"] button {{ margin-left: {gap_px}px !important; }}')
        gap_rules = "\n" + "\n".join(parts)

    return (
        f"{button_selectors} {{\n"
        f"    padding: 0 !important;\n"
        f"    min-width: {size_px}px !important;\n"
        f"    width: {size_px}px !important;\n"
        f"    height: {size_px}px !important;\n"
        f"    min-height: {size_px}px !important;\n"
        f"    text-transform: none !important;\n"
        f"    letter-spacing: 0 !important;\n"
        f"    font-size: {font_size_px}px !important;\n"
        f"    display: flex !important;\n"
        f"    align-items: center !important;\n"
        f"    justify-content: center !important;\n"
        f"    line-height: 1 !important;\n"
        f"}}\n"
        f"{text_selectors} {{\n"
        f"    line-height: 1 !important;\n"
        f"    margin: 0 !important;\n"
        f"    padding: 0 !important;\n"
        f"}}"
        f"{gap_rules}"
    )
