"""Phase 4: Output generation and QC report UI."""
import streamlit as st

from src.writer import write_annotations
from src.models import MatchRecord


def render_phase4() -> None:
    """Render Phase 4: Output page."""
    st.header("Phase 4: Generate Output aCRF")

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

    _render_generate_section(matches)

    output_pdf_path = st.session_state.get("output_pdf_path")
    if output_pdf_path and output_pdf_path.exists():
        _render_download(output_pdf_path)
        qc_report = st.session_state.get("qc_report")
        if qc_report:
            _render_qc_report(qc_report)


# ---------------------------------------------------------------------------
# B. Generate
# ---------------------------------------------------------------------------

def _render_generate_section(matches: list[MatchRecord]) -> None:
    st.subheader("Generate Output")
    session = st.session_state.get("session")
    profile = st.session_state.get("profile")
    annotations = st.session_state.get("annotations", [])
    target_pdf_path = st.session_state.get("target_pdf_path")

    if target_pdf_path is None or not target_pdf_path.exists():
        st.error("Target PDF not found. Please complete Phase 2 first.")
        return

    if st.button("Generate Output aCRF", type="primary"):
        output_pdf_path = session.workspace / "output_acrf.pdf"
        with st.spinner("Writing annotations to target PDF…"):
            try:
                qc_report = write_annotations(
                    target_pdf_path,
                    output_pdf_path,
                    matches,
                    annotations,
                    profile,
                )
                session.save_qc_report(qc_report)
                st.session_state["output_pdf_path"] = output_pdf_path
                st.session_state["qc_report"] = qc_report
                st.session_state["phases_complete"][4] = True
                session.log_action("phase4_write", qc_report)
                st.success(
                    f"Output generated: {qc_report['written']} annotations written."
                )
                st.rerun()
            except Exception as e:
                st.error(f"Output generation failed: {e}")


# ---------------------------------------------------------------------------
# C. Download
# ---------------------------------------------------------------------------

def _render_download(output_pdf_path) -> None:
    st.subheader("Download")
    st.download_button(
        "Download Output aCRF PDF",
        data=output_pdf_path.read_bytes(),
        file_name="output_acrf.pdf",
        mime="application/pdf",
    )


# ---------------------------------------------------------------------------
# D. QC Report
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
