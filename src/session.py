"""Session workspace management and audit logging."""
import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from src.models import AnnotationRecord, FieldRecord, MatchRecord


class Session:
    """Manages a per-session workspace directory and audit log."""

    def __init__(self, base_dir: Path) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.workspace = base_dir / f"session_{timestamp}"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def save_annotations(self, records: list[AnnotationRecord]) -> Path:
        """Serialize and write annotation records to annotations.json."""
        path = self.workspace / "annotations.json"
        data = [r.model_dump() for r in records]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def load_annotations(self) -> list[AnnotationRecord]:
        """Load and deserialize annotation records from annotations.json."""
        path = self.workspace / "annotations.json"
        if not path.exists():
            raise FileNotFoundError(
                f"annotations.json not found in {self.workspace}"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return [AnnotationRecord.model_validate(d) for d in data]

    def save_fields(self, records: list[FieldRecord]) -> Path:
        """Serialize and write field records to fields.json."""
        path = self.workspace / "fields.json"
        data = [r.model_dump() for r in records]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def load_fields(self) -> list[FieldRecord]:
        """Load and deserialize field records from fields.json."""
        path = self.workspace / "fields.json"
        if not path.exists():
            raise FileNotFoundError(
                f"fields.json not found in {self.workspace}"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return [FieldRecord.model_validate(d) for d in data]

    def save_matches(self, records: list[MatchRecord]) -> Path:
        """Serialize and write match records to matches.json."""
        path = self.workspace / "matches.json"
        data = [r.model_dump() for r in records]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def load_matches(self) -> list[MatchRecord]:
        """Load and deserialize match records from matches.json."""
        path = self.workspace / "matches.json"
        if not path.exists():
            raise FileNotFoundError(
                f"matches.json not found in {self.workspace}"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return [MatchRecord.model_validate(d) for d in data]

    def save_qc_report(self, report: dict) -> Path:
        """Serialize and write QC report to qc_report.json."""
        path = self.workspace / "qc_report.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return path

    def load_qc_report(self) -> dict:
        """Load QC report from qc_report.json."""
        path = self.workspace / "qc_report.json"
        if not path.exists():
            raise FileNotFoundError(f"qc_report.json not found in {self.workspace}")
        return json.loads(path.read_text(encoding="utf-8"))

    def log_action(self, action: str, details: dict) -> None:
        """Append a timestamped entry to audit_log.json."""
        log_path = self.workspace / "audit_log.json"
        existing: list[dict] = []
        if log_path.exists():
            existing = json.loads(log_path.read_text(encoding="utf-8"))

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            **copy.deepcopy(details),
        }
        log_path.write_text(
            json.dumps(existing + [entry], indent=2), encoding="utf-8"
        )

    def copy_profile(self, profile_path: Path) -> None:
        """Copy the active profile YAML into the workspace for reproducibility."""
        shutil.copy2(profile_path, self.workspace / "active_profile.yaml")

    @classmethod
    def open(cls, workspace: Path) -> "Session":
        """Attach to an existing workspace directory without creating a new one."""
        instance = cls.__new__(cls)
        instance.workspace = workspace
        return instance

    @classmethod
    def list_sessions(cls, base_dir: Path) -> list[str]:
        """Return all session_* directory names under base_dir, newest-first."""
        if not base_dir.is_dir():
            return []
        return sorted(
            [d.name for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("session_")],
            reverse=True,
        )
