"""Core rule evaluation engine -- no PDF or UI dependencies."""
import re
from typing import TypedDict

from src.profile_models import Profile, RuleCondition, VisitRule


class TextBlock(TypedDict):
    text: str
    font_size: float
    bold: bool
    rect: list[float]


class RuleEngine:
    """Evaluates profile rules against annotation data.

    All public methods are pure functions of their inputs; no mutable state
    is written after construction (immutability convention).
    """

    def __init__(self, profile: Profile) -> None:
        self._profile = profile
        self._form_name_excludes: list[re.Pattern[str]] = [
            re.compile(p, re.IGNORECASE)
            for p in profile.form_name_rules.exclude_patterns
        ]
        self._anchor_excludes: list[re.Pattern[str]] = self._form_name_excludes  # same object, same list

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, content: str, subject: str) -> tuple[str, str]:
        """Evaluate classification_rules in order; return (category, matched_rule).

        First matching rule wins (short-circuit evaluation).
        Falls back to 'sdtm_mapping' if no rule matches.
        """
        for index, rule in enumerate(self._profile.classification_rules):
            rule_num = index + 1
            if self._evaluate_conditions(rule.conditions, content, subject):
                description = self._describe_rule(rule_num, rule.conditions)
                return rule.category, description

        return "sdtm_mapping", "Rule N: ultimate fallback (no rule matched)"

    def extract_form_name(
        self,
        text_blocks: list[TextBlock],
        page_height: float | None = None,
    ) -> str:
        """Apply form_name_rules to identify the CRF form title.

        Evaluation order:
          1. label_prefix: scan blocks for '<prefix>: <value>', return value.
             Falls through to top-to-bottom scan if no block matches.
          2. top_region_fraction pre-filter: when page_height is provided and
             top_region_fraction is set, restrict candidates to blocks whose y0
             is within the top N% of the true page height.  This prevents footer
             text (stored at low y in some PDFs) from being picked up.
          3. Top-to-bottom scan: sort remaining blocks by y0 (ascending) and
             return the first block that passes min_font_size and exclude_patterns.

        Args:
            text_blocks: Text blocks extracted from a clean (annotation-free) page.
            page_height: True page height in PDF points from page.rect.height.
                         Used to anchor top_region_fraction against a reliable
                         denominator rather than the max y of text blocks.
        """
        config = self._profile.form_name_rules

        # --- 1. label_prefix (priority override) ---
        if config.label_prefix is not None:
            prefix_pat = re.compile(
                rf"^{re.escape(config.label_prefix.rstrip(':'))}\s*:\s*(.+)$",
                re.IGNORECASE,
            )
            for block in text_blocks:
                m = prefix_pat.match(block["text"].strip())
                if m:
                    return m.group(1).strip()
            # No block matched — fall through to top-to-bottom scan

        # --- 2. top_region_fraction pre-filter (uses true page height) ---
        if config.top_region_fraction is not None and page_height and page_height > 0:
            cutoff = config.top_region_fraction * page_height
            eligible = [b for b in text_blocks if b["rect"][1] <= cutoff]
        else:
            eligible = list(text_blocks)

        # --- 3. Strategy-specific scan ---
        if config.strategy == "top_left_block":
            return self._scan_top_left_block(eligible)

        # Default: "largest_bold_text" — top-to-bottom scan with min_font_size
        # Bold blocks first (0 < 1), then largest font (descending), then top-to-bottom
        sorted_blocks = sorted(
            eligible, key=lambda b: (0 if b["bold"] else 1, -b["font_size"], b["rect"][1])
        )
        for block in sorted_blocks:
            text = block["text"].strip()
            if not text:
                continue
            if block["font_size"] < config.min_font_size:
                continue
            if any(pat.search(text) for pat in self._form_name_excludes):
                continue
            return text

        return ""

    def _scan_top_left_block(self, eligible: list[TextBlock]) -> str:
        """Return the topmost left-column block that passes exclude_patterns.

        Left-column threshold: min(block x0) + left_column_tolerance_px.
        Blocks with x0 > threshold are excluded.  Remaining blocks are sorted
        by y0 ascending; the first that passes exclude_patterns is returned.
        min_font_size is intentionally ignored by this strategy.
        """
        if not eligible:
            return ""
        tolerance = self._profile.anchor_text_config.left_column_tolerance_px
        min_x0 = min(b["rect"][0] for b in eligible)
        left_threshold = min_x0 + tolerance
        left_column = [b for b in eligible if b["rect"][0] <= left_threshold]
        sorted_blocks = sorted(left_column, key=lambda b: b["rect"][1])
        for block in sorted_blocks:
            text = block["text"].strip()
            if not text:
                continue
            if any(pat.search(text) for pat in self._form_name_excludes):
                continue
            return text
        return ""

    def extract_visit(self, page_text: str) -> str:
        """Apply visit_rules to detect the visit label from page text.

        Returns the value string (with capture groups substituted) for the
        first matching rule, or empty string if no rule matches.
        """
        for visit_rule in self._profile.visit_rules:
            match = re.search(visit_rule.regex, page_text, re.IGNORECASE)
            if match:
                value = visit_rule.value
                for group_index, group_text in enumerate(match.groups(), start=1):
                    value = value.replace(f"{{{group_index}}}", group_text or "")
                return value

        return ""

    @property
    def anchor_exclude_patterns(self) -> list[re.Pattern[str]]:
        """Pre-compiled exclude patterns for anchor text extraction."""
        return self._anchor_excludes

    @property
    def form_name_exclude_patterns(self) -> list[re.Pattern[str]]:
        """Pre-compiled exclude patterns for form name identification."""
        return self._form_name_excludes

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evaluate_conditions(
        self, cond: RuleCondition, content: str, subject: str
    ) -> bool:
        """Return True only when ALL specified conditions are satisfied (AND logic).

        Conditions not set on the rule object (None) are ignored.
        """
        # --- length guards ---
        if cond.max_length is not None and len(content) > cond.max_length:
            return False

        if cond.min_length is not None and len(content) < cond.min_length:
            return False

        # --- subject annotation type ---
        if cond.subject_is is not None:
            if subject.lower() != cond.subject_is.lower():
                return False

        # --- substring check (literal, case-insensitive) ---
        if cond.contains is not None:
            if cond.contains.lower() not in content.lower():
                return False

        # --- prefix check (case-insensitive) ---
        if cond.starts_with is not None:
            if not content.lower().startswith(cond.starts_with.lower()):
                return False

        # --- multi-line check ---
        if cond.multi_line is not None and cond.multi_line:
            if "\r" not in content and "\n" not in content:
                return False

        # --- regex + optional domain membership ---
        regex_match = None
        if cond.regex is not None:
            regex_match = re.search(cond.regex, content, re.IGNORECASE)
            if not regex_match:
                return False

        if cond.domain_in is not None:
            # domain_in: "domain_codes" → capture group 1 from regex must be
            # present in profile.domain_codes.
            if regex_match is None:
                return False
            groups = regex_match.groups()
            if not groups or groups[0] not in self._profile.domain_codes:
                return False

        if cond.fallback:
            return True

        return True

    def _describe_rule(self, rule_num: int, cond: RuleCondition) -> str:
        """Build a human-readable description for the matched rule."""
        parts: list[str] = []

        if cond.fallback:
            parts.append("fallback")
        if cond.subject_is is not None:
            parts.append(f"subject_is='{cond.subject_is}'")
        if cond.max_length is not None:
            parts.append(f"max_length={cond.max_length}")
        if cond.min_length is not None:
            parts.append(f"min_length={cond.min_length}")
        if cond.contains is not None:
            parts.append(f"contains='{cond.contains}'")
        if cond.starts_with is not None:
            parts.append(f"starts_with='{cond.starts_with}'")
        if cond.multi_line:
            parts.append("multi_line")
        if cond.regex is not None:
            parts.append(f"regex='{cond.regex}'")
        if cond.domain_in is not None:
            parts.append("domain_in=domain_codes")

        description = ", ".join(parts) if parts else "conditions"
        return f"Rule {rule_num}: {description}"
