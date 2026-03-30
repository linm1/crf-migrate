"""Phase 3: Match review UI."""
from __future__ import annotations

import streamlit as st
from rapidfuzz import fuzz as _fuzz

from src.csv_handler import export_matches_csv, import_matches_csv
from src.matcher import apply_manual_match, batch_approve_exact, compute_target_rect, match_annotations
from src.models import AnnotationRecord, FieldRecord, MatchRecord
from src.session import Session
from ui.components import (
    get_page_dims_from_pdf,
    invalidate_phases,
    render_confidence_badge,
    render_match_type_badge,
)

_DEFAULT_VISIT_BOOST: float = 5.0
_DEFAULT_CROSS_FORM_THRESHOLD: float = 0.5
_HIGH_CONFIDENCE_THRESHOLD: float = 0.9


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


_REPAIR_ELIGIBLE_TYPES = {"fuzzy", "position_only", "unmatched", "manual"}


def _is_repair_eligible(match_type: str) -> bool:
    """Return True if this match type can be inline re-paired."""
    return match_type in _REPAIR_ELIGIBLE_TYPES


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
        /* Re-pair confirm button: dark neo-brutalist style */
        [class*="st-key-p3_confirm_repair_"] button {
            background-color: #383838 !important;
            border: 1px solid #383838 !important;
            color: #FFFFFF !important;
            font-weight: 700 !important;
            box-shadow: 4px 4px 0 #000000 !important;
        }
        [class*="st-key-p3_confirm_repair_"] button:disabled {
            background-color: #8A847F !important;
            border-color: #8A847F !important;
            box-shadow: none !important;
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

    session: Session | None = st.session_state.get("session")
    profile = st.session_state.get("profile")
    annotations: list[AnnotationRecord] = st.session_state.get("annotations", [])
    fields: list[FieldRecord] = st.session_state.get("fields", [])
    matches: list[MatchRecord] = st.session_state.get("matches", [])

    _render_topbar(session, profile, annotations, fields, matches)

    if session is None:
        st.error("No active session. Please restart the app.")
        return

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

def _render_topbar(
    session: Session | None,
    profile: object,
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
    matches: list[MatchRecord],
) -> None:
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
    col1, col2, col3 = st.columns(3)
    all_types = sorted({m.match_type for m in matches})
    all_statuses = sorted({m.status for m in matches})

    with col1:
        sel_types = st.multiselect("Match Type", all_types, default=all_types, key="p3_filter_type")
    with col2:
        sel_statuses = st.multiselect("Status", all_statuses, default=all_statuses, key="p3_filter_status")
    with col3:
        min_conf = st.slider("Min Confidence", 0.0, 1.0, 0.0, 0.01, key="p3_filter_conf")

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

def _render_row_actions(
    m: MatchRecord,
    is_open: bool,
    updated_matches: list[MatchRecord],
    match_index: dict[str, int],
) -> bool:
    """Render approve/reject/cancel buttons for one match row. Returns True if action taken."""
    approve_key = f"p3_approve_{m.annotation_id}"
    reject_key = f"p3_reject_{m.annotation_id}"
    cancel_key = f"p3_cancel_{m.annotation_id}"
    bcols = st.columns(3 if is_open else 2)

    if is_open:
        if bcols[0].button("✓", key=approve_key, help="Approve"):
            idx = match_index.get(m.annotation_id)
            if idx is not None:
                updated_matches[idx] = m.model_copy(update={"status": "approved"})
                st.session_state.pop("_p3_repairing", None)
                st.session_state.pop("_p3_repair_search", None)
                return True
        if bcols[1].button("✕", key=cancel_key, help="Cancel re-pair"):
            idx = match_index.get(m.annotation_id)
            if idx is not None:
                updated_matches[idx] = m.model_copy(update={"status": "pending"})
                st.session_state.pop("_p3_repairing", None)
                st.session_state.pop("_p3_repair_search", None)
                return True
    else:
        if bcols[0].button("✓", key=approve_key, help="Approve"):
            idx = match_index.get(m.annotation_id)
            if idx is not None:
                updated_matches[idx] = m.model_copy(update={"status": "approved"})
                return True
        if bcols[1].button("✕", key=reject_key, help="Reject"):
            idx = match_index.get(m.annotation_id)
            if idx is not None:
                updated_matches[idx] = m.model_copy(update={"status": "rejected"})
                if _is_repair_eligible(m.match_type):
                    st.session_state["_p3_repairing"] = m.annotation_id
                    st.session_state.pop("_p3_repair_search", None)
                return True
    return False


def _render_match_rows(
    filtered: list[MatchRecord],
    all_matches: list[MatchRecord],
    session: Session,
) -> None:
    st.subheader("Matches")
    annotations: list[AnnotationRecord] = st.session_state.get("annotations", [])
    annot_by_id = {a.id: a for a in annotations}
    fields: list[FieldRecord] = st.session_state.get("fields", [])
    field_by_id = {f.id: f for f in fields}
    profile = st.session_state.get("profile")
    visit_boost: float = profile.matching_config.visit_boost if profile else _DEFAULT_VISIT_BOOST
    cross_form_threshold: float = (
        profile.matching_config.fuzzy_cross_form_threshold if profile
        else _DEFAULT_CROSS_FORM_THRESHOLD
    )

    updated_matches = list(all_matches)
    match_index = {m.annotation_id: i for i, m in enumerate(all_matches)}
    repairing_id: str | None = st.session_state.get("_p3_repairing")

    action_taken = False
    for m in filtered:
        annot = annot_by_id.get(m.annotation_id)
        field = field_by_id.get(m.field_id) if m.field_id else None
        annot_label = annot.content[:40] if annot else m.annotation_id[:12]
        is_open = repairing_id == m.annotation_id

        col1, col2, col3, col4, col5 = st.columns([3, 3, 1, 1, 2])
        with col1:
            st.write(f"**{annot_label}**")
        with col2:
            suffix = "  · re-pairing" if is_open else ""
            st.write(_field_display_label(field) + suffix)
            if m.match_type == "manual" and not is_open:
                st.caption("manually paired · rect recomputed")
        with col3:
            render_confidence_badge(m.confidence)
        with col4:
            if is_open:
                st.markdown(
                    '<span style="background:#FEF3C7;border:1px solid #F59E0B;color:#92400E;'
                    'padding:2px 8px;font-size:11px;font-weight:700;border-radius:3px">'
                    'Rejected — Re-pair</span>',
                    unsafe_allow_html=True,
                )
            else:
                render_match_type_badge(m.match_type)
        with col5:
            action_taken = _render_row_actions(m, is_open, updated_matches, match_index) or action_taken

        if is_open and annot:
            new_matches = _render_repair_panel(
                m, annot, fields, field_by_id, updated_matches,
                match_index, visit_boost, cross_form_threshold,
            )
            if new_matches is not None:
                updated_matches = new_matches
                action_taken = True

    if action_taken:
        session.save_matches(updated_matches)
        st.session_state["matches"] = updated_matches
        invalidate_phases([4])
        st.rerun()


def _render_annotation_detail(
    m: MatchRecord,
    annot: AnnotationRecord,
    field_by_id: dict[str, FieldRecord],
) -> None:
    """Left panel: annotation context card."""
    st.markdown("**ANNOTATION**")
    with st.container(border=True):
        st.markdown(f"**{annot.anchor_text or annot.content[:30]}**")
        st.caption(annot.content[:60])
        st.markdown(
            f"**Domain:** {annot.domain}  \n"
            f"**Form:** {annot.form_name}  \n"
            f"**Anchor:** {annot.anchor_text}",
        )
        current_field = field_by_id.get(m.field_id) if m.field_id else None
        st.caption(
            f"Current confidence: {m.confidence:.2f}"
            + (f" → {_field_display_label(current_field)}" if current_field else "")
        )


def _render_field_row(
    annotation_id: str,
    score: float,
    f: FieldRecord,
    chosen_field_id: str | None,
) -> None:
    """Render one selectable field row in the picker."""
    is_selected = f.id == chosen_field_id
    bg = "#FFF9E6" if is_selected else "#FAFAFA"
    border = "2px solid #F59E0B" if is_selected else "1px solid #E8E2DC"
    is_high = score >= _HIGH_CONFIDENCE_THRESHOLD
    conf_color = "#065F46" if is_high else "#6B7280"
    conf_bg = "#D1FAE5" if is_high else "#F3F4F6"
    label_txt, fill, brd, txt = _FIELD_TYPE_BADGE.get(
        f.field_type, ("??", "#F4EFEA", "#D4CEC8", "#383838")
    )
    st.markdown(
        f'<div style="background:{bg};border:{border};padding:6px 10px;'
        f'margin:2px 0;display:flex;align-items:center;gap:8px">'
        f'<span style="background:{fill};border:1px solid {brd};color:{txt};'
        f'padding:1px 5px;font-size:10px;font-weight:700;border-radius:3px">{label_txt}</span>'
        f'<span style="flex:1;font-size:12px;font-weight:600">{f.label}</span>'
        f'<span style="font-size:10px;color:#8A847F">{f.field_type} · p.{f.page}</span>'
        f'<span style="background:{conf_bg};color:{conf_color};padding:1px 6px;'
        f'font-size:10px;font-weight:700;border-radius:3px">→ {score:.2f}</span></div>',
        unsafe_allow_html=True,
    )
    if st.button(
        "✓ Selected" if is_selected else "Select",
        key=f"p3_pick_{annotation_id}_{f.id}",
    ):
        sel = dict(st.session_state.get("_p3_repair_selected", {}))
        sel[annotation_id] = f.id
        st.session_state["_p3_repair_selected"] = sel
        st.rerun()


def _render_field_picker(
    m: MatchRecord,
    annot: AnnotationRecord,
    fields: list[FieldRecord],
    visit_boost: float,
    cross_form_threshold: float,
) -> str | None:
    """Right panel: search box + grouped field list. Returns chosen field_id or None."""
    st.markdown("**SELECT TARGET FIELD**")
    search_raw = st.text_input(
        "Search by field label",
        value=st.session_state.get("_p3_repair_search", ""),
        key=f"p3_repair_search_{m.annotation_id}",
        placeholder="Search by field label…",
        label_visibility="collapsed",
    )
    st.session_state["_p3_repair_search"] = search_raw
    search_lower = search_raw.lower()

    scored: list[tuple[float, FieldRecord]] = sorted(
        ((_compute_predicted_confidence(annot, f, visit_boost), f) for f in fields),
        key=lambda x: -x[0],
    )
    if search_lower:
        scored = [(s, f) for s, f in scored if search_lower in f.label.lower()]

    same_form_lower = annot.form_name.lower()
    same_form = [(s, f) for s, f in scored if f.form_name.lower() == same_form_lower]
    cross_form = [(s, f) for s, f in scored if f.form_name.lower() != same_form_lower]

    chosen_field_id: str | None = st.session_state.get("_p3_repair_selected", {}).get(m.annotation_id)

    if same_form:
        st.caption(f"**{annot.form_name.upper()}**")
        for score, f in same_form[:5]:
            _render_field_row(m.annotation_id, score, f, chosen_field_id)

    eligible_cross = [(s, f) for s, f in cross_form if s >= cross_form_threshold]
    if eligible_cross:
        st.caption("**OTHER FORMS (FUZZY)**")
        for score, f in eligible_cross[:3]:
            _render_field_row(m.annotation_id, score, f, chosen_field_id)

    return chosen_field_id


def _render_repair_panel(
    m: MatchRecord,
    annot: AnnotationRecord,
    fields: list[FieldRecord],
    field_by_id: dict[str, FieldRecord],
    updated_matches: list[MatchRecord],
    match_index: dict[str, int],
    visit_boost: float,
    cross_form_threshold: float = _DEFAULT_CROSS_FORM_THRESHOLD,
) -> list[MatchRecord] | None:
    """Render the two-column inline re-pair picker.

    Returns a new matches list when the user confirms a pairing, else None.
    """
    st.markdown(
        '<div style="border-top:2px solid #F59E0B;margin:4px 0 8px 0"></div>',
        unsafe_allow_html=True,
    )
    left_col, right_col = st.columns([1, 2])

    with left_col:
        _render_annotation_detail(m, annot, field_by_id)

    with right_col:
        chosen_field_id = _render_field_picker(
            m, annot, fields, visit_boost, cross_form_threshold
        )

        if st.button(
            "Confirm Pairing → rect will be recomputed",
            key=f"p3_confirm_repair_{m.annotation_id}",
            disabled=chosen_field_id is None,
            use_container_width=True,
            type="primary",
        ):
            chosen_field = field_by_id.get(chosen_field_id) if chosen_field_id else None
            if chosen_field:
                new_rect = compute_target_rect(annot, chosen_field, list(field_by_id.values()))
                predicted = _compute_predicted_confidence(annot, chosen_field, visit_boost)
                # apply_manual_match sets field_id, match_type="manual", target_rect, status="approved"
                new_list = apply_manual_match(
                    list(updated_matches), m.annotation_id, chosen_field.id, new_rect
                )
                idx = match_index.get(m.annotation_id)
                if idx is not None:
                    new_list[idx] = new_list[idx].model_copy(update={"confidence": predicted})
                st.session_state.pop("_p3_repairing", None)
                st.session_state.pop("_p3_repair_search", None)
                sel = dict(st.session_state.get("_p3_repair_selected", {}))
                sel.pop(m.annotation_id, None)
                st.session_state["_p3_repair_selected"] = sel
                return new_list

        st.caption("Score = fuzzy(anchor_text, label) + visit boost")

    return None


# ---------------------------------------------------------------------------
# F. Unmatched assignment
# ---------------------------------------------------------------------------

def _render_unmatched_assignment(
    matches: list[MatchRecord],
    fields: list[FieldRecord],
    session: Session,
) -> None:
    unmatched = [m for m in matches if m.match_type == "unmatched"]
    if not unmatched:
        return

    st.subheader(f"Unmatched Annotations ({len(unmatched)})")
    annotations = st.session_state.get("annotations", [])
    annot_by_id = {a.id: a for a in annotations}

    field_options = [f"{f.label} | {f.form_name} | p.{f.page}" for f in fields]

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
                    target_rect = (
                        compute_target_rect(annot, chosen_field, fields)
                        if annot is not None
                        else list(chosen_field.rect)
                    )
                    updated = apply_manual_match(
                        matches,
                        m.annotation_id,
                        chosen_field.id,
                        target_rect,
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

def _render_batch_approve(matches: list[MatchRecord], session: Session) -> None:
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
