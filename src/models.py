"""Pydantic v2 data models for CRF-Migrate.

Defines the three core record types used as intermediate artifacts:
- AnnotationRecord: extracted from source aCRF (Phase 1 output)
- FieldRecord: extracted from target blank CRF (Phase 2 output)
- MatchRecord: annotation-to-field match result (Phase 3 output)
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

# Bold/italic aliases known from PyMuPDF and common PDF font names
_BOLD_PATTERN = re.compile(r"(?i)(bold|hebo|hebi|cobo|cobi|tibo|tibi)")
_ITALIC_PATTERN = re.compile(r"(?i)(italic|oblique|heit|hebi|coit|cobi|tiit|tibi)")


class StyleInfo(BaseModel):
    """Font and color styling for a PDF FreeText annotation."""

    font: str = "Arial"
    font_size: float = 10.0
    text_color: list[float] = [0.0, 0.0, 0.0]
    border_color: list[float] = [0.0, 0.0, 0.0]
    fill_color: list[float] | None = None
    border_width: float = 1.0
    border_dashes: list[int] | None = None

    @property
    def is_bold(self) -> bool:
        return bool(_BOLD_PATTERN.search(self.font))

    @property
    def is_italic(self) -> bool:
        return bool(_ITALIC_PATTERN.search(self.font))


class AnnotationRecord(BaseModel):
    """A single SDTM annotation extracted from the source aCRF."""

    id: str                        # UUID4 string
    page: int                      # 1-indexed page number
    content: str                   # SDTM mapping text
    domain: str                    # SDTM domain code (derived from Subject field)
    category: str                  # domain_label | sdtm_mapping | not_submitted | note | cross_reference | _exclude
    matched_rule: str              # Description of the classification rule that matched
    rect: list[float]              # [x0, y0, x1, y1] bounding box in PDF points
    anchor_text: str = ""          # Nearby CRF text used for matching
    anchor_rect: list[float] | None = None  # Bounding box of the anchor text label
    form_name: str = ""            # CRF form/page title
    visit: str = ""                # Visit label
    style: StyleInfo = StyleInfo()
    rotation: int = 0              # Annotation rotation in degrees


class FieldRecord(BaseModel):
    """An identifiable field extracted from the target blank CRF."""

    id: str
    page: int
    label: str                     # Field label text as it appears on the CRF
    form_name: str = ""
    visit: str = ""
    rect: list[float]              # Bounding box of the field label text
    field_type: str                # text_field | checkbox | date_field | table_row | section_header
    page_width: float = 0.0        # target page width in PDF points
    page_height: float = 0.0       # target page height in PDF points


class MatchRecord(BaseModel):
    """Links a source annotation to a target field with match metadata."""

    annotation_id: str
    field_id: str | None           # None when the annotation could not be matched
    match_type: str                # exact | fuzzy | position_only | manual | unmatched | new
    confidence: float              # 0.0 to 1.0
    target_rect: list[float]       # Computed placement position on the target PDF
    target_page: int = 0           # 1-indexed page in the target PDF (0 = unknown/unmatched)
    status: Literal["pending", "approved", "re-pairing"] = "re-pairing"
    user_notes: str = ""
    placement_adjusted: bool = False  # True if target_rect was clamped or fallback-placed
