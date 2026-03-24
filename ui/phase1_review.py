"""Phase 1: Annotation extraction and review UI."""
import uuid
from pathlib import Path

import streamlit as st

from src.csv_handler import export_annotations_csv, import_annotations_csv
from src.extractor import extract_annotations, _make_clean_page, _get_text_blocks
from src.models import AnnotationRecord
from ui.components import (
    get_pdf_page_count,
    invalidate_phases,
    render_annotation_card,
    render_page_navigator,
)


def render_phase1(profiles_dir: Path) -> None:
    """Render Phase 1: Extract Annotations page."""
    st.header("Phase 1: Extract Annotations")

    session = st.session_state.get("session")
    profile = st.session_state.get("profile")
    rule_engine = st.session_state.get("rule_engine")

    if profile is None or rule_engine is None:
        st.warning("No profile loaded. Go to Profile Editor to select a profile.")
        return

    _render_upload_section(session, profile, rule_engine)

    annotations = st.session_state.get("annotations", [])
    if not annotations:
        return

    _render_summary(annotations)
    _render_parsed_crf_text()
    _render_page_view(annotations, session)
    _render_add_annotation(annotations, session)
    _render_reclassify(annotations, session, rule_engine)
    _render_csv_section(annotations, session)


# ---------------------------------------------------------------------------
# A. Upload + Extract
# ---------------------------------------------------------------------------

def _render_upload_section(session, profile, rule_engine) -> None:
    st.subheader("Upload & Extract")
    uploaded = st.file_uploader("Source aCRF PDF", type=["pdf"], key="phase1_upload")
    if uploaded is not None:
        pdf_path = session.workspace / "source_acrf.pdf"
        pdf_path.write_bytes(uploaded.read())
        st.session_state["source_pdf_path"] = pdf_path

    source_pdf_path = st.session_state.get("source_pdf_path")

    if source_pdf_path and source_pdf_path.exists():
        st.info(f"PDF loaded: {source_pdf_path.name}")
        if st.button("Extract Annotations", type="primary"):
            with st.spinner("Extracting annotations…"):
                try:
                    records = extract_annotations(source_pdf_path, profile, rule_engine)
                    session.save_annotations(records)
                    st.session_state["annotations"] = records
                    st.session_state["phases_complete"][1] = True
                    invalidate_phases([3, 4])
                    session.log_action("phase1_extract", {"count": len(records)})
                    st.success(f"Extracted {len(records)} annotations.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")


# ---------------------------------------------------------------------------
# B. Summary
# ---------------------------------------------------------------------------

def _render_summary(annotations: list[AnnotationRecord]) -> None:
    with st.expander("Summary", expanded=False):
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Annotations", len(annotations))
        by_domain: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for a in annotations:
            by_domain[a.domain] = by_domain.get(a.domain, 0) + 1
            by_category[a.category] = by_category.get(a.category, 0) + 1
        with col2:
            st.write("**By Domain**")
            for d, cnt in sorted(by_domain.items()):
                st.write(f"{d}: {cnt}")
        with col3:
            st.write("**By Category**")
            for c, cnt in sorted(by_category.items()):
                st.write(f"{c}: {cnt}")


# ---------------------------------------------------------------------------
# C. Parsed CRF Text (annotation-free)
# ---------------------------------------------------------------------------

def _render_parsed_crf_text() -> None:
    """Render a collapsible expander per page showing clean CRF text blocks.

    Opens the source PDF, strips all annotations from a temporary copy of each
    page via _make_clean_page, then extracts text via _get_text_blocks.  This
    confirms to the user that SDTM annotation content is excluded from the text
    used for form-name, visit, and anchor-text extraction.
    """
    source_pdf_path = st.session_state.get("source_pdf_path")
    if not source_pdf_path or not source_pdf_path.exists():
        return

    with st.expander("Parsed CRF Text (annotation-free)", expanded=False):
        st.caption(
            "Text blocks extracted from each page after removing all SDTM annotations. "
            "These are the blocks used for form name, visit, and anchor text extraction."
        )
        try:
            import fitz
            doc = fitz.open(str(source_pdf_path))
            try:
                for page_index in range(doc.page_count):
                    page = doc[page_index]
                    page_num = page_index + 1
                    temp_doc, clean_page = _make_clean_page(page)
                    try:
                        blocks = _get_text_blocks(clean_page)
                    finally:
                        temp_doc.close()
                    with st.expander(f"Page {page_num}", expanded=False):
                        if blocks:
                            for block in blocks:
                                st.text(
                                    f"[y={block['rect'][1]:.0f} size={block['font_size']:.1f}] "
                                    f"{block['text']}"
                                )
                        else:
                            st.info("No text blocks found on this page.")
            finally:
                doc.close()
        except Exception as e:
            st.error(f"Could not extract CRF text: {e}")


# ---------------------------------------------------------------------------
# D. Page navigator + cards
# ---------------------------------------------------------------------------

