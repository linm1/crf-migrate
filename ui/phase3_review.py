"""Phase 3: Match review UI."""
import streamlit as st

from src.csv_handler import export_matches_csv, import_matches_csv
from src.matcher import apply_manual_match, batch_approve_exact, match_annotations
from src.models import MatchRecord
from ui.components import (
    get_page_dims_from_pdf,
    invalidate_phases,
    render_confidence_badge,
    render_match_type_badge,
)


def _inject_page_css() -> None:
    st.markdown(
        """
        <style>
        /* Phase 3 toolbar buttons: 12px bold monospace */
        .st-key-p3_run_btn button p,
        .st-key-p3_export_btn button p,
        .st-key-p3_import_btn button p {
            font-size: 12px !important;
            font-weight: 700 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_phase3() -> None:
    """Render Phase 3: Match page."""
    _inject_page_css()

    phases = st.session_state.get("phases_complete", {})
    if not phases.get(1):
        st.warning("Phase 1 must be complete before running matching.")
        return
    if not phases.get(2):
        st.warning("Phase 2 must be complete before running matching.")
        return

    session = st.session_state.get("session")
    profile = st.session_state.get("profile")
    annotations = st.session_state.get("annotations", [])
    fields = st.session_state.get("fields", [])
    matches = st.session_state.get("matches", [])

    _render_topbar(session, profile, annotations, fields, matches)

    if not matches:
        return

    _render_dashboard(matches)
    _render_batch_approve(matches, session)
    filtered = _render_filters(matches)
    _render_match_rows(filtered, matches, session)
    _render_unmatched_assignment(matches, fields, session)


# ---------------------------------------------------------------------------
# A. Topbar
# ---------------------------------------------------------------------------

def _render_topbar(session, profile, annotations: list, fields: list, matches: list) -> None:
    """Header + toolbar: Run Matching | Export CSV | Import CSV."""
    st.header("Phase 3: Match Annotations to Fields")

    _, tb_run, tb_export, tb_import = st.columns([3, 1, 1, 1], gap="small")

    with tb_run:
        if st.button("Run Matching", key="p3_run_btn", use_container_width=True):
            source_pdf_path = st.session_state.get("source_pdf_path")
            target_pdf_path = st.session_state.get("target_pdf_path")
            with st.spinner("Running matching passes…"):
                try:
                    source_dims = get_page_dims_from_pdf(source_pdf_path) if source_pdf_path else {}
                    target_dims = get_page_dims_from_pdf(target_pdf_path) if target_pdf_path else {}
                    new_matches = match_annotations(annotations, fields, profile, source_dims, target_dims)
                    session.save_matches(new_matches)
                    st.session_state["matches"] = new_matches
                    st.session_state["phases_complete"][3] = True
                    invalidate_phases([4])
                    session.log_action("phase3_match", {"count": len(new_matches)})
                    st.success(f"Matched {len(new_matches)} annotations.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Matching failed: {e}")

    with tb_export:
        if st.button("Export CSV", key="p3_export_btn", use_container_width=True):
            if matches and session:
                csv_path = session.workspace / "matches_export.csv"
                export_matches_csv(matches, csv_path)
                st.session_state["_p3_csv_ready"] = csv_path.read_bytes()
        if st.session_state.get("_p3_csv_ready"):
            st.download_button(
                "Download CSV",
                data=st.session_state["_p3_csv_ready"],
                file_name="matches.csv",
                mime="text/csv",
                key="p3_csv_dl",
                use_container_width=True,
            )

    with tb_import:
        if st.button("Import CSV", key="p3_import_btn", use_container_width=True):
            st.session_state["_p3_show_import"] = not st.session_state.get("_p3_show_import", False)

    if st.session_state.get("_p3_show_import", False):
        csv_upload = st.file_uploader("Import Matches CSV", type=["csv"], key="p3_csv_upload")
        if csv_upload is not None and session:
            csv_path = session.workspace / "matches_import.csv"
            csv_path.write_bytes(csv_upload.read())
            updated, flagged = import_matches_csv(csv_path, matches)
            if flagged:
                st.warning(f"{len(flagged)} matches missing from CSV.")
            session.save_matches(updated)
            st.session_state["matches"] = updated
            invalidate_phases([4])
            st.rerun()


# ---------------------------------------------------------------------------
# C. Dashboard
# ---------------------------------------------------------------------------

def _render_dashboard(matches: list[MatchRecord]) -> None:
    st.subheader("Match Dashboard")
    type_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for m in matches:
        type_counts[m.match_type] = type_counts.get(m.match_type, 0) + 1
        status_counts[m.status] = status_counts.get(m.status, 0) + 1

    all_types = list(type_counts.keys())
    if all_types:
        cols = st.columns(len(all_types))
        for col, mt in zip(cols, all_types):
            col.metric(mt, type_counts[mt])

    st.write("**By Status:**", " | ".join(f"{s}: {c}" for s, c in status_counts.items()))


# ---------------------------------------------------------------------------
# D. Filters
# ---------------------------------------------------------------------------

def _render_filters(matches: list[MatchRecord]) -> list[MatchRecord]:
    st.subheader("Filters")
    col1, col2, col3, col4 = st.columns(4)
    all_types = sorted({m.match_type for m in matches})
    all_statuses = sorted({m.status for m in matches})
    all_domains: list[str] = []  # MatchRecord has no domain; skip domain filter

    with col1:
        sel_types = st.multiselect("Match Type", all_types, default=all_types, key="p3_filter_type")
    with col2:
        sel_statuses = st.multiselect("Status", all_statuses, default=all_statuses, key="p3_filter_status")
    with col3:
        min_conf = st.slider("Min Confidence", 0.0, 1.0, 0.0, 0.01, key="p3_filter_conf")
    with col4:
        pass  # domain filter omitted (not in MatchRecord)

    filtered = [
        m for m in matches
        if m.match_type in sel_types
        and m.status in sel_statuses
        and m.confidence >= min_conf
    ]
    st.caption(f"Showing {len(filtered)} of {len(matches)} matches")
    return filtered


# ---------------------------------------------------------------------------
# E. Match rows
# ---------------------------------------------------------------------------

def _render_match_rows(
    filtered: list[MatchRecord],
    all_matches: list[MatchRecord],
    session,
) -> None:
    st.subheader("Matches")
    annotations = st.session_state.get("annotations", [])
    annot_by_id = {a.id: a for a in annotations}

    updated_matches = list(all_matches)
    match_index = {m.annotation_id: i for i, m in enumerate(all_matches)}

    action_taken = False
    for m in filtered:
        annot = annot_by_id.get(m.annotation_id)
        annot_label = annot.content[:40] if annot else m.annotation_id[:12]
        col1, col2, col3, col4, col5 = st.columns([3, 3, 1, 1, 2])
        with col1:
            st.write(f"**{annot_label}**")
        with col2:
            st.write(m.field_id or "—")
        with col3:
            render_confidence_badge(m.confidence)
        with col4:
            render_match_type_badge(m.match_type)
        with col5:
            approve_key = f"p3_approve_{m.annotation_id}"
            reject_key = f"p3_reject_{m.annotation_id}"
            bcol1, bcol2 = st.columns(2)
            if bcol1.button("✓", key=approve_key, help="Approve"):
                idx = match_index.get(m.annotation_id)
                if idx is not None:
                    updated_matches[idx] = m.model_copy(update={"status": "approved"})
                    action_taken = True
            if bcol2.button("✗", key=reject_key, help="Reject"):
                idx = match_index.get(m.annotation_id)
                if idx is not None:
                    updated_matches[idx] = m.model_copy(update={"status": "rejected"})
                    action_taken = True

    if action_taken:
        session.save_matches(updated_matches)
        st.session_state["matches"] = updated_matches
        st.rerun()


# ---------------------------------------------------------------------------
# F. Unmatched assignment
# ---------------------------------------------------------------------------

def _render_unmatched_assignment(
    matches: list[MatchRecord],
    fields,
    session,
) -> None:
    unmatched = [m for m in matches if m.match_type == "unmatched"]
    if not unmatched:
        return

    st.subheader(f"Unmatched Annotations ({len(unmatched)})")
    annotations = st.session_state.get("annotations", [])
    annot_by_id = {a.id: a for a in annotations}

    field_options = [f"{f.label} | {f.form_name} | p.{f.page}" for f in fields]
    field_ids = [f.id for f in fields]

    for m in unmatched:
        annot = annot_by_id.get(m.annotation_id)
        label = annot.content[:40] if annot else m.annotation_id
        with st.expander(f"Unmatched: {label}"):
            if not fields:
                st.info("No fields available for assignment.")
                continue
            sel_idx = st.selectbox(
                "Assign to field",
                range(len(field_options)),
                format_func=lambda i: field_options[i],
                key=f"p3_assign_sel_{m.annotation_id}",
            )
            if st.button("Assign", key=f"p3_assign_btn_{m.annotation_id}"):
                chosen_field = fields[sel_idx]
                try:
                    updated = apply_manual_match(
                        matches,
                        m.annotation_id,
                        chosen_field.id,
                        list(chosen_field.rect),
                    )
                    session.save_matches(updated)
                    st.session_state["matches"] = updated
                    invalidate_phases([4])
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


# ---------------------------------------------------------------------------
# G. Batch approve exact
# ---------------------------------------------------------------------------

def _render_batch_approve(matches: list[MatchRecord], session) -> None:
    pending_exact = [m for m in matches if m.match_type == "exact" and m.status == "pending"]
    if not pending_exact:
        return
    st.info(f"{len(pending_exact)} pending exact matches.")
    if st.button(f"Batch Approve {len(pending_exact)} Exact Matches"):
        updated = batch_approve_exact(matches)
        session.save_matches(updated)
        st.session_state["matches"] = updated
        invalidate_phases([4])
        st.rerun()
