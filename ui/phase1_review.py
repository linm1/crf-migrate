"""Phase 1: Annotation extraction and review UI."""
import uuid
from pathlib import Path

import streamlit as st

from src.csv_handler import export_annotations_csv, import_annotations_csv
from src.extractor import extract_annotations
from src.field_parser import extract_fields
from src.models import AnnotationRecord, FieldRecord
from ui.components import (
    get_pdf_page_count,
    invalidate_phases,
    render_page_navigator_windowed,
)


def render_phase1(profiles_dir: Path) -> None:
    """Render Phase 1: Extract Annotations page."""
    session = st.session_state.get("session")
    profile = st.session_state.get("profile")
    rule_engine = st.session_state.get("rule_engine")

    if profile is None or rule_engine is None:
        st.warning("No profile loaded. Go to Profile Editor to select a profile.")
        return

    annotations = st.session_state.get("annotations", [])
    source_fields = st.session_state.get("source_fields", [])

    # ── Topbar ───────────────────────────────────────────────────────────────
    _render_topbar(annotations, session)

    # ── Fixed-height card row CSS ─────────────────────────────────────────────
    st.markdown(
        """
        <style>
        /* Pin all three top-row bordered containers to the same fixed height */
        .st-key-p1_upload_card,
        .st-key-p1_counts_card,
        .st-key-p1_category_card {
            min-height: 220px !important;
            height: 220px !important;
            box-sizing: border-box !important;
        }
        .st-key-p1_upload_card > div[data-testid="stVerticalBlock"],
        .st-key-p1_counts_card > div[data-testid="stVerticalBlock"],
        .st-key-p1_category_card > div[data-testid="stVerticalBlock"] {
            min-height: 204px !important;
            height: 100% !important;
        }
        /* Remove default top margin Streamlit adds before bordered containers */
        .st-key-p1_upload_card,
        .st-key-p1_counts_card,
        .st-key-p1_category_card {
            margin-top: 0 !important;
        }
        /* White background for counts and category cards */
        .st-key-p1_counts_card,
        .st-key-p1_category_card {
            background: #FFFFFF !important;
        }
        /* Remove border and shadow from upload card, white background */
        .st-key-p1_upload_card {
            border: none !important;
            box-shadow: none !important;
            background: #FFFFFF !important;
        }
        .st-key-p1_upload_card .stButton > button {
            background: #383838 !important;
            color: #FFFFFF !important;
            border: 1px solid #383838 !important;
            font-weight: 700;
        }
        .st-key-p1_upload_card .stButton > button:hover {
            background: #1a1a1a !important;
            color: #FFFFFF !important;
        }
        /* Phase 1 toolbar buttons: 12px bold monospace (matches Profile Editor pattern) */
        .st-key-p1_export_btn button p,
        .st-key-p1_import_btn button p {
            font-size: 12px !important;
            font-weight: 700 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── Single HTML row for all three section headers (guarantees same baseline) ──
    _hdr = (
        "font-family:Inter,sans-serif;font-size:12px;font-weight:700;"
        "color:#383838;text-transform:uppercase;letter-spacing:0.5px;"
        "margin:0;padding:0;"
    )
    st.markdown(
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem;'
        f'margin-bottom:6px;">'
        f'<p style="{_hdr}">Source aCRF PDF</p>'
        f'<p style="{_hdr}">Counts</p>'
        f'<p style="{_hdr}">By Category</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 3-column top row: upload card | counts card | by-category card ────────
    c1, c2, c3 = st.columns(3, gap="large")
    with c1:
        _render_upload_card(session, profile, rule_engine)
    with c2:
        _render_counts_card(annotations, source_fields)
    with c3:
        _render_category_card(annotations)

    # ── Review panel (tabs: SDTM Annotations | CRF Fields) ───────────────────
    annotations = st.session_state.get("annotations", [])
    source_fields = st.session_state.get("source_fields", [])
    if annotations or source_fields:
        source_pdf_path = st.session_state.get("source_pdf_path")
        _render_review_panel(annotations, source_fields, session, source_pdf_path)


# ---------------------------------------------------------------------------
# Topbar
# ---------------------------------------------------------------------------

def _render_topbar(annotations: list[AnnotationRecord], session) -> None:
    """Header + toolbar matching Profile Editor pattern."""
    st.header("Phase 1: Extract Annotations")

    _, tb_export, tb_import_btn = st.columns([5, 1, 1], gap="small")

    with tb_export:
        if st.button("Export CSV", key="p1_export_btn", use_container_width=True):
            if annotations and session:
                csv_path = session.workspace / "annotations_export.csv"
                export_annotations_csv(annotations, csv_path)
                st.session_state["_p1_csv_ready"] = csv_path.read_bytes()
        if st.session_state.get("_p1_csv_ready"):
            st.download_button(
                "Download CSV",
                data=st.session_state["_p1_csv_ready"],
                file_name="annotations.csv",
                mime="text/csv",
                key="p1_csv_dl",
                use_container_width=True,
            )

    with tb_import_btn:
        if st.button("Import CSV", key="p1_import_btn", use_container_width=True):
            st.session_state["_p1_show_import"] = not st.session_state.get("_p1_show_import", False)

    if st.session_state.get("_p1_show_import", False):
        csv_upload = st.file_uploader("Import CSV file", type=["csv"], key="p1_csv_upload")
        if csv_upload is not None and annotations and session:
            csv_path = session.workspace / "annotations_import.csv"
            csv_path.write_bytes(csv_upload.read())
            updated, flagged = import_annotations_csv(csv_path, annotations)
            if flagged:
                st.warning(
                    f"{len(flagged)} existing annotations missing from CSV: "
                    f"{', '.join(flagged[:5])}{'...' if len(flagged) > 5 else ''}"
                )
                c1, c2 = st.columns(2)
                if c1.button("Confirm (remove missing)", key="p1_csv_confirm"):
                    final = [r for r in updated if r.id not in flagged]
                    session.save_annotations(final)
                    st.session_state["annotations"] = final
                    st.session_state["_p1_show_import"] = False
                    invalidate_phases([3, 4])
                    st.rerun()
                if c2.button("Keep all", key="p1_csv_keep"):
                    session.save_annotations(updated)
                    st.session_state["annotations"] = updated
                    st.session_state["_p1_show_import"] = False
                    invalidate_phases([3, 4])
                    st.rerun()
            else:
                session.save_annotations(updated)
                st.session_state["annotations"] = updated
                st.session_state["_p1_show_import"] = False
                invalidate_phases([3, 4])
                st.rerun()


# ---------------------------------------------------------------------------
# Upload card
# ---------------------------------------------------------------------------

def _render_upload_card(session, profile, rule_engine) -> None:
    with st.container(border=True, key="p1_upload_card"):
        uploaded = st.file_uploader(
            "Source aCRF PDF", type=["pdf"],
            key="phase1_upload", label_visibility="collapsed",
        )
        if uploaded is not None:
            pdf_path = session.workspace / "source_acrf.pdf"
            pdf_path.write_bytes(uploaded.read())
            st.session_state["source_pdf_path"] = pdf_path
            st.session_state["source_pdf_name"] = uploaded.name

        source_pdf_path = st.session_state.get("source_pdf_path")
        has_pdf = bool(source_pdf_path and source_pdf_path.exists())
        if st.button(
            "Extract Annotations & Fields",
            use_container_width=True,
            key="p1_extract_btn",
            disabled=not has_pdf,
        ):
            with st.spinner("Extracting…"):
                try:
                    records = extract_annotations(source_pdf_path, profile, rule_engine)
                    fields = extract_fields(source_pdf_path, profile, rule_engine)
                    session.save_annotations(records)
                    st.session_state["annotations"] = records
                    st.session_state["source_fields"] = fields
                    st.session_state["phases_complete"][1] = True
                    invalidate_phases([3, 4])
                    session.log_action("phase1_extract", {
                        "annotations": len(records),
                        "fields": len(fields),
                    })
                    st.rerun()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")


# ---------------------------------------------------------------------------
# Top row cards
# ---------------------------------------------------------------------------

_CATEGORY_COLORS = {
    "sdtm_mapping":  ("#4F46E5", "#EEF2FF"),
    "domain_label":  ("#92400E", "#FEF9C3"),
    "not_submitted": ("#166534", "#F0FDF4"),
    "note":          ("#475569", "#F1F5F9"),
    "_exclude":      ("#DC2626", "#FEF2F2"),
}

_LABEL_STYLE = (
    "font-family:Inter,sans-serif;font-size:11px;font-weight:600;"
    "color:#8A847F;text-transform:uppercase;letter-spacing:0.5px;margin:0 0 4px 0;"
)
_NUMBER_STYLE = (
    "font-family:Inter,sans-serif;font-size:32px;font-weight:700;"
    "color:#383838;line-height:1.1;margin:0 0 16px 0;"
)


def _render_counts_card(
    annotations: list[AnnotationRecord],
    source_fields: list[FieldRecord],
) -> None:
    """Combined Annotations + Fields count card."""
    with st.container(border=True, key="p1_counts_card"):
        st.markdown(
            f'<div style="{_LABEL_STYLE}">Annotations</div>'
            f'<div style="{_NUMBER_STYLE}">{len(annotations)}</div>'
            f'<div style="{_LABEL_STYLE}">Fields</div>'
            f'<div style="{_NUMBER_STYLE.replace("margin:0 0 16px 0", "margin:0")}">'
            f'{len(source_fields)}</div>',
            unsafe_allow_html=True,
        )


def _render_category_card(annotations: list[AnnotationRecord]) -> None:
    """By Category breakdown card."""
    by_category: dict[str, int] = {}
    for a in annotations:
        by_category[a.category] = by_category.get(a.category, 0) + 1

    cat_rows = ""
    for cat in ["sdtm_mapping", "domain_label", "not_submitted", "note", "_exclude"]:
        cnt = by_category.get(cat, 0)
        text_color, bg_color = _CATEGORY_COLORS.get(cat, ("#383838", "#F8F8F8"))
        cat_rows += (
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:4px 0;font-family:Inter,sans-serif;font-size:12px;">'
            f'<span style="background:{bg_color};color:{text_color};padding:1px 7px;'
            f'font-weight:600;">{cat}</span>'
            f'<strong style="color:#383838;">{cnt}</strong>'
            f'</div>'
        )

    with st.container(border=True, key="p1_category_card"):
        st.markdown(
            f'<div style="{_LABEL_STYLE}">By Category</div>'
            f'{cat_rows}',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Review panel — color dicts (mirroring components.py, inlined to avoid coupling)
# ---------------------------------------------------------------------------

_ANNOT_LABEL_COLORS = {
    "sdtm_mapping":  "blue",
    "domain_label":  "orange",
    "not_submitted": "green",
    "note":          "gray",
    "_exclude":      "red",
}
_ANNOT_BG_COLORS = {
    "sdtm_mapping":  "rgba(0,122,255,0.06)",
    "domain_label":  "rgba(255,215,0,0.10)",
    "not_submitted": "rgba(39,201,63,0.07)",
    "note":          "rgba(238,238,238,0.35)",
    "_exclude":      "rgba(255,95,86,0.07)",
}
_FIELD_TYPE_COLORS = {
    "text_field":     "blue",
    "checkbox":       "green",
    "date_field":     "orange",
    "table_row":      "violet",
    "section_header": "gray",
}
_FIELD_TYPE_BG_COLORS = {
    "text_field":     "rgba(0,122,255,0.06)",
    "checkbox":       "rgba(39,201,63,0.07)",
    "date_field":     "rgba(255,215,0,0.10)",
    "table_row":      "rgba(150,80,255,0.06)",
    "section_header": "rgba(238,238,238,0.35)",
}
_ANNOT_CATEGORIES = ["sdtm_mapping", "domain_label", "not_submitted", "note", "_exclude"]
_FIELD_TYPES = ["text_field", "checkbox", "date_field", "table_row", "section_header"]


# ---------------------------------------------------------------------------
# Review panel
# ---------------------------------------------------------------------------

def _render_review_panel(
    annotations: list[AnnotationRecord],
    source_fields: list[FieldRecord],
    session,
    source_pdf_path,
) -> None:
    # Page count
    total_pages = 1
    if source_pdf_path and source_pdf_path.exists():
        try:
            total_pages = get_pdf_page_count(source_pdf_path)
        except Exception:
            total_pages = max((a.page for a in annotations), default=1)

    # Full-width tabs; paginator lives as first row inside each tab
    tab_annot, tab_fields = st.tabs(["SDTM Annotations", "CRF Fields"])

    # ── SDTM Annotations tab ─────────────────────────────────────────────────
    with tab_annot:
        selected_page = render_page_navigator_windowed(total_pages, key="p1_annot_nav")
        page_annots = [(i, a) for i, a in enumerate(annotations) if a.page == selected_page]
        if not page_annots:
            st.info(f"No annotations on page {selected_page}.")
        else:
            for local_idx, (global_idx, annot) in enumerate(page_annots):
                color = _ANNOT_LABEL_COLORS.get(annot.category, "gray")
                bg = _ANNOT_BG_COLORS.get(annot.category, "rgba(238,238,238,0.18)")
                badge = annot.category or "—"
                suffix = (annot.content[:60] if annot.content else annot.anchor_text[:60]) or "—"
                label = f":{color}[**{badge}**] :gray[*{suffix}*]"
                container_key = f"p1_annot_p{selected_page}_{local_idx}"
                st.markdown(
                    f"<style>.st-key-{container_key} details"
                    f"{{background:{bg} !important}}</style>",
                    unsafe_allow_html=True,
                )
                with st.container(key=container_key):
                    with st.expander(label, expanded=False):
                        c1, c2 = st.columns(2)
                        with c1:
                            content = st.text_area(
                                "Content", value=annot.content,
                                key=f"p1_a_p{selected_page}_{local_idx}_content",
                            )
                            domain = st.text_input(
                                "Domain", value=annot.domain,
                                key=f"p1_a_p{selected_page}_{local_idx}_domain",
                            )
                            cat_idx = _ANNOT_CATEGORIES.index(annot.category) \
                                if annot.category in _ANNOT_CATEGORIES else 0
                            category = st.selectbox(
                                "Category", _ANNOT_CATEGORIES, index=cat_idx,
                                key=f"p1_a_p{selected_page}_{local_idx}_cat",
                            )
                        with c2:
                            anchor = st.text_input(
                                "Anchor Text", value=annot.anchor_text,
                                key=f"p1_a_p{selected_page}_{local_idx}_anchor",
                            )
                            form_name = st.text_input(
                                "Form Name", value=annot.form_name,
                                key=f"p1_a_p{selected_page}_{local_idx}_form",
                            )
                            visit = st.text_input(
                                "Visit", value=annot.visit,
                                key=f"p1_a_p{selected_page}_{local_idx}_visit",
                            )
                        btn_save, btn_del, _ = st.columns([1, 1, 4], gap="small")
                        with btn_save:
                            if st.button("Save", key=f"p1_a_p{selected_page}_{local_idx}_save",
                                         use_container_width=True):
                                updated = annot.model_copy(update={
                                    "content": content, "domain": domain,
                                    "category": category, "anchor_text": anchor,
                                    "form_name": form_name, "visit": visit,
                                })
                                new_list = [
                                    updated if i == global_idx else a
                                    for i, a in enumerate(st.session_state["annotations"])
                                ]
                                session.save_annotations(new_list)
                                st.session_state["annotations"] = new_list
                                invalidate_phases([3, 4])
                                st.rerun()
                        with btn_del:
                            if st.button("Delete", key=f"p1_a_p{selected_page}_{local_idx}_del",
                                         use_container_width=True):
                                new_list = [
                                    a for i, a in enumerate(st.session_state["annotations"])
                                    if i != global_idx
                                ]
                                session.save_annotations(new_list)
                                st.session_state["annotations"] = new_list
                                invalidate_phases([3, 4])
                                st.rerun()
        _render_add_annotation(annotations, session)

    # ── CRF Fields tab ───────────────────────────────────────────────────────
    with tab_fields:
        selected_page_f = render_page_navigator_windowed(total_pages, key="p1_fields_nav")
        page_fields = [(i, f) for i, f in enumerate(source_fields) if f.page == selected_page_f]
        if not page_fields:
            st.info(f"No fields on page {selected_page_f}.")
        else:
            for local_idx, (global_idx, field) in enumerate(page_fields):
                color = _FIELD_TYPE_COLORS.get(field.field_type, "gray")
                bg = _FIELD_TYPE_BG_COLORS.get(field.field_type, "rgba(238,238,238,0.18)")
                badge = field.field_type or "—"
                suffix = field.label[:60] if field.label else "—"
                label = f":{color}[**{badge}**] :gray[*{suffix}*]"
                container_key = f"p1_field_p{selected_page_f}_{local_idx}"
                st.markdown(
                    f"<style>.st-key-{container_key} details"
                    f"{{background:{bg} !important}}</style>",
                    unsafe_allow_html=True,
                )
                with st.container(key=container_key):
                    with st.expander(label, expanded=False):
                        c1, c2 = st.columns(2)
                        with c1:
                            label_val = st.text_input(
                                "Label", value=field.label,
                                key=f"p1_f_p{selected_page_f}_{local_idx}_label",
                            )
                            form_name = st.text_input(
                                "Form Name", value=field.form_name,
                                key=f"p1_f_p{selected_page_f}_{local_idx}_form",
                            )
                            visit = st.text_input(
                                "Visit", value=field.visit,
                                key=f"p1_f_p{selected_page_f}_{local_idx}_visit",
                            )
                        with c2:
                            type_idx = _FIELD_TYPES.index(field.field_type) \
                                if field.field_type in _FIELD_TYPES else 0
                            field_type = st.selectbox(
                                "Field Type", _FIELD_TYPES, index=type_idx,
                                key=f"p1_f_p{selected_page_f}_{local_idx}_type",
                            )
                        btn_save, btn_del, _ = st.columns([1, 1, 4], gap="small")
                        with btn_save:
                            if st.button("Save", key=f"p1_f_p{selected_page_f}_{local_idx}_save",
                                         use_container_width=True):
                                updated = field.model_copy(update={
                                    "label": label_val, "form_name": form_name,
                                    "visit": visit, "field_type": field_type,
                                })
                                new_list = [
                                    updated if i == global_idx else f
                                    for i, f in enumerate(st.session_state["source_fields"])
                                ]
                                st.session_state["source_fields"] = new_list
                                st.rerun()
                        with btn_del:
                            if st.button("Delete", key=f"p1_f_p{selected_page_f}_{local_idx}_del",
                                         use_container_width=True):
                                new_list = [
                                    f for i, f in enumerate(st.session_state["source_fields"])
                                    if i != global_idx
                                ]
                                st.session_state["source_fields"] = new_list
                                st.rerun()


# ---------------------------------------------------------------------------
# Add new annotation
# ---------------------------------------------------------------------------

def _render_add_annotation(annotations: list[AnnotationRecord], session) -> None:
    with st.expander("Add New Annotation"):
        with st.form("p1_add_form"):
            col1, col2 = st.columns(2)
            with col1:
                content = st.text_area("Content")
                domain = st.text_input("Domain")
            with col2:
                category = st.selectbox(
                    "Category",
                    ["sdtm_mapping", "domain_label", "not_submitted", "note", "_exclude"],
                )
                form_name = st.text_input("Form Name")
                visit = st.text_input("Visit")
            submitted = st.form_submit_button("Add Annotation")
            if submitted and content.strip():
                new_record = AnnotationRecord(
                    id=str(uuid.uuid4()),
                    page=1,
                    content=content,
                    domain=domain,
                    category=category,
                    matched_rule="manual",
                    rect=[0.0, 0.0, 100.0, 20.0],
                    anchor_text="",
                    form_name=form_name,
                    visit=visit,
                )
                updated = annotations + [new_record]
                session.save_annotations(updated)
                st.session_state["annotations"] = updated
                invalidate_phases([3, 4])
                st.rerun()
