"""Phase 1: Annotation extraction and review UI."""
import threading
import time
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
from ui.loader import clear_loader, loader_html


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
        /* White background for upload card */
        .st-key-p1_upload_card {
            background: #FFFFFF !important;
        }
        .st-key-p1_upload_card .stButton > button {
            background: #383838 !important;
            color: #FFFFFF !important;
            border: 2px solid #383838 !important;
            font-weight: 400 !important;
        }
        .st-key-p1_upload_card .stButton > button:hover {
            background: #383838 !important;
            color: #FFFFFF !important;
            border-color: #383838 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── Single HTML row for all three section headers (guarantees same baseline) ──
    _hdr = (
        "font-family:'Aeonik Mono',ui-monospace,monospace;font-size:12px;font-weight:700;"
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
        _p1_export_data: bytes = b""
        if annotations and session:
            csv_path = session.workspace / "annotations_export.csv"
            export_annotations_csv(annotations, csv_path)
            _p1_export_data = csv_path.read_bytes()
        st.download_button(
            "Export CSV",
            data=_p1_export_data,
            file_name="annotations.csv",
            mime="text/csv",
            key="p1_export_btn",
            use_container_width=True,
            disabled=not bool(annotations and session),
        )

    with tb_import_btn:
        csv_upload = st.file_uploader(
            "Import CSV",
            type=["csv"],
            key="p1_csv_upload",
            label_visibility="collapsed",
        )

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
                invalidate_phases([3, 4])
                st.rerun()
            if c2.button("Keep all", key="p1_csv_keep"):
                session.save_annotations(updated)
                st.session_state["annotations"] = updated
                invalidate_phases([3, 4])
                st.rerun()
        else:
            session.save_annotations(updated)
            st.session_state["annotations"] = updated
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
            _loader_ph = st.empty()
            _loader_ph.html(loader_html("Extracting…"))

            _result: dict = {}

            def _work() -> None:
                try:
                    _result["records"] = extract_annotations(source_pdf_path, profile, rule_engine)
                    _result["fields"] = extract_fields(source_pdf_path, profile, rule_engine)
                except Exception as exc:
                    _result["error"] = exc

            _t = threading.Thread(target=_work, daemon=True)
            _t.start()
            while _t.is_alive():
                time.sleep(0.05)
            _t.join()
            clear_loader(_loader_ph)

            if "error" in _result:
                st.error(f"Extraction failed: {_result['error']}")
            else:
                records = _result["records"]
                fields = _result["fields"]
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


# ---------------------------------------------------------------------------
# Top row cards
# ---------------------------------------------------------------------------

_CATEGORY_COLORS = {
    "sdtm_mapping":  ("#EAF0FF", "#C7D2FE", "#383838"),
    "domain_label":  ("#F9FBE7", "#B3C419", "#383838"),
    "not_submitted": ("#E8F5E9", "#38c1b0", "#383838"),
    "note":          ("#ECEFF1", "#84A6BC", "#383838"),
    "_exclude":      ("#FFEBE9", "#FFBDBA", "#383838"),
}

_LABEL_STYLE = (
    "font-family:'Aeonik Mono',ui-monospace,monospace;font-size:11px;font-weight:600;"
    "color:#818181;text-transform:uppercase;letter-spacing:0.5px;margin:0 0 4px 0;"
)
_NUMBER_STYLE = (
    "font-family:'Aeonik Mono',ui-monospace,monospace;font-size:32px;font-weight:700;"
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
        bg_col, border_col, text_col = _CATEGORY_COLORS.get(cat, ("#F8F8F8", "#CCCCCC", "#383838"))
        cat_rows += (
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:3px 0;font-family:\'Aeonik Mono\',ui-monospace,monospace;font-size:12px;">'
            f'<span style="background:{bg_col};color:{text_col};border:2px solid {border_col};'
            f'padding:1px 7px;font-weight:600;">{cat}</span>'
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
    "sdtm_mapping":  "#EAF0FF",
    "domain_label":  "#F9FBE7",
    "not_submitted": "#E8F5E9",
    "note":          "#f8f8f7",
    "_exclude":      "#FFEBE9",
}
_FIELD_TYPE_COLORS = {
    "text_field":     "blue",
    "checkbox":       "green",
    "date_field":     "orange",
    "table_row":      "violet",
    "section_header": "gray",
}
_FIELD_TYPE_BG_COLORS = {
    "text_field":     "#EAF0FF",
    "checkbox":       "#E8F5E9",
    "date_field":     "#F9FBE7",
    "table_row":      "#f8f8f7",
    "section_header": "#f8f8f7",
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
                bg = _ANNOT_BG_COLORS.get(annot.category, "#f8f8f7")
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
                bg = _FIELD_TYPE_BG_COLORS.get(field.field_type, "#f8f8f7")
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
