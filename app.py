"""CRF-Migrate Streamlit application entry point."""
from pathlib import Path

import streamlit as st

from src.profile_loader import list_profiles, load_profile
from src.rule_engine import RuleEngine
from src.session import Session
from ui.components import render_phase_status_bar
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
    layout="wide",
    initial_sidebar_state="expanded",
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
        st.title("CRF-Migrate")
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

    phases = st.session_state.get("phases_complete", {})
    render_phase_status_bar(phases)
    st.divider()

    current_page = st.session_state.get("current_page", "Profile Editor")

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
