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
        .pe-section-title { font-family: 'Aeonik Mono', ui-monospace, monospace; font-size: 18px;
            font-weight: 400; color: #383838; text-transform: uppercase;
            letter-spacing: 0.5px; margin: 0 0 4px 0; }
        .pe-help-text { font-size: 12px; color: #818181;
            font-family: 'Aeonik Mono', ui-monospace, monospace; margin: -4px 0 8px 0; }
        .pe-field-label { font-size: 13px; font-weight: 600; color: #383838;
            font-family: Aeonik, ui-sans-serif, sans-serif; margin: 0 0 2px 0; }
        .pe-yaml-header { background: #383838; display: flex; align-items: center;
            justify-content: space-between; padding: 8px 12px; }
        .pe-yaml-dots { display: flex; gap: 6px; align-items: center; }
        .pe-yaml-dot { display: inline-block; width: 12px; height: 12px;
            border-radius: 50% !important; }
        /* Hide native slider min/max tick labels (we show our own badge) */
        div[data-testid="stSlider"] [data-testid="stTickBarMin"],
        div[data-testid="stSlider"] [data-testid="stTickBarMax"] {
            display: none !important; }
        /* YAML st.code block: remove extra margin, match terminal aesthetic */
        div[data-testid="stCode"] {
            margin-top: 0 !important;
            border-radius: 0 !important; }
        .pe-chip { display: inline-flex; align-items: center;
            background: #f8f8f7; border: 2px solid #383838; height: 28px; padding: 0 12px;
            font-size: 13px; color: #383838;
            font-family: Aeonik, ui-sans-serif, sans-serif; margin: 2px; }
        /* Domain code badge-buttons: pill shape with inline × */
        [class*="st-key-del_code_"] button {
            background: #f8f8f7 !important;
            color: #383838 !important;
            border: 2px solid #383838 !important;
            border-radius: 0 !important;
            padding: 2px 10px !important;
            font-size: 12px !important;
            font-family: 'Aeonik Mono', ui-monospace, monospace !important;
            font-weight: 400 !important;
            height: auto !important;
            min-height: 0 !important;
            line-height: 1.4 !important;
            white-space: nowrap !important;
            width: auto !important;
            outline: none !important; }
        [class*="st-key-del_code_"] button:hover {
            background: #f4efea !important;
            border-color: #383838 !important; }
        [class*="st-key-del_code_"] { padding: 2px !important; }

        .pe-cat-badge { display: inline-block; background: #EAF0FF;
            padding: 2px 8px; font-size: 11px; font-weight: 600;
            color: #383838; font-family: Aeonik, ui-monospace, monospace; }
        .pe-table-header { font-family: Aeonik, ui-sans-serif, sans-serif; font-size: 11px;
            font-weight: 600; color: #818181; text-transform: uppercase;
            letter-spacing: 0.5px; padding: 4px 0 8px 0; }
        .pe-slider-badge { display: inline-block; background: #383838;
            color: #FFFFFF; font-size: 11px;
            font-family: ui-monospace, Consolas, monospace;
            padding: 2px 8px; min-width: 40px; text-align: center; }
        .pe-swatch { display: inline-block; width: 20px; height: 20px;
            border: 2px solid #383838; vertical-align: middle; margin-left: 8px; }
        .pe-yaml-terminal { background: #383838; color: #d7d7d7;
            font-family: ui-monospace, Consolas, 'Courier New', monospace;
            font-size: 12px; padding: 16px; min-height: 300px;
            overflow-x: auto; white-space: pre; margin: 0;
            list-style: none !important; }
        .pe-yaml-filename { font-size: 11px; color: #d7d7d7;
            font-family: ui-monospace, monospace; }
        /* Profile editor inner action buttons (non-topbar): row actions, add buttons */
        .st-key-add_rule_btn button p, .st-key-add_visit_rule button p,
        .st-key-add_exclude_pat button p, .st-key-yaml_download button p,
        .st-key-add_domain_code button p,
        [class*="st-key-rule_"][class*="_save"] button p,
        [class*="st-key-rule_"][class*="_del"] button p,
        [class*="st-key-rule_"][class*="_up"] button p {
            font-size: 12px !important;
            font-weight: 400 !important; }
        /* Rule tester TEST button */
        .st-key-rt_test_btn button p {
            font-size: 12px !important;
            font-weight: 400 !important; }
        /* Full-width list rows with delete icon */
        [class*="st-key-list_row_"] {
            background: #f8f8f7 !important;
            border: 2px solid #383838 !important;
            padding: 4px 8px !important;
            margin-bottom: 4px !important; }
        [class*="st-key-list_row_"]:hover {
            background: #f4efea !important; }
        [class*="st-key-list_row_"] [class*="st-key-del_row_"] button {
            background: #FFFFFF !important;
            border: 2px solid #383838 !important;
            color: #383838 !important;
            font-size: 14px !important;
            padding: 0 !important;
            width: 24px !important;
            height: 24px !important;
            min-height: 0 !important;
            line-height: 24px !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important; }
        [class*="st-key-list_row_"] [class*="st-key-del_row_"] button:hover {
            color: #FFFFFF !important;
            background: #FF5F56 !important;
            border-color: #FF5F56 !important; }
        /* Right-align the delete button within its column */
        [class*="st-key-list_row_"] [class*="st-key-del_row_"] {
            min-width: 0 !important;
            padding: 0 !important; }
        /* stColumn grandparent: flex row, push content to right */
        [class*="st-key-list_row_"] .stColumn:has([class*="st-key-del_row_"]) {
            flex: 0 0 30px !important;
            max-width: 30px !important;
            min-width: 0 !important;
            display: flex !important;
            justify-content: flex-end !important;
            align-items: center !important;
            padding-right: 8px !important; }
        /* stVerticalBlock inside that column: shrink to content and push right */
        [class*="st-key-list_row_"] .stColumn:has([class*="st-key-del_row_"]) .stVerticalBlock {
            width: fit-content !important;
            margin-left: auto !important; }
        [class*="st-key-list_row_"] [class*="st-key-del_row_"] div[data-testid="stButton"] {
            display: flex !important;
            justify-content: flex-end !important; }
        [class*="st-key-list_row_"] [class*="st-key-del_row_"] div[data-testid="stButton"] button {
            margin-left: auto !important; }
        /* Muted labels inside classification rule drawers */
        [class*="st-key-rule_"] label p,
        [class*="st-key-rule_"] .stCheckbox label p {
            color: #383838 !important;
            font-size: 12px !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_profile_editor(profiles_dir: Path) -> None:
    """Render the full profile editor page."""
    profile_names = list_profiles(profiles_dir)
    if not profile_names:
        st.warning("No profiles found in profiles/ directory.")
        return

    # Derive selected profile
    selected = st.session_state.get("profile_name", profile_names[0])
    if selected not in profile_names:
        selected = profile_names[0]

    # Title row
    st.header("Profile Editor")

    # Toolbar row: buttons sized to content
    _, tb_dup, tb_imp_btn, tb_save = st.columns([5, 1, 1, 1], gap="small")
    with tb_dup:
        if st.button("Duplicate", key="pe_dup", use_container_width=True):
            _duplicate_profile(profiles_dir, selected)
            st.rerun()
    with tb_imp_btn:
        if st.button("Import", key="pe_imp_toggle", use_container_width=True):
            st.session_state["pe_show_import"] = not st.session_state.get("pe_show_import", False)
    with tb_save:
        if st.button("Save", key="pe_save_top", use_container_width=True):
            if "draft_profile_data" in st.session_state:
                _save_profile(profiles_dir, selected, st.session_state["draft_profile_data"])
            else:
                st.warning("No draft to save — try reloading the profile.")

    # File uploader: toggle-revealed below toolbar
    if st.session_state.get("pe_show_import", False):
        uploaded_yaml = st.file_uploader(
            "Upload YAML profile", type=["yaml", "yml"],
            key="profile_import", label_visibility="visible",
        )
        if uploaded_yaml is not None:
            _import_yaml(profiles_dir, uploaded_yaml)
            st.session_state["pe_show_import"] = False
            st.rerun()

    # Ensure draft data is initialized
    if "draft_profile_data" not in st.session_state:
        _reset_draft(profiles_dir, selected)

    draft = st.session_state["draft_profile_data"]

    # Tabs
    tabs = st.tabs([
        "Domains", "Classification", "Visits",
        "Form Name", "Matching", "Style", "YAML"
    ])

    with tabs[0]:
        _render_domain_codes_tab(draft)

    with tabs[1]:
        _render_classification_rules_tab(draft, profiles_dir, selected)
        st.divider()
        _render_rule_tester()

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
    codes: list[str] = sorted(draft.get("domain_codes", []))

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
                draft["domain_codes"] = sorted(codes + [val])
                st.rerun()

    # Badge-buttons: 10 per row, left-aligned, alphabetically sorted
    if codes:
        cols_per_row = 10
        to_delete = None
        for row_start in range(0, len(codes), cols_per_row):
            row_codes = codes[row_start : row_start + cols_per_row]
            row_cols = st.columns(cols_per_row)
            for j, code in enumerate(row_codes):
                g_idx = row_start + j
                with row_cols[j]:
                    if st.button(f"{code} ×", key=f"del_code_{g_idx}", help=f"Remove {code}"):
                        to_delete = g_idx
        if to_delete is not None:
            draft["domain_codes"] = sorted(c for i, c in enumerate(codes) if i != to_delete)
            st.rerun()
    else:
        st.markdown('<p class="pe-help-text">No domain codes defined.</p>', unsafe_allow_html=True)


def _render_classification_rules_tab(draft: dict, profiles_dir: Path, selected: str) -> None:
    rules = draft.get("classification_rules", [])
    st.markdown('<p class="pe-section-title">Classification Rules</p>', unsafe_allow_html=True)
    to_delete = None
    to_move_up = None
    for i, rule in enumerate(rules):
        cond = rule.get("conditions", {})
        cat = rule.get("category", "sdtm_mapping")
        color = _RULE_LABEL_COLORS.get(cat, "gray")
        bg = _RULE_BG_COLORS.get(cat, "#ECEFF1")
        expander_label = f":{color}[**{cat}**] - :gray[*{_cond_summary(cond) or '(empty rule)'}*]"
        st.markdown(
            f'<style>.st-key-rule_exp_{i} details{{background:{bg} !important}}</style>',
            unsafe_allow_html=True,
        )
        with st.container(key=f"rule_exp_{i}"):
            with st.expander(expander_label):
                new_cond: dict = {}
                col1, col2 = st.columns(2)
                with col1:
                    for field in ["contains", "starts_with", "regex", "subject_is", "domain_in"]:
                        val = st.text_input(
                            field, value=cond.get(field) or "",
                            key=f"rule_{i}_{field}"
                        )
                        new_cond[field] = val if val.strip() else None
                with col2:
                    for field in ["max_length", "min_length"]:
                        raw_val = cond.get(field)
                        val_str = str(raw_val) if raw_val is not None else ""
                        entered = st.text_input(
                            field, value=val_str, key=f"rule_{i}_{field}"
                        )
                        if entered.strip():
                            try:
                                new_cond[field] = int(entered)
                            except ValueError:
                                new_cond[field] = None
                        else:
                            new_cond[field] = None
                    multi_line = st.checkbox(
                        "multi_line", value=bool(cond.get("multi_line")),
                        key=f"rule_{i}_multi_line"
                    )
                    new_cond["multi_line"] = multi_line or None
                    fallback = st.checkbox(
                        "fallback", value=bool(cond.get("fallback")),
                        key=f"rule_{i}_fallback"
                    )
                    new_cond["fallback"] = fallback or None

                categories = ["sdtm_mapping", "domain_label", "not_submitted", "note", "_exclude"]
                cat_idx = categories.index(cat) if cat in categories else 0
                new_cat = st.selectbox("Category", categories, index=cat_idx, key=f"rule_{i}_cat")
                rules[i] = {**rule, "category": new_cat, "conditions": new_cond}

                btn_save, btn_del, btn_up, _ = st.columns([1, 1, 1, 3], gap="small")
                with btn_save:
                    if st.button("Save", key=f"rule_{i}_save", use_container_width=True):
                        draft["classification_rules"] = list(rules)
                        _save_profile(profiles_dir, selected, draft)
                with btn_del:
                    if st.button("Delete", key=f"rule_{i}_del", use_container_width=True):
                        to_delete = i
                with btn_up:
                    if i > 0 and st.button("↑ Up", key=f"rule_{i}_up", use_container_width=True):
                        to_move_up = i

    if to_delete is not None:
        draft["classification_rules"] = [r for j, r in enumerate(rules) if j != to_delete]
        st.rerun()
    elif to_move_up is not None:
        new_rules = list(rules)
        new_rules[to_move_up - 1], new_rules[to_move_up] = new_rules[to_move_up], new_rules[to_move_up - 1]
        draft["classification_rules"] = new_rules
        st.rerun()
    if st.button("＋ Add Rule", key="add_rule_btn", use_container_width=True):
        draft["classification_rules"] = rules + [
            {"conditions": {"fallback": True}, "category": "sdtm_mapping"}
        ]
        st.rerun()


_RULE_LABEL_COLORS = {
    "sdtm_mapping":  "blue",    # link-blue #007AFF
    "domain_label":  "orange",  # duck-yellow #FFD700
    "not_submitted": "green",   # terminal-green #27C93F
    "note":          "gray",    # terminal-gray #EEEEEE
    "_exclude":      "red",     # terminal-red #FF5F56
}

_RULE_BG_COLORS = {
    "sdtm_mapping":  "#EAF0FF",
    "domain_label":  "#F9FBE7",
    "not_submitted": "#E8F5E9",
    "note":          "#ECEFF1",
    "_exclude":      "#FFEBE9",
}


def _cond_summary(cond: dict) -> str:
    parts = []
    for k, v in cond.items():
        if v is not None and v is not False:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts[:3]) or "(empty)"


def _render_list_row(
    index: int,
    content_fn,
    del_key: str,
    col_ratio: list | None = None,
    prefix: str = "row",
) -> bool:
    """Render a full-width styled row with a delete icon on the right.

    Wraps content_fn in a keyed container (targeted by CSS .st-key-list_row_*)
    and places a delete icon button in the last column.
    Returns True if delete was clicked.
    """
    if col_ratio is None:
        col_ratio = [20, 1]
    with st.container(key=f"list_row_{prefix}_{index}"):
        cols = st.columns(col_ratio, gap="small", vertical_alignment="center")
        with cols[0]:
            content_fn()
        with cols[-1]:
            return st.button("✕", key=del_key, use_container_width=False)
    return False  # unreachable; satisfies type checkers


def _render_visit_rules_tab(draft: dict) -> None:
    rules: list[dict] = draft.get("visit_rules", [])

    st.markdown('<p class="pe-section-title">Visit Rules</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="pe-help-text">Map regex patterns to visit names</p>',
        unsafe_allow_html=True,
    )

    orig_rules = list(rules)
    to_delete = None
    for i, rule in enumerate(orig_rules):
        def _rule_content(i=i, rule=rule):
            rules[i]["regex"] = st.text_input(
                "", value=rule.get("regex", ""),
                key=f"vr_regex_{i}", label_visibility="collapsed",
            )
        if _render_list_row(i, _rule_content, del_key=f"del_row_vr_{i}", col_ratio=[20, 1], prefix="vr"):
            to_delete = i

    if to_delete is not None:
        draft["visit_rules"] = [r for i, r in enumerate(orig_rules) if i != to_delete]
        st.rerun()
    else:
        draft["visit_rules"] = rules

    st.markdown('<div class="pe-btn-dark">', unsafe_allow_html=True)
    if st.button("+ Add Visit Rule", key="add_visit_rule", use_container_width=True):
        draft["visit_rules"] = rules + [{"regex": ""}]
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

    orig_patterns = list(config.get("exclude_patterns", []))
    patterns = list(orig_patterns)
    st.markdown('<p style="font-size:13px;font-weight:600;color:#383838;margin:0 0 4px 0">Exclude Patterns</p>', unsafe_allow_html=True)
    to_delete = None
    for i, pat in enumerate(orig_patterns):
        def _pat_content(i=i, pat=pat):
            new_pat = st.text_input(
                "", value=pat, key=f"fnr_pat_{i}", label_visibility="collapsed"
            )
            patterns[i] = new_pat
        if _render_list_row(i, _pat_content, del_key=f"del_row_fnr_{i}", col_ratio=[6, 1], prefix="fnr"):
            to_delete = i
    if to_delete is not None:
        config["exclude_patterns"] = [p for j, p in enumerate(orig_patterns) if j != to_delete]
    else:
        config["exclude_patterns"] = patterns
    if st.button("＋ Add Exclude Pattern", key="add_exclude_pat", use_container_width=True):
        config["exclude_patterns"] = patterns + [""]
    draft["form_name_rules"] = config


def _render_matching_tab(draft: dict) -> None:
    config: dict = draft.get("matching_config", {})
    st.markdown('<p class="pe-section-title">Matching Configuration</p>', unsafe_allow_html=True)

    _SLIDERS = [
        ("exact_threshold",              "Exact Threshold",              1.0),
        ("fuzzy_same_form_threshold",    "Fuzzy Same-Form Threshold",    0.80),
        ("fuzzy_cross_form_threshold",   "Fuzzy Cross-Form Threshold",   0.90),
        ("position_fallback_confidence", "Position Fallback Confidence", 0.50),
    ]
    new_config: dict = dict(config)
    for key, label, default in _SLIDERS:
        # Read live value from session state if slider already rendered, else use config/default
        live_val = float(st.session_state.get(f"mc_{key}", config.get(key, default)))
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'margin-bottom:2px;margin-top:8px">'
            f'  <span class="pe-field-label">{label}</span>'
            f'  <span class="pe-slider-badge">{live_val:.2f}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        new_val = st.slider(
            label, 0.0, 1.0, value=live_val, step=0.01,
            key=f"mc_{key}", label_visibility="collapsed",
        )
        new_config[key] = new_val
    draft["matching_config"] = new_config


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    """Convert 0.0–1.0 RGB floats to #RRGGBB hex string."""
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def _render_style_tab(draft: dict) -> None:
    config: dict = draft.get("style_defaults", {})
    st.markdown('<p class="pe-section-title">Style Defaults</p>', unsafe_allow_html=True)

    new_config: dict = dict(config)
    new_config["font"] = config.get("font", "Arial,BoldItalic")
    new_config["font_size"] = st.number_input(
        "Font Size", min_value=4.0, max_value=72.0,
        value=float(config.get("font_size", 18.0)), step=0.5, key="style_font_size",
    )
    new_config["domain_label_font_size"] = st.number_input(
        "Domain Label Font Size", min_value=4.0, max_value=72.0,
        value=float(config.get("domain_label_font_size", 14.0)), step=0.5,
        key="style_domain_label_font_size",
    )

    tc = list(config.get("text_color", [0.0, 0.0, 0.0]))
    if len(tc) < 3:
        tc = list(tc) + [0.0] * (3 - len(tc))
    tc_hex = _rgb_to_hex(float(tc[0]), float(tc[1]), float(tc[2]))
    st.markdown(
        f'Text Color (R, G, B): <span class="pe-swatch" style="background:{tc_hex}"></span>',
        unsafe_allow_html=True,
    )
    tc_cols = st.columns(3)
    new_tc = [
        tc_cols[0].number_input("R", 0.0, 1.0, float(tc[0]), 0.01, key="style_tc_r"),
        tc_cols[1].number_input("G", 0.0, 1.0, float(tc[1]), 0.01, key="style_tc_g"),
        tc_cols[2].number_input("B", 0.0, 1.0, float(tc[2]), 0.01, key="style_tc_b"),
    ]
    new_config["text_color"] = new_tc

    bc = list(config.get("border_color", [0.75, 1.0, 1.0]))
    if len(bc) < 3:
        bc = list(bc) + [0.0] * (3 - len(bc))
    bc_hex = _rgb_to_hex(float(bc[0]), float(bc[1]), float(bc[2]))
    st.markdown(
        f'Border Color (R, G, B): <span class="pe-swatch" style="background:{bc_hex}"></span>',
        unsafe_allow_html=True,
    )
    bc_cols = st.columns(3)
    new_bc = [
        bc_cols[0].number_input("R", 0.0, 1.0, float(bc[0]), 0.01, key="style_bc_r"),
        bc_cols[1].number_input("G", 0.0, 1.0, float(bc[1]), 0.01, key="style_bc_g"),
        bc_cols[2].number_input("B", 0.0, 1.0, float(bc[2]), 0.01, key="style_bc_b"),
    ]
    new_config["border_color"] = new_bc

    draft["style_defaults"] = new_config


def _render_yaml_tab(draft: dict, profiles_dir: Path, name: str) -> None:
    st.markdown('<p class="pe-section-title">YAML View</p>', unsafe_allow_html=True)
    yaml_text = yaml.dump(draft, allow_unicode=True, sort_keys=False)
    # macOS-style terminal header bar
    st.markdown(
        f'<div style="background:#F4EFEA;border:2px solid #383838;overflow:hidden;">'
        f'  <div class="pe-yaml-header">'
        f'    <div class="pe-yaml-dots">'
        f'      <span class="pe-yaml-dot" style="background:#FF5F56"></span>'
        f'      <span class="pe-yaml-dot" style="background:#FFBD2E"></span>'
        f'      <span class="pe-yaml-dot" style="background:#27C93F"></span>'
        f'    </div>'
        f'    <span class="pe-yaml-filename">{name}.yaml</span>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    # Use st.code for proper IDE-style display (no markdown list parsing)
    st.code(yaml_text, language="yaml")
    st.download_button(
        "↓ Download YAML",
        data=yaml_text.encode("utf-8"),
        file_name=f"{name}.yaml",
        mime="text/yaml",
        use_container_width=True,
        key="yaml_download",
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
    if st.button("TEST", key="rt_test_btn"):
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
        Profile.model_validate(draft)
    except Exception as e:
        st.error(f"Validation error: {e}")
        return
    profile_path = profiles_dir / f"{name}.yaml"
    profile_path.write_text(
        yaml.dump(draft, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    # Reload from disk so session state always matches the saved file exactly
    profile = load_profile(profile_path, profiles_dir)
    st.session_state["profile"] = profile
    st.session_state["rule_engine"] = RuleEngine(profile)
    st.session_state["draft_profile_data"] = profile.model_dump()
    st.success(f"Profile '{name}' saved.")
    st.rerun()
