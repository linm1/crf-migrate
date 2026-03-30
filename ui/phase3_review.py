"""Phase 3: Match review UI."""
import streamlit as st

from src.csv_handler import export_matches_csv, import_matches_csv
from src.matcher import apply_manual_match, batch_approve_exact, match_annotations
from src.models import AnnotationRecord, FieldRecord, MatchRecord
from rapidfuzz import fuzz as _fuzz
from ui.components import (
    get_page_dims_from_pdf,
    invalidate_phases,
    render_confidence_badge,
    render_match_type_badge,
)


def _compute_predicted_confidence(
    annot: AnnotationRecord,
    field: FieldRecord,
    visit_boost: float,
) -> float:
    """Predict match confidence using the same formula as matcher fuzzy passes.

    Score = token_sort_ratio(anchor_text, label) + visit_boost × visit_match
    Normalised to 0.0–1.0, capped at 1.0.
    """
    raw = _fuzz.token_sort_ratio(annot.anchor_text, field.label)
    visit_a, visit_b = annot.visit.lower(), field.visit.lower()
    if visit_a and visit_b:
        if visit_a == visit_b:
            boost = visit_boost
        elif visit_a in visit_b or visit_b in visit_a:
            boost = visit_boost * 0.5
        else:
            boost = 0.0
    else:
        boost = 0.0
    return min((raw + boost) / 100.0, 1.0)


def _field_display_label(field: FieldRecord | None) -> str:
    """Format a FieldRecord as a human-readable string for display."""
    if field is None:
        return "—"
    return f"{field.label}  ·  {field.form_name}  ·  p.{field.page}"


# (label_text, fill, border, text_color) — matches Phase 2 field-type color system
_FIELD_TYPE_BADGE: dict[str, tuple[str, str, str, str]] = {
    "text_field":     ("TF", "#EEF2FF", "#C7D2FE", "#4F46E5"),
    "checkbox":       ("CB", "#FEF9C3", "#FDE047", "#A16207"),
    "date_field":     ("DF", "#F0FDF4", "#BBF7D0", "#16A34A"),
    "table_row":      ("TR", "#F4EFEA", "#D4CEC8", "#6B7280"),
    "section_header": ("SH", "#F4EFEA", "#D4CEC8", "#383838"),
}


def _render_field_type_badge(field_type: str) -> None:
    """Render a Phase-2-consistent field-type badge via st.markdown."""
    label, fill, border, color = _FIELD_TYPE_BADGE.get(
        field_type, ("??", "#F4EFEA", "#D4CEC8", "#383838")
    )
    st.markdown(
        f'<span style="background:{fill};border:1px solid {border};color:{color};'
        f'padding:2px 6px;font-size:10px;font-weight:700;border-radius:3px">{label}</span>',
        unsafe_allow_html=True,
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

    if msg_count := st.session_state.pop("_p3_match_success", None):
        st.success(f"Matched {msg_count} annotations.")

    _, tb_run, tb_export, tb_import = st.columns([3, 1, 1, 1], gap="small")

    with tb_run:
        if st.button("Run Matching", key="p3_run_btn", use_container_width=True):
            if not session:
                st.error("No active session. Please restart the app.")
            else:
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
                        st.session_state.pop("_p3_csv_ready", None)
                        session.log_action("phase3_match", {"count": len(new_matches)})
                        st.session_state["_p3_match_success"] = len(new_matches)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Matching failed: {e}")

    with tb_export:
        if st.button("Export CSV", key="p3_export_btn", use_container_width=True):
            st.session_state.pop("_p3_csv_ready", None)
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
        if not matches:
            st.info("Run matching first before importing a CSV.")
        else:
            csv_upload = st.file_uploader("Import Matches CSV", type=["csv"], key="p3_csv_upload")
            if csv_upload is not None and session:
                csv_path = session.workspace / "matches_import.csv"
                csv_path.write_bytes(csv_upload.read())
                updated, flagged = import_matches_csv(csv_path, matches)
                if flagged:
                    st.warning(f"{len(flagged)} matches missing from CSV.")
                session.save_matches(updated)
                st.session_state["matches"] = updated
                st.session_state["_p3_show_import"] = False
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
    fields = st.session_state.get("fields", [])
    field_by_id = {f.id: f for f in fields}

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
            field = field_by_id.get(m.field_id) if m.field_id else None
            st.write(_field_display_label(field))
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
