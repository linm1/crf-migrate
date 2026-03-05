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


# ---------------------------------------------------------------------------
# Annotation card
# ---------------------------------------------------------------------------

def render_annotation_card(
    annot: AnnotationRecord, index: int, key_prefix: str
) -> AnnotationRecord | None:
    """Render an editable annotation card. Returns None if delete clicked."""
    with st.expander(
        f"[{index + 1}] {annot.content[:60]} | {annot.domain} | p.{annot.page}",
        expanded=False,
    ):
        col1, col2 = st.columns(2)
        with col1:
            content = st.text_area(
                "Content", value=annot.content, key=f"{key_prefix}_{index}_content"
            )
            domain = st.text_input(
                "Domain", value=annot.domain, key=f"{key_prefix}_{index}_domain"
            )
            category = st.text_input(
                "Category", value=annot.category, key=f"{key_prefix}_{index}_category"
            )
        with col2:
            anchor_text = st.text_input(
                "Anchor Text", value=annot.anchor_text,
                key=f"{key_prefix}_{index}_anchor"
            )
            form_name = st.text_input(
                "Form Name", value=annot.form_name,
                key=f"{key_prefix}_{index}_form"
            )
            visit = st.text_input(
                "Visit", value=annot.visit, key=f"{key_prefix}_{index}_visit"
            )
        if st.button("Delete", key=f"{key_prefix}_{index}_delete", type="secondary"):
            return None
        return annot.model_copy(update={
            "content": content,
            "domain": domain,
            "category": category,
            "anchor_text": anchor_text,
            "form_name": form_name,
            "visit": visit,
        })


# ---------------------------------------------------------------------------
# Field card
# ---------------------------------------------------------------------------

_FIELD_TYPES = ["text_field", "checkbox", "date_field", "table_row", "section_header"]


def render_field_card(
    field: FieldRecord, index: int, key_prefix: str
) -> FieldRecord | None:
    """Render an editable field card. Returns None if delete clicked."""
    with st.expander(
        f"[{index + 1}] {field.label[:60]} | {field.field_type} | p.{field.page}",
        expanded=False,
    ):
        col1, col2 = st.columns(2)
        with col1:
            label = st.text_input(
                "Label", value=field.label, key=f"{key_prefix}_{index}_label"
            )
            form_name = st.text_input(
                "Form Name", value=field.form_name,
                key=f"{key_prefix}_{index}_form"
            )
            visit = st.text_input(
                "Visit", value=field.visit, key=f"{key_prefix}_{index}_visit"
            )
        with col2:
            type_idx = _FIELD_TYPES.index(field.field_type) if field.field_type in _FIELD_TYPES else 0
            field_type = st.selectbox(
                "Field Type", _FIELD_TYPES, index=type_idx,
                key=f"{key_prefix}_{index}_type"
            )
        if st.button("Delete", key=f"{key_prefix}_{index}_delete", type="secondary"):
            return None
        return field.model_copy(update={
            "label": label,
            "form_name": form_name,
            "visit": visit,
            "field_type": field_type,
        })


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------

def render_confidence_badge(confidence: float) -> None:
    """Render a colored confidence badge."""
    if confidence >= 0.9:
        color, bg = "#155724", "#d4edda"
    elif confidence >= 0.7:
        color, bg = "#856404", "#fff3cd"
    else:
        color, bg = "#721c24", "#f8d7da"
    st.markdown(
        f'<span style="background:{bg};color:{color};padding:2px 8px;'
        f'border-radius:4px;font-size:12px">{confidence:.0%}</span>',
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
        f'border-radius:4px;font-size:12px">{match_type}</span>',
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
