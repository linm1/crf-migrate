"""Shared UI widgets and utility functions for CRF-Migrate."""
from pathlib import Path

import fitz  # PyMuPDF
import streamlit as st

from src.models import AnnotationRecord, FieldRecord


# ---------------------------------------------------------------------------
# Phase status bar
# ---------------------------------------------------------------------------

def render_phase_status_bar(phases_complete: dict[int, bool]) -> None:
    """Render a 4-column phase status bar with color-coded dots."""
    cols = st.columns(4)
    labels = {
        1: "Phase 1: Extract Annotations",
        2: "Phase 2: Extract Fields",
        3: "Phase 3: Match",
        4: "Phase 4: Output",
    }
    # Determine current phase = first incomplete
    current = next((p for p in [1, 2, 3, 4] if not phases_complete.get(p, False)), None)
    for i, col in enumerate(cols):
        phase_num = i + 1
        if phases_complete.get(phase_num, False):
            color = "#28a745"  # green
            symbol = "●"
        elif phase_num == current:
            color = "#ffc107"  # yellow
            symbol = "●"
        else:
            color = "#6c757d"  # gray
            symbol = "●"
        col.markdown(
            f'<span style="color:{color};font-size:18px">{symbol}</span> '
            f'<strong>{labels[phase_num]}</strong>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Page navigator
# ---------------------------------------------------------------------------

def render_page_navigator(total_pages: int, key: str) -> int:
    """Return 1-indexed selected page number."""
    if total_pages <= 0:
        return 1
    if total_pages <= 100:
        options = list(range(1, total_pages + 1))
        return st.selectbox(
            f"Page (1–{total_pages})", options, key=key, index=0
        )
    else:
        return st.number_input(
            f"Page (1–{total_pages})", min_value=1, max_value=total_pages,
            value=1, step=1, key=key
        )


def render_page_navigator_inline(total_pages: int, key: str) -> int:
    """Render a compact ‹ [N] / T › inline navigator. Returns current 1-indexed page.

    Stores page in st.session_state[key] so prev/next buttons update it.
    Designed to sit inside a narrow column alongside a tab bar.
    """
    if total_pages <= 0:
        return 1

    state_key = f"_pgnav_{key}"
    if state_key not in st.session_state:
        st.session_state[state_key] = 1

    current = int(st.session_state[state_key])

    # Inject CSS: hide native number_input steppers, tighten width
    st.markdown(
        f"""<style>
        .st-key-{key}_nav_input input[type=number] {{
            -moz-appearance: textfield;
        }}
        .st-key-{key}_nav_input input::-webkit-outer-spin-button,
        .st-key-{key}_nav_input input::-webkit-inner-spin-button {{
            -webkit-appearance: none; margin: 0;
        }}
        .st-key-{key}_nav_input div[data-testid="stNumberInput"] {{
            width: 64px !important;
        }}
        .st-key-{key}_nav_input div[data-testid="stNumberInput"] > div {{
            min-width: 0 !important;
        }}
        .st-key-{key}_nav_wrap {{
            display: flex; align-items: center; gap: 4px;
            justify-content: flex-end; padding-top: 4px;
        }}
        </style>""",
        unsafe_allow_html=True,
    )

    prev_col, input_col, label_col, next_col = st.columns(
        [1, 2, 2, 1], gap="small", vertical_alignment="center"
    )

    with prev_col:
        if st.button("‹", key=f"{key}_prev", disabled=current <= 1,
                     use_container_width=True):
            st.session_state[state_key] = max(1, current - 1)
            st.rerun()

    with input_col:
        with st.container(key=f"{key}_nav_input"):
            jumped = st.number_input(
                "page", min_value=1, max_value=total_pages,
                value=current, step=1,
                label_visibility="collapsed",
                key=f"{key}_num",
            )
        if jumped != current:
            st.session_state[state_key] = int(jumped)
            st.rerun()

    with label_col:
        st.markdown(
            f'<span style="font-family:Inter,sans-serif;font-size:13px;'
            f'color:#8A847F;white-space:nowrap;">/ {total_pages}</span>',
            unsafe_allow_html=True,
        )

    with next_col:
        if st.button("›", key=f"{key}_next", disabled=current >= total_pages,
                     use_container_width=True):
            st.session_state[state_key] = min(total_pages, current + 1)
            st.rerun()

    return int(st.session_state[state_key])


def render_page_navigator_windowed(total_pages: int, key: str) -> int:
    """Windowed paginator: shows 10 page buttons at a time with Prev/Next window shift.

    Renders as a full-width horizontal row. Returns current 1-indexed page.
    State keys: _pgnav_{key} (current page), _pgwin_{key} (window start).
    """
    if total_pages <= 0:
        return 1

    page_key = f"_pgnav_{key}"
    win_key  = f"_pgwin_{key}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 1
    if win_key not in st.session_state:
        st.session_state[win_key] = 1

    current   = int(st.session_state[page_key])

    # Window boundaries: 1–9, 10–19, 20–29, ..., 100–109, ...
    # win_start: 1 for pages 1-9, then multiples of 10 (10, 20, 30...)
    def _window_start_for(p: int) -> int:
        return 1 if p < 10 else (p // 10) * 10

    def _window_end_for(ws: int) -> int:
        return min(ws + 8 if ws == 1 else ws + 9, total_pages)

    win_start = _window_start_for(current)
    win_end   = _window_end_for(win_start)
    # Store win_start so prev/next can navigate
    st.session_state[win_key] = win_start

    def _go_prev_window():
        ws = int(st.session_state[win_key])
        new_win = 1 if ws <= 10 else ws - 10
        st.session_state[win_key]  = new_win
        st.session_state[page_key] = new_win

    def _go_next_window():
        ws = int(st.session_state[win_key])
        new_win = min((10 if ws == 1 else ws + 10), total_pages)
        st.session_state[win_key]  = new_win
        st.session_state[page_key] = new_win

    def _go_page(p: int):
        st.session_state[page_key] = p

    _FW = str.maketrans("0123456789", "０１２３４５６７８９")

    def _fw(n: int) -> str:
        """Convert integer to full-width digit string."""
        return str(n).translate(_FW)

    st.markdown(
        f"""<style>
        /* Tight flex row, right-aligned */
        .st-key-{key}_pgwrap [data-testid="stHorizontalBlock"] {{
            justify-content: flex-end !important;
            gap: 4px !important;
            flex-wrap: nowrap !important;
            align-items: center !important;
        }}
        /* All buttons: fixed height, no extra margin */
        .st-key-{key}_pgwrap button {{
            height: 32px !important;
            min-height: 32px !important;
            padding: 0 4px !important;
            font-size: 13px !important;
            margin: 0 !important;
            line-height: 32px !important;
        }}
        /* Page number buttons only (not < and >): fixed width for 3 full-width chars */
        .st-key-{key}_pgwrap [data-testid="stButton"]:not(:first-child):not(:last-child) button {{
            width: 52px !important;
            min-width: 52px !important;
            max-width: 52px !important;
            text-align: center !important;
            justify-content: center !important;
            display: flex !important;
            align-items: center !important;
            letter-spacing: 0 !important;
        }}
        /* Active page button: dark fill */
        .st-key-{key}_pgwrap [data-testid="stButton"]:nth-child({current - win_start + 2}) button {{
            background: #383838 !important;
            color: #FFFFFF !important;
            border: 1px solid #383838 !important;
            font-weight: 700 !important;
        }}
        </style>""",
        unsafe_allow_html=True,
    )

    with st.container(key=f"{key}_pgwrap"):
        with st.container(horizontal=True):
            st.button("<", key=f"{key}_pgprev",
                      on_click=_go_prev_window, disabled=(win_start == 1))
            for p in range(win_start, win_end + 1):
                st.button(_fw(p), key=f"{key}_pg{p}",
                          on_click=_go_page, args=[p])
            st.button(">", key=f"{key}_pgnext",
                      on_click=_go_next_window, disabled=win_end >= total_pages)

    return int(st.session_state[page_key])


# ---------------------------------------------------------------------------
# Annotation card
# ---------------------------------------------------------------------------

_ANNOT_LABEL_COLORS = {
    "sdtm_mapping":  "blue",
    "domain_label":  "orange",
    "not_submitted": "green",
    "note":          "gray",
    "_exclude":      "red",
}

_ANNOT_BG_COLORS = {
    "sdtm_mapping":  "rgba(111,194,255,0.12)",
    "domain_label":  "rgba(255,222,0,0.10)",
    "not_submitted": "rgba(39,201,63,0.07)",
    "note":          "rgba(238,238,238,0.35)",
    "_exclude":      "rgba(255,95,86,0.07)",
}

_ANNOT_CATEGORIES = ["sdtm_mapping", "domain_label", "not_submitted", "note", "_exclude"]


def render_annotation_card(
    annot: AnnotationRecord,
    index: int,
    key_prefix: str,
    on_save,    # callable(updated: AnnotationRecord) -> None
    on_delete,  # callable() -> None
) -> None:
    """Render annotation card matching profile-editor classification rule style.

    Expander label: **form_name** (colored by category) — *content preview* (gray italic)
    Background tint driven by category. Per-item Save + Delete in footer.
    """
    color = _ANNOT_LABEL_COLORS.get(annot.category, "gray")
    bg = _ANNOT_BG_COLORS.get(annot.category, "rgba(238,238,238,0.18)")
    form_badge = f"**{annot.form_name}**" if annot.form_name else f"**{annot.domain}**"
    pattern_suffix = annot.content[:50] if annot.content else annot.anchor_text[:50]
    label = f":{color}[{form_badge}] :gray[*{pattern_suffix}*]"

    st.markdown(
        f"<style>.st-key-annot_exp_{key_prefix}_{index} details"
        f"{{background:{bg} !important}}</style>",
        unsafe_allow_html=True,
    )
    with st.container(key=f"annot_exp_{key_prefix}_{index}"):
        with st.expander(label, expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                content = st.text_area(
                    "Content", value=annot.content,
                    key=f"{key_prefix}_{index}_content",
                )
                domain = st.text_input(
                    "Domain", value=annot.domain,
                    key=f"{key_prefix}_{index}_domain",
                )
                cat_idx = _ANNOT_CATEGORIES.index(annot.category) \
                    if annot.category in _ANNOT_CATEGORIES else 0
                category = st.selectbox(
                    "Category", _ANNOT_CATEGORIES, index=cat_idx,
                    key=f"{key_prefix}_{index}_category",
                )
            with col2:
                anchor_text = st.text_input(
                    "Anchor Text", value=annot.anchor_text,
                    key=f"{key_prefix}_{index}_anchor",
                )
                form_name = st.text_input(
                    "Form Name", value=annot.form_name,
                    key=f"{key_prefix}_{index}_form",
                )
                visit = st.text_input(
                    "Visit", value=annot.visit,
                    key=f"{key_prefix}_{index}_visit",
                )
            btn_save, btn_del, _ = st.columns([1, 1, 4], gap="small")
            with btn_save:
                if st.button("Save", key=f"{key_prefix}_{index}_save",
                             use_container_width=True):
                    on_save(annot.model_copy(update={
                        "content": content,
                        "domain": domain,
                        "category": category,
                        "anchor_text": anchor_text,
                        "form_name": form_name,
                        "visit": visit,
                    }))
            with btn_del:
                if st.button("Delete", key=f"{key_prefix}_{index}_del",
                             use_container_width=True):
                    on_delete()


# ---------------------------------------------------------------------------
# Field card
# ---------------------------------------------------------------------------

_FIELD_TYPES = ["text_field", "checkbox", "date_field", "table_row", "section_header"]

_FIELD_TYPE_COLORS = {
    "text_field":     "blue",
    "checkbox":       "green",
    "date_field":     "orange",
    "table_row":      "violet",
    "section_header": "gray",
}

_FIELD_TYPE_BG_COLORS = {
    "text_field":     "rgba(111,194,255,0.12)",
    "checkbox":       "rgba(39,201,63,0.07)",
    "date_field":     "rgba(255,222,0,0.10)",
    "table_row":      "rgba(150,80,255,0.06)",
    "section_header": "rgba(238,238,238,0.35)",
}


def render_field_card(
    field: FieldRecord,
    index: int,
    key_prefix: str,
    on_save,    # callable(updated: FieldRecord) -> None
    on_delete,  # callable() -> None
) -> None:
    """Render field card matching profile-editor classification rule style.

    Expander label: **form_name** (colored by field_type) — *label* (gray italic)
    Background tint driven by field_type. Per-item Save + Delete in footer.
    """
    color = _FIELD_TYPE_COLORS.get(field.field_type, "gray")
    bg = _FIELD_TYPE_BG_COLORS.get(field.field_type, "rgba(238,238,238,0.18)")
    form_badge = f"**{field.form_name}**" if field.form_name else f"**{field.label[:30]}**"
    label_suffix = field.label[:50] if field.form_name else field.field_type
    expander_label = f":{color}[{form_badge}] :gray[*{label_suffix}*]"

    st.markdown(
        f"<style>.st-key-field_exp_{key_prefix}_{index} details"
        f"{{background:{bg} !important}}</style>",
        unsafe_allow_html=True,
    )
    with st.container(key=f"field_exp_{key_prefix}_{index}"):
        with st.expander(expander_label, expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                label_val = st.text_input(
                    "Label", value=field.label,
                    key=f"{key_prefix}_{index}_label",
                )
                form_name = st.text_input(
                    "Form Name", value=field.form_name,
                    key=f"{key_prefix}_{index}_form",
                )
                visit = st.text_input(
                    "Visit", value=field.visit,
                    key=f"{key_prefix}_{index}_visit",
                )
            with col2:
                type_idx = _FIELD_TYPES.index(field.field_type) \
                    if field.field_type in _FIELD_TYPES else 0
                field_type = st.selectbox(
                    "Field Type", _FIELD_TYPES, index=type_idx,
                    key=f"{key_prefix}_{index}_type",
                )
            btn_save, btn_del, _ = st.columns([1, 1, 4], gap="small")
            with btn_save:
                if st.button("Save", key=f"{key_prefix}_{index}_save",
                             use_container_width=True):
                    on_save(field.model_copy(update={
                        "label": label_val,
                        "form_name": form_name,
                        "visit": visit,
                        "field_type": field_type,
                    }))
            with btn_del:
                if st.button("Delete", key=f"{key_prefix}_{index}_del",
                             use_container_width=True):
                    on_delete()


# ---------------------------------------------------------------------------
# Annotation row (compact, design-spec style)
# ---------------------------------------------------------------------------

# Domain badge chip colors  [bg, border, text]
_DOMAIN_BADGE_COLORS: dict[str, tuple[str, str, str]] = {}

_CATEGORY_CHIP: dict[str, tuple[str, str, str]] = {
    "sdtm_mapping":  ("#EEF2FF", "#C7D2FE", "#4F46E5"),
    "domain_label":  ("#FEF9C3", "#FDE68A", "#92400E"),
    "not_submitted": ("#F0FDF4", "#BBF7D0", "#16A34A"),
    "note":          ("#F1F5F9", "#CBD5E1", "#475569"),
    "_exclude":      ("#FEF2F2", "#FECACA", "#DC2626"),
}


def render_annotation_row(
    annot: AnnotationRecord,
    index: int,
    key_prefix: str,
    on_delete,  # callable() -> None
) -> None:
    """Render a compact 64px annotation row matching the Penpot design spec.

    Layout: [domain badge chip] | [title + subtitle] | [✕ button]
    """
    chip_bg, chip_border, chip_text = _CATEGORY_CHIP.get(
        annot.category, ("#F1F5F9", "#CBD5E1", "#475569")
    )
    domain_label = (annot.domain or annot.category or "—")[:6].upper()
    title = annot.form_name or annot.content[:40] if annot.content else "—"
    subtitle_parts = [
        "FreeText",
        f"Page {annot.page}",
    ]
    if annot.anchor_text:
        subtitle_parts.append(f"anchor: {annot.anchor_text[:30]}")
    subtitle = " · ".join(subtitle_parts)

    row_html = f"""
    <div style="
        display:flex; align-items:center; gap:12px;
        background:#FFFFFF;
        border:1px solid #E8E2DC;
        box-shadow:3px 3px 0 #00000011;
        padding:0 16px;
        height:64px;
        margin-bottom:8px;
        border-radius:2px;
    ">
        <div style="
            min-width:36px; height:22px;
            background:{chip_bg}; border:1px solid {chip_border};
            display:flex; align-items:center; justify-content:center;
            font-family:Inter,sans-serif; font-size:11px; font-weight:700;
            color:{chip_text};
        ">{domain_label}</div>
        <div style="flex:1; overflow:hidden;">
            <div style="font-family:Inter,sans-serif;font-size:13px;font-weight:600;
                        color:#1E293B;white-space:nowrap;overflow:hidden;
                        text-overflow:ellipsis;">{title}</div>
            <div style="font-family:Inter,sans-serif;font-size:11px;color:#94A3B8;
                        white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
                        margin-top:2px;">{subtitle}</div>
        </div>
    </div>
    """
    col_row, col_btn = st.columns([10, 1], gap="small")
    with col_row:
        st.markdown(row_html, unsafe_allow_html=True)
    with col_btn:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        if st.button("✕", key=f"{key_prefix}_{index}_del", help="Delete annotation"):
            on_delete()


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------

def render_confidence_badge(confidence: float) -> None:
    """Render a monochrome progress-bar confidence widget (number above filled bar)."""
    fill_width = int(confidence * 64)
    st.markdown(
        f'<span style="display:inline-flex;flex-direction:column;align-items:center;gap:3px;vertical-align:middle">'
        f'<span style="font-size:11px;font-weight:700;color:#262730">{confidence:.0%}</span>'
        f'<span style="display:inline-block;width:64px;height:6px;background:#E8E2DC">'
        f'<span style="display:block;width:{fill_width}px;height:6px;background:#262730"></span>'
        f'</span>'
        f'</span>',
        unsafe_allow_html=True,
    )


def render_match_type_badge(match_type: str) -> None:
    """Render a colored match type badge."""
    colors = {
        "exact": ("#004085", "#cce5ff"),
        "fuzzy": ("#3d1a78", "#e2d9f3"),
        "position_only": ("#7d4e00", "#fff3cd"),
        "manual": ("#0c5460", "#d1ecf1"),
        "unmatched": ("#721c24", "#f8d7da"),
    }
    color, bg = colors.get(match_type, ("#343a40", "#e2e3e5"))
    st.markdown(
        f'<span style="background:{bg};color:{color};padding:2px 8px;'
        f'border-radius:4px;font-size:12px;display:block;text-align:center">{match_type}</span>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# PDF utilities
# ---------------------------------------------------------------------------

def get_page_dims_from_pdf(pdf_path: Path) -> dict[int, tuple[float, float]]:
    """Return {page_num (1-indexed): (width, height)} for all pages."""
    doc = fitz.open(str(pdf_path))
    try:
        dims = {}
        for i in range(doc.page_count):
            page = doc[i]
            dims[i + 1] = (page.rect.width, page.rect.height)
        return dims
    finally:
        doc.close()


def get_pdf_page_count(pdf_path: Path) -> int:
    """Return total page count of a PDF."""
    doc = fitz.open(str(pdf_path))
    try:
        return doc.page_count
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Phase invalidation
# ---------------------------------------------------------------------------

def invalidate_phases(phase_numbers: list[int]) -> None:
    """Mark phases as incomplete and clear downstream state."""
    phases = st.session_state.setdefault("phases_complete", {1: False, 2: False, 3: False, 4: False})
    for n in phase_numbers:
        phases[n] = False
    if 3 in phase_numbers:
        st.session_state.pop("matches", None)
    if 4 in phase_numbers:
        st.session_state.pop("output_pdf_path", None)
        st.session_state.pop("qc_report", None)
