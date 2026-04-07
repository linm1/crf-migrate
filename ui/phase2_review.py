"""Phase 2: Field extraction and review UI."""
import threading
import time
import uuid
from pathlib import Path

import streamlit as st

from src.csv_handler import export_fields_csv, import_fields_csv
from src.field_parser import extract_fields
from src.models import FieldRecord
from ui.components import (
    get_pdf_page_count,
    invalidate_phases,
    render_page_navigator_windowed,
)
from ui.loader import clear_loader, loader_html

# ---------------------------------------------------------------------------
# Color dicts (mirroring components.py, inlined to avoid coupling)
# ---------------------------------------------------------------------------

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
_FIELD_TYPES = ["text_field", "checkbox", "date_field", "table_row", "section_header"]

_LABEL_STYLE = (
    "font-family:'Aeonik Mono', ui-monospace, monospace;font-size:11px;font-weight:600;"
    "color:#818181;text-transform:uppercase;letter-spacing:0.5px;margin:0 0 4px 0;"
)
_NUMBER_STYLE = (
    "font-family:'Aeonik Mono', ui-monospace, monospace;font-size:32px;font-weight:700;"
    "color:#383838;line-height:1.1;margin:0 0 16px 0;"
)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render_phase2(profiles_dir: Path) -> None:
    """Render Phase 2: Extract Fields page."""
    session = st.session_state.get("session")
    profile = st.session_state.get("profile")
    rule_engine = st.session_state.get("rule_engine")

    if profile is None or rule_engine is None:
        st.warning("No profile loaded. Go to Profile Editor to select a profile.")
        return

    fields = st.session_state.get("fields", [])

    # ── Topbar ────────────────────────────────────────────────────────────────
    _render_topbar(fields, session)

    # ── Fixed-height card row CSS ─────────────────────────────────────────────
    st.markdown(
        """
        <style>
        .st-key-p2_upload_card,
        .st-key-p2_counts_card,
        .st-key-p2_bytype_card {
            min-height: 220px !important;
            height: 220px !important;
            box-sizing: border-box !important;
            margin-top: 0 !important;
        }
        .st-key-p2_upload_card > div[data-testid="stVerticalBlock"],
        .st-key-p2_counts_card > div[data-testid="stVerticalBlock"],
        .st-key-p2_bytype_card > div[data-testid="stVerticalBlock"] {
            min-height: 204px !important;
            height: 100% !important;
        }
        .st-key-p2_counts_card,
        .st-key-p2_bytype_card {
            background: #FFFFFF !important;
        }
        .st-key-p2_upload_card {
            background: #FFFFFF !important;
        }
        .st-key-p2_upload_card .stButton > button {
            background: #383838 !important;
            color: #FFFFFF !important;
            border: 2px solid #383838 !important;
            font-weight: 700;
        }
        .st-key-p2_upload_card .stButton > button:hover {
            background: #1a1a1a !important;
            color: #FFFFFF !important;
        }
        /* Phase 2 toolbar buttons: 12px bold monospace (matches Profile Editor pattern) */
        .st-key-p2_export_btn button p,
        .st-key-p2_import_btn button p {
            font-size: 12px !important;
            font-weight: 700 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── Single HTML row for section headers ───────────────────────────────────
    _hdr = (
        "font-family:'Aeonik Mono', ui-monospace, monospace;font-size:12px;font-weight:700;"
        "color:#383838;text-transform:uppercase;letter-spacing:0.5px;"
        "margin:0;padding:0;"
    )
    st.markdown(
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem;'
        f'margin-bottom:6px;">'
        f'<p style="{_hdr}">Target CRF PDF</p>'
        f'<p style="{_hdr}">Counts</p>'
        f'<p style="{_hdr}">By Type</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 3-column top row ──────────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3, gap="large")
    with c1:
        _render_upload_card(session, profile, rule_engine)
    with c2:
        _render_counts_card(fields)
    with c3:
        _render_bytype_card(fields)

    # ── Review panel ──────────────────────────────────────────────────────────
    fields = st.session_state.get("fields", [])
    if fields:
        target_pdf_path = st.session_state.get("target_pdf_path")
        _render_review_panel(fields, session, target_pdf_path)


# ---------------------------------------------------------------------------
# Topbar
# ---------------------------------------------------------------------------

def _render_topbar(fields: list[FieldRecord], session) -> None:
    """Header + Export/Import CSV toolbar."""
    st.header("Phase 2: Extract Fields")

    _, tb_export, tb_import_btn = st.columns([5, 1, 1], gap="small")

    with tb_export:
        if st.button("Export CSV", key="p2_export_btn", use_container_width=True):
            if fields and session:
                csv_path = session.workspace / "fields_export.csv"
                export_fields_csv(fields, csv_path)
                st.session_state["_p2_csv_ready"] = csv_path.read_bytes()
        if st.session_state.get("_p2_csv_ready"):
            st.download_button(
                "Download CSV",
                data=st.session_state["_p2_csv_ready"],
                file_name="fields.csv",
                mime="text/csv",
                key="p2_csv_dl",
                use_container_width=True,
            )

    with tb_import_btn:
        if st.button("Import CSV", key="p2_import_btn", use_container_width=True):
            st.session_state["_p2_show_import"] = not st.session_state.get("_p2_show_import", False)

    if st.session_state.get("_p2_show_import", False):
        csv_upload = st.file_uploader("Import CSV file", type=["csv"], key="p2_csv_upload")
        if csv_upload is not None and fields and session:
            csv_path = session.workspace / "fields_import.csv"
            csv_path.write_bytes(csv_upload.read())
            updated, flagged = import_fields_csv(csv_path, fields)
            if flagged:
                st.warning(
                    f"{len(flagged)} existing fields missing from CSV: "
                    f"{', '.join(flagged[:5])}{'...' if len(flagged) > 5 else ''}"
                )
                c1, c2 = st.columns(2)
                if c1.button("Confirm (remove missing)", key="p2_csv_confirm"):
                    final = [r for r in updated if r.id not in flagged]
                    session.save_fields(final)
                    st.session_state["fields"] = final
                    st.session_state["_p2_show_import"] = False
                    invalidate_phases([3, 4])
                    st.rerun()
                if c2.button("Keep all", key="p2_csv_keep"):
                    session.save_fields(updated)
                    st.session_state["fields"] = updated
                    st.session_state["_p2_show_import"] = False
                    invalidate_phases([3, 4])
                    st.rerun()
            else:
                session.save_fields(updated)
                st.session_state["fields"] = updated
                st.session_state["_p2_show_import"] = False
                invalidate_phases([3, 4])
                st.rerun()


# ---------------------------------------------------------------------------
# Upload card
# ---------------------------------------------------------------------------

def _render_upload_card(session, profile, rule_engine) -> None:
    with st.container(border=True, key="p2_upload_card"):
        uploaded = st.file_uploader(
            "Target CRF PDF", type=["pdf"],
            key="phase2_upload", label_visibility="collapsed",
        )
        if uploaded is not None:
            pdf_path = session.workspace / "target_crf.pdf"
            pdf_path.write_bytes(uploaded.read())
            st.session_state["target_pdf_path"] = pdf_path
            st.session_state["target_pdf_name"] = uploaded.name

        target_pdf_path = st.session_state.get("target_pdf_path")
        has_pdf = bool(target_pdf_path and target_pdf_path.exists())
        if st.button(
            "Extract Fields",
            use_container_width=True,
            key="p2_extract_btn",
            disabled=not has_pdf,
        ):
            _loader_ph = st.empty()
            _loader_ph.html(loader_html("Extracting…"))

            _result: dict = {}

            def _work() -> None:
                try:
                    _result["records"] = extract_fields(target_pdf_path, profile, rule_engine)
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
                session.save_fields(records)
                st.session_state["fields"] = records
                st.session_state["phases_complete"][2] = True
                invalidate_phases([3, 4])
                session.log_action("phase2_extract", {"count": len(records)})
                st.rerun()


# ---------------------------------------------------------------------------
# Top row cards
# ---------------------------------------------------------------------------

def _render_counts_card(fields: list[FieldRecord]) -> None:
    """Forms count + Fields count card."""
    forms = len({f.form_name for f in fields if f.form_name})

    with st.container(border=True, key="p2_counts_card"):
        st.markdown(
            f'<div style="{_LABEL_STYLE}">Forms</div>'
            f'<div style="{_NUMBER_STYLE}">{forms}</div>'
            f'<div style="{_LABEL_STYLE}">Fields</div>'
            f'<div style="{_NUMBER_STYLE}">{len(fields)}</div>',
            unsafe_allow_html=True,
        )


def _render_bytype_card(fields: list[FieldRecord]) -> None:
    """By field_type breakdown card."""
    by_type: dict[str, int] = {}
    for f in fields:
        by_type[f.field_type] = by_type.get(f.field_type, 0) + 1

    _color_hex = {
        "blue":   ("#EEF2FF", "#C7D2FE", "#383838"),   # Periwinkle (text_field)
        "green":  ("#E8F5E9", "#38c1b0", "#383838"),   # Mint (checkbox)
        "orange": ("#F9FBE7", "#B3C419", "#383838"),   # Lime (date_field)
        "violet": ("#F7F1FF", "#B291DE", "#383838"),   # Lavender (table_row)
        "gray":   ("#ECEFF1", "#84A6BC", "#383838"),   # Slate (section_header)
    }
    type_rows = ""
    for ft in _FIELD_TYPES:
        cnt = by_type.get(ft, 0)
        color = _FIELD_TYPE_COLORS.get(ft, "gray")
        bg_col, border_col, text_col = _color_hex.get(color, ("#F1F5F9", "#CBD5E1", "#475569"))
        type_rows += (
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:3px 0;font-family:&quot;Aeonik Mono&quot;,ui-monospace,monospace;font-size:12px;">'
            f'<span style="background:{bg_col};color:{text_col};border:2px solid {border_col};'
            f'padding:1px 7px;font-weight:600;">{ft}</span>'
            f'<strong style="color:#383838;">{cnt}</strong>'
            f'</div>'
        )

    with st.container(border=True, key="p2_bytype_card"):
        st.markdown(
            f'<div style="{_LABEL_STYLE}">By Type</div>'
            f'{type_rows}',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Review panel
# ---------------------------------------------------------------------------

def _render_review_panel(
    fields: list[FieldRecord],
    session,
    target_pdf_path,
) -> None:
    # Page count
    total_pages = 1
    if target_pdf_path and target_pdf_path.exists():
        try:
            total_pages = get_pdf_page_count(target_pdf_path)
        except Exception:
            total_pages = max((f.page for f in fields), default=1)

    # Header row
    st.markdown(
        '<p style="font-family:&quot;Aeonik Mono&quot;,ui-monospace,monospace;font-size:15px;font-weight:700;'
        'color:#383838;margin:0 0 4px 0;">Review Fields</p>',
        unsafe_allow_html=True,
    )
    # Paginator as its own full-width row
    selected_page = render_page_navigator_windowed(total_pages, key="phase2_nav")

    # Field expanders
    page_fields = [(i, f) for i, f in enumerate(fields) if f.page == selected_page]
    if not page_fields:
        st.info(f"No fields on page {selected_page}.")
    else:
        for local_idx, (global_idx, field) in enumerate(page_fields):
            color = _FIELD_TYPE_COLORS.get(field.field_type, "gray")
            bg = _FIELD_TYPE_BG_COLORS.get(field.field_type, "rgba(238,238,238,0.18)")
            badge = field.field_type or "—"
            suffix = field.label[:60] if field.label else "—"
            label = f":{color}[**{badge}**] :gray[*{suffix}*]"
            container_key = f"p2_field_p{selected_page}_{local_idx}"
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
                            key=f"p2_f_p{selected_page}_{local_idx}_label",
                        )
                        form_name = st.text_input(
                            "Form Name", value=field.form_name,
                            key=f"p2_f_p{selected_page}_{local_idx}_form",
                        )
                        visit = st.text_input(
                            "Visit", value=field.visit,
                            key=f"p2_f_p{selected_page}_{local_idx}_visit",
                        )
                    with c2:
                        type_idx = _FIELD_TYPES.index(field.field_type) \
                            if field.field_type in _FIELD_TYPES else 0
                        field_type = st.selectbox(
                            "Field Type", _FIELD_TYPES, index=type_idx,
                            key=f"p2_f_p{selected_page}_{local_idx}_type",
                        )
                    btn_save, btn_del, _ = st.columns([1, 1, 4], gap="small")
                    with btn_save:
                        if st.button("Save", key=f"p2_f_p{selected_page}_{local_idx}_save",
                                     use_container_width=True):
                            updated = field.model_copy(update={
                                "label": label_val, "form_name": form_name,
                                "visit": visit, "field_type": field_type,
                            })
                            new_list = [
                                updated if i == global_idx else f
                                for i, f in enumerate(st.session_state["fields"])
                            ]
                            session.save_fields(new_list)
                            st.session_state["fields"] = new_list
                            invalidate_phases([3, 4])
                            session.log_action("phase2_edit", {"count": len(new_list)})
                            st.rerun()
                    with btn_del:
                        if st.button("Delete", key=f"p2_f_p{selected_page}_{local_idx}_del",
                                     use_container_width=True):
                            new_list = [
                                f for i, f in enumerate(st.session_state["fields"])
                                if i != global_idx
                            ]
                            session.save_fields(new_list)
                            st.session_state["fields"] = new_list
                            invalidate_phases([3, 4])
                            st.rerun()

    _render_add_field(fields, session)


# ---------------------------------------------------------------------------
# Add new field
# ---------------------------------------------------------------------------

def _render_add_field(fields: list[FieldRecord], session) -> None:
    with st.expander("Add New Field"):
        with st.form("p2_add_form"):
            col1, col2 = st.columns(2)
            with col1:
                label = st.text_input("Label")
                form_name = st.text_input("Form Name")
            with col2:
                field_type = st.selectbox("Field Type", _FIELD_TYPES)
                visit = st.text_input("Visit")
            submitted = st.form_submit_button("Add Field")
            if submitted and label.strip():
                new_record = FieldRecord(
                    id=str(uuid.uuid4()),
                    page=1,
                    label=label,
                    form_name=form_name,
                    visit=visit,
                    rect=[0.0, 0.0, 100.0, 20.0],
                    field_type=field_type,
                )
                updated = fields + [new_record]
                session.save_fields(updated)
                st.session_state["fields"] = updated
                invalidate_phases([3, 4])
                st.rerun()
