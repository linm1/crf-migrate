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


class TestSessionOpen:
    def test_open_attaches_to_existing_workspace(self, tmp_path):
        """Session.open() attaches to an existing directory without creating anything new."""
        existing = tmp_path / "session_20260101_120000"
        existing.mkdir()
        before = list(tmp_path.iterdir())
        sess = Session.open(existing)
        after = list(tmp_path.iterdir())
        assert sess.workspace == existing
        assert before == after  # no new directories created

    def test_open_does_not_require_annotations(self, tmp_path):
        """Session.open() works on an empty directory."""
        existing = tmp_path / "session_20260101_120000"
        existing.mkdir()
        sess = Session.open(existing)
        assert sess.workspace == existing


class TestListSessions:
    def test_returns_session_dirs_newest_first(self, tmp_path):
        """list_sessions returns session_* dirs sorted newest-first."""
        (tmp_path / "session_20260101_090000").mkdir()
        (tmp_path / "session_20260301_120000").mkdir()
        (tmp_path / "session_20260201_060000").mkdir()
        result = Session.list_sessions(tmp_path)
        assert result == [
            "session_20260301_120000",
            "session_20260201_060000",
            "session_20260101_090000",
        ]

    def test_ignores_non_session_dirs(self, tmp_path):
        """list_sessions ignores non-directory entries and dirs not starting with 'session_'."""
        (tmp_path / "session_20260101_090000").mkdir()
        (tmp_path / "some_other_dir").mkdir()
        (tmp_path / "temp_work").mkdir()
        # A file that looks like a session directory name — must be excluded
        (tmp_path / "session_20260201_000000").write_text("not a dir")
        result = Session.list_sessions(tmp_path)
        assert result == ["session_20260101_090000"]

    def test_returns_empty_when_base_dir_is_empty(self, tmp_path):
        """list_sessions returns [] when base_dir exists but contains no session dirs."""
        result = Session.list_sessions(tmp_path)
        assert result == []

    def test_returns_empty_when_base_missing(self, tmp_path):
        """list_sessions returns [] when base_dir does not exist."""
        missing = tmp_path / "nonexistent"
        result = Session.list_sessions(missing)
        assert result == []


class TestLatestSession:
    def test_returns_most_recent_with_annotations(self, tmp_path):
        """latest() returns the newest session that has annotations.json."""
        old = tmp_path / "session_20260101_090000"
        old.mkdir()
        (old / "annotations.json").write_text("[]")

        newer = tmp_path / "session_20260301_120000"
        newer.mkdir()
        (newer / "annotations.json").write_text("[]")

        sess = Session.latest(tmp_path)
        assert sess is not None
        assert sess.workspace == newer

    def test_skips_sessions_without_annotations(self, tmp_path):
        """latest() skips empty session dirs and returns the newest that has annotations.json."""
        empty = tmp_path / "session_20260401_080000"
        empty.mkdir()  # no annotations.json

        with_work = tmp_path / "session_20260301_120000"
        with_work.mkdir()
        (with_work / "annotations.json").write_text("[]")

        sess = Session.latest(tmp_path)
        assert sess is not None
        assert sess.workspace == with_work

    def test_returns_none_when_no_qualified_session(self, tmp_path):
        """latest() returns None when no session has annotations.json."""
        (tmp_path / "session_20260101_090000").mkdir()  # empty
        (tmp_path / "session_20260201_060000").mkdir()  # empty
        sess = Session.latest(tmp_path)
        assert sess is None


class TestRenameDelete:
    def test_rename_changes_directory_name(self, tmp_path):
        """Renamed workspace exists at the new path and old path is gone."""
        session = Session(tmp_path)
        old_path = session.workspace
        new_path = Session.rename(old_path, "session_renamed_001")
        assert new_path.exists()
        assert not old_path.exists()
        assert new_path.name == "session_renamed_001"

    def test_rename_preserves_artifacts(self, tmp_path):
        """Files inside the workspace survive renaming."""
        session = Session(tmp_path)
        (session.workspace / "annotations.json").write_text("[]")
        new_path = Session.rename(session.workspace, "session_renamed_artifacts")
        assert (new_path / "annotations.json").exists()

    def test_rename_session_remains_discoverable(self, tmp_path):
        """A session renamed with a session_ prefix is still found by list_sessions."""
        session = Session(tmp_path)
        Session.rename(session.workspace, "session_renamed_disc")
        sessions = Session.list_sessions(tmp_path)
        assert "session_renamed_disc" in sessions

    def test_delete_removes_directory(self, tmp_path):
        """Deleted workspace directory no longer exists."""
        session = Session(tmp_path)
        workspace_path = session.workspace
        Session.delete(workspace_path)
        assert not workspace_path.exists()

    def test_delete_removes_contents(self, tmp_path):
        """Delete removes workspace directory and all its contents."""
        session = Session(tmp_path)
        (session.workspace / "annotations.json").write_text("[]")
        workspace_path = session.workspace
        Session.delete(workspace_path)
        assert not workspace_path.exists()
