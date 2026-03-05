"""Phase 2: Field extraction and review UI."""
import uuid
from pathlib import Path

import streamlit as st

from src.csv_handler import export_fields_csv, import_fields_csv
from src.field_parser import extract_fields
from src.models import FieldRecord
from ui.components import (
    get_pdf_page_count,
    invalidate_phases,
    render_field_card,
    render_page_navigator,
)


def render_phase2(profiles_dir: Path) -> None:
    """Render Phase 2: Extract Fields page."""
    st.header("Phase 2: Extract Fields")

    session = st.session_state.get("session")
    profile = st.session_state.get("profile")
    rule_engine = st.session_state.get("rule_engine")

    if profile is None or rule_engine is None:
        st.warning("No profile loaded. Go to Profile Editor to select a profile.")
        return

    _render_upload_section(session, profile, rule_engine)

    fields = st.session_state.get("fields", [])
    if not fields:
        return

    _render_summary(fields)
    _render_page_view(fields, session)
    _render_add_field(fields, session)
    _render_csv_section(fields, session)


# ---------------------------------------------------------------------------
# A. Upload + Extract
# ---------------------------------------------------------------------------

def _render_upload_section(session, profile, rule_engine) -> None:
    st.subheader("Upload & Extract")
    uploaded = st.file_uploader("Target CRF PDF", type=["pdf"], key="phase2_upload")
    if uploaded is not None:
        pdf_path = session.workspace / "target_crf.pdf"
        pdf_path.write_bytes(uploaded.read())
        st.session_state["target_pdf_path"] = pdf_path

    target_pdf_path = st.session_state.get("target_pdf_path")

    if target_pdf_path and target_pdf_path.exists():
        st.info(f"PDF loaded: {target_pdf_path.name}")
        if st.button("Extract Fields", type="primary"):
            with st.spinner("Extracting fields…"):
                try:
                    records = extract_fields(target_pdf_path, profile, rule_engine)
                    session.save_fields(records)
                    st.session_state["fields"] = records
                    st.session_state["phases_complete"][2] = True
                    invalidate_phases([3, 4])
                    session.log_action("phase2_extract", {"count": len(records)})
                    st.success(f"Extracted {len(records)} fields.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")


# ---------------------------------------------------------------------------
# B. Summary
# ---------------------------------------------------------------------------

def _render_summary(fields: list[FieldRecord]) -> None:
    with st.expander("Summary", expanded=False):
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Fields", len(fields))
        by_type: dict[str, int] = {}
        by_form: dict[str, int] = {}
        for f in fields:
            by_type[f.field_type] = by_type.get(f.field_type, 0) + 1
            by_form[f.form_name or "(none)"] = by_form.get(f.form_name or "(none)", 0) + 1
        with col2:
            st.write("**By Type**")
            for t, cnt in sorted(by_type.items()):
                st.write(f"{t}: {cnt}")
        with col3:
            st.write("**By Form**")
            for fm, cnt in sorted(by_form.items()):
                st.write(f"{fm}: {cnt}")


# ---------------------------------------------------------------------------
# C. Page navigator + cards
# ---------------------------------------------------------------------------

def _render_page_view(fields: list[FieldRecord], session) -> None:
    st.subheader("Review Fields")
    target_pdf_path = st.session_state.get("target_pdf_path")
    total_pages = 1
    if target_pdf_path and target_pdf_path.exists():
        try:
            total_pages = get_pdf_page_count(target_pdf_path)
        except Exception:
            total_pages = max((f.page for f in fields), default=1)

    selected_page = render_page_navigator(total_pages, key="phase2_nav")
    page_fields = [f for f in fields if f.page == selected_page]

    if not page_fields:
        st.info(f"No fields on page {selected_page}.")
        return

    updated_fields = list(fields)
    indices_on_page = [i for i, f in enumerate(fields) if f.page == selected_page]

    cards = []
    for local_idx, global_idx in enumerate(indices_on_page):
        result = render_field_card(fields[global_idx], local_idx, "p2_field")
        cards.append((global_idx, result))

    if st.button("Save Changes", key="p2_save"):
        changed = False
        for global_idx, result in cards:
            if result is None:
                updated_fields[global_idx] = None
                changed = True
            elif result != fields[global_idx]:
                updated_fields[global_idx] = result
                changed = True
        updated_fields = [f for f in updated_fields if f is not None]
        if changed:
            session.save_fields(updated_fields)
            st.session_state["fields"] = updated_fields
            invalidate_phases([3, 4])
            session.log_action("phase2_edit", {"count": len(updated_fields)})
            st.rerun()


# ---------------------------------------------------------------------------
# D. Add new field
# ---------------------------------------------------------------------------

def _render_add_field(fields: list[FieldRecord], session) -> None:
    with st.expander("Add New Field"):
        with st.form("p2_add_form"):
            col1, col2 = st.columns(2)
            with col1:
                label = st.text_input("Label")
                form_name = st.text_input("Form Name")
            with col2:
                field_type = st.selectbox(
                    "Field Type",
                    ["text_field", "checkbox", "date_field", "table_row", "section_header"]
                )
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


# ---------------------------------------------------------------------------
# E. CSV Export/Import
# ---------------------------------------------------------------------------

def _render_csv_section(fields: list[FieldRecord], session) -> None:
    st.subheader("CSV Export / Import")
    col1, col2 = st.columns(2)

    with col1:
        if st.button("Export CSV"):
            csv_path = session.workspace / "fields_export.csv"
            export_fields_csv(fields, csv_path)
            csv_bytes = csv_path.read_bytes()
            st.download_button(
                "Download fields.csv",
                data=csv_bytes,
                file_name="fields.csv",
                mime="text/csv",
                key="p2_csv_dl",
            )

    with col2:
        csv_upload = st.file_uploader("Import CSV", type=["csv"], key="p2_csv_upload")
        if csv_upload is not None:
            csv_path = session.workspace / "fields_import.csv"
            csv_path.write_bytes(csv_upload.read())
            updated, flagged = import_fields_csv(csv_path, fields)
            if flagged:
                st.warning(
                    f"{len(flagged)} existing fields are missing from the CSV: "
                    f"{', '.join(flagged[:5])}{'...' if len(flagged) > 5 else ''}"
                )
                col_confirm, col_keep = st.columns(2)
                if col_confirm.button("Confirm (remove missing)", key="p2_csv_confirm"):
                    final = [r for r in updated if r.id not in flagged]
                    session.save_fields(final)
                    st.session_state["fields"] = final
                    invalidate_phases([3, 4])
                    st.rerun()
                if col_keep.button("Keep all", key="p2_csv_keep"):
                    session.save_fields(updated)
                    st.session_state["fields"] = updated
                    invalidate_phases([3, 4])
                    st.rerun()
            else:
                session.save_fields(updated)
                st.session_state["fields"] = updated
                invalidate_phases([3, 4])
                st.rerun()