def _render_page_view(annotations: list[AnnotationRecord], session) -> None:
    st.subheader("Review Annotations")
    source_pdf_path = st.session_state.get("source_pdf_path")
    total_pages = 1
    if source_pdf_path and source_pdf_path.exists():
        try:
            total_pages = get_pdf_page_count(source_pdf_path)
        except Exception:
            total_pages = max((a.page for a in annotations), default=1)

    selected_page = render_page_navigator(total_pages, key="phase1_nav")
    page_annots = [a for a in annotations if a.page == selected_page]

    if not page_annots:
        st.info(f"No annotations on page {selected_page}.")
        return

    updated_annots = list(annotations)
    changed = False
    indices_on_page = [i for i, a in enumerate(annotations) if a.page == selected_page]

    cards = []
    for local_idx, global_idx in enumerate(indices_on_page):
        result = render_annotation_card(annotations[global_idx], local_idx, "p1_annot")
        cards.append((global_idx, result))

    if st.button("Save Changes", key="p1_save"):
        for global_idx, result in cards:
            if result is None:
                updated_annots[global_idx] = None  # mark for deletion
                changed = True
            elif result != annotations[global_idx]:
                updated_annots[global_idx] = result
                changed = True
        updated_annots = [a for a in updated_annots if a is not None]
        if changed:
            session.save_annotations(updated_annots)
            st.session_state["annotations"] = updated_annots
            invalidate_phases([3, 4])
            session.log_action("phase1_edit", {"count": len(updated_annots)})
            st.rerun()


# ---------------------------------------------------------------------------
# D. Add new annotation
# ---------------------------------------------------------------------------

def _render_add_annotation(annotations: list[AnnotationRecord], session) -> None:
    with st.expander("Add New Annotation"):
        with st.form("p1_add_form"):
            col1, col2 = st.columns(2)
            with col1:
                content = st.text_area("Content")
                domain = st.text_input("Domain")
            with col2:
                category = st.selectbox(
                    "Category",
                    ["sdtm_mapping", "domain_label", "not_submitted", "note", "_exclude"]
                )
                form_name = st.text_input("Form Name")
                visit = st.text_input("Visit")
            submitted = st.form_submit_button("Add Annotation")
            if submitted and content.strip():
                new_record = AnnotationRecord(
                    id=str(uuid.uuid4()),
                    page=1,
                    content=content,
                    domain=domain,
                    category=category,
                    matched_rule="manual",
                    rect=[0.0, 0.0, 100.0, 20.0],
                    anchor_text="",
                    form_name=form_name,
                    visit=visit,
                )
                updated = annotations + [new_record]
                session.save_annotations(updated)
                st.session_state["annotations"] = updated
                invalidate_phases([3, 4])
                st.rerun()


# ---------------------------------------------------------------------------
# E. Re-classify
# ---------------------------------------------------------------------------

def _render_reclassify(annotations: list[AnnotationRecord], session, rule_engine) -> None:
    st.subheader("Re-classify All")
    if st.button("Re-classify with Current Profile"):
        updated = []
        for a in annotations:
            cat, rule = rule_engine.classify(a.content, a.domain)
            updated.append(a.model_copy(update={"category": cat, "matched_rule": rule}))
        session.save_annotations(updated)
        st.session_state["annotations"] = updated
        invalidate_phases([3, 4])
        session.log_action("phase1_reclassify", {"count": len(updated)})
        st.rerun()


# ---------------------------------------------------------------------------
# F. CSV Export/Import
# ---------------------------------------------------------------------------

def _render_csv_section(annotations: list[AnnotationRecord], session) -> None:
    st.subheader("CSV Export / Import")
    col1, col2 = st.columns(2)

    with col1:
        if st.button("Export CSV"):
            csv_path = session.workspace / "annotations_export.csv"
            export_annotations_csv(annotations, csv_path)
            csv_bytes = csv_path.read_bytes()
            st.download_button(
                "Download annotations.csv",
                data=csv_bytes,
                file_name="annotations.csv",
                mime="text/csv",
                key="p1_csv_dl",
            )

    with col2:
        csv_upload = st.file_uploader("Import CSV", type=["csv"], key="p1_csv_upload")
        if csv_upload is not None:
            csv_path = session.workspace / "annotations_import.csv"
            csv_path.write_bytes(csv_upload.read())
            updated, flagged = import_annotations_csv(csv_path, annotations)
            if flagged:
                st.warning(
                    f"{len(flagged)} existing annotations are missing from the CSV: "
                    f"{', '.join(flagged[:5])}{'...' if len(flagged) > 5 else ''}"
                )
                col_confirm, col_keep = st.columns(2)
                if col_confirm.button("Confirm (remove missing)", key="p1_csv_confirm"):
                    final = [r for r in updated if r.id not in flagged]
                    session.save_annotations(final)
                    st.session_state["annotations"] = final
                    invalidate_phases([3, 4])
                    st.rerun()
                if col_keep.button("Keep all", key="p1_csv_keep"):
                    session.save_annotations(updated)
                    st.session_state["annotations"] = updated
                    invalidate_phases([3, 4])
                    st.rerun()
            else:
                session.save_annotations(updated)
                st.session_state["annotations"] = updated
                invalidate_phases([3, 4])
                st.rerun()
