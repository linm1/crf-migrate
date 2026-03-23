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

    def extract_form_name(self, text_blocks: list[TextBlock]) -> str:
        """Apply form_name_rules to identify the CRF form title.

        Evaluation order:
          1. label_prefix: scan blocks for '<prefix>: <value>', return value.
             Falls through to strategy if no block matches.
          2. top_region_fraction: restrict candidates to top N% of page height
             (estimated from max y1 across all blocks).
          3. Strategy 'largest_bold_text': select block with largest font_size
             (bold as tiebreaker) from candidates passing min_font_size and
             exclude_patterns filters.
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
            # No block matched — fall through to strategy

        # --- 2. top_region_fraction pre-filter ---
        if config.top_region_fraction is not None and text_blocks:
            max_y = max(b["rect"][3] for b in text_blocks)
            cutoff = config.top_region_fraction * max_y
            eligible = [b for b in text_blocks if b["rect"][1] <= cutoff]
        else:
            eligible = list(text_blocks)

        # --- 3. largest_bold_text strategy ---
        compiled_excludes = [
            re.compile(p, re.IGNORECASE) for p in config.exclude_patterns
        ]
        candidates = [
            block for block in eligible
            if block["text"].strip()
            and block["font_size"] >= config.min_font_size
            and not any(pat.search(block["text"]) for pat in compiled_excludes)
        ]

        if not candidates:
            return ""

        best = max(candidates, key=lambda b: (b["font_size"], b["bold"]))
        return best["text"].strip()

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

        # --- fallback: always True (acts as an unconditional catch-all) ---
        # Evaluated last so that it cannot short-circuit other conditions when
        # combined with other fields (though in practice fallback is used alone).
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
