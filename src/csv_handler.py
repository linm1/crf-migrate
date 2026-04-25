"""CSV import/export for CRF-Migrate record types."""
import copy
import json
import uuid
from pathlib import Path

import pandas as pd

from src.models import AnnotationRecord, ArrowMatch, ArrowRecord, FieldRecord, MatchRecord


# Columns that hold JSON-serialized nested structures
_JSON_COLUMNS = {"rect", "style", "anchor_rect"}


def _flatten_record(record: AnnotationRecord) -> dict:
    """Convert AnnotationRecord to a flat dict suitable for CSV export."""
    data = record.model_dump()
    # Serialize nested objects to JSON strings for CSV compatibility
    data["rect"] = json.dumps(data["rect"])
    data["style"] = json.dumps(data["style"])
    data["anchor_rect"] = json.dumps(data["anchor_rect"])
    return data


def _unflatten_row(row: dict) -> dict:
    """Convert a flat CSV row back to a dict for AnnotationRecord.model_validate."""
    result = dict(row)
    # Deserialize JSON strings back to Python objects
    if "rect" in result and isinstance(result["rect"], str):
        result["rect"] = json.loads(result["rect"])
    if "style" in result and isinstance(result["style"], str):
        result["style"] = json.loads(result["style"])
    if "anchor_rect" in result and isinstance(result["anchor_rect"], str):
        parsed = json.loads(result["anchor_rect"]) if result["anchor_rect"] else None
        result["anchor_rect"] = parsed
    # Convert NaN to empty string for optional string fields
    for key in ("anchor_text", "form_name", "visit", "matched_rule", "domain"):
        if key in result and (result[key] != result[key]):  # NaN check
            result[key] = ""
    return result


def export_annotations_csv(records: list[AnnotationRecord], path: Path) -> None:
    """Export a list of AnnotationRecord to a CSV file at path."""
    rows = [_flatten_record(r) for r in records]
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, encoding="utf-8")


def import_annotations_csv(
    path: Path,
    existing: list[AnnotationRecord],
) -> tuple[list[AnnotationRecord], list[str]]:
    """Import annotations from a CSV file.

    Rules:
    - Rows with a non-empty id: update the matching existing record.
    - Rows with an empty id: create a new AnnotationRecord with a generated UUID.
    - Existing records whose id does not appear in the CSV: flagged (not deleted).

    Returns:
        (updated_records, flagged_deletion_ids)
        updated_records includes all originals (with modifications applied) plus
        new rows. flagged_deletion_ids are IDs of existing records missing from
        the CSV.
    """
    df = pd.read_csv(path, encoding="utf-8", dtype=str)
    df = df.fillna("")

    csv_ids: set[str] = set()
    id_to_row: dict[str, dict] = {}
    new_rows: list[dict] = []

    for _, row in df.iterrows():
        row_dict = _unflatten_row(row.to_dict())
        row_id = str(row_dict.get("id", "")).strip()
        if row_id:
            csv_ids.add(row_id)
            id_to_row[row_id] = row_dict
        else:
            new_rows.append(row_dict)

    # Build updated records from existing (preserving order)
    updated: list[AnnotationRecord] = []
    for record in existing:
        if record.id in id_to_row:
            updated.append(AnnotationRecord.model_validate(id_to_row[record.id]))
        else:
            # Missing from CSV -- keep in updated but flag for deletion confirmation
            updated.append(record)

    # Add new records
    for row_dict in new_rows:
        row_dict = copy.deepcopy(row_dict)
        row_dict["id"] = str(uuid.uuid4())
        # Ensure page is int
        if "page" in row_dict:
            try:
                row_dict["page"] = int(float(row_dict["page"]))
            except (ValueError, TypeError):
                row_dict["page"] = 1
        updated.append(AnnotationRecord.model_validate(row_dict))

    # Compute flagged IDs (existing records not present in CSV)
    flagged = [r.id for r in existing if r.id not in csv_ids]

    return updated, flagged


# ---------------------------------------------------------------------------
# FieldRecord CSV support
# ---------------------------------------------------------------------------

def _flatten_field_record(record: FieldRecord) -> dict:
    """Convert FieldRecord to a flat dict suitable for CSV export."""
    data = record.model_dump()
    data["rect"] = json.dumps(data["rect"])
    return data


