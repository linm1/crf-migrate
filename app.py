"""CRF-Migrate Streamlit application entry point."""
from pathlib import Path

import streamlit as st

from src.profile_loader import list_profiles, load_profile
from src.rule_engine import RuleEngine
from src.session import Session
from ui.phase1_review import render_phase1
from ui.phase2_review import render_phase2
from ui.phase3_review import render_phase3
from ui.phase4_review import render_phase4
from ui.profile_editor import render_profile_editor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFILES_DIR = Path(__file__).parent / "profiles"
SESSION_BASE = Path(__file__).parent / "sessions"

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

    /* Shadow-free: remove shadows on containers (not buttons) */
    div[data-testid="stContainer"][data-border="true"],
    div[data-testid="stVerticalBlock"] {
        box-shadow: none !important;
    }
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

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------


def _init_session_state() -> None:
    """Initialize all session state keys exactly once per browser session."""
    if "session" not in st.session_state:
        SESSION_BASE.mkdir(parents=True, exist_ok=True)
        sess = Session(SESSION_BASE)
        st.session_state["session"] = sess

        # Restore workspace artifacts if present
        ws = sess.workspace
        try:
            st.session_state["annotations"] = sess.load_annotations()
        except FileNotFoundError:
            st.session_state.setdefault("annotations", [])

        try:
            st.session_state["fields"] = sess.load_fields()
        except FileNotFoundError:
            st.session_state.setdefault("fields", [])

        try:
            st.session_state["matches"] = sess.load_matches()
        except FileNotFoundError:
            st.session_state.setdefault("matches", [])

        try:
            st.session_state["qc_report"] = sess.load_qc_report()
        except FileNotFoundError:
            st.session_state.setdefault("qc_report", None)

        # Restore PDF paths
        source_pdf = ws / "source_acrf.pdf"
        if source_pdf.exists():
            st.session_state["source_pdf_path"] = source_pdf
        target_pdf = ws / "target_crf.pdf"
        if target_pdf.exists():
            st.session_state["target_pdf_path"] = target_pdf
        output_pdf = ws / "output_acrf.pdf"
        if output_pdf.exists():
            st.session_state["output_pdf_path"] = output_pdf

    # Phase completion state
    st.session_state.setdefault(
        "phases_complete", {1: False, 2: False, 3: False, 4: False}
    )

    # Default page
    st.session_state.setdefault("current_page", "Profile Editor")

    # Load default profile if none loaded
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
            st.markdown(
                '<p class="pe-sidebar-label">ACTIVE PROFILE</p>',
                unsafe_allow_html=True,
            )
            selected = st.selectbox(
                "Profile",
                profiles,
                index=profiles.index(current_profile),
                key="sidebar_profile",
            )
            if selected != st.session_state.get("profile_name"):
                try:
                    profile_path = PROFILES_DIR / f"{selected}.yaml"
                    profile = load_profile(profile_path, PROFILES_DIR)
                    st.session_state["profile"] = profile
                    st.session_state["profile_name"] = selected
                    st.session_state["rule_engine"] = RuleEngine(profile)
                    st.session_state.pop("draft_profile_data", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to load profile: {e}")

        st.divider()
        ws = st.session_state.get("session")
        if ws:
            st.caption(f"Workspace: {ws.workspace.name}")


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
