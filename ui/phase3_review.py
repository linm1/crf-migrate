"""Phase 3: Match review UI."""
from __future__ import annotations

import html as _html
import threading
import time
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
from ui.loader import clear_loader, loader_html, show_loader

_DEFAULT_VISIT_BOOST: float = 5.0
_DEFAULT_CROSS_FORM_THRESHOLD: float = 0.5
_HIGH_CONFIDENCE_THRESHOLD: float = 0.9

# Phase 2-style card typography (mirrors ui/phase2_review.py)
_LABEL_STYLE = (
    "font-family:'Aeonik Mono', ui-monospace, monospace;font-size:11px;font-weight:600;"
    "color:#818181;text-transform:uppercase;letter-spacing:0.5px;margin:0 0 4px 0;"
)
_NUMBER_STYLE = (
    "font-family:'Aeonik Mono', ui-monospace, monospace;font-size:32px;font-weight:700;"
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
            background-color: #6fc2ff !important;
            border: 2px solid #383838 !important;
            border-radius: 2px !important;
            color: #383838 !important;
            font-family: 'Aeonik Mono', ui-monospace, monospace !important;
            font-weight: 400 !important;
            font-size: 16px !important;
            text-transform: uppercase !important;
        }
        /* ── Phase 3 topbar CSV buttons (matches Phase 2 style) ── */
        .st-key-p3_export_btn button p,
        .st-key-p3_import_btn button p {
            font-size: 12px !important;
            font-weight: 700 !important;
            text-transform: uppercase !important;
        }
        /* ── Stat cards ── */
        .p3-stat-card {
            background: #FFFFFF;
            border: 2px solid #000000;
            padding: 16px 20px;
            display: flex;
            flex-direction: column;
            gap: 2px;
        }
        .p3-stat-num  { font-size: 24px; font-weight: 700; color: #383838; }
        .p3-stat-lbl  { font-size: 11px; color: #818181; }
        /* ── Batch approve button ── */
        .st-key-p3_batch_approve button {
            background-color: #f4efea !important;
            border: 2px solid #383838 !important;
            border-radius: 2px !important;
            color: #383838 !important;
            font-family: 'Aeonik Mono', ui-monospace, monospace !important;
            font-size: 12px !important;
            font-weight: 400 !important;
            text-transform: uppercase !important;
            padding: 2px 12px !important;
            height: 30px !important;
        }
        /* ── Match row cards — applied via container key ── */
        [class*="st-key-row_"] > div:first-child {
            border: 2px solid #000000;
            padding: 4px 16px 4px 8px;
            margin-bottom: 4px;
            background: #FFFFFF;
        }
        /* Vertically center all columns inside match rows */
        [class*="st-key-row_"] > div:first-child [data-testid="stHorizontalBlock"] {
            align-items: center !important;
        }
        /* Repair state: amber border */
        [class*="st-key-row_repair_"] > div:first-child {
            border: 2px solid #F59E0B !important;
            background: #FFFBEF !important;
        }
        /* Manual state: teal border */
        [class*="st-key-row_manual_"] > div:first-child {
            border: 2px solid #000000 !important;
            background: #F0FAFA !important;
        }
        /* Domain badge */
        .p3-domain-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border: 2px solid #000000;
            background: #FFFFFF;
            width: 36px;
            height: 22px;
            font-size: 11px;
            font-weight: 700;
            color: #262730;
        }
        /* ── Re-pair confirm button: primary CTA style ── */
        [class*="st-key-p3_confirm_repair_"] button {
            background-color: #6fc2ff !important;
            border: 2px solid #383838 !important;
            border-radius: 2px !important;
            color: #383838 !important;
            font-family: 'Aeonik Mono', ui-monospace, monospace !important;
            font-weight: 400 !important;
            font-size: 14px !important;
            text-transform: uppercase !important;
        }
        [class*="st-key-p3_confirm_repair_"] button:disabled {
            background-color: #f4efea !important;
            border-color: #383838 !important;
        }
        /* ── Match type multiselect tag colors ── */
        [data-baseweb="tag"]:has(span[title="exact"])         { background: #cce5ff !important; color: #004085 !important; }
        [data-baseweb="tag"]:has(span[title="fuzzy"])         { background: #e2d9f3 !important; color: #3d1a78 !important; }
        [data-baseweb="tag"]:has(span[title="position_only"]) { background: #fff3cd !important; color: #7d4e00 !important; }
        [data-baseweb="tag"]:has(span[title="manual"])        { background: #d1ecf1 !important; color: #0c5460 !important; }
        [data-baseweb="tag"]:has(span[title="unmatched"])     { background: #f8d7da !important; color: #721c24 !important; }
        /* ── Status filter tag colors ── */
        [data-baseweb="tag"]:has(span[title="approved"])      { background: #F0FFF4 !important; color: #166534 !important; border-color: #27C93F !important; }
        [data-baseweb="tag"]:has(span[title="re-pairing"])    { background: #FEF3C7 !important; color: #92400E !important; border-color: #F59E0B !important; }
        /* ── Phase 3 card white backgrounds (matches Phase 2) ── */
        .st-key-p3_action_card,
        .st-key-p3_rate_card,
        .st-key-p3_bytype_card {
            background: #FFFFFF !important;
        }
        /* ── Vertical centering: all columns inside match rows ── */
        [class*="st-key-row_"] > div:first-child [data-testid="stHorizontalBlock"] {
            align-items: center !important;
        }
        /* ── Approve buttons: same style as Export/Import CSV topbar buttons ── */
        [class*="st-key-p3_approve_"] button {
            width: 32px !important;
            height: 32px !important;
            min-width: 32px !important;
            max-width: 32px !important;
            padding: 0 !important;
            font-size: 13px !important;
            font-weight: 700 !important;
            border-radius: 0 !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            transition: background-color 0.15s, color 0.15s !important;
        }
        [class*="st-key-p3_approve_"] button:hover {
            background-color: #383838 !important;
            color: #FFFFFF !important;
            border-color: #383838 !important;
        }
        /* ── ↺ re-pair open button on approved/re-pairing rows ── */
        [class*="st-key-p3_repairopen_"] button {
            height: 28px !important;
            padding: 0 !important;
            line-height: 1 !important;
            font-size: 13px !important;
            font-weight: 700 !important;
            border-radius: 2px !important;
            border: 2px solid #383838 !important;
            background: #f4efea !important;
            color: #818181 !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            transition: background-color 0.15s, color 0.15s !important;
        }
        [class*="st-key-p3_repairopen_"] button p {
            line-height: 1 !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        [class*="st-key-p3_repairopen_"] button:hover {
            background-color: #383838 !important;
            color: #FFFFFF !important;
            border-color: #383838 !important;
        }

        /* ── Re-pairing row: amber, cursor pointer ── */
        [class*="st-key-row_repair_"] > div:first-child {
            cursor: pointer !important;
        }

        /* ── Drawer header wrap: relative so ✕ button floats inside ── */
        .st-key-p3_drawer_header_wrap {
            position: relative !important;
        }
        .st-key-p3_drawer_header_wrap > div:first-child {
            position: relative !important;
        }

        /* ── Drawer close button: absolute top-right inside header bar ── */
        .st-key-p3_drawer_close {
            position: absolute !important;
            top: 0px !important;
            right: 0px !important;
            z-index: 10 !important;
        }
        .st-key-p3_drawer_close button {
            background: transparent !important;
            border: none !important;
            color: #FFFFFF !important;
            font-size: 16px !important;
            font-weight: 700 !important;
            padding: 4px 10px !important;
            line-height: 1 !important;
        }

        /* ── Field pick card button — unselected ── */
        [class*="st-key-p3_pick_"] button {
            background: #FAFAFA !important;
            border: 2px solid #000000 !important;
            text-align: left !important;
            padding: 6px 10px !important;
            font-size: 12px !important;
            font-weight: 600 !important;
            height: auto !important;
            min-height: 36px !important;
            border-radius: 0 !important;
            margin: 2px 0 !important;
            color: #383838 !important;
        }
        [class*="st-key-p3_pick_"] button p,
        [class*="st-key-p3_picksel_"] button p {
            text-align: left !important;
            white-space: pre-wrap !important;
            margin: 0 !important;
            width: 100% !important;
        }
        /* override Streamlit's flex centering on the button's inner div */
        [class*="st-key-p3_pick_"] button > div,
        [class*="st-key-p3_picksel_"] button > div {
            justify-content: flex-start !important;
            align-items: flex-start !important;
            width: 100% !important;
        }
        /* italic meta line (3rd line) rendered as <em> — make it gray, 2pt smaller */
        [class*="st-key-p3_pick_"] button em,
        [class*="st-key-p3_picksel_"] button em {
            color: #818181 !important;
            font-style: italic !important;
            font-weight: 400 !important;
            font-size: 10px !important;
        }
        /* remove expander built-in indent so pick rows look like TOP SUGGESTIONS */
        [data-testid="stExpanderDetails"],
        [data-testid="stExpanderDetails"] > div,
        [data-testid="stExpanderDetails"] [data-testid="stVerticalBlock"] {
            padding-left: 0 !important;
            padding-right: 0 !important;
        }
        [class*="st-key-p3_pick_"] button:hover {
            border-color: #F59E0B !important;
            background: #FFFDF5 !important;
            color: #383838 !important;
        }
        [class*="st-key-p3_picksel_"] button:hover {
            border-color: #F59E0B !important;
            background: #FFF9E6 !important;
            color: #383838 !important;
        }

        /* ── Field pick card button — selected (amber) ── */
        [class*="st-key-p3_picksel_"] button {
            background: #FFF9E6 !important;
            border: 2px solid #F59E0B !important;
            text-align: left !important;
            padding: 6px 10px !important;
            font-size: 12px !important;
            font-weight: 600 !important;
            box-shadow: none !important;
            height: auto !important;
            min-height: 36px !important;
            border-radius: 0 !important;
            margin: 2px 0 !important;
            color: #383838 !important;
        }

        /* ── Skip drawer button: same 12px/700 weight as CSV toolbar ── */
        [class*="st-key-p3_drawer_skip_"] button p {
            font-size: 12px !important;
            font-weight: 700 !important;
        }

        /* ── Status badges ── */
        .p3-status-approved {
            display: block;
            text-align: center;
            background: #F0FFF4;
            border: 2px solid #27C93F;
            color: #166534;
            padding: 1px 8px;
            font-size: 11px;
            font-weight: 700;
        }
        .p3-status-repairing {
            display: block;
            text-align: center;
            background: #FEF3C7;
            border: 2px solid #F59E0B;
            color: #92400E;
            padding: 1px 8px;
            font-size: 11px;
            font-weight: 700;
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
            f'<div style="font-family:\'Aeonik Mono\', ui-monospace, monospace;font-size:13px;font-weight:600;'
            f'color:#383838;margin:0 0 12px 0;word-break:break-all;">{source_name}</div>'
            f'<div style="{_LABEL_STYLE}">Target CRF</div>'
            f'<div style="font-family:\'Aeonik Mono\', ui-monospace, monospace;font-size:13px;font-weight:600;'
            f'color:#383838;margin:0 0 0 0;word-break:break-all;">{target_name}</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div style="margin-top:32px;"></div>', unsafe_allow_html=True)
        if st.button("Run Matching", key="p3_run_btn", use_container_width=True):
            if not session:
                _no_session_error = True
            else:
                source_pdf_path = st.session_state.get("source_pdf_path")
                target_pdf_path = st.session_state.get("target_pdf_path")
                _loader_ph = st.empty()
                _loader_ph.html(loader_html("Running matching passes…"))

                _result: dict = {}

                def _work() -> None:
                    try:
                        source_dims = get_page_dims_from_pdf(source_pdf_path) if source_pdf_path else {}
                        target_dims = get_page_dims_from_pdf(target_pdf_path) if target_pdf_path else {}
                        _result["matches"] = match_annotations(annotations, fields, profile, source_dims, target_dims)
                    except Exception as exc:
                        _result["error"] = exc

                _t = threading.Thread(target=_work, daemon=True)
                _t.start()
                while _t.is_alive():
                    time.sleep(0.05)
                    _loader_ph.html(loader_html("Running matching passes…"))
                _t.join()
                clear_loader(_loader_ph)

                if "error" in _result:
                    st.error(f"Matching failed: {_result['error']}")
                else:
                    new_matches = _result["matches"]
                    session.save_matches(new_matches)
                    st.session_state["matches"] = new_matches
                    st.session_state["phases_complete"][3] = True
                    invalidate_phases([4])
                    st.session_state.pop("_p3_csv_ready", None)
                    session.log_action("phase3_match", {"count": len(new_matches)})
                    st.rerun()
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
            f'<div style="font-family:\'Aeonik Mono\', ui-monospace, monospace;font-size:24px;font-weight:700;'
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
            f'padding:3px 0;font-family:\'Aeonik Mono\', ui-monospace, monospace;font-size:12px;">'
            f'<span style="background:{bg};color:{text};padding:1px 7px;font-weight:600;'
            f'border:2px solid {border};">{mt}</span>'
            f'<strong style="color:#383838;">{count}</strong>'
            f'</div>'
        )

    with st.container(border=True, key="p3_bytype_card"):
        st.markdown(
            f'<div style="{_LABEL_STYLE}">By Type</div>'
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
        "font-family:'Aeonik Mono', ui-monospace, monospace;font-size:12px;font-weight:700;"
        "color:#383838;text-transform:uppercase;letter-spacing:0.5px;"
        "margin:0;padding:0;"
    )
    st.markdown(
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem;margin-bottom:6px;">'
        f'<p style="{_hdr}">Match Files</p>'
        f'<p style="{_hdr}">Exact Match Rate</p>'
        f'<p style="{_hdr}">By Type</p>'
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


# ---------------------------------------------------------------------------
# D. Filters
# ---------------------------------------------------------------------------

def _render_filters(matches: list[MatchRecord], session: Session) -> list[MatchRecord]:
    all_types = sorted({m.match_type for m in matches})
    all_statuses = sorted({m.status for m in matches})

    t_col, s_col, ba_col = st.columns([3, 3, 2])
    with t_col:
        sel_types = st.multiselect("Match Type \u25be", all_types, default=all_types,
                                   key="p3_filter_type", label_visibility="visible")
    with s_col:
        sel_statuses = st.multiselect("Status \u25be", all_statuses, default=all_statuses,
                                      key="p3_filter_status", label_visibility="visible")
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
    ]
    st.caption(f"Showing {len(filtered)} of {len(matches)} matches")
    return filtered


# ---------------------------------------------------------------------------
# E. Match rows
# ---------------------------------------------------------------------------

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
    drawer_id: str | None = st.session_state.get("_p3_drawer_id")

    # Decide layout: columns([2,1]) when drawer open, container() when closed
    if drawer_id and any(m.annotation_id == drawer_id for m in filtered):
        col_list, col_drawer = st.columns([2, 1], gap="medium")
    else:
        col_list = st.container()
        col_drawer = None

    action_taken = False

    with col_list:
        for m in filtered:
            annot = annot_by_id.get(m.annotation_id)
            field = field_by_id.get(m.field_id) if m.field_id else None
            annot_label = annot.content[:40] if annot else m.annotation_id[:12]

            # Container key drives CSS styling
            if m.status == "re-pairing":
                row_key = f"row_repair_{m.annotation_id}"
            elif m.match_type == "manual":
                row_key = f"row_manual_{m.annotation_id}"
            else:
                row_key = f"row_std_{m.annotation_id}"

            with st.container(key=row_key):
                col_type, col1, col2, col3, col_status, col_action = st.columns(
                    [0.8, 2.5, 2.5, 1, 1.2, 0.35]
                )

                with col_type:
                    render_match_type_badge(m.match_type)

                with col1:
                    st.markdown(
                        f'<span style="font-size:13px;font-weight:600;color:#383838">{annot_label}</span>',
                        unsafe_allow_html=True,
                    )

                with col2:
                    field_lbl = _field_display_label(field)
                    form_sub = "manually paired · rect recomputed" if m.match_type == "manual" else ""
                    st.markdown(
                        f'<span style="font-size:13px;font-weight:600;color:#383838">{field_lbl}</span>'
                        + (f'<br><span style="font-size:11px;color:#818181">{form_sub}</span>' if form_sub else ""),
                        unsafe_allow_html=True,
                    )

                with col3:
                    render_confidence_badge(m.confidence)

                with col_status:
                    # Status badge
                    if m.status == "approved":
                        st.markdown('<span class="p3-status-approved">approved</span>', unsafe_allow_html=True)
                    elif m.status == "re-pairing":
                        st.markdown('<span class="p3-status-repairing">re-pairing</span>', unsafe_allow_html=True)

                with col_action:
                    if m.status == "approved":
                        # ↺ button to re-open as re-pairing
                        if st.button("↺", key=f"p3_repairopen_{m.annotation_id}", help="Re-pair this match",
                                     use_container_width=True):
                            idx = match_index.get(m.annotation_id)
                            if idx is not None:
                                updated_matches[idx] = m.model_copy(update={"status": "re-pairing"})
                                st.session_state["_p3_drawer_id"] = m.annotation_id
                                action_taken = True
                    elif m.status == "re-pairing":
                        if st.button("↺", key=f"p3_repairopen_{m.annotation_id}", help="Open re-pair panel",
                                     use_container_width=True):
                            st.session_state["_p3_drawer_id"] = m.annotation_id
                            action_taken = True

    # Render drawer in right column
    if col_drawer is not None and drawer_id:
        drawer_annot_id = drawer_id
        drawer_m = next((m for m in all_matches if m.annotation_id == drawer_annot_id), None)
        drawer_annot = annot_by_id.get(drawer_annot_id)
        if drawer_m and drawer_annot:
            with col_drawer:
                new_matches = _render_drawer_panel(
                    drawer_m, drawer_annot, fields, field_by_id,
                    updated_matches, match_index, visit_boost, cross_form_threshold,
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
    """Left panel: annotation context card — simplified."""
    st.markdown(
        '<div style="font-family:\'Aeonik Mono\', ui-monospace, monospace;font-size:11px;font-weight:600;'
        'color:#818181;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">'
        'ANNOTATION</div>',
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.markdown(f"**{annot.content}**")
        st.caption(f"{annot.domain} in {annot.form_name}")
        st.caption(f"{annot.form_name} · p.{annot.page}")


def _render_field_row(
    annotation_id: str,
    score: float,
    f: FieldRecord,
    chosen_field_id: str | None,
    section: str = "",
) -> None:
    """Render one selectable field row as a full-width clickable button card."""
    is_selected = f.id == chosen_field_id
    label_txt, _fill, _brd, _txt = _FIELD_TYPE_BADGE.get(
        f.field_type, ("??", "#F4EFEA", "#D4CEC8", "#383838")
    )
    sec = f"_{section}" if section else ""
    key_prefix = "p3_picksel" if is_selected else "p3_pick"
    btn_key = f"{key_prefix}{sec}_{annotation_id}_{f.id}"

    check = "✓ " if is_selected else ""
    label = f"{check}[{label_txt}]\n**{f.label}**\n*{f.form_name} · p.{f.page}  → {score:.2f}*"

    if st.button(label, key=btn_key, use_container_width=True):
        sel = dict(st.session_state.get("_p3_drawer_selected", {}))
        if is_selected:
            sel.pop(annotation_id, None)
        else:
            sel[annotation_id] = f.id
        st.session_state["_p3_drawer_selected"] = sel
        st.rerun()


def _render_drawer_field_picker(
    m: MatchRecord,
    annot: AnnotationRecord,
    fields: list[FieldRecord],
    visit_boost: float,
    cross_form_threshold: float,
) -> str | None:
    """Drawer field picker: top suggestions + browse by form. Returns chosen field_id or None."""
    chosen_field_id: str | None = st.session_state.get("_p3_drawer_selected", {}).get(m.annotation_id)

    # Score all fields
    scored: list[tuple[float, FieldRecord]] = sorted(
        ((_compute_predicted_confidence(annot, f, visit_boost), f) for f in fields),
        key=lambda x: -x[0],
    )

    # Top suggestions
    st.markdown(
        '<div style="font-family:\'Aeonik Mono\', ui-monospace, monospace;font-size:11px;font-weight:600;'
        'color:#818181;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">'
        'TOP SUGGESTIONS</div>',
        unsafe_allow_html=True,
    )
    for score, f in scored[:3]:
        _render_field_row(m.annotation_id, score, f, chosen_field_id, section="top")

    st.markdown("---")

    # Browse by Form
    st.markdown(
        '<div style="font-family:\'Aeonik Mono\', ui-monospace, monospace;font-size:11px;font-weight:600;'
        'color:#818181;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">'
        'BROWSE BY FORM</div>',
        unsafe_allow_html=True,
    )

    all_forms = sorted({f.form_name for f in fields})
    default_form_idx = 0
    if annot.form_name in all_forms:
        default_form_idx = all_forms.index(annot.form_name)

    selected_form = st.selectbox(
        "Form",
        all_forms,
        index=default_form_idx,
        key=f"p3_drawer_form_{m.annotation_id}",
        label_visibility="collapsed",
    )

    form_fields = [f for f in fields if f.form_name == selected_form]
    pages = sorted({f.page for f in form_fields})

    # Search / filter by field label
    search_text = st.text_input(
        "",
        placeholder="Filter fields...",
        key=f"p3_drawer_search_{m.annotation_id}",
        label_visibility="collapsed",
    )

    for page_num in pages:
        page_fields = [f for f in form_fields if f.page == page_num]
        if search_text:
            page_fields = [f for f in page_fields if search_text.lower() in f.label.lower()]
        if not page_fields:
            continue
        with st.expander(f"Page {page_num}  ·  {len(page_fields)} fields", expanded=False):
            for f in page_fields:
                score = _compute_predicted_confidence(annot, f, visit_boost)
                _render_field_row(m.annotation_id, score, f, chosen_field_id, section=f"browse_p{page_num}")

    return chosen_field_id


def _render_drawer_panel(
    m: MatchRecord,
    annot: AnnotationRecord,
    fields: list[FieldRecord],
    field_by_id: dict[str, FieldRecord],
    updated_matches: list[MatchRecord],
    match_index: dict[str, int],
    visit_boost: float,
    cross_form_threshold: float = _DEFAULT_CROSS_FORM_THRESHOLD,
) -> list[MatchRecord] | None:
    """Render the right-column repair panel. Returns updated match list on Confirm, else None."""

    def _close_drawer() -> None:
        st.session_state["_p3_drawer_id"] = None
        st.session_state.pop(f"p3_drawer_search_{m.annotation_id}", None)
        sel = dict(st.session_state.get("_p3_drawer_selected", {}))
        sel.pop(m.annotation_id, None)
        st.session_state["_p3_drawer_selected"] = sel

    # ── Full-width header bar with ✕ absolutely positioned inside ──
    with st.container(key="p3_drawer_header_wrap"):
        st.markdown(
            f'<div style="background:#383838;padding:10px 14px;padding-right:48px;">'
            f'<div style="color:#FFFFFF;font-family:\'Aeonik Mono\', ui-monospace, monospace;font-size:14px;font-weight:700;">'
            f'Re-pair Field</div>'
            f'<div style="color:#818181;font-family:\'Aeonik Mono\', ui-monospace, monospace;font-size:11px;">'
            f'{_html.escape(annot.anchor_text or annot.content[:40])} · {_html.escape(m.match_type)} · conf {m.confidence:.2f}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if st.button("✕", key="p3_drawer_close"):
            _close_drawer()
            st.rerun()

    st.markdown("")

    # ── Annotation context (simplified) ──
    _render_annotation_detail(m, annot, field_by_id)

    # ── Skip / Confirm — immediately below annotation, before suggestions ──
    # chosen_field_id read from session state here so Confirm can be enabled/disabled
    chosen_field_id: str | None = st.session_state.get("_p3_drawer_selected", {}).get(m.annotation_id)
    skip_col, confirm_col = st.columns([1, 1])
    with skip_col:
        if st.button("Skip", key=f"p3_drawer_skip_{m.annotation_id}", use_container_width=True):
            _close_drawer()
            st.rerun()
    with confirm_col:
        if st.button(
            "Confirm",
            key=f"p3_confirm_repair_{m.annotation_id}",
            disabled=chosen_field_id is None,
            use_container_width=True,
        ):
            chosen_field = field_by_id.get(chosen_field_id) if chosen_field_id else None
            if chosen_field:
                new_rect = compute_target_rect(annot, chosen_field, list(field_by_id.values()))
                predicted = _compute_predicted_confidence(annot, chosen_field, visit_boost)
                new_list = apply_manual_match(
                    list(updated_matches), m.annotation_id, chosen_field.id, new_rect
                )
                idx = match_index.get(m.annotation_id)
                if idx is not None:
                    new_list[idx] = new_list[idx].model_copy(update={"confidence": predicted})
                _close_drawer()
                return new_list

    st.markdown("---")

    # ── Scored field list (top suggestions + browse by form) ──
    _render_drawer_field_picker(m, annot, fields, visit_boost, cross_form_threshold)

    return None
