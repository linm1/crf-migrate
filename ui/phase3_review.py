"""Phase 3: Match review UI."""
from __future__ import annotations

import html as _html
from pathlib import Path

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

# Phase 2-style card typography (mirrors ui/phase2_review.py)
_LABEL_STYLE = (
    "font-family:Inter,sans-serif;font-size:11px;font-weight:600;"
    "color:#8A847F;text-transform:uppercase;letter-spacing:0.5px;margin:0 0 4px 0;"
)
_NUMBER_STYLE = (
    "font-family:Inter,sans-serif;font-size:32px;font-weight:700;"
    "color:#383838;line-height:1.1;margin:0 0 16px 0;"
)

# (bg, border, text) — mirrors render_match_type_badge() in ui/components.py
_MATCH_TYPE_BADGE_COLORS: dict[str, tuple[str, str, str]] = {
    "exact":         ("#cce5ff", "#99c9ff", "#004085"),
    "fuzzy":         ("#e2d9f3", "#c5b3e7", "#3d1a78"),
    "position_only": ("#fff3cd", "#ffe69c", "#7d4e00"),
    "manual":        ("#d1ecf1", "#a3d8e4", "#0c5460"),
    "unmatched":     ("#f8d7da", "#f1aeb5", "#721c24"),
}

_MATCH_TYPE_ORDER = ["exact", "fuzzy", "position_only", "unmatched", "manual"]


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
        /* ── Phase 3 card heights (matches Phase 2 pattern exactly) ── */
        .st-key-p3_action_card,
        .st-key-p3_rate_card,
        .st-key-p3_bytype_card {
            min-height: 220px !important;
            height: 220px !important;
            box-sizing: border-box !important;
            margin-top: 0 !important;
        }
        .st-key-p3_action_card > div[data-testid="stVerticalBlock"],
        .st-key-p3_rate_card > div[data-testid="stVerticalBlock"],
        .st-key-p3_bytype_card > div[data-testid="stVerticalBlock"] {
            min-height: 204px !important;
            height: 100% !important;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }
        /* ── Phase 3 topbar ── */
        .st-key-p3_run_btn button {
            background-color: #383838 !important;
            border: 1px solid #383838 !important;
            color: #FFFFFF !important;
            font-weight: 700 !important;
            font-size: 14px !important;
            box-shadow: 4px 4px 0 rgba(0,0,0,0.22) !important;
        }
        /* ── Phase 3 topbar CSV buttons (matches Phase 2 style) ── */
        .st-key-p3_export_btn button p,
        .st-key-p3_import_btn button p {
            font-size: 12px !important;
            font-weight: 700 !important;
        }
        /* ── Stat cards ── */
        .p3-stat-card {
            background: #FFFFFF;
            border: 1px solid #E8E2DC;
            box-shadow: 4px 4px 0 rgba(0,0,0,0.07);
            padding: 14px 20px;
            display: flex;
            flex-direction: column;
            gap: 2px;
        }
        .p3-stat-num  { font-size: 24px; font-weight: 700; color: #383838; }
        .p3-stat-lbl  { font-size: 11px; color: #8A847F; }
        /* ── Batch approve button ── */
        .st-key-p3_batch_approve button {
            background-color: #383838 !important;
            border: 1px solid #383838 !important;
            color: #FFFFFF !important;
            font-size: 12px !important;
            font-weight: 700 !important;
            box-shadow: 4px 4px 0 rgba(0,0,0,0.22) !important;
            padding: 2px 12px !important;
            height: 30px !important;
        }
        /* ── Match row cards — applied via container key ── */
        [class*="st-key-row_"] > div:first-child {
            border: 1px solid #383838;
            box-shadow: 3px 3px 0 rgba(0,0,0,0.07);
            padding: 4px 16px 4px 8px;
            margin-bottom: 4px;
            background: #FFFFFF;
        }
        /* Repair state: amber border */
        [class*="st-key-row_repair_"] > div:first-child {
            border: 2px solid #F59E0B !important;
            background: #FFFBEF !important;
            box-shadow: 3px 3px 0 rgba(0,0,0,0.09) !important;
        }
        /* Manual state: teal border */
        [class*="st-key-row_manual_"] > div:first-child {
            border: 1px solid #0c5460 !important;
            background: #F0FAFA !important;
        }
        /* Domain badge */
        .p3-domain-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border: 1px solid #D0D0D0;
            background: #FFFFFF;
            width: 36px;
            height: 22px;
            font-size: 11px;
            font-weight: 700;
            color: #262730;
        }
        /* ── Re-pair confirm button: dark neo-brutalist style ── */
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
        /* ── Match type multiselect tag colors ── */
        [data-baseweb="tag"]:has(span[title="exact"])         { background: #cce5ff !important; color: #004085 !important; }
        [data-baseweb="tag"]:has(span[title="fuzzy"])         { background: #e2d9f3 !important; color: #3d1a78 !important; }
        [data-baseweb="tag"]:has(span[title="position_only"]) { background: #fff3cd !important; color: #7d4e00 !important; }
        [data-baseweb="tag"]:has(span[title="manual"])        { background: #d1ecf1 !important; color: #0c5460 !important; }
        [data-baseweb="tag"]:has(span[title="unmatched"])     { background: #f8d7da !important; color: #721c24 !important; }
        /* ── Phase 3 card white backgrounds (matches Phase 2) ── */
        .st-key-p3_action_card,
        .st-key-p3_rate_card,
        .st-key-p3_bytype_card {
            background: #FFFFFF !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_topbar_p3(
    matches: list[MatchRecord],
    session: Session | None,
) -> None:
    """Topbar row: Export CSV and Import CSV buttons (Phase 2 style)."""
    _, tb_export, tb_import_btn = st.columns([5, 1, 1], gap="small")

    with tb_export:
        if st.button("Export CSV", key="p3_export_btn", use_container_width=True):
            st.session_state.pop("_p3_csv_ready", None)
            if not session:
                st.error("No active session — cannot export.")
            elif matches:
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

    with tb_import_btn:
        if st.button("Import CSV", key="p3_import_btn", use_container_width=True):
            st.session_state["_p3_show_import"] = not st.session_state.get("_p3_show_import", False)

    if st.session_state.get("_p3_show_import", False):
        if not matches:
            st.info("Run matching first before importing a CSV.")
        else:
            csv_upload = st.file_uploader("Import Matches CSV", type=["csv"], key="p3_csv_upload")
            if csv_upload is not None and not session:
                st.error("No active session — cannot import.")
            elif csv_upload is not None and session:
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


def _render_action_card(
    session: Session | None,
    profile: object,
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
) -> None:
    """Card 1: Source/Target filenames + Run Matching button at bottom."""
    source_path = st.session_state.get("source_pdf_path")
    target_path = st.session_state.get("target_pdf_path")
    source_name = _html.escape(
        st.session_state.get("source_pdf_name")
        or (Path(source_path).name if source_path else "—")
    )
    target_name = _html.escape(
        st.session_state.get("target_pdf_name")
        or (Path(target_path).name if target_path else "—")
    )

    _no_session_error = False
    with st.container(border=True, key="p3_action_card"):
        st.markdown(
            f'<div style="{_LABEL_STYLE}">Source aCRF</div>'
            f'<div style="font-family:Inter,sans-serif;font-size:13px;font-weight:600;'
            f'color:#383838;margin:0 0 12px 0;word-break:break-all;">{source_name}</div>'
            f'<div style="{_LABEL_STYLE}">Target CRF</div>'
            f'<div style="font-family:Inter,sans-serif;font-size:13px;font-weight:600;'
            f'color:#383838;margin:0 0 0 0;word-break:break-all;">{target_name}</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div style="margin-top:16px;"></div>', unsafe_allow_html=True)
        if st.button("Run Matching", key="p3_run_btn", use_container_width=True):
            if not session:
                _no_session_error = True
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
                        st.rerun()
                    except Exception as e:
                        st.error(f"Matching failed: {e}")
    if _no_session_error:
        st.error("No active session. Please restart the app.")


def _render_rate_card(matches: list[MatchRecord]) -> None:
    """Card 2: Match Rate % and exact count."""
    total_annotations = len(st.session_state.get("annotations", []))
    exact_count = len([m for m in matches if m.match_type == "exact"])
    pct = round(exact_count / total_annotations * 100) if total_annotations else 0

    with st.container(border=True, key="p3_rate_card"):
        st.markdown(
            f'<div style="{_LABEL_STYLE}">Exact Match Rate</div>'
            f'<div style="{_NUMBER_STYLE}">{pct}%</div>'
            f'<div style="{_LABEL_STYLE}">Exact Matches</div>'
            f'<div style="font-family:Inter,sans-serif;font-size:24px;font-weight:700;'
            f'color:#383838;line-height:1.1;margin:0;">{exact_count}</div>',
            unsafe_allow_html=True,
        )


def _render_bytype_card_p3(matches: list[MatchRecord]) -> None:
    """Card 3: By Match Type breakdown with badge-colored rows."""
    type_counts: dict[str, int] = {mt: 0 for mt in _MATCH_TYPE_ORDER}
    for m in matches:
        if m.match_type in type_counts:
            type_counts[m.match_type] += 1

    type_rows = ""
    for mt in _MATCH_TYPE_ORDER:
        count = type_counts[mt]
        bg, border, text = _MATCH_TYPE_BADGE_COLORS[mt]
        type_rows += (
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:3px 0;font-family:Inter,sans-serif;font-size:12px;">'
            f'<span style="background:{bg};color:{text};padding:1px 7px;font-weight:600;'
            f'border:1px solid {border};">{mt}</span>'
            f'<strong style="color:#383838;">{count}</strong>'
            f'</div>'
        )

    with st.container(border=True, key="p3_bytype_card"):
        st.markdown(
            f'<div style="{_LABEL_STYLE}">By Match Type</div>'
            f'{type_rows}',
            unsafe_allow_html=True,
        )


def _render_cards(
    session: Session | None,
    profile: object,
    annotations: list[AnnotationRecord],
    fields: list[FieldRecord],
    matches: list[MatchRecord],
) -> None:
    """Always-visible 3-column card row (Phase 2 pattern)."""
    _render_topbar_p3(matches, session)
    _hdr = (
        "font-family:Inter,sans-serif;font-size:12px;font-weight:700;"
        "color:#383838;text-transform:uppercase;letter-spacing:0.5px;"
        "margin:0;padding:0;"
    )
    st.markdown(
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem;margin-bottom:6px;">'
        f'<p style="{_hdr}">Match Files</p>'
        f'<p style="{_hdr}">Exact Match Rate</p>'
        f'<p style="{_hdr}">By Match Type</p>'
        f'</div>',
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3, gap="large")
    with c1:
        _render_action_card(session, profile, annotations, fields)
    with c2:
        _render_rate_card(matches)
    with c3:
        _render_bytype_card_p3(matches)


def render_phase3() -> None:
    """Render Phase 3: Match page."""
    st.header("Phase 3: Match Annotations to Fields")
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

    _render_cards(session, profile, annotations, fields, matches)

    if session is None:
        st.error("No active session. Please restart the app.")
        return

    filtered = _render_filters(matches, session)
    _render_match_rows(filtered, matches, session)
    _render_unmatched_assignment(matches, fields, session)


# ---------------------------------------------------------------------------
# D. Filters
# ---------------------------------------------------------------------------

def _render_filters(matches: list[MatchRecord], session: Session) -> list[MatchRecord]:
    all_types = sorted({m.match_type for m in matches})
    all_statuses = sorted({m.status for m in matches})

    t_col, s_col, c_col, spacer, ba_col = st.columns([2, 2, 2, 1, 2])
    with t_col:
        sel_types = st.multiselect("Match Type \u25be", all_types, default=all_types,
                                   key="p3_filter_type", label_visibility="visible")
    with s_col:
        sel_statuses = st.multiselect("Status \u25be", all_statuses, default=all_statuses,
                                      key="p3_filter_status", label_visibility="visible")
    with c_col:
        min_conf = st.slider("Confidence", 0.0, 1.0, 0.0, 0.01,
                             key="p3_filter_conf", label_visibility="visible")
    with ba_col:
        pending_exact = [m for m in matches if m.match_type == "exact" and m.status == "pending"]
        if pending_exact:
            if st.button(f"Batch Approve Exact ({len(pending_exact)})",
                         key="p3_batch_approve", use_container_width=True):
                updated = batch_approve_exact(matches)
                session.save_matches(updated)
                st.session_state["matches"] = updated
                invalidate_phases([4])
                st.rerun()

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
        domain = annot.domain[:4] if annot and annot.domain else "—"

        # card wrapper — use CSS key on st.container for border/shadow
        if is_open:
            row_css = "p3-match-row-repair"
        elif m.match_type == "manual":
            row_css = "p3-match-row-manual"
        else:
            row_css = "p3-match-row"

        state_prefix = "repair" if is_open else ("manual" if m.match_type == "manual" else "std")
        with st.container(key=f"row_{state_prefix}_{m.annotation_id}"):
            col_domain, col1, col2, col3, col4, col5 = st.columns([0.5, 3, 3, 1, 1, 1.5])
            with col_domain:
                st.markdown(
                    f'<span class="p3-domain-badge">{domain}</span>',
                    unsafe_allow_html=True,
                )
            with col1:
                st.markdown(
                    f'<span style="font-size:13px;font-weight:600;color:#1E293B">{annot_label}</span>',
                    unsafe_allow_html=True,
                )
            with col2:
                suffix = "  · re-pairing" if is_open else ""
                field_lbl = _field_display_label(field) + suffix
                form_sub = "manually paired · rect recomputed" if (m.match_type == "manual" and not is_open) else ""
                st.markdown(
                    f'<span style="font-size:13px;font-weight:600;color:#1E293B">{field_lbl}</span>'
                    + (f'<br><span style="font-size:11px;color:#94A3B8">{form_sub}</span>' if form_sub else ""),
                    unsafe_allow_html=True,
                )
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


