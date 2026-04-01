"""Tests for src/csv_handler.py -- CSV export/import for AnnotationRecord.
Covers T1.12-T1.15.
"""
import uuid
import pytest
from pathlib import Path

from src.models import AnnotationRecord, StyleInfo
from src.csv_handler import export_annotations_csv, import_annotations_csv


def make_annotation(**kwargs) -> AnnotationRecord:
    defaults = {
        "id": str(uuid.uuid4()),
        "page": 1,
        "content": "BRTHDTC",
        "domain": "DM",
        "category": "sdtm_mapping",
        "matched_rule": "Rule 9: fallback",
        "rect": [100.0, 200.0, 300.0, 220.0],
        "anchor_text": "Date of Birth",
        "form_name": "DEMOGRAPHICS",
        "visit": "Screening",
    }
    defaults.update(kwargs)
    return AnnotationRecord(**defaults)


class TestExportAnnotations:
    def test_t1_12_export_creates_csv(self, tmp_path):
        """T1.12: CSV export creates a file with correct row count + header."""
        records = [make_annotation() for _ in range(10)]
        path = tmp_path / "annotations.csv"
        export_annotations_csv(records, path)
        assert path.exists()

    def test_t1_12_csv_has_correct_row_count(self, tmp_path):
        """T1.12: Exported CSV has N rows + header."""
        import pandas as pd
        records = [make_annotation(content=f"FIELD_{i}") for i in range(10)]
        path = tmp_path / "annotations.csv"
        export_annotations_csv(records, path)
        df = pd.read_csv(path)
        assert len(df) == 10

    def test_t1_12_csv_has_required_columns(self, tmp_path):
        """T1.12: All required fields are present as columns."""
        import pandas as pd
        records = [make_annotation()]
        path = tmp_path / "annotations.csv"
        export_annotations_csv(records, path)
        df = pd.read_csv(path)
        required_cols = ["id", "page", "content", "domain", "category",
                         "matched_rule", "anchor_text", "form_name", "visit",
                         "rotation"]
        for col in required_cols:
            assert col in df.columns, f"Missing column: {col}"

    def test_export_preserves_content(self, tmp_path):
        """Exported CSV preserves content values."""
        import pandas as pd
        record = make_annotation(content="VSTESTCD", domain="VS", visit="Week 4")
        path = tmp_path / "annotations.csv"
        export_annotations_csv([record], path)
        df = pd.read_csv(path)
        assert df.iloc[0]["content"] == "VSTESTCD"
        assert df.iloc[0]["domain"] == "VS"
        assert df.iloc[0]["visit"] == "Week 4"


