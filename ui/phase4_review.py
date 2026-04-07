"""Phase 4: Output generation UI."""
import threading
import time
from pathlib import Path

import fitz
import streamlit as st

from src.models import MatchRecord
from src.writer import write_annotations
from ui.components import render_page_navigator_windowed
from ui.loader import clear_loader, loader_html


def _inject_page_css() -> None:
    st.markdown(
        """
        <style>
        /* Phase 4 Generate / Download buttons — design system spec */
        .st-key-p4_generate_btn > button,
        .st-key-p4_download_btn > button,
        .st-key-p4_download_btn a {
            background-color: #6fc2ff !important;
            color: #383838 !important;
            border: 2px solid #383838 !important;
            border-radius: 2px !important;
            padding: 16.5px 22px !important;
            font-family: 'Aeonik Mono', ui-monospace, monospace !important;
            font-size: 16px !important;
            font-weight: 400 !important;
            text-transform: uppercase !important;
            box-shadow: none !important;
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
            width: 100% !important;
            text-decoration: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_phase4() -> None:
    """Render Phase 4: Output page."""
    _inject_page_css()

    phases = st.session_state.get("phases_complete", {})
    if not phases.get(3):
        st.warning("Phase 3 must be complete before generating output.")
        return

    matches = st.session_state.get("matches", [])
    approved = [m for m in matches if m.status == "approved"]
    if not approved:
        st.warning(
            "No approved matches found. Go to Phase 3 to approve matches before generating output."
        )
        return

    _render_topbar(matches)

    output_pdf_path = st.session_state.get("output_pdf_path")
    if output_pdf_path and output_pdf_path.exists():
        _render_pdf_preview(output_pdf_path)


# ---------------------------------------------------------------------------
# B. Topbar: Generate | Download PDF
# ---------------------------------------------------------------------------

def _render_topbar(matches: list[MatchRecord]) -> None:
    """Header + toolbar: Generate | Download PDF."""
    st.header("Phase 4: Generate Output aCRF")

    session = st.session_state.get("session")
    profile = st.session_state.get("profile")
    annotations = st.session_state.get("annotations", [])
    target_pdf_path = st.session_state.get("target_pdf_path")
    output_pdf_path = st.session_state.get("output_pdf_path")

    _, tb_generate, tb_download = st.columns([4, 1, 1], gap="small")

    with tb_generate:
        disabled = not session or target_pdf_path is None or not target_pdf_path.exists()
        if st.button("Generate", key="p4_generate_btn", use_container_width=True, disabled=disabled):
            out_path = session.workspace / "output_acrf.pdf"
            _loader_ph = st.empty()
            _loader_ph.html(loader_html("Writing annotations to target PDF…"))

            _result: dict = {}

            def _work() -> None:
                try:
                    _result["qc_report"] = write_annotations(
                        target_pdf_path,
                        out_path,
                        matches,
                        annotations,
                        profile,
                    )
                except Exception as exc:
                    _result["error"] = exc

            _t = threading.Thread(target=_work, daemon=True)
            _t.start()
            while _t.is_alive():
                time.sleep(0.05)
            _t.join()
            clear_loader(_loader_ph)

            if "error" in _result:
                st.error(f"Output generation failed: {_result['error']}")
            else:
                qc_report = _result["qc_report"]
                session.save_qc_report(qc_report)
                st.session_state["output_pdf_path"] = out_path
                st.session_state["qc_report"] = qc_report
                st.session_state["phases_complete"][4] = True
                session.log_action("phase4_write", qc_report)
                st.rerun()

    with tb_download:
        if output_pdf_path and output_pdf_path.exists():
            st.download_button(
                "Download PDF",
                data=output_pdf_path.read_bytes(),
                file_name="output_acrf.pdf",
                mime="application/pdf",
                key="p4_download_btn",
                use_container_width=True,
            )


# ---------------------------------------------------------------------------
# C. PDF Preview
# ---------------------------------------------------------------------------

def _render_pdf_preview(output_pdf_path: Path) -> None:
    """Render inline PDF viewer with height slider and windowed page navigator."""
    st.markdown("---")
    st.subheader("Preview")

    # Read page count once from the PDF
    with fitz.open(str(output_pdf_path)) as doc:
        page_count = doc.page_count

    # Initialize height default once
    if "p4_preview_height" not in st.session_state:
        st.session_state["p4_preview_height"] = 800

    # Height slider (full width above paginator)
    height = st.slider(
        "Viewer height (px)",
        min_value=400,
        max_value=1200,
        step=50,
        key="p4_preview_height",
    )

    # Windowed page navigator — same component as Phase 1 / Phase 2
    page_num = render_page_navigator_windowed(page_count, key="p4_preview_nav")

    page_idx = page_num - 1  # convert 1-indexed to 0-indexed
    with fitz.open(str(output_pdf_path)) as source_doc:
        single_page_doc = fitz.Document()
        single_page_doc.insert_pdf(source_doc, from_page=page_idx, to_page=page_idx)
        page_bytes = single_page_doc.tobytes()

    st.pdf(page_bytes, height=height)
