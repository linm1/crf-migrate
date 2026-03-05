"""YAML profile loading, validation, and inheritance resolution."""
from pathlib import Path
import copy
import yaml
from src.profile_models import Profile


def list_profiles(profiles_dir: Path) -> list[str]:
    """Return list of profile names (stem of .yaml files) in profiles_dir."""
    return [p.stem for p in sorted(profiles_dir.glob("*.yaml"))]


def _load_raw(path: Path) -> dict:
    """Load raw YAML dict from file."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base; returns new dict (immutable)."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, dict):
            # List field with _append or _replace directive
            if "_append" in value:
                result[key] = result[key] + list(value["_append"])
            elif "_replace" in value:
                result[key] = list(value["_replace"])
            else:
                result[key] = value
        else:
            result[key] = copy.deepcopy(value)
    return result


def _resolve_inheritance(raw: dict, profiles_dir: Path, visited: set[str] | None = None) -> dict:
    """Recursively resolve parent profile inheritance."""
    if visited is None:
        visited = set()

    parent_name = raw.get("meta", {}).get("parent")
    if parent_name is None:
        return raw

    if parent_name in visited:
        raise ValueError(f"Circular profile inheritance detected: {parent_name}")

    visited = visited | {parent_name}
    parent_path = profiles_dir / f"{parent_name}.yaml"
    if not parent_path.exists():
        raise FileNotFoundError(f"Parent profile not found: {parent_path}")

    parent_raw = _load_raw(parent_path)
    parent_resolved = _resolve_inheritance(parent_raw, profiles_dir, visited)

    return _deep_merge(parent_resolved, raw)


def load_profile(path: Path, profiles_dir: Path | None = None) -> Profile:
    """Load, validate, and return a Profile from a YAML file.

    profiles_dir is used for resolving parent inheritance.
    Defaults to the parent directory of path.
    """
    if profiles_dir is None:
        profiles_dir = path.parent

    raw = _load_raw(path)
    resolved = _resolve_inheritance(raw, profiles_dir)
    return Profile.model_validate(resolved)
