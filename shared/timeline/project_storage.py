from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from ..contracts.video_timeline import (
    DEFAULT_PROJECT_NAME,
    PROJECT_STORAGE_SCHEMA_VERSION,
)
from .global_settings import resolve_global_asset_root


PROJECT_ID_PREFIX = "proj_"


class ProjectStorageError(ValueError):
    """Raised when project storage cannot be resolved safely."""


def create_default_project_identity() -> dict[str, str]:
    project_id = generate_project_id()
    return {
        "project_id": project_id,
        "name": DEFAULT_PROJECT_NAME,
    }


def create_default_project_storage(
    *,
    project_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    project_id = _safe_project_id(project_id) or generate_project_id()
    name = _safe_name(name) or DEFAULT_PROJECT_NAME
    return {
        "schema_version": PROJECT_STORAGE_SCHEMA_VERSION,
        "project_directory_name": project_directory_name(name, project_id),
    }


def normalize_project_identity_and_storage(project: dict[str, Any]) -> None:
    identity = project.get("identity")
    if not isinstance(identity, dict):
        identity = {}
    project_id = _safe_project_id(identity.get("project_id")) or generate_project_id()
    name = _safe_name(identity.get("name")) or DEFAULT_PROJECT_NAME
    project["identity"] = {
        "project_id": project_id,
        "name": name,
    }

    storage = project.get("storage")
    if not isinstance(storage, dict):
        storage = {}
    directory_name = _safe_path_part(storage.get("project_directory_name"))
    if not directory_name or project_id.lower() not in directory_name.lower():
        directory_name = project_directory_name(name, project_id)
    project["storage"] = {
        "schema_version": PROJECT_STORAGE_SCHEMA_VERSION,
        "project_directory_name": directory_name,
    }


def normalized_project(project: dict[str, Any] | None) -> dict[str, Any]:
    copy = deepcopy(project if isinstance(project, dict) else {})
    normalize_project_identity_and_storage(copy)
    return copy


def generate_project_id() -> str:
    return f"{PROJECT_ID_PREFIX}{uuid4().hex[:12]}"


def project_directory_name(name: str, project_id: str) -> str:
    base = _safe_path_part(name).lower() or "project"
    project_id = _safe_project_id(project_id) or generate_project_id()
    return _safe_path_part(f"{base}_{project_id}").lower()


def resolve_project_asset_root(project: dict[str, Any], *, create: bool = True) -> Path:
    return resolve_global_asset_root(create=create)


def resolve_project_directory(project: dict[str, Any], *, create: bool = True) -> Path:
    project = normalized_project(project)
    root = resolve_project_asset_root(project, create=create)
    directory = (root / project["storage"]["project_directory_name"]).resolve()
    _ensure_inside(root, directory)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def resolve_project_take_directory(
    project: dict[str, Any],
    shot_id: str,
    *,
    create: bool = True,
) -> Path:
    project_directory = resolve_project_directory(project, create=create)
    shot_directory = (project_directory / "takes" / (_safe_path_part(shot_id) or "shot")).resolve()
    _ensure_inside(project_directory, shot_directory)
    if create:
        shot_directory.mkdir(parents=True, exist_ok=True)
    return shot_directory


def resolved_project_storage_summary(project: dict[str, Any]) -> dict[str, Any]:
    normalized = normalized_project(project)
    root = resolve_project_asset_root(normalized, create=False)
    directory = resolve_project_directory(normalized, create=False)
    return {
        "project_id": normalized["identity"]["project_id"],
        "project_name": normalized["identity"]["name"],
        "asset_root_directory": str(root),
        "project_directory": str(directory),
        "project_directory_name": normalized["storage"]["project_directory_name"],
    }


def _ensure_inside(root: Path, candidate: Path) -> None:
    if root != candidate and root not in candidate.parents:
        raise ProjectStorageError("PROJECT_STORAGE_PATH_OUTSIDE_ROOT: Project storage path escaped the configured root.")


def _safe_project_id(value: Any) -> str:
    text = _safe_string(value)
    if re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", text):
        return text
    return ""


def _safe_name(value: Any) -> str:
    text = _safe_string(value)
    return text[:120]


def _safe_path_part(value: Any) -> str:
    text = _safe_string(value)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")
    return text[:96]


def _safe_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
