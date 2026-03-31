"""Phase 4: Output generation and QC report UI."""
import streamlit as st

from src.writer import write_annotations
from src.models import MatchRecord


def _inject_page_css() -> None:
    st.markdown(
        """
        <style>
        /* Phase 4 toolbar buttons: 12px bold monospace */
        .st-key-p4_generate_btn button p,
        .st-key-p4_download_btn button p,
        .st-key-p4_download_btn a p {
            font-size: 12px !important;
            font-weight: 700 !important;
        }
        /* Make download button visually identical to generate button */
        .st-key-p4_download_btn a {
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
            width: 100% !important;
            background-color: transparent !important;
            border: 1px solid rgba(49, 51, 63, 0.2) !important;
            color: inherit !important;
            text-decoration: none !important;
            padding: 0.25rem 0.75rem !important;
            border-radius: 0.5rem !important;
        }
        .st-key-p4_download_btn a:hover {
            border-color: rgba(49, 51, 63, 0.5) !important;
            background-color: rgba(49, 51, 63, 0.05) !important;
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
    approved = [m for m in matches if m.status in ("approved", "modified")]
    if not approved:
        st.warning(
            "No approved matches found. Go to Phase 3 to approve matches before generating output."
        )
        return

    _render_topbar(matches)

    qc_report = st.session_state.get("qc_report")
    if qc_report:
        _render_qc_report(qc_report)


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
            with st.spinner("Writing annotations to target PDF…"):
                try:
                    qc_report = write_annotations(
                        target_pdf_path,
                        out_path,
                        matches,
                        annotations,
                        profile,
                    )
                    session.save_qc_report(qc_report)
                    st.session_state["output_pdf_path"] = out_path
                    st.session_state["qc_report"] = qc_report
                    st.session_state["phases_complete"][4] = True
                    session.log_action("phase4_write", qc_report)
                    st.rerun()
                except Exception as e:
                    st.error(f"Output generation failed: {e}")

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
# C. QC Report
# ---------------------------------------------------------------------------

def _render_qc_report(qc_report: dict) -> None:
    st.subheader("QC Report")
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Matches", qc_report.get("total_matches", 0))
    col2.metric("Written", qc_report.get("written", 0))
    col3.metric("Skipped", qc_report.get("skipped", 0))

    with st.expander("Counts by Match Type"):
        st.json(qc_report.get("counts_by_match_type", {}))

    unmatched_ids = qc_report.get("unmatched_annotation_ids", [])
    if unmatched_ids:
        with st.expander(f"Unmatched Annotations ({len(unmatched_ids)})"):
            st.caption("Go to Phase 3 to assign these manually.")
            for aid in unmatched_ids:
                st.write(f"• {aid}")

    rejected_ids = qc_report.get("rejected_annotation_ids", [])
    if rejected_ids:
        with st.expander(f"Rejected Annotations ({len(rejected_ids)})"):
            for aid in rejected_ids:
                st.write(f"• {aid}")
