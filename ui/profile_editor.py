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
        .pe-section-title { font-family: Inter, sans-serif; font-size: 18px;
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
    profile_names = list_profiles(profiles_dir)
    if not profile_names:
        st.warning("No profiles found in profiles/ directory.")
        return

    # Derive selected profile
    selected = st.session_state.get("profile_name", profile_names[0])
    if selected not in profile_names:
        selected = profile_names[0]

    # Toolbar: title left, action buttons right
    tb_left, _tb_spacer, tb_dup, tb_imp, tb_save = st.columns([4, 2, 1, 1, 1])
    with tb_left:
        st.markdown(
            '<p class="pe-section-title" style="margin-top:6px">Profile Editor</p>',
            unsafe_allow_html=True,
        )
    with tb_dup:
        if st.button("Duplicate", key="pe_dup"):
            _duplicate_profile(profiles_dir, selected)
            st.rerun()
    with tb_imp:
        uploaded_yaml = st.file_uploader(
            "", type=["yaml", "yml"], key="profile_import", label_visibility="collapsed"
        )
        if uploaded_yaml is not None:
            _import_yaml(profiles_dir, uploaded_yaml)
            st.rerun()
    with tb_save:
        st.markdown('<div class="pe-btn-dark">', unsafe_allow_html=True)
        if st.button("Save Profile", key="pe_save_top"):
            if "draft_profile_data" in st.session_state:
                _save_profile(profiles_dir, selected, st.session_state["draft_profile_data"])
            else:
                st.warning("No draft to save — try reloading the profile.")
        st.markdown('</div>', unsafe_allow_html=True)

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
    codes: list[str] = draft.get("domain_codes", [])

    # Header row: title | spacer | input | add button
    h_title, _sp, h_input, h_add = st.columns([3, 2, 2, 1])
    with h_title:
        st.markdown('<p class="pe-section-title">Domain Codes</p>', unsafe_allow_html=True)
    with h_input:
        new_code = st.text_input(
            "", placeholder="New domain code...",
            key="new_domain_code", label_visibility="collapsed",
        )
    with h_add:
        if st.button("+ Add", key="add_domain_code"):
            val = new_code.strip().upper()
            if val and val not in codes:
                draft["domain_codes"] = codes + [val]
                st.rerun()

    # Chips with inline delete buttons
    if codes:
        cols_per_row = 8
        to_delete = None
        for row_start in range(0, len(codes), cols_per_row):
            row_codes = codes[row_start : row_start + cols_per_row]
            row_cols = st.columns(len(row_codes))
            for j, code in enumerate(row_codes):
                g_idx = row_start + j
                with row_cols[j]:
                    st.markdown(f'<span class="pe-chip">{code}</span>', unsafe_allow_html=True)
                    if st.button("✕", key=f"del_code_{g_idx}", help=f"Remove {code}"):
                        to_delete = g_idx
        if to_delete is not None:
            draft["domain_codes"] = [c for i, c in enumerate(codes) if i != to_delete]
            st.rerun()
    else:
        st.markdown('<p class="pe-help-text">No domain codes defined.</p>', unsafe_allow_html=True)


def _render_classification_rules_tab(draft: dict) -> None:
    rules = draft.get("classification_rules", [])
    st.markdown('<p class="pe-section-title">Classification Rules</p>', unsafe_allow_html=True)
    to_delete = None
    to_move_up = None
    for i, rule in enumerate(rules):
        cond = rule.get("conditions", {})
        cat = rule.get("category", "sdtm_mapping")
        bg = _RULE_BADGE_COLORS.get(cat, "#e2e3e5")
        st.markdown(
            f'<div style="background:{bg};padding:6px 12px;display:flex;gap:8px;'
            f'align-items:center;border-left:3px solid #383838;margin-bottom:2px">'
            f'<span class="pe-cat-badge">{cat}</span>'
            f'<span style="font-size:12px;color:#6c757d">{_cond_summary(cond)}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
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
        new_rules = list(rules)
        new_rules[to_move_up - 1], new_rules[to_move_up] = new_rules[to_move_up], new_rules[to_move_up - 1]
        draft["classification_rules"] = new_rules

    st.markdown('<div class="pe-btn-dark">', unsafe_allow_html=True)
    if st.button("Add Rule", key="add_rule_btn", use_container_width=True):
        draft["classification_rules"] = rules + [
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"}
        ]
    st.markdown('</div>', unsafe_allow_html=True)


_RULE_BADGE_COLORS = {
    "sdtm_mapping": "#cce5ff",
    "domain_label": "#d4edda",
    "not_submitted": "#fff3cd",
    "note": "#d1ecf1",
    "_exclude": "#f8d7da",
}


def _cond_summary(cond: dict) -> str:
    parts = []
    for k, v in cond.items():
        if v is not None and v is not False:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts[:3]) or "(empty)"


def _render_visit_rules_tab(draft: dict) -> None:
    rules: list[dict] = draft.get("visit_rules", [])

    st.markdown('<p class="pe-section-title">Visit Rules</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="pe-help-text">Map regex patterns to visit names</p>',
        unsafe_allow_html=True,
    )

    # Column headers
    hc1, hc2, _hc3 = st.columns([3, 3, 1])
    hc1.markdown('<div class="pe-table-header">Regex Pattern</div>', unsafe_allow_html=True)
    hc2.markdown('<div class="pe-table-header">Visit Value</div>', unsafe_allow_html=True)

    to_delete = None
    for i, rule in enumerate(rules):
        c1, c2, c3 = st.columns([3, 3, 1])
        with c1:
            rules[i]["regex"] = st.text_input(
                "", value=rule.get("regex", ""),
                key=f"vr_regex_{i}", label_visibility="collapsed",
            )
        with c2:
            rules[i]["value"] = st.text_input(
                "", value=rule.get("value", ""),
                key=f"vr_value_{i}", label_visibility="collapsed",
            )
        with c3:
            st.markdown('<div class="pe-btn-danger">', unsafe_allow_html=True)
            if st.button("🗑", key=f"del_vr_{i}"):
                to_delete = i
            st.markdown('</div>', unsafe_allow_html=True)

    if to_delete is not None:
        draft["visit_rules"] = [r for i, r in enumerate(rules) if i != to_delete]
        st.rerun()
    else:
        draft["visit_rules"] = rules

    st.markdown('<div class="pe-btn-dark">', unsafe_allow_html=True)
    if st.button("+ Add Visit Rule", key="add_visit_rule", use_container_width=True):
        draft["visit_rules"] = rules + [{"regex": "", "value": ""}]
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


def _render_form_name_tab(draft: dict) -> None:
    config = draft.get("form_name_rules", {})
    st.markdown('<p class="pe-section-title">Form Name Rules</p>', unsafe_allow_html=True)
    strategies = ["largest_bold_text"]
    strat = config.get("strategy", "largest_bold_text")
    strat_idx = strategies.index(strat) if strat in strategies else 0
    config["strategy"] = st.selectbox("Strategy", strategies, index=strat_idx, key="fnr_strategy")
    config["min_font_size"] = st.number_input(
        "Min Font Size", value=float(config.get("min_font_size", 12.0)),
        min_value=1.0, step=0.5, key="fnr_min_font"
    )

    st.divider()

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

    st.divider()

    # label_prefix: optional string
    raw_prefix = config.get("label_prefix") or ""
    entered = st.text_input(
        "Label Prefix", value=raw_prefix, key="fnr_label_prefix",
        help='If set, scans blocks for "<prefix>: <value>" and returns the value. E.g. "Form:" for Medidata Rave.'
    )
    config["label_prefix"] = entered if entered.strip() else None

    st.divider()

    patterns = config.get("exclude_patterns", [])
    st.markdown('<p style="font-size:13px;font-weight:600;color:#383838;margin:0 0 4px 0">Exclude Patterns</p>', unsafe_allow_html=True)
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
    st.markdown('<div class="pe-btn-dark">', unsafe_allow_html=True)
    if st.button("Add Exclude Pattern", key="add_exclude_pat", use_container_width=True):
        config["exclude_patterns"] = patterns + [""]
    st.markdown('</div>', unsafe_allow_html=True)
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
    st.rerun()
