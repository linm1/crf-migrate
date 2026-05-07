"""Pydantic v2 models for CRF-Migrate profile schema."""
import re
from typing import Any
from pydantic import BaseModel, model_validator, field_validator


class RuleCondition(BaseModel):
    contains: str | None = None
    starts_with: str | None = None
    regex: str | None = None
    domain_in: str | None = None   # "domain_codes" — references profile's domain_codes list
    multi_line: bool | None = None
    max_length: int | None = None
    min_length: int | None = None
    subject_is: str | None = None
    fallback: bool | None = None

    model_config = {"extra": "forbid"}  # TR.03: reject unknown condition types

    @model_validator(mode="after")
    def validate_regex_syntax(self) -> "RuleCondition":
        if self.regex is not None:
            try:
                re.compile(self.regex)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{self.regex}': {e}") from e
        return self


class ClassificationRule(BaseModel):
    conditions: RuleCondition
    category: str


_VALID_FORM_NAME_STRATEGIES = {"largest_bold_text", "top_left_block"}


class FormNameConfig(BaseModel):
    strategy: str = "largest_bold_text"
    min_font_size: float = 12.0
    exclude_patterns: list[str] = []
    top_region_fraction: float | None = None
    label_prefix: str | None = None

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        if v not in _VALID_FORM_NAME_STRATEGIES:
            raise ValueError(
                f"Unknown form_name strategy '{v}'. "
                f"Valid values: {sorted(_VALID_FORM_NAME_STRATEGIES)}"
            )
        return v


class VisitRule(BaseModel):
    regex: str
    value: str

    @field_validator("regex")
    @classmethod
    def validate_regex(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Invalid regex '{v}': {e}") from e
        return v


class AnchorTextConfig(BaseModel):
    radius_px: float = 100.0
    prefer_direction: list[str] = ["left", "above"]
    left_column_tolerance_px: float = 50.0


class AnnotationFilter(BaseModel):
    include_types: list[str] = ["FreeText"]
    exclude_empty: bool = True
    min_content_length: int = 1


class MatchingConfig(BaseModel):
    exact_threshold: float = 1.0
    fuzzy_same_form_threshold: float = 0.80
    fuzzy_cross_form_threshold: float = 0.90
    position_fallback_confidence: float = 0.50
    visit_boost: float = 5.0


class StyleDefaults(BaseModel):
    font: str = "Arial"
    font_size: float = 10.0
    domain_label_font_size: float = 14.0
    text_color: list[float] = [0.0, 0.0, 0.0]
    border_color: list[float] = [0.0, 0.0, 0.0]
    fill_color: list[float] | None = None
    use_source_style: bool = False


class ProfileMeta(BaseModel):
    name: str
    version: str = "1.0"
    description: str = ""
    author: str = ""
    parent: str | None = None


class Profile(BaseModel):
    meta: ProfileMeta
    domain_codes: list[str]
    classification_rules: list[ClassificationRule]
    form_name_rules: FormNameConfig = FormNameConfig()
    visit_rules: list[VisitRule] = []
    anchor_text_config: AnchorTextConfig = AnchorTextConfig()
    annotation_filter: AnnotationFilter = AnnotationFilter()
    matching_config: MatchingConfig = MatchingConfig()
    style_defaults: StyleDefaults = StyleDefaults()