def _unflatten_field_row(row: dict) -> dict:
    """Convert a flat CSV row back to a dict for FieldRecord.model_validate."""
    result = dict(row)
    if "rect" in result and isinstance(result["rect"], str):
        result["rect"] = json.loads(result["rect"])
    for key in ("form_name", "visit"):
        if key in result and (result[key] != result[key]):  # NaN check
            result[key] = ""
    return result


def export_fields_csv(records: list[FieldRecord], path: Path) -> None:
    """Export a list of FieldRecord to a CSV file at path."""
    rows = [_flatten_field_record(r) for r in records]
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, encoding="utf-8")


def import_fields_csv(
    path: Path,
    existing: list[FieldRecord],
) -> tuple[list[FieldRecord], list[str]]:
    """Import fields from a CSV file.

    Rules:
    - Rows with a non-empty id: update the matching existing record.
    - Rows with an empty id: create a new FieldRecord with a generated UUID.
    - Existing records whose id does not appear in the CSV: flagged (not deleted).

    Returns:
        (updated_records, flagged_deletion_ids)
    """
    df = pd.read_csv(path, encoding="utf-8", dtype=str)
    df = df.fillna("")

    csv_ids: set[str] = set()
    id_to_row: dict[str, dict] = {}
    new_rows: list[dict] = []

    for _, row in df.iterrows():
        row_dict = _unflatten_field_row(row.to_dict())
        row_id = str(row_dict.get("id", "")).strip()
        if row_id:
            csv_ids.add(row_id)
            id_to_row[row_id] = row_dict
        else:
            new_rows.append(row_dict)

    updated: list[FieldRecord] = []
    for record in existing:
        if record.id in id_to_row:
            updated.append(FieldRecord.model_validate(id_to_row[record.id]))
        else:
            updated.append(record)

    for row_dict in new_rows:
        row_dict = copy.deepcopy(row_dict)
        row_dict["id"] = str(uuid.uuid4())
        if "page" in row_dict:
            try:
                row_dict["page"] = int(float(row_dict["page"]))
            except (ValueError, TypeError):
                row_dict["page"] = 1
        updated.append(FieldRecord.model_validate(row_dict))

    flagged = [r.id for r in existing if r.id not in csv_ids]
    return updated, flagged


# ---------------------------------------------------------------------------
# MatchRecord CSV support
# ---------------------------------------------------------------------------

def _flatten_match_record(record: MatchRecord) -> dict:
    """Convert MatchRecord to a flat dict suitable for CSV export."""
    data = record.model_dump()
    data["target_rect"] = json.dumps(data["target_rect"])
    data["field_id"] = "" if data["field_id"] is None else data["field_id"]
    return data


def export_matches_csv(records: list[MatchRecord], path: Path) -> None:
    """Export a list of MatchRecord to a CSV file at path."""
    rows = [_flatten_match_record(r) for r in records]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


def import_matches_csv(
    path: Path, existing: list[MatchRecord]
) -> tuple[list[MatchRecord], list[str]]:
    """Import matches from a CSV file, updating existing records by annotation_id.

    Returns:
        (updated_records, flagged_annotation_ids)
        flagged_annotation_ids are IDs of existing records missing from the CSV.
    """
    df = pd.read_csv(path, encoding="utf-8", dtype=str).fillna("")
    id_to_row: dict[str, dict] = {}
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        if "target_rect" in row_dict and isinstance(row_dict["target_rect"], str):
            row_dict["target_rect"] = json.loads(row_dict["target_rect"])
        fid = str(row_dict.get("field_id", "")).strip()
        row_dict["field_id"] = None if fid == "" else fid
        row_dict["confidence"] = float(row_dict.get("confidence", 0.0))
        for key in ("user_notes", "status", "match_type"):
            if key in row_dict and (row_dict[key] != row_dict[key]):  # NaN check
                row_dict[key] = ""
        # Migrate legacy status values from old CSVs
        _status = row_dict.get("status", "")
        if _status == "rejected":
            row_dict["status"] = "re-pairing"
        elif _status == "modified":
            row_dict["status"] = "approved"
        annot_id = str(row_dict.get("annotation_id", "")).strip()
        if annot_id:
            id_to_row[annot_id] = row_dict
    updated = []
    for record in existing:
        if record.annotation_id in id_to_row:
            updated.append(MatchRecord.model_validate(id_to_row[record.annotation_id]))
        else:
            updated.append(record)
    flagged = [r.annotation_id for r in existing if r.annotation_id not in id_to_row]
    return updated, flagged