class TestImportAnnotations:
    def test_t1_13_round_trip_preserves_data(self, tmp_path):
        """T1.13: CSV round-trip -- export, modify 1 row, re-import, verify."""
        import pandas as pd
        records = [
            make_annotation(content="BRTHDTC", domain="DM"),
            make_annotation(content="AESTDTC", domain="AE"),
            make_annotation(content="VSTESTCD", domain="VS"),
        ]
        path = tmp_path / "annotations.csv"
        export_annotations_csv(records, path)

        # Modify 1 row
        df = pd.read_csv(path)
        df.loc[df["content"] == "BRTHDTC", "domain"] = "XX"
        df.to_csv(path, index=False)

        updated, flagged = import_annotations_csv(path, records)
        brthdtc = next(r for r in updated if r.content == "BRTHDTC")
        assert brthdtc.domain == "XX"
        # Other records unchanged
        aestdtc = next(r for r in updated if r.content == "AESTDTC")
        assert aestdtc.domain == "AE"

    def test_t1_14_new_row_with_empty_id_creates_annotation(self, tmp_path):
        """T1.14: CSV import with new rows (empty id) creates new annotations."""
        import pandas as pd
        records = [make_annotation(content="BRTHDTC")]
        path = tmp_path / "annotations.csv"
        export_annotations_csv(records, path)

        # Add new row with empty id
        df = pd.read_csv(path)
        new_row = df.iloc[0].copy()
        new_row["id"] = ""
        new_row["content"] = "NEW_FIELD"
        df = pd.concat([df, new_row.to_frame().T], ignore_index=True)
        df.to_csv(path, index=False)

        updated, flagged = import_annotations_csv(path, records)
        contents = [r.content for r in updated]
        assert "NEW_FIELD" in contents
        # New record should have a valid UUID
        new_record = next(r for r in updated if r.content == "NEW_FIELD")
        import re
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            re.IGNORECASE,
        )
        assert uuid_pattern.match(new_record.id)

    def test_t1_15_missing_row_flagged_not_deleted(self, tmp_path):
        """T1.15: CSV import flags missing rows for confirmation, not auto-delete."""
        import pandas as pd
        records = [
            make_annotation(content="BRTHDTC"),
            make_annotation(content="AESTDTC"),
        ]
        path = tmp_path / "annotations.csv"
        export_annotations_csv(records, path)

        # Remove AESTDTC row from CSV
        df = pd.read_csv(path)
        df = df[df["content"] != "AESTDTC"]
        df.to_csv(path, index=False)

        updated, flagged = import_annotations_csv(path, records)

        # AESTDTC should be in flagged list, NOT removed from updated
        aestdtc_ids = [r.id for r in records if r.content == "AESTDTC"]
        assert any(fid in flagged for fid in aestdtc_ids)
        # Updated list should still contain AESTDTC (not auto-deleted)
        updated_contents = [r.content for r in updated]
        assert "AESTDTC" in updated_contents

    def test_import_returns_tuple(self, tmp_path):
        """import_annotations_csv returns (list[AnnotationRecord], list[str])."""
        records = [make_annotation()]
        path = tmp_path / "annotations.csv"
        export_annotations_csv(records, path)
        result = import_annotations_csv(path, records)
        assert isinstance(result, tuple)
        assert len(result) == 2
        updated, flagged = result
        assert isinstance(updated, list)
        assert isinstance(flagged, list)


def test_import_matches_csv_migrates_legacy_status(tmp_path):
    """Migration shim converts 'rejected' -> 're-pairing' and 'modified' -> 'approved'."""
    import csv
    from src.models import MatchRecord
    from src.csv_handler import import_matches_csv

    csv_path = tmp_path / "matches.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "annotation_id", "field_id", "match_type", "confidence",
            "target_rect", "target_page", "status", "user_notes", "placement_adjusted"
        ])
        writer.writeheader()
        writer.writerow({
            "annotation_id": "a1", "field_id": "f1", "match_type": "fuzzy",
            "confidence": "0.8", "target_rect": "[0, 0, 1, 1]", "target_page": "1",
            "status": "rejected", "user_notes": "", "placement_adjusted": "False",
        })
        writer.writerow({
            "annotation_id": "a2", "field_id": "f2", "match_type": "exact",
            "confidence": "1.0", "target_rect": "[0, 0, 1, 1]", "target_page": "1",
            "status": "modified", "user_notes": "", "placement_adjusted": "False",
        })

    existing = [
        MatchRecord(annotation_id="a1", field_id="f1", match_type="fuzzy",
                    confidence=0.8, target_rect=[0, 0, 1, 1]),
        MatchRecord(annotation_id="a2", field_id="f2", match_type="exact",
                    confidence=1.0, target_rect=[0, 0, 1, 1]),
    ]
    updated, flagged = import_matches_csv(csv_path, existing)

    assert flagged == []
    a1 = next(m for m in updated if m.annotation_id == "a1")
    a2 = next(m for m in updated if m.annotation_id == "a2")
    assert a1.status == "re-pairing", f"Expected 're-pairing', got {a1.status!r}"
    assert a2.status == "approved", f"Expected 'approved', got {a2.status!r}"
