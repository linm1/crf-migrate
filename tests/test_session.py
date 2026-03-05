"""Tests for src/session.py -- session workspace management and audit logging."""
import json
import uuid
import pytest
from pathlib import Path
from datetime import datetime

from src.models import AnnotationRecord, StyleInfo
from src.session import Session


def make_annotation(**kwargs) -> AnnotationRecord:
    defaults = {
        "id": str(uuid.uuid4()),
        "page": 1,
        "content": "BRTHDTC",
        "domain": "DM",
        "category": "sdtm_mapping",
        "matched_rule": "Rule 9: fallback",
        "rect": [100.0, 200.0, 300.0, 220.0],
    }
    defaults.update(kwargs)
    return AnnotationRecord(**defaults)


class TestSessionCreation:
    def test_creates_workspace_directory(self, tmp_path):
        """Session creates a workspace directory on init."""
        session = Session(tmp_path)
        assert session.workspace.exists()
        assert session.workspace.is_dir()

    def test_workspace_name_starts_with_session(self, tmp_path):
        """Workspace directory is named session_<timestamp>."""
        session = Session(tmp_path)
        assert session.workspace.name.startswith("session_")

    def test_two_sessions_have_different_workspaces(self, tmp_path):
        """Two sessions get distinct workspace directories."""
        import time
        session1 = Session(tmp_path)
        time.sleep(1.1)  # ensure different timestamp
        session2 = Session(tmp_path)
        assert session1.workspace != session2.workspace


class TestSaveLoadAnnotations:
    def test_save_annotations_creates_json(self, tmp_path):
        """save_annotations writes annotations.json to workspace."""
        session = Session(tmp_path)
        records = [make_annotation(content="BRTHDTC"), make_annotation(content="VSTESTCD")]
        path = session.save_annotations(records)
        assert path.exists()
        assert path.name == "annotations.json"

    def test_save_annotations_valid_json(self, tmp_path):
        """annotations.json contains valid JSON."""
        session = Session(tmp_path)
        records = [make_annotation()]
        session.save_annotations(records)
        data = json.loads((session.workspace / "annotations.json").read_text())
        assert isinstance(data, list)
        assert len(data) == 1

    def test_load_annotations_round_trip(self, tmp_path):
        """save then load preserves all record data."""
        session = Session(tmp_path)
        original = [
            make_annotation(content="BRTHDTC", form_name="DEMOGRAPHICS"),
            make_annotation(content="DMDTC", visit="Screening"),
        ]
        session.save_annotations(original)
        loaded = session.load_annotations()
        assert len(loaded) == 2
        assert loaded[0].content == "BRTHDTC"
        assert loaded[0].form_name == "DEMOGRAPHICS"
        assert loaded[1].visit == "Screening"

    def test_load_annotations_returns_annotation_records(self, tmp_path):
        """Loaded items are AnnotationRecord instances."""
        session = Session(tmp_path)
        session.save_annotations([make_annotation()])
        loaded = session.load_annotations()
        assert all(isinstance(r, AnnotationRecord) for r in loaded)

    def test_load_annotations_missing_file_raises(self, tmp_path):
        """load_annotations raises FileNotFoundError if no file exists."""
        session = Session(tmp_path)
        with pytest.raises(FileNotFoundError):
            session.load_annotations()


class TestAuditLog:
    def test_log_action_creates_audit_log(self, tmp_path):
        """log_action creates audit_log.json."""
        session = Session(tmp_path)
        session.log_action("test_action", {"key": "value"})
        log_path = session.workspace / "audit_log.json"
        assert log_path.exists()

    def test_log_action_appends_entries(self, tmp_path):
        """Multiple log_action calls append entries."""
        session = Session(tmp_path)
        session.log_action("action_1", {"data": 1})
        session.log_action("action_2", {"data": 2})
        log = json.loads((session.workspace / "audit_log.json").read_text())
        assert len(log) == 2
        assert log[0]["action"] == "action_1"
        assert log[1]["action"] == "action_2"

    def test_log_action_includes_timestamp(self, tmp_path):
        """Each log entry has a timestamp field."""
        session = Session(tmp_path)
        session.log_action("save", {"records": 5})
        log = json.loads((session.workspace / "audit_log.json").read_text())
        assert "timestamp" in log[0]

    def test_log_action_includes_details(self, tmp_path):
        """Log entry includes all provided detail fields."""
        session = Session(tmp_path)
        session.log_action("edit", {"field": "content", "old": "A", "new": "B"})
        log = json.loads((session.workspace / "audit_log.json").read_text())
        assert log[0]["field"] == "content"
        assert log[0]["old"] == "A"
        assert log[0]["new"] == "B"


class TestCopyProfile:
    def test_copy_profile_creates_file(self, tmp_path):
        """copy_profile copies YAML into workspace as active_profile.yaml."""
        session = Session(tmp_path)
        src_profile = tmp_path / "test_profile.yaml"
        src_profile.write_text("meta:\n  name: Test\n")
        session.copy_profile(src_profile)
        assert (session.workspace / "active_profile.yaml").exists()

    def test_copy_profile_content_preserved(self, tmp_path):
        """Copied profile content matches source."""
        session = Session(tmp_path)
        src_profile = tmp_path / "test_profile.yaml"
        src_profile.write_text("meta:\n  name: Test\n  version: '1.0'\n")
        session.copy_profile(src_profile)
        content = (session.workspace / "active_profile.yaml").read_text()
        assert "name: Test" in content