# ---------------------------------------------------------------------------
# ArrowRecord CSV support
# ---------------------------------------------------------------------------

def _flatten_arrow_record(record: ArrowRecord) -> dict:
    """Convert ArrowRecord to a flat dict suitable for CSV export."""
    data = record.model_dump()
    data["tail_vertex"] = json.dumps(data["tail_vertex"])
    data["head_vertex"] = json.dumps(data["head_vertex"])
    data["head_source_rect"] = json.dumps(data["head_source_rect"])  # None → "null"
    data["head_search_hint"] = json.dumps(data["head_search_hint"])
    data["style"] = json.dumps(data["style"])
    return data


def _unflatten_arrow_row(row: dict) -> dict:
    """Convert a flat CSV row back to a dict for ArrowRecord.model_validate."""
    result = dict(row)
    for key in ("tail_vertex", "head_vertex", "head_search_hint", "style"):
        if key in result and isinstance(result[key], str):
            result[key] = json.loads(result[key])
    if "head_source_rect" in result:
        val = result["head_source_rect"]
        if isinstance(val, str):
            stripped = val.strip()
            if stripped == "" or stripped == "null":
                result["head_source_rect"] = None
            else:
                result["head_source_rect"] = json.loads(stripped)
        elif val != val:  # NaN
            result["head_source_rect"] = None
    if "tail_annotation_id" in result:
        val = result["tail_annotation_id"]
        if val != val or val == "":  # NaN or empty string
            result["tail_annotation_id"] = None
    return result


def export_arrows_csv(records: list[ArrowRecord], path: Path) -> None:
    """Export a list of ArrowRecord to a CSV file at path."""
    rows = [_flatten_arrow_record(r) for r in records]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


def import_arrows_csv(path: Path) -> list[ArrowRecord]:
    """Import arrow records from a CSV file (read-only, no merge)."""
    df = pd.read_csv(path, encoding="utf-8", dtype=str).fillna("")
    records = []
    for _, row in df.iterrows():
        row_dict = _unflatten_arrow_row(row.to_dict())
        records.append(ArrowRecord.model_validate(row_dict))
    return records


# ---------------------------------------------------------------------------
# ArrowMatch CSV support
# ---------------------------------------------------------------------------

def _flatten_arrow_match(record: ArrowMatch) -> dict:
    """Convert ArrowMatch to a flat dict suitable for CSV export."""
    data = record.model_dump()
    data["head_target_rect"] = json.dumps(data["head_target_rect"])
    return data


def _unflatten_arrow_match_row(row: dict) -> dict:
    """Convert a flat CSV row back to a dict for ArrowMatch.model_validate."""
    result = dict(row)
    if "head_target_rect" in result and isinstance(result["head_target_rect"], str):
        val = result["head_target_rect"]
        result["head_target_rect"] = json.loads(val) if val else None
    if "target_page" in result:
        tp = result["target_page"]
        if tp != tp or tp == "":  # NaN or empty string
            result["target_page"] = None
        else:
            result["target_page"] = int(float(tp))
    if "target_field_id" in result:
        val = result["target_field_id"]
        if val != val or val == "":  # NaN or empty string
            result["target_field_id"] = None
    return result


def export_arrow_matches_csv(records: list[ArrowMatch], path: Path) -> None:
    """Export a list of ArrowMatch to a CSV file at path."""
    rows = [_flatten_arrow_match(r) for r in records]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


def import_arrow_matches_csv(path: Path) -> list[ArrowMatch]:
    """Import arrow match records from a CSV file (read-only, no merge)."""
    df = pd.read_csv(path, encoding="utf-8", dtype=str).fillna("")
    records = []
    for _, row in df.iterrows():
        row_dict = _unflatten_arrow_match_row(row.to_dict())
        records.append(ArrowMatch.model_validate(row_dict))
    return records

