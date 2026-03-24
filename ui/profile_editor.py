"""Profile editor UI for CRF-Migrate."""
from pathlib import Path

import yaml
import streamlit as st

from src.profile_loader import list_profiles, load_profile
from src.profile_models import Profile
from src.rule_engine import RuleEngine


def _inject_page_css() -> None:
    """Inject Profile Editor page-scoped CSS classes."""
    st.markdown(
        """
        <style>
        .pe-section-title { font-family: Inter, sans-serif; font-size: 15px;
            font-weight: 700; color: #1E293B; margin: 0 0 4px 0; }
        .pe-help-text { font-size: 12px; color: #8A847F;
            font-family: Inter, sans-serif; margin: -4px 0 8px 0; }
        .pe-chip { display: inline-flex; align-items: center;
            background: rgba(0,122,255,0.2); height: 28px; padding: 0 12px;
            font-size: 13px; color: #004085; font-family: Inter, sans-serif; margin: 2px; }
        .pe-cat-badge { display: inline-block; background: rgba(255,215,0,0.2);
            padding: 2px 8px; font-size: 11px; font-weight: 600;
            color: #383838; font-family: Inter, monospace; }
        .pe-table-header { font-family: Inter, sans-serif; font-size: 11px;
            font-weight: 700; color: #8A847F; text-transform: uppercase;
            letter-spacing: 0.5px; padding: 4px 0 8px 0; }
        .pe-btn-dark > div[data-testid="stButton"] > button {
            background: #383838 !important; color: #FFFFFF !important;
            border: 1px solid #383838 !important;
            box-shadow: 4px 4px 0 rgba(0,0,0,0.13) !important;
            font-weight: 600 !important; }
        .pe-btn-dark > div[data-testid="stButton"] > button:hover {
            box-shadow: 6px 6px 0 rgba(0,0,0,0.18) !important; }
        .pe-btn-danger > div[data-testid="stButton"] > button {
            background: #dc3545 !important; color: #FFFFFF !important;
            border: 1px solid #dc3545 !important; }
        .pe-slider-badge { display: inline-block; background: #383838;
            color: #FFFFFF; font-size: 11px;
            font-family: ui-monospace, Consolas, monospace;
            padding: 2px 8px; min-width: 40px; text-align: center; }
        .pe-swatch { display: inline-block; width: 20px; height: 20px;
            border: 1px solid #383838; vertical-align: middle; margin-left: 8px; }
        .pe-yaml-terminal { background: #1E293B; color: #94A3B8;
            font-family: ui-monospace, Consolas, 'Courier New', monospace;
            font-size: 12px; padding: 16px; min-height: 300px;
            overflow-x: auto; white-space: pre-wrap; }
        .pe-yaml-filename { font-size: 10px; color: #64748B;
            font-family: ui-monospace, monospace; text-align: right; padding: 4px 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_profile_editor(profiles_dir: Path) -> None:
    """Render the full profile editor page."""
    _inject_page_css()
    st.header("Profile Editor")

    profile_names = list_profiles(profiles_dir)
    if not profile_names:
        st.warning("No profiles found in profiles/ directory.")
        return

    # Profile selector + actions
    col1, col2, col3 = st.columns([3, 1, 2])
    with col1:
        current_name = st.session_state.get("profile_name", profile_names[0])
        if current_name not in profile_names:
            current_name = profile_names[0]
        selected = st.selectbox(
            "Active Profile", profile_names,
            index=profile_names.index(current_name),
            key="profile_selector",
        )
        if selected != st.session_state.get("profile_name"):
            _load_profile_into_state(profiles_dir, selected)
            st.rerun()

    with col2:
        if st.button("Duplicate"):
            _duplicate_profile(profiles_dir, selected)
            st.rerun()

    with col3:
        uploaded_yaml = st.file_uploader(
            "Import YAML", type=["yaml", "yml"], key="profile_import"
        )
        if uploaded_yaml is not None:
            _import_yaml(profiles_dir, uploaded_yaml)
            st.rerun()

    # Ensure draft data is initialized
    if "draft_profile_data" not in st.session_state:
        _reset_draft(profiles_dir, selected)

    draft = st.session_state["draft_profile_data"]

    # Tabs
    tabs = st.tabs([
        "Domain Codes", "Classification Rules", "Visit Rules",
        "Form Name", "Matching", "Style", "YAML"
    ])

    with tabs[0]:
        _render_domain_codes_tab(draft)

    with tabs[1]:
        _render_classification_rules_tab(draft)

    with tabs[2]:
        _render_visit_rules_tab(draft)

    with tabs[3]:
        _render_form_name_tab(draft)

    with tabs[4]:
        _render_matching_tab(draft)

    with tabs[5]:
        _render_style_tab(draft)

    with tabs[6]:
        _render_yaml_tab(draft, profiles_dir, selected)

    st.divider()
    _render_rule_tester()

    st.divider()
    if st.button("Save Profile", type="primary"):
        _save_profile(profiles_dir, selected, draft)


# ---------------------------------------------------------------------------
# Draft management
# ---------------------------------------------------------------------------

def _load_profile_into_state(profiles_dir: Path, name: str) -> None:
    profile_path = profiles_dir / f"{name}.yaml"
    profile = load_profile(profile_path, profiles_dir)
    st.session_state["profile"] = profile
    st.session_state["profile_name"] = name
    st.session_state["rule_engine"] = RuleEngine(profile)
    st.session_state["draft_profile_data"] = profile.model_dump()


def _reset_draft(profiles_dir: Path, name: str) -> None:
    profile_path = profiles_dir / f"{name}.yaml"
    try:
        profile = load_profile(profile_path, profiles_dir)
        st.session_state["draft_profile_data"] = profile.model_dump()
    except Exception:
        st.session_state["draft_profile_data"] = {}


def _duplicate_profile(profiles_dir: Path, source_name: str) -> None:
    source_path = profiles_dir / f"{source_name}.yaml"
    new_name = f"{source_name}_copy"
    dest_path = profiles_dir / f"{new_name}.yaml"
    i = 2
    while dest_path.exists():
        new_name = f"{source_name}_copy{i}"
        dest_path = profiles_dir / f"{new_name}.yaml"
        i += 1
    with source_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw.setdefault("meta", {})["name"] = new_name
    dest_path.write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")
    st.session_state["profile_name"] = new_name
    st.session_state.pop("draft_profile_data", None)
    st.success(f"Duplicated to '{new_name}'")


def _import_yaml(profiles_dir: Path, uploaded) -> None:
    try:
        raw = yaml.safe_load(uploaded.read()) or {}
        name = raw.get("meta", {}).get("name", uploaded.name.replace(".yaml", "").replace(".yml", ""))
        dest = profiles_dir / f"{name}.yaml"
        dest.write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")
        st.session_state["profile_name"] = name
        st.session_state.pop("draft_profile_data", None)
        st.success(f"Imported profile '{name}'")
    except Exception as e:
        st.error(f"Import failed: {e}")


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------

def _render_domain_codes_tab(draft: dict) -> None:
    codes = draft.get("domain_codes", [])
    st.subheader("Domain Codes")
    cols_per_row = 6
    rows = [codes[i:i + cols_per_row] for i in range(0, len(codes), cols_per_row)]
    to_delete = None
    for row_index, row in enumerate(rows):
        row_cols = st.columns(cols_per_row)
        for j, code in enumerate(row):
            global_idx = row_index * cols_per_row + j
            with row_cols[j]:
                color = "#cce5ff"
                st.markdown(
                    f'<span style="background:{color};padding:2px 6px;border-radius:4px">{code}</span>',
                    unsafe_allow_html=True,
                )
                if st.button("✕", key=f"del_code_{global_idx}"):
                    to_delete = global_idx
    if to_delete is not None:
        draft["domain_codes"] = [c for i, c in enumerate(codes) if i != to_delete]
    col_input, col_add = st.columns([3, 1])
    with col_input:
        new_code = st.text_input("New domain code", key="new_domain_code")
    with col_add:
        st.write("")
        st.write("")
        if st.button("Add Code") and new_code.strip():
            if new_code.strip().upper() not in draft["domain_codes"]:
                draft["domain_codes"] = draft["domain_codes"] + [new_code.strip().upper()]


def _render_classification_rules_tab(draft: dict) -> None:
    rules = draft.get("classification_rules", [])
    st.subheader("Classification Rules")
    to_delete = None
    to_move_up = None
    for i, rule in enumerate(rules):
        cond = rule.get("conditions", {})
        with st.expander(f"Rule {i + 1}: {rule.get('category', '')} — {_cond_summary(cond)}"):
            col1, col2 = st.columns(2)
            with col1:
                for field in ["contains", "starts_with", "regex", "subject_is", "domain_in"]:
                    val = st.text_input(
                        field, value=cond.get(field) or "",
                        key=f"rule_{i}_{field}"
                    )
                    cond[field] = val if val.strip() else None
            with col2:
                for field in ["max_length", "min_length"]:
                    raw_val = cond.get(field)
                    val_str = str(raw_val) if raw_val is not None else ""
                    entered = st.text_input(
                        field, value=val_str, key=f"rule_{i}_{field}"
                    )
                    if entered.strip():
                        try:
                            cond[field] = int(entered)
                        except ValueError:
                            cond[field] = None
                    else:
                        cond[field] = None
                multi_line = st.checkbox(
                    "multi_line", value=bool(cond.get("multi_line")),
                    key=f"rule_{i}_multi_line"
                )
                cond["multi_line"] = multi_line or None
                fallback = st.checkbox(
                    "fallback", value=bool(cond.get("fallback")),
                    key=f"rule_{i}_fallback"
                )
                cond["fallback"] = fallback or None

            categories = ["sdtm_mapping", "domain_label", "not_submitted", "note", "_exclude"]
            cat = rule.get("category", "sdtm_mapping")
            cat_idx = categories.index(cat) if cat in categories else 0
            new_cat = st.selectbox("Category", categories, index=cat_idx, key=f"rule_{i}_cat")
            rule["category"] = new_cat
            rule["conditions"] = cond

            btn_col1, btn_col2, _ = st.columns([1, 1, 4])
            with btn_col1:
                if i > 0 and st.button("Move Up", key=f"rule_{i}_up"):
                    to_move_up = i
            with btn_col2:
                if st.button("Delete", key=f"rule_{i}_del"):
                    to_delete = i

    if to_delete is not None:
        draft["classification_rules"] = [r for j, r in enumerate(rules) if j != to_delete]
    elif to_move_up is not None:
        rules[to_move_up - 1], rules[to_move_up] = rules[to_move_up], rules[to_move_up - 1]
        draft["classification_rules"] = rules

    if st.button("Add Rule"):
        draft["classification_rules"] = rules + [
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"}
        ]


def _cond_summary(cond: dict) -> str:
    parts = []
    for k, v in cond.items():
        if v is not None and v is not False:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts[:3]) or "(empty)"


def _render_visit_rules_tab(draft: dict) -> None:
    rules = draft.get("visit_rules", [])
    st.subheader("Visit Rules")
    to_delete = None
    for i, rule in enumerate(rules):
        col1, col2, col3 = st.columns([3, 3, 1])
        with col1:
            new_regex = st.text_input("Regex", value=rule.get("regex", ""), key=f"visit_regex_{i}")
            rule["regex"] = new_regex
        with col2:
            new_val = st.text_input("Value", value=rule.get("value", ""), key=f"visit_val_{i}")
            rule["value"] = new_val
        with col3:
            st.write("")
            if st.button("✕", key=f"visit_del_{i}"):
                to_delete = i
    if to_delete is not None:
        draft["visit_rules"] = [r for j, r in enumerate(rules) if j != to_delete]
    if st.button("Add Visit Rule"):
        draft["visit_rules"] = rules + [{"regex": "", "value": ""}]


def _render_form_name_tab(draft: dict) -> None:
    config = draft.get("form_name_rules", {})
    st.subheader("Form Name Rules")
    strategies = ["largest_bold_text"]
    strat = config.get("strategy", "largest_bold_text")
    strat_idx = strategies.index(strat) if strat in strategies else 0
    config["strategy"] = st.selectbox("Strategy", strategies, index=strat_idx, key="fnr_strategy")
    config["min_font_size"] = st.number_input(
        "Min Font Size", value=float(config.get("min_font_size", 12.0)),
        min_value=1.0, step=0.5, key="fnr_min_font"
    )

    # top_region_fraction: optional float, enabled via checkbox
    trf_enabled = config.get("top_region_fraction") is not None
    use_trf = st.checkbox(
        "Restrict to top region of page", value=trf_enabled, key="fnr_trf_enabled",
        help="Only consider text blocks in the top N% of page height when selecting the form name."
    )
    if use_trf:
        config["top_region_fraction"] = st.number_input(
            "Top Region Fraction", value=float(config.get("top_region_fraction") or 0.25),
            min_value=0.05, max_value=1.0, step=0.05, key="fnr_trf_value",
            help="Fraction of page height (0.05–1.0). E.g. 0.25 = top quarter of page."
        )
    else:
        config["top_region_fraction"] = None

    # label_prefix: optional string
    raw_prefix = config.get("label_prefix") or ""
    entered = st.text_input(
        "Label Prefix", value=raw_prefix, key="fnr_label_prefix",
        help='If set, scans blocks for "<prefix>: <value>" and returns the value. E.g. "Form:" for Medidata Rave.'
    )
    config["label_prefix"] = entered if entered.strip() else None

    patterns = config.get("exclude_patterns", [])
    st.write("Exclude Patterns:")
    to_delete = None
    for i, pat in enumerate(patterns):
        col1, col2 = st.columns([5, 1])
        with col1:
            new_pat = st.text_input("Pattern", value=pat, key=f"fnr_pat_{i}", label_visibility="collapsed")
            patterns[i] = new_pat
        with col2:
            if st.button("✕", key=f"fnr_pat_del_{i}"):
                to_delete = i
    if to_delete is not None:
        config["exclude_patterns"] = [p for j, p in enumerate(patterns) if j != to_delete]
    else:
        config["exclude_patterns"] = patterns
    if st.button("Add Exclude Pattern"):
        config["exclude_patterns"] = patterns + [""]
    draft["form_name_rules"] = config


def _render_matching_tab(draft: dict) -> None:
    config = draft.get("matching_config", {})
    st.subheader("Matching Configuration")
    config["exact_threshold"] = st.slider(
        "Exact Threshold", 0.0, 1.0,
        value=float(config.get("exact_threshold", 1.0)), step=0.01, key="mc_exact"
    )
    config["fuzzy_same_form_threshold"] = st.slider(
        "Fuzzy Same-Form Threshold", 0.0, 1.0,
        value=float(config.get("fuzzy_same_form_threshold", 0.80)), step=0.01, key="mc_fuzzy_same"
    )
    config["fuzzy_cross_form_threshold"] = st.slider(
        "Fuzzy Cross-Form Threshold", 0.0, 1.0,
        value=float(config.get("fuzzy_cross_form_threshold", 0.90)), step=0.01, key="mc_fuzzy_cross"
    )
    config["position_fallback_confidence"] = st.slider(
        "Position Fallback Confidence", 0.0, 1.0,
        value=float(config.get("position_fallback_confidence", 0.50)), step=0.01, key="mc_pos"
    )
    draft["matching_config"] = config


def _render_style_tab(draft: dict) -> None:
    config = draft.get("style_defaults", {})
    st.subheader("Style Defaults")
    config["font"] = st.text_input("Font", value=config.get("font", "Arial,BoldItalic"), key="style_font")
    config["font_size"] = st.number_input(
        "Font Size", value=float(config.get("font_size", 18.0)),
        min_value=4.0, max_value=72.0, step=0.5, key="style_font_size"
    )
    tc = config.get("text_color", [0.0, 0.0, 0.0])
    st.write("Text Color (R, G, B):")
    tc_cols = st.columns(3)
    tc[0] = tc_cols[0].number_input("R", 0.0, 1.0, float(tc[0]), 0.01, key="style_tc_r")
    tc[1] = tc_cols[1].number_input("G", 0.0, 1.0, float(tc[1]), 0.01, key="style_tc_g")
    tc[2] = tc_cols[2].number_input("B", 0.0, 1.0, float(tc[2]), 0.01, key="style_tc_b")
    config["text_color"] = tc
    bc = config.get("border_color", [0.75, 1.0, 1.0])
    st.write("Border Color (R, G, B):")
    bc_cols = st.columns(3)
    bc[0] = bc_cols[0].number_input("R", 0.0, 1.0, float(bc[0]), 0.01, key="style_bc_r")
    bc[1] = bc_cols[1].number_input("G", 0.0, 1.0, float(bc[1]), 0.01, key="style_bc_g")
    bc[2] = bc_cols[2].number_input("B", 0.0, 1.0, float(bc[2]), 0.01, key="style_bc_b")
    config["border_color"] = bc
    draft["style_defaults"] = config


def _render_yaml_tab(draft: dict, profiles_dir: Path, name: str) -> None:
    st.subheader("YAML View")
    yaml_text = yaml.dump(draft, allow_unicode=True, sort_keys=False)
    st.text_area("Profile YAML (read-only)", value=yaml_text, height=400, disabled=True, key="yaml_view")
    st.download_button(
        "Download YAML",
        data=yaml_text.encode("utf-8"),
        file_name=f"{name}.yaml",
        mime="text/yaml",
    )


# ---------------------------------------------------------------------------
# Rule tester
# ---------------------------------------------------------------------------

def _render_rule_tester() -> None:
    st.subheader("Rule Tester")
    col1, col2 = st.columns(2)
    with col1:
        test_content = st.text_area("Content", key="rt_content", height=80)
    with col2:
        test_subject = st.text_input("Subject (domain)", key="rt_subject")
    if st.button("Test Rule"):
        engine = st.session_state.get("rule_engine")
        if engine is None:
            st.warning("No profile loaded. Save a profile first.")
        else:
            try:
                category, matched_rule = engine.classify(test_content, test_subject)
                st.success(f"**Category:** {category}")
                st.info(f"**Matched Rule:** {matched_rule}")
            except Exception as e:
                st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def _save_profile(profiles_dir: Path, name: str, draft: dict) -> None:
    try:
        profile = Profile.model_validate(draft)
    except Exception as e:
        st.error(f"Validation error: {e}")
        return
    profile_path = profiles_dir / f"{name}.yaml"
    profile_path.write_text(
        yaml.dump(draft, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    st.session_state["profile"] = profile
    st.session_state["rule_engine"] = RuleEngine(profile)
    st.success(f"Profile '{name}' saved.")
