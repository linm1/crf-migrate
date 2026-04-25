"""CRF-Migrate Streamlit application entry point."""
import html
import re
from pathlib import Path

import streamlit as st

from src.profile_loader import list_profiles, load_profile
from src.rule_engine import RuleEngine
from src.session import Session
from ui.phase1_review import render_phase1
from ui.phase2_review import render_phase2
from ui.phase3_review import render_phase3, _inject_page_css as _inject_phase3_css
from ui.phase4_review import _inject_page_css as _inject_phase4_css
from ui.profile_editor import _inject_page_css as _inject_pe_css
from ui.phase4_review import render_phase4
from ui.profile_editor import render_profile_editor
from ui.style_helpers import build_centered_icon_button_css

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFILES_DIR = Path(__file__).parent / "profiles"
SESSION_BASE = Path(__file__).parent / "sessions"

# Keys cleared when switching sessions
CLEARABLE_STATE_KEYS = [
    "annotations", "fields", "matches", "qc_report",
    "source_pdf_path", "target_pdf_path", "output_pdf_path",
    "phases_complete", "current_page",
    "p1_page", "p2_page", "p3_page",
    "p3_filter_type", "p3_filter_status",
    "sidebar_workspace",
]

_WORKSPACE_ICON_BUTTON_CSS = build_centered_icon_button_css(
    key_prefixes=["ws_rename_btn_", "ws_del_btn_"],
    size_px=24,
    font_size_px=12,
    gap_px=6,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="CRF-Migrate",
    page_icon="assets/icon.png",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Design system CSS injection
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    /* Neo-Brutalist: sharp edges everywhere */
    *, *::before, *::after { border-radius: 0px !important; }

    /* Uppercase monospace headers + letter-spacing */
    h1, h2, h3, h4 {
        text-transform: uppercase;
        letter-spacing: 1px;
        font-family: ui-monospace, Consolas, 'Courier New', monospace !important;
    }

    /* Hard offset shadow + hover animation on buttons */
    .stButton > button {
        border: 2px solid #383838 !important;
        box-shadow: 3px 3px 0 #38383820;
        transition: transform 0.15s ease, box-shadow 0.15s ease;
        font-family: ui-monospace, Consolas, monospace !important;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }

    .stButton > button:hover {
        background: #383838 !important;
        color: #FFFFFF !important;
        transform: translate(-1px, -1px);
        font-family: ui-monospace, Consolas, monospace !important;
        text-transform: uppercase;
        box-shadow: 3px 3px 0 #38383840;
    }

    /* Hard shadow on bordered containers */
    div[data-testid="stContainer"][data-border="true"] {
        box-shadow: 4px 4px 0 rgba(0,0,0,0.08);
    }

    /* Duck-yellow hard shadow on focused inputs */
    input:focus, textarea:focus,
    [data-baseweb="input"]:focus-within,
    [data-baseweb="textarea"]:focus-within {
        border-color: #FFD700 !important;
        box-shadow: 4px 4px 0 #FFD700 !important;
    }

    /* terminal-red errors */
    div[data-testid="stNotification"][kind="error"],
    .stException {
        border-left: 3px solid #FF5F56 !important;
        box-shadow: 4px 4px 0 rgba(255,95,86,0.12);
    }

    /* terminal-green success */
    div[data-testid="stNotification"][kind="success"] {
        border-left: 3px solid #27C93F !important;
        box-shadow: 4px 4px 0 rgba(39,201,63,0.12);
    }

    /* Thin 1px borders on expanders and forms */
    details[data-testid="stExpander"] {
        border: 2px solid #383838 !important;
        box-shadow: 4px 4px 0 rgba(0,0,0,0.08);
    }

    /* Full-width canvas: remove Streamlit max-width constraint */
    .main .block-container {
        max-width: 100% !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
    }

    /* Always show scrollbar so content width stays constant across phases */
    section[data-testid="stMain"] {
        overflow-y: scroll !important;
    }

    /* Sidebar nav: bold text */
    section[data-testid="stSidebar"] .stButton > button p {
        font-weight: 700 !important;
    }
    /* Sidebar nav: active button = yellow fill */
    section[data-testid="stSidebar"] .stButton > button[kind="primary"],
    section[data-testid="stSidebar"] .stButton > button[kind="primary"]:focus,
    section[data-testid="stSidebar"] .stButton > button[kind="primary"]:active {
        background-color: #FFD700 !important;
        border-color: #FFD700 !important;
        color: #383838 !important;
        box-shadow: 2px 2px 0 #383838 !important;
    }
    /* Sidebar nav: hover on any sidebar button = yellow + #383838 shadow */
    section[data-testid="stSidebar"] .stButton > button:hover,
    section[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
        background-color: #383838 !important;
        border-color: #383838 !important;
        color: #FFFFFF !important;
        transform: none !important;
        box-shadow: 3px 3px 0 #38383840 !important;
    }

    /* Page background: Warm Parchment */
    .stApp, .main, [data-testid="stAppViewContainer"] {
        background-color: #f4efea !important;
    }

    /* Global density: zoom sidebar and main content independently.
       Applying zoom to children (not the container) keeps stMain at full
       viewport height, so the scrollable area fills the screen correctly. */
    section[data-testid="stSidebar"] {
        zoom: 0.67;
        min-width: 260px !important;
        max-width: 260px !important;
        width: 260px !important;
    }
    section[data-testid="stMain"] > div {
        zoom: 0.67;
    }

    /* Shadow-free: remove shadows on containers (not buttons) */
    div[data-testid="stContainer"][data-border="true"],
    div[data-testid="stVerticalBlock"] {
        box-shadow: none !important;
    }

    /* Session row icon buttons (✎ ✕) — Phase 3 style centered icon treatment */
    """
    + _WORKSPACE_ICON_BUTTON_CSS
    + """
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
    /* === Profile Editor: Tab bar pill redesign === */
    /* NOTE: uses data-testid selectors, stable in Streamlit 1.29+ */
    /* Exception to neo-brutalist sharp edges: tab pill container uses 8px radius per mockup design */
    div[data-testid="stTabs"] > div[role="tablist"] {
        background: #FFFFFF;
        border-radius: 8px !important;
        padding: 4px;
        gap: 2px;
        height: 40px;
        align-items: center;
        box-shadow: none;
        border: none;
    }
    .pe-sidebar-label {
        font-size: 10px !important;
        font-weight: 600 !important;
        color: #818181 !important;
        letter-spacing: 0.5px !important;
        text-transform: uppercase !important;
        margin: 0 0 4px 0 !important;
        font-family: Aeonik, ui-sans-serif, sans-serif !important;
    }
    /* Sidebar picker current-value display */
    .pe-sidebar-current {
        font-size: 13px !important;
        color: #e0e0e0 !important;
        margin: 0 0 6px 0 !important;
        font-family: Aeonik, ui-sans-serif, sans-serif !important;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    div[data-testid="stTabs"] button[role="tab"] {
        background: #FFFFFF !important;
        border: 2px solid #383838 !important;
        border-radius: 0px !important;
        height: 32px !important;
        padding: 0 10px !important;
        font-size: 12px !important;
        font-weight: 400 !important;
        color: #383838 !important;
        font-family: 'Aeonik Mono', ui-monospace, monospace !important;
        text-transform: none !important;
        letter-spacing: 0 !important;
        box-shadow: none !important;
        transition: none !important;
    }
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
        background: #FFD700 !important;
        border: 2px solid #383838 !important;
        color: #383838 !important;
        font-weight: 600 !important;
        box-shadow: 4px 4px 0 #383838 !important;
    }
    div[data-testid="stTabs"] button[role="tab"]::after,
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"]::after {
        display: none !important;
    }
    div[data-testid="stTabs"] > div[role="tabpanel"] > div[data-testid="stVerticalBlock"] {
        background: #FFFFFF;
        border-radius: 8px !important;
        box-shadow: 4px 4px 0 #38383818;
        padding: 16px 20px;
        margin-top: 4px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
    /* === Topbar CSV buttons — scoped to widget key wrappers only === */
    /* Does NOT affect the card file uploader in column 1               */

    /* Export CSV: stDownloadButton styled to match .stButton */
    .st-key-p1_export_btn button,
    .st-key-p2_export_btn button,
    .st-key-p3_export_btn button {
        border: 2px solid #383838 !important;
        box-shadow: 3px 3px 0 #38383820 !important;
        transition: transform 0.15s ease, box-shadow 0.15s ease !important;
        font-family: ui-monospace, Consolas, monospace !important;
        font-size: 14px !important;
        font-weight: 400 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.5px !important;
        background: transparent !important;
        color: #383838 !important;
        width: 100% !important;
        border-radius: 0px !important;
        padding: 0.25rem 0.75rem !important;
        height: 38px !important;
    }
    .st-key-p1_export_btn button:hover,
    .st-key-p2_export_btn button:hover,
    .st-key-p3_export_btn button:hover {
        background: #383838 !important;
        color: #FFFFFF !important;
        transform: translate(-1px, -1px) !important;
        box-shadow: 3px 3px 0 #38383840 !important;
    }
    /* Profile Editor topbar buttons — match other phases */
    .st-key-pe_dup button,
    .st-key-pe_imp_toggle button,
    .st-key-pe_save_top button {
        background: transparent !important;
        font-size: 14px !important;
        font-family: ui-monospace, Consolas, monospace !important;
        font-weight: 400 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.5px !important;
        height: 38px !important;
        border-radius: 0px !important;
    }
    .st-key-pe_dup button p,
    .st-key-pe_imp_toggle button p,
    .st-key-pe_save_top button p {
        font-family: ui-monospace, Consolas, monospace !important;
        font-weight: 400 !important;
        font-size: 14px !important;
        letter-spacing: 0.5px !important;
        text-transform: uppercase !important;
    }

    /* Normalize <p> inside stMarkdownContainer within export button — prevent bold bleed */
    .st-key-p1_export_btn button p,
    .st-key-p2_export_btn button p,
    .st-key-p3_export_btn button p {
        font-family: ui-monospace, Consolas, monospace !important;
        font-weight: 400 !important;
        font-size: 14px !important;
        letter-spacing: 0.5px !important;
        text-transform: uppercase !important;
    }

    /* Import CSV: collapse dropzone, style Browse button identically */
    .st-key-p1_csv_upload [data-testid="stFileUploaderDropzone"],
    .st-key-p2_csv_upload [data-testid="stFileUploaderDropzone"],
    .st-key-p3_csv_upload [data-testid="stFileUploaderDropzone"] {
        border: none !important;
        padding: 0 !important;
        background: transparent !important;
        box-shadow: none !important;
        min-height: unset !important;
        display: flex !important;
        align-items: center !important;
    }
    .st-key-p1_csv_upload [data-testid="stFileUploaderDropzoneInstructions"],
    .st-key-p2_csv_upload [data-testid="stFileUploaderDropzoneInstructions"],
    .st-key-p3_csv_upload [data-testid="stFileUploaderDropzoneInstructions"] {
        display: none !important;
    }
    /* Width: force dropzone, span wrapper, and button to fill column */
    .st-key-p1_csv_upload [data-testid="stFileUploader"],
    .st-key-p2_csv_upload [data-testid="stFileUploader"],
    .st-key-p3_csv_upload [data-testid="stFileUploader"],
    .st-key-p1_csv_upload [data-testid="stFileUploaderDropzone"],
    .st-key-p2_csv_upload [data-testid="stFileUploaderDropzone"],
    .st-key-p3_csv_upload [data-testid="stFileUploaderDropzone"],
    .st-key-p1_csv_upload [data-testid="stFileUploaderDropzone"] > span,
    .st-key-p2_csv_upload [data-testid="stFileUploaderDropzone"] > span,
    .st-key-p3_csv_upload [data-testid="stFileUploaderDropzone"] > span {
        width: 100% !important;
    }
    .st-key-p1_csv_upload [data-testid="stFileUploaderDropzone"] button,
    .st-key-p2_csv_upload [data-testid="stFileUploaderDropzone"] button,
    .st-key-p3_csv_upload [data-testid="stFileUploaderDropzone"] button {
        border: 2px solid #383838 !important;
        box-shadow: 3px 3px 0 #38383820 !important;
        transition: transform 0.15s ease, box-shadow 0.15s ease !important;
        font-weight: 400 !important;
        background: transparent !important;
        width: 100% !important;
        border-radius: 0px !important;
        padding: 0.25rem 0.75rem !important;
        height: 38px !important;
        /* font-size: 0 below hides the bare "Browse files" text node */
        font-size: 0 !important;
        color: transparent !important;
    }
    .st-key-p1_csv_upload [data-testid="stFileUploaderDropzone"] button:hover,
    .st-key-p2_csv_upload [data-testid="stFileUploaderDropzone"] button:hover,
    .st-key-p3_csv_upload [data-testid="stFileUploaderDropzone"] button:hover {
        background: #383838 !important;
        transform: translate(-1px, -1px) !important;
        box-shadow: 3px 3px 0 #38383840 !important;
    }
    /* Inject "IMPORT CSV" via ::after — button text is a bare text node,
       confirmed via DevTools: <button>Browse files</button> (no child elements).
       span::after doesn't work; button::after does. */
    .st-key-p1_csv_upload [data-testid="stFileUploaderDropzone"] button::after,
    .st-key-p2_csv_upload [data-testid="stFileUploaderDropzone"] button::after,
    .st-key-p3_csv_upload [data-testid="stFileUploaderDropzone"] button::after {
        content: "IMPORT CSV" !important;
        font-size: 14px !important;
        color: #383838 !important;
        font-family: ui-monospace, Consolas, monospace !important;
        text-transform: uppercase !important;
        letter-spacing: 0.5px !important;
    }
    .st-key-p1_csv_upload [data-testid="stFileUploaderDropzone"] button:hover::after,
    .st-key-p2_csv_upload [data-testid="stFileUploaderDropzone"] button:hover::after,
    .st-key-p3_csv_upload [data-testid="stFileUploaderDropzone"] button:hover::after {
        color: #FFFFFF !important;
    }
    /* Hide filename badge after upload — keep topbar clean */
    .st-key-p1_csv_upload [data-testid="stFileUploaderFile"],
    .st-key-p2_csv_upload [data-testid="stFileUploaderFile"],
    .st-key-p3_csv_upload [data-testid="stFileUploaderFile"] {
        display: none !important;
    }
    /* Hide label (label_visibility="collapsed" in Python) */
    .st-key-p1_csv_upload label,
    .st-key-p2_csv_upload label,
    .st-key-p3_csv_upload label {
        display: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

_inject_phase3_css()
_inject_phase4_css()
_inject_pe_css()

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------


_SAFE_SESSION_NAME = re.compile(r'^session_[\w\-]+$')


def _valid_session_name(name: str) -> bool:
    return bool(_SAFE_SESSION_NAME.match(name)) and len(name) <= 64


def _load_session_into_state(sess: Session) -> None:
    """Load all artifacts from sess into st.session_state."""
    st.session_state["session"] = sess
    ws = sess.workspace

    try:
        st.session_state["annotations"] = sess.load_annotations()
    except FileNotFoundError:
        st.session_state["annotations"] = []

    try:
        st.session_state["fields"] = sess.load_fields()
    except FileNotFoundError:
        st.session_state["fields"] = []

    try:
        st.session_state["matches"] = sess.load_matches()
    except FileNotFoundError:
        st.session_state["matches"] = []

    try:
        st.session_state["qc_report"] = sess.load_qc_report()
    except FileNotFoundError:
        st.session_state["qc_report"] = None

    for key, fname in [
        ("source_pdf_path", "source_acrf.pdf"),
        ("target_pdf_path", "target_crf.pdf"),
        ("output_pdf_path", "output_acrf.pdf"),
    ]:
        p = ws / fname
        st.session_state[key] = p if p.exists() else None

    st.session_state["phases_complete"] = {
        1: (ws / "annotations.json").exists(),
        2: (ws / "fields.json").exists(),
        3: (ws / "matches.json").exists(),
        4: (ws / "output_acrf.pdf").exists(),
    }


def _init_session_state() -> None:
    """Initialize all session state keys exactly once per browser session."""
    if "session" not in st.session_state:
        SESSION_BASE.mkdir(parents=True, exist_ok=True)
        sess = Session.latest(SESSION_BASE)
        if sess is None:
            sess = Session(SESSION_BASE)
        _load_session_into_state(sess)

    st.session_state.setdefault("current_page", "Profile Editor")

    if "profile" not in st.session_state:
        profiles = list_profiles(PROFILES_DIR)
        if profiles:
            default_name = profiles[0]
            st.session_state["profile_name"] = default_name
            try:
                profile_path = PROFILES_DIR / f"{default_name}.yaml"
                profile = load_profile(profile_path, PROFILES_DIR)
                st.session_state["profile"] = profile
                st.session_state["rule_engine"] = RuleEngine(profile)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------


def _render_sidebar() -> None:
    with st.sidebar:
        import base64
        with open("assets/icon.png", "rb") as _f:
            _icon_b64 = base64.b64encode(_f.read()).decode()
        st.markdown(
            f"""<div style="display:flex;align-items:center;gap:10px;padding:8px 0">
            <img src="data:image/png;base64,{_icon_b64}" width="36" style="display:block"/>
            <span style="font-size:1.4rem;font-weight:400;letter-spacing:0.04em;font-family:monospace">CRF-Migrate</span>
            </div>""",
            unsafe_allow_html=True,
        )
        st.divider()

        phases = st.session_state.get("phases_complete", {})

        pages = [
            ("Profile Editor", True),
            ("Phase 1: Extract Annotations", True),
            ("Phase 2: Extract Fields", phases.get(1, False)),
            ("Phase 3: Match", phases.get(1, False) and phases.get(2, False)),
            ("Phase 4: Output", phases.get(3, False)),
        ]

        current = st.session_state.get("current_page", "Profile Editor")
        for page_name, enabled in pages:
            if st.button(
                page_name,
                key=f"nav_{page_name}",
                disabled=not enabled,
                use_container_width=True,
                type="primary" if page_name == current else "secondary",
            ):
                st.session_state["current_page"] = page_name
                st.rerun()

        st.divider()
        # Profile quick-switch in sidebar
        profiles = list_profiles(PROFILES_DIR)
        if profiles:
            current_profile = st.session_state.get("profile_name", profiles[0])
            if current_profile not in profiles:
                current_profile = profiles[0]
                st.session_state["profile_name"] = profiles[0]
            st.markdown(
                '<p class="pe-sidebar-label">ACTIVE PROFILE</p>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p class="pe-sidebar-current">{html.escape(current_profile)}</p>',
                unsafe_allow_html=True,
            )
            with st.popover("Change", use_container_width=True):
                for p in profiles:
                    if st.button(
                        p,
                        key=f"prof_pick_{p}",
                        use_container_width=True,
                        type="primary" if p == current_profile else "secondary",
                    ):
                        if p != current_profile:
                            try:
                                profile_path = PROFILES_DIR / f"{p}.yaml"
                                profile = load_profile(profile_path, PROFILES_DIR)
                                st.session_state["profile"] = profile
                                st.session_state["profile_name"] = p
                                st.session_state["rule_engine"] = RuleEngine(profile)
                                st.session_state.pop("draft_profile_data", None)
                            except Exception as e:
                                st.error(f"Failed to load profile: {e}")
                        st.rerun()

        st.divider()
        st.markdown(
            '<p class="pe-sidebar-label">WORKSPACE</p>',
            unsafe_allow_html=True,
        )
        all_sessions = Session.list_sessions(SESSION_BASE)
        current_sess = st.session_state.get("session")
        current_name = current_sess.workspace.name if current_sess else None

        if all_sessions:
            _raw_name = current_name or all_sessions[0]
            display_name = _raw_name.removeprefix("session_")
            st.markdown(
                f'<p class="pe-sidebar-current">{html.escape(display_name)}</p>',
                unsafe_allow_html=True,
            )
            with st.popover("Change", use_container_width=True):
                if st.button("+ New Session", key="ws_new_session", use_container_width=True, type="primary"):
                    for k in CLEARABLE_STATE_KEYS:
                        st.session_state.pop(k, None)
                    SESSION_BASE.mkdir(parents=True, exist_ok=True)
                    new_sess = Session(SESSION_BASE)
                    _load_session_into_state(new_sess)
                    st.session_state["current_page"] = "Profile Editor"
                    st.rerun()
                st.divider()
                for ws in all_sessions:
                    rename_key = f"ws_rename_{ws}"
                    del_key = f"ws_delete_confirm_{ws}"

                    if st.session_state.get(rename_key):
                        # Rename mode — show only the human-editable suffix; session_ is auto-prepended on save
                        _PREFIX = "session_"
                        suffix_default = ws[len(_PREFIX):] if ws.startswith(_PREFIX) else ws
                        new_suffix = st.text_input(
                            "New name", value=suffix_default,
                            key=f"ws_rename_input_{ws}",
                            label_visibility="collapsed",
                            placeholder="e.g. trial_v2",
                        )
                        c1, c2 = st.columns(2)
                        if c1.button("Save", key=f"ws_rename_save_{ws}", use_container_width=True, type="primary"):
                            new_name = _PREFIX + new_suffix.strip()
                            if _valid_session_name(new_name) and (new_name == ws or new_name not in all_sessions):
                                old_path = SESSION_BASE / ws
                                new_path = Session.rename(old_path, new_name)
                                if ws == current_name:
                                    _load_session_into_state(Session.open(new_path))
                                st.session_state.pop(rename_key, None)
                                st.rerun()
                            else:
                                st.error("Invalid or duplicate name.")
                        if c2.button("Cancel", key=f"ws_rename_cancel_{ws}", use_container_width=True):
                            st.session_state.pop(rename_key, None)
                            st.rerun()

                    elif st.session_state.get(del_key):
                        # Delete confirm mode
                        st.warning(f"Delete **{ws.removeprefix('session_')}**?", icon="⚠️")
                        c1, c2 = st.columns(2)
                        if c1.button("Delete", key=f"ws_del_confirm_{ws}", use_container_width=True, type="primary"):
                            workspace_path = SESSION_BASE / ws
                            remaining = [s for s in all_sessions if s != ws]
                            if ws == current_name:
                                for k in CLEARABLE_STATE_KEYS:
                                    st.session_state.pop(k, None)
                                if remaining:
                                    _load_session_into_state(Session.open(SESSION_BASE / remaining[0]))
                                else:
                                    new_sess = Session(SESSION_BASE)
                                    _load_session_into_state(new_sess)
                                st.session_state.setdefault("current_page", "Profile Editor")
                            Session.delete(workspace_path)
                            st.session_state.pop(del_key, None)
                            st.rerun()
                        if c2.button("Cancel", key=f"ws_del_cancel_{ws}", use_container_width=True):
                            st.session_state.pop(del_key, None)
                            st.rerun()

                    else:
                        # Normal mode — session name + edit/delete icons
                        c1, c2, c3 = st.columns([6, 0.5, 0.5])
                        if c1.button(ws.removeprefix("session_"), key=f"ws_pick_{ws}", use_container_width=True,
                                     type="primary" if ws == current_name else "secondary"):
                            if ws != current_name:
                                for k in CLEARABLE_STATE_KEYS:
                                    st.session_state.pop(k, None)
                                _load_session_into_state(Session.open(SESSION_BASE / ws))
                                st.session_state.setdefault("current_page", "Profile Editor")
                            st.rerun()
                        if c2.button("✎", key=f"ws_rename_btn_{ws}", use_container_width=True):
                            st.session_state[rename_key] = True
                            st.rerun()
                        if c3.button("✕", key=f"ws_del_btn_{ws}", use_container_width=True):
                            st.session_state[del_key] = True
                            st.rerun()


# ---------------------------------------------------------------------------
# Main routing
# ---------------------------------------------------------------------------


def main() -> None:
    _init_session_state()
    _render_sidebar()

    current_page = st.session_state.get("current_page", "Profile Editor")
    phases = st.session_state.get("phases_complete", {})

    match current_page:
        case "Profile Editor":
            render_profile_editor(PROFILES_DIR)
        case "Phase 1: Extract Annotations":
            render_phase1(PROFILES_DIR)
        case "Phase 2: Extract Fields":
            render_phase2(PROFILES_DIR)
        case "Phase 3: Match":
            render_phase3()
        case "Phase 4: Output":
            render_phase4()
        case _:
            render_profile_editor(PROFILES_DIR)


main()
