"""Local prompt optimization helpers for Helto Director."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import gc
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import struct
import threading
import time
from typing import Any
from urllib.parse import unquote, urlparse
import uuid

from PIL import Image, ImageOps

try:
    import folder_paths
except Exception:  # noqa: BLE001 - tests can import this module outside ComfyUI.
    folder_paths = None

try:
    from .media_browser import resolve_browser_media_path
except Exception:  # noqa: BLE001 - direct unit-test imports.
    resolve_browser_media_path = None


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
QWEN_DEPS = ("transformers", "huggingface_hub", "accelerate", "qwen_vl_utils")
FLORENCE_DEPS = ("transformers", "huggingface_hub", "accelerate", "torchvision")
GEMMA_SAFETENSORS_DEPS = ("transformers", "huggingface_hub", "accelerate")
LLAMA_CPP_DEPS = ("llama_cpp", "huggingface_hub")
GEMMA4_E4B_FP8_URL = (
    "https://huggingface.co/Comfy-Org/gemma-4/blob/main/"
    "text_encoders/gemma4_e4b_it_fp8_scaled.safetensors"
)
GEMMA4_E4B_UNCENSORED_Q8_GGUF_URL = (
    "https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive/blob/main/"
    "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_K_P.gguf"
)
GEMMA4_E4B_UNCENSORED_MMPROJ_URL = (
    "https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive/blob/main/"
    "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf"
)
CONFIG_DIR = PACKAGE_ROOT / "config"
ASSETS_DIR = PACKAGE_ROOT / "assets"
SETTINGS_FILE = CONFIG_DIR / "ltx_prompt_optimizer_settings.json"
TIMING_FILE = CONFIG_DIR / "ltx_prompt_optimizer_timing.json"
REFERENCE_CAPTION_PROMPT_FILE = ASSETS_DIR / "prompts" / "ltx_reference_caption_prompt.txt"
OPTIMIZER_IMAGE_MAX_SIDE = 768
DEFAULT_OPTIMIZER_PROMPT_TEMPLATE = (
    "You are optimizing a local prompt for LTX Director Prompt Relay. "
    "Generate one {rating} video prompt for segment {segment_index} of {segment_total}. "
    "{text_segment_instruction} "
    "{visual_context} "
    "Use provided images only as motion references, not as caption targets. "
    "Infer pose, action, motion direction, expression changes, camera movement, temporal continuation, "
    "and visible or implied sound cues. "
    "Do not describe static image facts like setting, clothing, lighting, object appearance, composition, "
    "or background unless the user explicitly asks or a tiny actor reference is required for clarity. "
    "Write one concise present-tense LTX segment prompt with literal chronological motion. "
    "Do not output bullets, labels, quotes, markdown, negative prompts, or explanations. "
    "Avoid repeated global context and static visual inventory. "
    "User direction to preserve: {direction}. "
    "{continuity}"
)
REFERENCE_CAPTION_PROMPT_FALLBACK = (
    "You are writing a character reference caption for LTX Director identity conditioning.\n\n"
    "Use the supplied reference image as the identity source. Write exactly one concise descriptive caption "
    "for the referenced subject. The caption must help a video model preserve likeness across prompts.\n\n"
    "If the user description is empty, describe only stable visual identity details: subject type, apparent "
    "gender or age category when visible, face/head features, hair/fur/skin/markings, body build, clothing, "
    "accessories, and other distinctive appearance cues.\n\n"
    "If the user description requests different clothes or changed features, follow the user description for "
    "those requested changes while preserving the subject's stable likeness cues from the image.\n\n"
    "Do not describe actions, poses, gestures, camera movement, framing, background, lighting, mood, scene "
    "events, or story. Do not mention that this is a reference image. Do not output bullets, labels, quotes, "
    "markdown, negative prompts, or explanations.\n\n"
    "User description to respect: {direction}"
)


def load_reference_caption_prompt_template(path: str | os.PathLike[str] | None = None) -> str:
    prompt_path = Path(path) if path is not None else REFERENCE_CAPTION_PROMPT_FILE
    try:
        text = prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text or REFERENCE_CAPTION_PROMPT_FALLBACK


DEFAULT_REFERENCE_CAPTION_PROMPT_TEMPLATE = load_reference_caption_prompt_template()


@dataclass(frozen=True)
class OptimizerModelSpec:
    alias: str
    repo_id: str
    backend: str
    model_subdir: str
    dependencies: tuple[str, ...] = ()
    file_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class OptimizerModelFile:
    url: str
    repo_id: str
    revision: str
    filename: str


MODEL_REGISTRY: dict[str, OptimizerModelSpec] = {
    "qwen3_vl_8b_quality": OptimizerModelSpec(
        "qwen3_vl_8b_quality",
        "Qwen/Qwen3-VL-8B-Instruct",
        "qwen",
        "VLM",
        QWEN_DEPS,
    ),
    "qwen3_vl_4b_fast": OptimizerModelSpec(
        "qwen3_vl_4b_fast",
        "Qwen/Qwen3-VL-4B-Instruct",
        "qwen",
        "VLM",
        QWEN_DEPS,
    ),
    "qwen3_vl_4b_unredacted": OptimizerModelSpec(
        "qwen3_vl_4b_unredacted",
        "prithivMLmods/Qwen3-VL-4B-Instruct-abliterated-v1",
        "qwen",
        "VLM",
        QWEN_DEPS,
    ),
    "qwen3_vl_8b_nsfw_caption": OptimizerModelSpec(
        "qwen3_vl_8b_nsfw_caption",
        "monkeyslikebananas/Qwen3-VL-8B-NSFW-Caption-V4.5",
        "qwen",
        "VLM",
        QWEN_DEPS,
    ),
    "qwen2_5_vl_7b_abliterated_legacy": OptimizerModelSpec(
        "qwen2_5_vl_7b_abliterated_legacy",
        "prithivMLmods/Qwen2.5-VL-7B-Abliterated-Caption-it",
        "qwen",
        "VLM",
        QWEN_DEPS,
    ),
    "florence2_fast_caption": OptimizerModelSpec(
        "florence2_fast_caption",
        "MiaoshouAI/Florence-2-base-PromptGen-v2.0",
        "florence",
        "LLM",
        FLORENCE_DEPS,
    ),
    "gemma4_e4b_it_fp8_scaled": OptimizerModelSpec(
        "gemma4_e4b_it_fp8_scaled",
        "Comfy-Org/gemma-4",
        "gemma_safetensors",
        "text_encoders",
        GEMMA_SAFETENSORS_DEPS,
        (GEMMA4_E4B_FP8_URL,),
    ),
    "gemma4_e4b_uncensored_gguf_q8": OptimizerModelSpec(
        "gemma4_e4b_uncensored_gguf_q8",
        "HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive",
        "llama_cpp_vision",
        "VLM/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive",
        LLAMA_CPP_DEPS,
        (GEMMA4_E4B_UNCENSORED_Q8_GGUF_URL, GEMMA4_E4B_UNCENSORED_MMPROJ_URL),
    ),
    "fallback_text_backend": OptimizerModelSpec(
        "fallback_text_backend",
        "local/fallback-text-backend",
        "fallback",
        "",
        (),
    ),
}

_LOADED_MODELS: dict[str, dict[str, Any]] = {}
_OPTIMIZER_JOBS: dict[str, dict[str, Any]] = {}
_OPTIMIZER_JOBS_LOCK = threading.Lock()
_TIMING_LOCK = threading.Lock()
CUT_SCENE_RE = re.compile(r"\b(cut scene|hard cut|scene cut|new scene|transition)\b", re.I)


class PromptOptimizerError(RuntimeError):
    """Readable optimizer error surfaced through the UI."""


def _noop_status(_message: str, _current: int | None = None, _total: int | None = None) -> None:
    return None


def _emit_status(
    status_cb: Any,
    message: str,
    current: int | None = None,
    total: int | None = None,
    progress: dict[str, Any] | None = None,
) -> None:
    try:
        status_cb(message, current, total, progress)
    except TypeError:
        status_cb(message, current, total)


def _progress(
    current: int | None = None,
    total: int | None = None,
    phase: str = "idle",
    percent: float | None = None,
    eta_seconds: float | None = None,
    elapsed_seconds: float | None = None,
    prompt_elapsed_seconds: float | None = None,
    estimated: bool = False,
    download_current_bytes: int | None = None,
    download_total_bytes: int | None = None,
    download_file: str | None = None,
    download_file_index: int | None = None,
    download_file_total: int | None = None,
) -> dict[str, Any]:
    payload = {
        "current": current,
        "total": total,
        "phase": phase,
        "percent": percent,
        "eta_seconds": eta_seconds,
        "elapsed_seconds": elapsed_seconds,
        "prompt_elapsed_seconds": prompt_elapsed_seconds,
        "estimated": estimated,
    }
    if download_current_bytes is not None:
        payload["download_current_bytes"] = download_current_bytes
    if download_total_bytes is not None:
        payload["download_total_bytes"] = download_total_bytes
    if download_file:
        payload["download_file"] = download_file
    if download_file_index is not None:
        payload["download_file_index"] = download_file_index
    if download_file_total is not None:
        payload["download_file_total"] = download_file_total
    return payload


def settings_path(base_dir: str | os.PathLike[str] | None = None) -> Path:
    return Path(base_dir) / SETTINGS_FILE.name if base_dir is not None else SETTINGS_FILE


def timing_path(base_dir: str | os.PathLike[str] | None = None) -> Path:
    return Path(base_dir) / TIMING_FILE.name if base_dir is not None else TIMING_FILE


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    tmp_path.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_optimizer_settings(base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = settings_path(base_dir)
    if not path.exists():
        return {"version": 1, "hf_token": "", "prompt_template": ""}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {"version": 1, "hf_token": "", "prompt_template": ""}
    return {
        "version": 1,
        "hf_token": str(payload.get("hf_token") or ""),
        "prompt_template": str(payload.get("prompt_template") or ""),
    }


def _save_optimizer_settings(settings: dict[str, Any], base_dir: str | os.PathLike[str] | None = None) -> None:
    payload = {
        "version": 1,
        "hf_token": str(settings.get("hf_token") or ""),
        "prompt_template": str(settings.get("prompt_template") or ""),
    }
    if not payload["hf_token"] and not payload["prompt_template"]:
        settings_path(base_dir).unlink(missing_ok=True)
        return
    _write_private_json(settings_path(base_dir), payload)


def save_hf_token(token: str, base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    token = str(token or "").strip()
    settings = load_optimizer_settings(base_dir)
    settings["hf_token"] = token
    if not token:
        clear_hf_token(base_dir)
        return get_optimizer_settings_status(base_dir)
    _save_optimizer_settings(settings, base_dir)
    return get_optimizer_settings_status(base_dir)


def clear_hf_token(base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    settings = load_optimizer_settings(base_dir)
    settings["hf_token"] = ""
    _save_optimizer_settings(settings, base_dir)
    return get_optimizer_settings_status(base_dir)


def configured_prompt_template(base_dir: str | os.PathLike[str] | None = None) -> str:
    return str(load_optimizer_settings(base_dir).get("prompt_template") or "").strip()


def active_prompt_template(base_dir: str | os.PathLike[str] | None = None) -> str:
    return configured_prompt_template(base_dir) or DEFAULT_OPTIMIZER_PROMPT_TEMPLATE


def save_prompt_template(template: str, base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    template = str(template or "").strip()
    settings = load_optimizer_settings(base_dir)
    settings["prompt_template"] = template
    _save_optimizer_settings(settings, base_dir)
    return get_optimizer_settings_status(base_dir)


def reset_prompt_template(base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    settings = load_optimizer_settings(base_dir)
    settings["prompt_template"] = ""
    _save_optimizer_settings(settings, base_dir)
    return get_optimizer_settings_status(base_dir)


def env_hf_token() -> str:
    return str(os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN") or "").strip()


def configured_hf_token(base_dir: str | os.PathLike[str] | None = None) -> str:
    return str(load_optimizer_settings(base_dir).get("hf_token") or "").strip()


def hf_auth_token(base_dir: str | os.PathLike[str] | None = None) -> str | None:
    return configured_hf_token(base_dir) or env_hf_token() or None


def get_optimizer_settings_status(base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    configured = bool(configured_hf_token(base_dir))
    env_available = bool(env_hf_token())
    if configured:
        auth_source = "configured"
    elif env_available:
        auth_source = "environment"
    else:
        auth_source = "anonymous"
    return {
        "ok": True,
        "configPath": str(settings_path(base_dir)),
        "tokenConfigured": configured,
        "envTokenAvailable": env_available,
        "authSource": auth_source,
        "promptTemplate": active_prompt_template(base_dir),
        "defaultPromptTemplate": DEFAULT_OPTIMIZER_PROMPT_TEMPLATE,
        "promptTemplateConfigured": bool(configured_prompt_template(base_dir)),
    }


def model_timing_key(spec: OptimizerModelSpec) -> str:
    return f"{spec.alias}:{spec.backend}"


def load_optimizer_timing(base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = timing_path(base_dir)
    if not path.exists():
        return {"version": 1, "profiles": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {"version": 1, "profiles": {}}
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    clean_profiles = {}
    for key, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        try:
            average = float(profile.get("average_seconds") or 0)
            count = int(profile.get("sample_count") or 0)
            last = float(profile.get("last_seconds") or 0)
            updated = float(profile.get("updated_at") or 0)
        except (TypeError, ValueError):
            continue
        if average <= 0 or count <= 0:
            continue
        clean_profiles[str(key)] = {
            "average_seconds": average,
            "sample_count": count,
            "last_seconds": max(0.0, last),
            "updated_at": max(0.0, updated),
        }
    return {"version": 1, "profiles": clean_profiles}


def timing_profile_average(model_key: str, base_dir: str | os.PathLike[str] | None = None) -> float | None:
    profile = load_optimizer_timing(base_dir).get("profiles", {}).get(model_key)
    if not isinstance(profile, dict):
        return None
    average = float(profile.get("average_seconds") or 0)
    return average if average > 0 else None


def record_prompt_timing(
    spec: OptimizerModelSpec,
    duration_seconds: float,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    duration = max(0.001, float(duration_seconds or 0))
    key = model_timing_key(spec)
    with _TIMING_LOCK:
        payload = load_optimizer_timing(base_dir)
        profiles = payload.setdefault("profiles", {})
        previous = profiles.get(key) if isinstance(profiles.get(key), dict) else {}
        count = int(previous.get("sample_count") or 0)
        average = float(previous.get("average_seconds") or 0)
        new_count = count + 1
        new_average = duration if count <= 0 or average <= 0 else average + ((duration - average) / new_count)
        profiles[key] = {
            "average_seconds": new_average,
            "sample_count": new_count,
            "last_seconds": duration,
            "updated_at": time.time(),
        }
        _write_private_json(timing_path(base_dir), payload)
        return profiles[key]


def _models_dir() -> Path:
    if folder_paths is not None and getattr(folder_paths, "models_dir", None):
        return Path(folder_paths.models_dir)
    return Path.cwd() / "models"


def parse_hf_file_url(url: str) -> OptimizerModelFile:
    parsed = urlparse(str(url or "").strip())
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if parsed.netloc != "huggingface.co" or len(parts) < 5 or parts[2] != "blob":
        raise PromptOptimizerError(f"Unsupported Hugging Face file URL: {url}")
    repo_id = f"{parts[0]}/{parts[1]}"
    revision = parts[3]
    filename = "/".join(parts[4:])
    if not filename:
        raise PromptOptimizerError(f"Hugging Face file URL is missing a filename: {url}")
    return OptimizerModelFile(url=url, repo_id=repo_id, revision=revision, filename=filename)


def model_files_for(spec: OptimizerModelSpec) -> list[OptimizerModelFile]:
    return [parse_hf_file_url(url) for url in spec.file_urls]


def model_file_path_for(spec: OptimizerModelSpec, model_file: OptimizerModelFile) -> Path:
    if spec.backend == "gemma_safetensors":
        return _models_dir() / model_file.filename
    base = _models_dir() / spec.model_subdir
    return base / model_file.filename


def model_file_paths_for(spec: OptimizerModelSpec) -> list[Path]:
    return [model_file_path_for(spec, model_file) for model_file in model_files_for(spec)]


def model_path_for(spec: OptimizerModelSpec) -> Path | None:
    if spec.backend == "fallback":
        return None
    if spec.file_urls:
        paths = model_file_paths_for(spec)
        if spec.backend == "gemma_safetensors":
            return paths[0] if paths else None
        return _models_dir() / spec.model_subdir
    return _models_dir() / spec.model_subdir / spec.repo_id.rsplit("/", 1)[-1]


def model_downloaded(spec: OptimizerModelSpec) -> bool:
    if spec.backend == "fallback":
        return True
    if spec.file_urls:
        paths = model_file_paths_for(spec)
        return bool(paths) and all(path.exists() for path in paths)
    path = model_path_for(spec)
    return bool(path and path.exists())


def missing_dependencies(spec: OptimizerModelSpec) -> list[str]:
    return [name for name in spec.dependencies if importlib.util.find_spec(name) is None]


def resolve_model(alias: str | None) -> OptimizerModelSpec:
    key = alias or "fallback_text_backend"
    if key not in MODEL_REGISTRY:
        raise PromptOptimizerError(f"Unknown prompt optimizer model: {key}")
    return MODEL_REGISTRY[key]


def get_model_statuses() -> dict[str, Any]:
    models = []
    for spec in MODEL_REGISTRY.values():
        path = model_path_for(spec)
        missing = missing_dependencies(spec)
        downloaded = model_downloaded(spec)
        if spec.backend == "fallback":
            status = "ready"
        elif missing:
            status = "missing_dependencies"
        elif downloaded:
            status = "downloaded"
        else:
            status = "not_downloaded"
        models.append(
            {
                "alias": spec.alias,
                "repo_id": spec.repo_id,
                "backend": spec.backend,
                "downloaded": downloaded,
                "local_path": str(path) if path else "",
                "file_urls": list(spec.file_urls),
                "local_files": [str(file_path) for file_path in model_file_paths_for(spec)] if spec.file_urls else [],
                "missing_dependencies": missing,
                "status": status,
            }
        )
    return {"ok": True, "models": models}


def unload_optimizer_model(alias: str | None = None) -> dict[str, Any]:
    unloaded = []
    if alias:
        spec = resolve_model(alias)
        keys = [spec.alias]
    else:
        keys = list(_LOADED_MODELS.keys())

    torch_modules = []
    for key in keys:
        loaded = _LOADED_MODELS.pop(key, None)
        if not loaded:
            continue
        unloaded.append(key)
        torch_module = loaded.get("torch")
        if torch_module is not None:
            torch_modules.append(torch_module)
        loaded.clear()

    gc.collect()
    for torch_module in torch_modules:
        _clear_torch_cuda_cache(torch_module)

    return {"ok": True, "unloaded": unloaded}


def _clear_torch_cuda_cache(torch_module: Any) -> list[str]:
    actions = []
    cuda = getattr(torch_module, "cuda", None)
    if cuda is None or not callable(getattr(cuda, "is_available", None)):
        return actions
    try:
        if cuda.is_available():
            cuda.empty_cache()
            actions.append("torch.cuda.empty_cache")
            ipc_collect = getattr(cuda, "ipc_collect", None)
            if callable(ipc_collect):
                ipc_collect()
                actions.append("torch.cuda.ipc_collect")
    except Exception:
        pass
    return actions


def prompt_optimizer_vram_preflight(status_cb: Any = None) -> dict[str, Any]:
    status = status_cb or _noop_status
    status("Releasing Comfy model cache before loading optimizer model...")
    actions = []

    try:
        import comfy.model_management as model_management  # type: ignore[import-not-found]
    except Exception:
        model_management = None

    if model_management is not None:
        for hook_name in ("unload_all_models", "cleanup_models", "soft_empty_cache"):
            hook = getattr(model_management, hook_name, None)
            if not callable(hook):
                continue
            try:
                hook()
                actions.append(f"comfy.model_management.{hook_name}")
            except Exception:
                pass

    gc.collect()
    actions.append("gc.collect")
    try:
        import torch

        actions.extend(_clear_torch_cuda_cache(torch))
    except Exception:
        pass

    return {"ok": True, "actions": actions}


class DownloadProgressReporter:
    def __init__(
        self,
        status_cb: Any,
        total_bytes: int | None = None,
        completed_bytes: int = 0,
        units: str = "bytes",
    ) -> None:
        self.status_cb = status_cb or _noop_status
        self.total_bytes = int(total_bytes) if total_bytes and total_bytes > 0 else None
        self.completed_bytes = max(0, int(completed_bytes or 0))
        self.units = units
        self.current_file = ""
        self.current_file_index = 1
        self.current_file_total = 1
        self.current_file_bytes = 0
        self.current_file_total_bytes: int | None = None

    def begin_file(
        self,
        file_name: str,
        file_index: int = 1,
        file_total: int = 1,
        file_total_bytes: int | None = None,
    ) -> None:
        self.current_file = file_name
        self.current_file_index = max(1, int(file_index or 1))
        self.current_file_total = max(1, int(file_total or 1))
        self.current_file_bytes = 0
        self.current_file_total_bytes = int(file_total_bytes) if file_total_bytes and file_total_bytes > 0 else None
        self.emit()

    def update(self, value: int, total: int | None = None) -> None:
        self.current_file_bytes = max(0, int(value or 0))
        if total and total > 0:
            self.current_file_total_bytes = int(total)
            if self.total_bytes is None and self.current_file_total == 1 and self.units == "bytes":
                self.total_bytes = int(total)
        self.emit()

    def finish_file(self) -> None:
        completed = self.current_file_bytes
        if self.current_file_total_bytes:
            completed = max(completed, self.current_file_total_bytes)
        self.completed_bytes += max(0, int(completed or 0))
        self.current_file_bytes = 0
        self.current_file_total_bytes = None

    def mark_cached(
        self,
        file_name: str,
        file_index: int,
        file_total: int,
        file_total_bytes: int | None = None,
    ) -> None:
        self.begin_file(file_name, file_index, file_total, file_total_bytes)
        self.current_file_bytes = max(0, int(file_total_bytes or 0))
        self.emit()
        self.finish_file()

    def progress_payload(self) -> dict[str, Any]:
        current_total = self.completed_bytes + self.current_file_bytes
        payload: dict[str, Any] = {
            "download_file": self.current_file,
            "download_file_index": self.current_file_index,
            "download_file_total": self.current_file_total,
            "estimated": False,
        }
        if self.total_bytes and self.units == "bytes":
            payload.update(
                {
                    "download_current_bytes": min(current_total, self.total_bytes),
                    "download_total_bytes": self.total_bytes,
                    "percent": (min(current_total, self.total_bytes) / self.total_bytes) * 100.0,
                }
            )
        elif self.current_file_total_bytes:
            payload["percent"] = min(100.0, (self.current_file_bytes / self.current_file_total_bytes) * 100.0)
        return payload

    def emit(self) -> None:
        label = self.current_file or "model files"
        _emit_status(
            self.status_cb,
            f"Downloading {label}...",
            self.current_file_index,
            self.current_file_total,
            self.progress_payload(),
        )

    def tqdm_class(
        self,
        file_name: str,
        file_index: int = 1,
        file_total: int = 1,
        file_total_bytes: int | None = None,
    ) -> type:
        reporter = self

        class DownloadProgressBar:
            def __init__(self, iterable=None, total=None, desc=None, **_kwargs):
                self.iterable = iterable
                self.total = int(total) if total and total > 0 else file_total_bytes
                self.n = 0
                self.closed = False
                reporter.begin_file(str(desc or file_name), file_index, file_total, self.total)

            def update(self, n=1):
                self.n += int(n or 0)
                reporter.update(self.n, self.total)

            def close(self):
                if self.closed:
                    return
                self.closed = True
                reporter.finish_file()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()
                return False

            def __iter__(self):
                if self.iterable is None:
                    return iter(())
                for item in self.iterable:
                    yield item
                    self.update(1)

            def set_description(self, desc=None, refresh=True):
                if desc:
                    reporter.current_file = str(desc)
                if refresh:
                    reporter.emit()

            def set_postfix(self, *args, **kwargs):
                return None

            def refresh(self):
                reporter.emit()

        return DownloadProgressBar


def _safe_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _exact_file_sizes(files: list[OptimizerModelFile], paths: list[Path]) -> list[int | None]:
    token = hf_auth_token()
    sizes: list[int | None] = []
    try:
        from huggingface_hub import get_hf_file_metadata, hf_hub_url
    except Exception:
        get_hf_file_metadata = None
        hf_hub_url = None
    for model_file, path in zip(files, paths, strict=True):
        if path.exists():
            sizes.append(_safe_int(path.stat().st_size))
            continue
        size = None
        if get_hf_file_metadata is not None and hf_hub_url is not None:
            try:
                url = hf_hub_url(model_file.repo_id, model_file.filename, revision=model_file.revision)
                size = _safe_int(getattr(get_hf_file_metadata(url, token=token), "size", None))
            except Exception:
                size = None
        sizes.append(size)
    return sizes


def _verify_downloaded_file(spec: OptimizerModelSpec, model_file: OptimizerModelFile, target_path: Path, downloaded: Any) -> None:
    downloaded_path = Path(downloaded) if downloaded else target_path
    candidate = downloaded_path if downloaded_path.exists() else target_path
    if not candidate.exists():
        raise PromptOptimizerError(
            f"Downloaded '{model_file.url}' but could not find expected file at {target_path}"
        )
    if candidate.stat().st_size <= 0:
        raise PromptOptimizerError(
            f"Downloaded '{model_file.url}' to {candidate}, but the file is empty. Delete it and try the download again."
        )
    if spec.backend == "llama_cpp_vision" and candidate.suffix.lower() == ".gguf":
        validate_gguf_file(candidate, model_file.filename)


def _download_exact_model_files(
    spec: OptimizerModelSpec,
    status_cb: Any = None,
) -> Path | None:
    status = status_cb or _noop_status
    files = model_files_for(spec)
    paths = model_file_paths_for(spec)
    path = model_path_for(spec)
    if paths and all(file_path.exists() for file_path in paths):
        status(f"Using cached model at {path}")
        return path

    from huggingface_hub import hf_hub_download

    sizes = _exact_file_sizes(files, paths)
    total_bytes = sum(size for size in sizes if size is not None) if all(size is not None for size in sizes) else None
    reporter = DownloadProgressReporter(status, total_bytes=total_bytes, units="bytes")
    file_total = len(files)
    for index, (model_file, target_path, size) in enumerate(zip(files, paths, sizes, strict=True), start=1):
        if target_path.exists():
            reporter.mark_cached(model_file.filename, index, file_total, size)
            status(f"Using cached model file at {target_path}")
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        local_dir = _models_dir() if spec.backend == "gemma_safetensors" else (_models_dir() / spec.model_subdir)
        local_dir.mkdir(parents=True, exist_ok=True)
        try:
            status(f"Downloading {model_file.url} into {target_path}")
            downloaded_path = hf_hub_download(
                repo_id=model_file.repo_id,
                filename=model_file.filename,
                revision=model_file.revision,
                local_dir=str(local_dir),
                local_dir_use_symlinks=False,
                token=hf_auth_token(),
                tqdm_class=reporter.tqdm_class(model_file.filename, index, file_total, size),
            )
        except Exception as exc:  # noqa: BLE001 - Hugging Face raises several HTTP wrapper types.
            raise _download_error(spec, exc) from exc
        _verify_downloaded_file(spec, model_file, target_path, downloaded_path)
    status(f"Downloaded model into {path}")
    return path


def ensure_model_downloaded(
    spec: OptimizerModelSpec,
    status_cb: Any = None,
) -> Path | None:
    status = status_cb or _noop_status
    path = model_path_for(spec)
    if path is None:
        return None
    status("Checking optional dependencies...")
    if model_downloaded(spec):
        status(f"Using cached model at {path}")
        return path
    missing = missing_dependencies(spec)
    if missing:
        raise PromptOptimizerError(
            f"Model '{spec.alias}' requires optional packages: {', '.join(missing)}"
        )
    if spec.file_urls:
        return _download_exact_model_files(spec, status)
    from huggingface_hub import snapshot_download

    path.parent.mkdir(parents=True, exist_ok=True)
    reporter = DownloadProgressReporter(status, units="items")
    try:
        status(f"Downloading {spec.repo_id} into {path}")
        snapshot_download(
            repo_id=spec.repo_id,
            local_dir=str(path),
            local_dir_use_symlinks=False,
            token=hf_auth_token(),
            tqdm_class=reporter.tqdm_class(spec.repo_id),
        )
    except Exception as exc:  # noqa: BLE001 - Hugging Face raises several HTTP wrapper types.
        raise _download_error(spec, exc) from exc
    status(f"Downloaded model into {path}")
    return path


def _download_error(spec: OptimizerModelSpec, exc: Exception) -> PromptOptimizerError:
    raw = str(exc)
    lower = raw.lower()
    authish = any(
        marker in lower
        for marker in (
            "401",
            "403",
            "404",
            "repository not found",
            "gated",
            "private",
            "unauthorized",
            "forbidden",
        )
    )
    if authish:
        status = get_optimizer_settings_status()
        token_hint = (
            "A Hugging Face token is configured."
            if status["authSource"] != "anonymous"
            else "No Hugging Face token is configured."
        )
        return PromptOptimizerError(
            f"Could not download '{spec.repo_id}'. The model may be gated, private, moved, or require accepting "
            f"terms on its Hugging Face page. {token_hint} Add or refresh a token in the optimizer settings, "
            f"accept any model access terms in your browser, then try again. Original error: {raw}"
        )
    return PromptOptimizerError(f"Could not download '{spec.repo_id}': {raw}")


def normalize_optimizer_image(image: Image.Image, max_side: int = OPTIMIZER_IMAGE_MAX_SIDE) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    largest = max(width, height)
    if largest <= max_side:
        return image.copy()
    scale = max_side / float(largest)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _load_rgb_image(path: Path) -> Image.Image:
    with Image.open(Path(path)) as image:
        return normalize_optimizer_image(image)


def _load_video_preview(path: Path) -> Image.Image | None:
    try:
        import av
    except Exception:
        return None
    try:
        with av.open(str(path)) as container:
            stream = next((candidate for candidate in container.streams if candidate.type == "video"), None)
            if stream is None:
                return None
            for frame in container.decode(stream):
                return normalize_optimizer_image(frame.to_image())
    except Exception as exc:  # noqa: BLE001
        raise PromptOptimizerError(f"Could not decode video preview for segment path '{path}': {exc}") from exc
    return None


def decode_image(segment: dict[str, Any]) -> Image.Image | None:
    image_data = str(segment.get("image_data") or segment.get("imageData") or "").strip()
    if image_data.startswith("data:image/"):
        try:
            _, encoded = image_data.split(",", 1)
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
                return normalize_optimizer_image(image)
        except Exception as exc:  # noqa: BLE001
            raise PromptOptimizerError(f"Could not decode image data for segment '{segment.get('id', '')}': {exc}") from exc

    media_path = str(segment.get("mediaPath") or segment.get("path") or "").strip()
    media_type = segment_type(segment)
    if media_path:
        candidate = Path(media_path).expanduser()
        if candidate.exists():
            if media_type == "video":
                return _load_video_preview(candidate)
            return _load_rgb_image(candidate)

    folder_alias = segment.get("imageFolderAlias") or segment.get("mediaFolderAlias")
    image_file = segment.get("imageFile") or segment.get("mediaFile")
    if folder_alias and image_file and resolve_browser_media_path is not None:
        resolved_type = "video" if media_type == "video" else "image"
        path = resolve_browser_media_path(resolved_type, str(folder_alias), str(image_file))
        if resolved_type == "video":
            return _load_video_preview(path)
        return _load_rgb_image(path)

    if image_file and folder_paths is not None and hasattr(folder_paths, "get_input_directory"):
        candidate = Path(folder_paths.get_input_directory()) / str(image_file)
        if candidate.exists():
            if media_type == "video":
                return _load_video_preview(candidate)
            return _load_rgb_image(candidate)

    return None


def clean_prompt_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^(prompt|caption|description)\s*:\s*", "", text, flags=re.I).strip()
    return text.strip(" \t\r\n\"'")


def _sentence_join(parts: list[str]) -> str:
    out = []
    for part in parts:
        part = clean_prompt_text(part)
        if part and part not in out:
            out.append(part)
    return ". ".join(p.rstrip(".") for p in out if p).strip()


def segment_direction_text(segment: dict[str, Any] | None) -> str:
    if not isinstance(segment, dict):
        return ""
    return clean_prompt_text(segment.get("direction") or segment.get("prompt"))


def segment_requests_cut(segment: dict[str, Any]) -> bool:
    return bool(CUT_SCENE_RE.search(segment_direction_text(segment)))


def segment_type(segment: dict[str, Any] | None) -> str:
    if not isinstance(segment, dict):
        return "image"
    return clean_prompt_text(segment.get("type") or "image").lower() or "image"


def is_text_segment(segment: dict[str, Any] | None) -> bool:
    return segment_type(segment) == "text"


def visual_context_instruction(segment: dict[str, Any], cut: bool) -> str:
    if is_text_segment(segment):
        if cut:
            return "No adjacent images are used because this text segment requests a cut."
        return "This text segment has no current image; any provided previous or next images are only continuity references."
    if cut:
        return "Use the current image as the only visual reference for this cut segment."
    return "Use the current image as the primary visual reference; adjacent images may be provided for continuity."


def text_segment_instruction(segment: dict[str, Any]) -> str:
    if not is_text_segment(segment):
        return ""
    return (
        "This is a text-only timeline segment. Use the text row as the main direction and generate the motion, "
        "action, camera movement, and sound that should occur during this T2V-style section."
    )


def fallback_optimize_segment(
    segment: dict[str, Any],
    mode: str,
    index: int,
    total: int,
    previous_prompt: str = "",
    next_prompt: str = "",
) -> str:
    direction = clean_prompt_text(segment.get("direction") or segment.get("prompt"))
    label = "opening" if index == 0 else "closing" if index == total - 1 else "continuing"
    cut = segment_requests_cut(segment)
    if direction:
        core = direction
    elif segment.get("type") == "text":
        core = "A text-driven timeline section continues with clear subject motion, camera movement, and temporal action"
    else:
        core = "The visible subject moves naturally with clear action and camera movement"

    tone = (
        "Use explicit adult visual language only for visible adult content"
        if mode == "nsfw"
        else "Keep the description cinematic and non-explicit"
    )
    continuity = ""
    if not cut:
        continuity = _sentence_join(
            [
                f"Continue from: {previous_prompt}" if clean_prompt_text(previous_prompt) else "",
                f"Move toward: {next_prompt}" if clean_prompt_text(next_prompt) else "",
            ]
        )
    return _sentence_join(
        [
            core,
            f"{label.capitalize()} moment in the video timeline, described in present tense",
            "focus on action, expression changes, camera motion, temporal movement, and visible or implied sound cues",
            continuity,
            tone,
        ]
    )


def reference_direction_text(reference: dict[str, Any] | None) -> str:
    if not isinstance(reference, dict):
        return ""
    return clean_prompt_text(reference.get("direction") or reference.get("description") or reference.get("prompt"))


def build_reference_caption_instruction(
    reference: dict[str, Any],
    template: str | None = None,
) -> str:
    values = {
        "direction": reference_direction_text(reference) or "none",
        "label": clean_prompt_text(reference.get("label") or reference.get("id")) or "reference",
    }
    try:
        return (template or DEFAULT_REFERENCE_CAPTION_PROMPT_TEMPLATE).format_map(values)
    except (KeyError, ValueError) as exc:
        raise PromptOptimizerError(f"Could not format reference caption prompt template: {exc}") from exc


def fallback_reference_caption(reference: dict[str, Any]) -> str:
    direction = reference_direction_text(reference)
    if direction:
        return direction
    label = clean_prompt_text(reference.get("label") or "reference subject")
    return f"{label} with distinctive visible identity features, clothing, accessories, and appearance cues"


def build_optimizer_instruction(
    segment: dict[str, Any],
    mode: str,
    index: int,
    total: int,
    previous_prompt: str = "",
    next_prompt: str = "",
    template: str | None = None,
) -> str:
    direction = clean_prompt_text(segment.get("direction") or segment.get("prompt"))
    rating = "NSFW/unredacted" if mode == "nsfw" else "SFW"
    cut = segment_requests_cut(segment)
    previous_prompt = "" if cut else clean_prompt_text(previous_prompt)
    next_prompt = "" if cut else clean_prompt_text(next_prompt)
    continuity = (
        "Treat this segment as a new cut; do not bridge motion from adjacent segments."
        if cut
        else (
            f"Previous segment motion context: {previous_prompt or 'none'}. "
            f"Next segment motion hint: {next_prompt or 'none'}."
        )
    )
    values = {
        "mode": mode,
        "rating": rating,
        "segment_index": index + 1,
        "segment_total": total,
        "direction": direction or "none",
        "continuity": continuity,
        "previous_prompt": previous_prompt or "none",
        "next_prompt": next_prompt or "none",
        "cut_instruction": "new cut" if cut else "continue naturally",
        "segment_type": segment_type(segment),
        "visual_context": visual_context_instruction(segment, cut),
        "text_segment_instruction": text_segment_instruction(segment),
    }
    try:
        return (template or DEFAULT_OPTIMIZER_PROMPT_TEMPLATE).format_map(values)
    except (KeyError, ValueError) as exc:
        raise PromptOptimizerError(f"Could not format prompt optimizer template: {exc}") from exc


def _load_qwen_model(spec: OptimizerModelSpec, path: Path, status_cb: Any = None) -> dict[str, Any]:
    status = status_cb or _noop_status
    cache_key = spec.alias
    if cache_key in _LOADED_MODELS:
        status(f"Using loaded Qwen model '{spec.alias}'.")
        return _LOADED_MODELS[cache_key]
    status(f"Loading Qwen model from {path}...")
    import torch
    from transformers import AutoProcessor

    try:
        from transformers import Qwen3VLForConditionalGeneration
        model_cls = Qwen3VLForConditionalGeneration if "Qwen3-VL" in spec.repo_id else None
    except Exception:  # noqa: BLE001
        model_cls = None
    if model_cls is None:
        from transformers import AutoModelForVision2Seq
        model_cls = AutoModelForVision2Seq

    model = model_cls.from_pretrained(
        str(path),
        torch_dtype="auto",
        device_map="auto",
        attn_implementation="sdpa",
    ).eval()
    processor = AutoProcessor.from_pretrained(str(path), trust_remote_code=True)
    loaded = {"model": model, "processor": processor, "torch": torch}
    _LOADED_MODELS[cache_key] = loaded
    status(f"Loaded Qwen model '{spec.alias}'.")
    return loaded


def _generate_qwen(
    spec: OptimizerModelSpec,
    path: Path,
    images: list[tuple[str, Image.Image]],
    instruction: str,
    status_cb: Any = None,
    loaded: dict[str, Any] | None = None,
) -> str:
    loaded = loaded or _load_qwen_model(spec, path, status_cb)
    model = loaded["model"]
    processor = loaded["processor"]
    torch = loaded["torch"]
    content: list[dict[str, Any]] = []
    image_values = [image for _, image in images]
    for label, image in images:
        content.append({"type": "text", "text": f"{label} image:"})
        content.append({"type": "image", "image": image})
    content.append({"type": "text", "text": instruction})
    conversation = [{"role": "user", "content": content}]
    chat = processor.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[chat], images=image_values or None, padding=True, return_tensors="pt")
    device = next(model.parameters()).device
    model_inputs = {key: value.to(device) if torch.is_tensor(value) else value for key, value in inputs.items()}
    outputs = model.generate(**model_inputs, max_new_tokens=180, do_sample=False, repetition_penalty=1.05)
    input_len = model_inputs["input_ids"].shape[-1]
    return processor.batch_decode(outputs[:, input_len:], skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]


def _load_florence_model(spec: OptimizerModelSpec, path: Path, status_cb: Any = None) -> dict[str, Any]:
    status = status_cb or _noop_status
    cache_key = spec.alias
    if cache_key in _LOADED_MODELS:
        status(f"Using loaded Florence model '{spec.alias}'.")
        return _LOADED_MODELS[cache_key]
    status(f"Loading Florence model from {path}...")
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor

    model = AutoModelForCausalLM.from_pretrained(
        str(path),
        trust_remote_code=True,
        torch_dtype="auto",
    ).eval()
    if torch.cuda.is_available():
        model = model.to("cuda")
    processor = AutoProcessor.from_pretrained(str(path), trust_remote_code=True)
    loaded = {"model": model, "processor": processor, "torch": torch}
    _LOADED_MODELS[cache_key] = loaded
    status(f"Loaded Florence model '{spec.alias}'.")
    return loaded


def _generate_florence(
    spec: OptimizerModelSpec,
    path: Path,
    image: Image.Image | None,
    instruction: str,
    status_cb: Any = None,
    loaded: dict[str, Any] | None = None,
) -> str:
    if image is None:
        return clean_prompt_text(instruction)
    loaded = loaded or _load_florence_model(spec, path, status_cb)
    model = loaded["model"]
    processor = loaded["processor"]
    torch = loaded["torch"]
    inputs = processor(text=instruction, images=image, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {key: value.to(device) if torch.is_tensor(value) else value for key, value in inputs.items()}
    outputs = model.generate(**inputs, max_new_tokens=180, do_sample=False)
    return processor.batch_decode(outputs, skip_special_tokens=True)[0]


GGUF_MAGIC = b"GGUF"
_GGUF_VALUE_SIZES = {
    0: 1,
    1: 1,
    2: 2,
    3: 2,
    4: 4,
    5: 4,
    6: 4,
    7: 1,
    10: 8,
    11: 8,
    12: 8,
}
_GGUF_TYPE_STRING = 8
_GGUF_TYPE_ARRAY = 9


def _read_gguf_u32(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from("<I", data, offset)[0], offset + 4


def _read_gguf_u64(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from("<Q", data, offset)[0], offset + 8


def _read_gguf_string(data: bytes, offset: int) -> tuple[str, int]:
    length, offset = _read_gguf_u64(data, offset)
    end = offset + length
    if end > len(data):
        raise ValueError("string extends past GGUF metadata buffer")
    return data[offset:end].decode("utf-8", errors="replace"), end


def _skip_gguf_value(data: bytes, offset: int, value_type: int) -> int:
    if value_type == _GGUF_TYPE_STRING:
        _, offset = _read_gguf_string(data, offset)
        return offset
    if value_type == _GGUF_TYPE_ARRAY:
        item_type, offset = _read_gguf_u32(data, offset)
        count, offset = _read_gguf_u64(data, offset)
        if item_type == _GGUF_TYPE_STRING:
            for _ in range(count):
                _, offset = _read_gguf_string(data, offset)
            return offset
        item_size = _GGUF_VALUE_SIZES.get(item_type)
        if item_size is None:
            raise ValueError(f"unsupported GGUF array item type {item_type}")
        return offset + (count * item_size)
    value_size = _GGUF_VALUE_SIZES.get(value_type)
    if value_size is None:
        raise ValueError(f"unsupported GGUF value type {value_type}")
    return offset + value_size


def gguf_architecture(path: Path) -> str:
    try:
        data = Path(path).read_bytes()[:1024 * 1024]
        if len(data) < 24 or data[:4] != GGUF_MAGIC:
            return ""
        offset = 4
        _version, offset = _read_gguf_u32(data, offset)
        _tensor_count, offset = _read_gguf_u64(data, offset)
        kv_count, offset = _read_gguf_u64(data, offset)
        for _ in range(min(kv_count, 256)):
            key, offset = _read_gguf_string(data, offset)
            value_type, offset = _read_gguf_u32(data, offset)
            if key == "general.architecture" and value_type == _GGUF_TYPE_STRING:
                value, _offset = _read_gguf_string(data, offset)
                return clean_prompt_text(value)
            offset = _skip_gguf_value(data, offset, value_type)
            if offset >= len(data):
                break
    except Exception:
        return ""
    return ""


def validate_gguf_file(path: Path, label: str = "GGUF file") -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise PromptOptimizerError(f"Missing {label}: expected {path}")
    if not path.is_file():
        raise PromptOptimizerError(f"Invalid {label}: expected a file at {path}")
    if path.stat().st_size <= 0:
        raise PromptOptimizerError(f"Invalid {label}: {path} is empty. Delete it and try the download again.")
    with path.open("rb") as handle:
        magic = handle.read(4)
    if magic != GGUF_MAGIC:
        raise PromptOptimizerError(
            f"Invalid {label}: {path} is not a valid GGUF file. Delete it and download it again."
        )
    return {"architecture": gguf_architecture(path)}


def _image_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _discover_gguf_files(path: Path) -> list[Path]:
    search_dir = path if path.is_dir() else path.parent
    if not search_dir.exists():
        return []
    return sorted(candidate for candidate in search_dir.glob("*.gguf") if candidate.is_file())


def _format_discovered_files(files: list[Path]) -> str:
    if not files:
        return "No .gguf files were found in the model directory."
    return "Found: " + ", ".join(file.name for file in files)


def _expected_llama_cpp_paths(spec: OptimizerModelSpec) -> tuple[Path | None, Path | None]:
    paths = model_file_paths_for(spec)
    model_path = next(
        (file_path for file_path in paths if file_path.suffix.lower() == ".gguf" and "mmproj" not in file_path.name.lower()),
        None,
    )
    mmproj_path = next((file_path for file_path in paths if "mmproj" in file_path.name.lower()), None)
    return model_path, mmproj_path


def _missing_llama_cpp_file_error(
    spec: OptimizerModelSpec,
    role: str,
    expected_path: Path | None,
    discovered: list[Path],
) -> PromptOptimizerError:
    expected = str(expected_path) if expected_path is not None else "unknown"
    return PromptOptimizerError(
        f"Model '{spec.alias}' is missing the expected {role} GGUF file at {expected}. "
        f"{_format_discovered_files(discovered)} Delete the incomplete model folder and try Generate again."
    )


def _llama_cpp_model_paths(spec: OptimizerModelSpec, path: Path) -> tuple[Path, Path]:
    expected_model_path, expected_mmproj_path = _expected_llama_cpp_paths(spec)
    discovered = _discover_gguf_files(path)
    discovered_model_path = next((file_path for file_path in discovered if "mmproj" not in file_path.name.lower()), None)
    discovered_mmproj_path = next((file_path for file_path in discovered if "mmproj" in file_path.name.lower()), None)

    model_path = expected_model_path if expected_model_path is not None and expected_model_path.exists() else None
    mmproj_path = expected_mmproj_path if expected_mmproj_path is not None and expected_mmproj_path.exists() else None
    if model_path is None and expected_model_path is None and discovered_model_path is not None:
        model_path = discovered_model_path
    if mmproj_path is None and expected_mmproj_path is None and discovered_mmproj_path is not None:
        mmproj_path = discovered_mmproj_path
    if model_path is None:
        raise _missing_llama_cpp_file_error(spec, "main model", expected_model_path, discovered)
    if mmproj_path is None:
        raise _missing_llama_cpp_file_error(spec, "mmproj", expected_mmproj_path, discovered)

    validate_gguf_file(model_path, "main model GGUF")
    validate_gguf_file(mmproj_path, "mmproj GGUF")
    return model_path, mmproj_path


def _load_llama_cpp_vision_model(spec: OptimizerModelSpec, path: Path, status_cb: Any = None) -> dict[str, Any]:
    status = status_cb or _noop_status
    cache_key = spec.alias
    if cache_key in _LOADED_MODELS:
        status(f"Using loaded llama.cpp model '{spec.alias}'.")
        return _LOADED_MODELS[cache_key]
    model_path, mmproj_path = _llama_cpp_model_paths(spec, path)
    status(f"Loading llama.cpp model from {model_path} with {mmproj_path}...")
    try:
        from llama_cpp import Llama
        from llama_cpp.llama_chat_format import Llava15ChatHandler
    except Exception as exc:  # noqa: BLE001
        raise PromptOptimizerError(
            "Model 'gemma4_e4b_uncensored_gguf_q8' requires optional package: llama-cpp-python"
        ) from exc

    try:
        chat_handler = Llava15ChatHandler(clip_model_path=str(mmproj_path))
        model = Llama(
            model_path=str(model_path),
            chat_handler=chat_handler,
            n_ctx=8192,
            n_gpu_layers=-1,
            verbose=False,
        )
    except Exception as exc:  # noqa: BLE001
        architecture = gguf_architecture(model_path)
        compatibility_hint = ""
        if architecture == "gemma4" or "_K_P" in model_path.name:
            compatibility_hint = (
                " The file appears to be a valid Gemma 4/K_P GGUF, so this usually means the installed "
                "llama-cpp-python/llama.cpp runtime is too old or was built without Gemma 4/K_P support. "
                "Upgrade or reinstall llama-cpp-python, then try again."
            )
        raise PromptOptimizerError(
            f"Could not load llama.cpp optimizer model '{spec.alias}' from {model_path}: {exc}.{compatibility_hint}"
        ) from exc
    loaded = {"model": model, "chat_handler": chat_handler, "mmproj_path": mmproj_path, "model_path": model_path}
    _LOADED_MODELS[cache_key] = loaded
    status(f"Loaded llama.cpp model '{spec.alias}'.")
    return loaded


def _generate_llama_cpp_vision(
    spec: OptimizerModelSpec,
    path: Path,
    images: list[tuple[str, Image.Image]],
    instruction: str,
    status_cb: Any = None,
    loaded: dict[str, Any] | None = None,
) -> str:
    loaded = loaded or _load_llama_cpp_vision_model(spec, path, status_cb)
    model = loaded["model"]
    content: list[dict[str, Any]] = []
    for label, image in images:
        content.append({"type": "text", "text": f"{label} image:"})
        content.append({"type": "image_url", "image_url": {"url": _image_data_url(image)}})
    content.append({"type": "text", "text": instruction})
    response = model.create_chat_completion(
        messages=[{"role": "user", "content": content}],
        max_tokens=180,
        temperature=0.2,
        top_p=0.95,
    )
    choices = response.get("choices") if isinstance(response, dict) else None
    if choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                return str(message.get("content") or "")
            return str(first.get("text") or "")
    return str(response or "")


def _generate_gemma_safetensors(
    spec: OptimizerModelSpec,
    path: Path,
    instruction: str,
    _status_cb: Any = None,
) -> str:
    if not path.exists():
        raise PromptOptimizerError(f"Downloaded Gemma safetensors file is missing at {path}")
    raise PromptOptimizerError(
        f"Model '{spec.alias}' downloaded the exact Comfy-Org safetensors file, but this file is a ComfyUI "
        "text-encoder checkpoint and is not a standalone prompt-generating optimizer model in the installed "
        "runtime. Use a GGUF/Transformers prompt optimizer model for generation, or provide a compatible "
        "generator config/tokenizer for this exact checkpoint."
    )


def _neighbor_segment(segments: list[Any], index: int, offset: int) -> dict[str, Any] | None:
    neighbor_index = index + offset
    if 0 <= neighbor_index < len(segments) and isinstance(segments[neighbor_index], dict):
        return segments[neighbor_index]
    return None


def _previous_context(
    segments: list[Any],
    index: int,
    generated_by_id: dict[str, str],
) -> str:
    previous = _neighbor_segment(segments, index, -1)
    if not previous:
        return ""
    previous_id = str(previous.get("id") or "")
    return clean_prompt_text(generated_by_id.get(previous_id) or segment_direction_text(previous))


def _next_context(segments: list[Any], index: int) -> str:
    return segment_direction_text(_neighbor_segment(segments, index, 1))


def _nearest_context_image(
    segments: list[Any],
    index: int,
    step: int,
    label: str,
) -> tuple[str, Image.Image] | None:
    neighbor_index = index + step
    while 0 <= neighbor_index < len(segments):
        segment = segments[neighbor_index]
        if isinstance(segment, dict):
            image = decode_image(segment)
            if image is not None:
                return (label, image)
        neighbor_index += step
    return None


def _qwen_context_images(
    segments: list[Any],
    index: int,
    include_neighbors: bool,
) -> list[tuple[str, Image.Image]]:
    segment = _neighbor_segment(segments, index, 0)
    if is_text_segment(segment):
        if not include_neighbors:
            return []
        images: list[tuple[str, Image.Image]] = []
        previous = _nearest_context_image(segments, index, -1, "Previous")
        next_image = _nearest_context_image(segments, index, 1, "Next")
        if previous is not None:
            images.append(previous)
        if next_image is not None:
            images.append(next_image)
        return images

    offsets = [0] if not include_neighbors else [-1, 0, 1]
    labels = {-1: "Previous", 0: "Current", 1: "Next"}
    images: list[tuple[str, Image.Image]] = []
    for offset in offsets:
        segment = _neighbor_segment(segments, index, offset)
        if not segment:
            continue
        image = decode_image(segment)
        if image is not None:
            images.append((labels[offset], image))
    return images


def _florence_context_image(segments: list[Any], index: int, include_neighbors: bool) -> Image.Image | None:
    segment = _neighbor_segment(segments, index, 0)
    if not segment:
        return None
    image = decode_image(segment)
    if image is not None:
        return image
    if not include_neighbors or not is_text_segment(segment):
        return None
    previous = _nearest_context_image(segments, index, -1, "Previous")
    if previous is not None:
        return previous[1]
    next_image = _nearest_context_image(segments, index, 1, "Next")
    return next_image[1] if next_image is not None else None


def _reference_context_images(reference: dict[str, Any]) -> list[tuple[str, Image.Image]]:
    image = decode_image(reference)
    return [("Reference", image)] if image is not None else []


def _reference_context_image(reference: dict[str, Any]) -> Image.Image | None:
    return decode_image(reference)


def optimize_segments(payload: dict[str, Any], status_cb: Any = None) -> dict[str, Any]:
    status = status_cb or _noop_status
    status("Checking selected model...")
    spec = resolve_model(payload.get("model"))
    mode = str(payload.get("mode") or "sfw").lower()
    if mode not in {"sfw", "nsfw"}:
        raise PromptOptimizerError("mode must be 'sfw' or 'nsfw'")
    segments = payload.get("segments", [])
    references = payload.get("references", [])
    if not isinstance(segments, list):
        raise PromptOptimizerError("segments must be a list")
    if not isinstance(references, list):
        raise PromptOptimizerError("references must be a list")

    selected = [seg for seg in segments if isinstance(seg, dict) and seg.get("selected", True)]
    selected_references = [ref for ref in references if isinstance(ref, dict) and ref.get("selected", True)]
    if not selected and not selected_references:
        raise PromptOptimizerError("Select at least one segment or reference to optimize.")

    path = ensure_model_downloaded(spec, status)
    total = len(segments)
    selected_total = len(selected) + len(selected_references)
    generated_count = 0
    results = []
    generated_by_id: dict[str, str] = {}
    prompt_template = active_prompt_template()
    reference_prompt_template = DEFAULT_REFERENCE_CAPTION_PROMPT_TEMPLATE

    for index, segment in enumerate(segments):
        seg_id = str(segment.get("id") or "")
        if not segment.get("selected", True):
            continue
        generated_count += 1
        cut = segment_requests_cut(segment)
        previous_prompt = "" if cut else _previous_context(segments, index, generated_by_id)
        next_prompt = "" if cut else _next_context(segments, index)
        instruction = build_optimizer_instruction(segment, mode, index, total, previous_prompt, next_prompt, prompt_template)

        if spec.backend == "fallback":
            status(f"Generating fallback prompt {generated_count} of {selected_total}...", generated_count, selected_total)
            optimized = fallback_optimize_segment(segment, mode, index, total, previous_prompt, next_prompt)
            status(f"Completed prompt {generated_count} of {selected_total}.", generated_count, selected_total)
        else:
            status(f"Preparing image context {generated_count} of {selected_total}...", generated_count, selected_total)
            if spec.backend == "qwen":
                images = _qwen_context_images(segments, index, not cut)
                prompt_optimizer_vram_preflight(status)
                loaded = _load_qwen_model(spec, path, status)  # type: ignore[arg-type]
                status(f"Generating prompt {generated_count} of {selected_total}...", generated_count, selected_total)
                optimized = _generate_qwen(spec, path, images, instruction, _noop_status, loaded=loaded)  # type: ignore[arg-type]
            elif spec.backend == "florence":
                image = _florence_context_image(segments, index, not cut)
                if image is None:
                    status(f"Generating fallback prompt {generated_count} of {selected_total}...", generated_count, selected_total)
                    optimized = fallback_optimize_segment(segment, mode, index, total, previous_prompt, next_prompt)
                    status(f"Completed prompt {generated_count} of {selected_total}.", generated_count, selected_total)
                    generated_by_id[seg_id] = optimized
                    results.append({"id": seg_id, "prompt": optimized})
                    continue
                if image is not None:
                    prompt_optimizer_vram_preflight(status)
                loaded = _load_florence_model(spec, path, status)  # type: ignore[arg-type]
                status(f"Generating prompt {generated_count} of {selected_total}...", generated_count, selected_total)
                optimized = _generate_florence(spec, path, image, instruction, _noop_status, loaded=loaded)  # type: ignore[arg-type]
            elif spec.backend == "llama_cpp_vision":
                images = _qwen_context_images(segments, index, not cut)
                prompt_optimizer_vram_preflight(status)
                loaded = _load_llama_cpp_vision_model(spec, path, status)  # type: ignore[arg-type]
                status(f"Generating prompt {generated_count} of {selected_total}...", generated_count, selected_total)
                optimized = _generate_llama_cpp_vision(spec, path, images, instruction, _noop_status, loaded=loaded)  # type: ignore[arg-type]
            elif spec.backend == "gemma_safetensors":
                status(f"Generating prompt {generated_count} of {selected_total}...", generated_count, selected_total)
                optimized = _generate_gemma_safetensors(spec, path, instruction, _noop_status)  # type: ignore[arg-type]
            else:
                raise PromptOptimizerError(f"Unsupported optimizer backend: {spec.backend}")
            status(f"Completed prompt {generated_count} of {selected_total}.", generated_count, selected_total)
            status(f"Cleaning generated prompt {generated_count} of {selected_total}...", generated_count, selected_total)
            optimized = clean_prompt_text(optimized)

        if not optimized:
            optimized = fallback_optimize_segment(segment, mode, index, total, previous_prompt, next_prompt)
        generated_by_id[seg_id] = optimized
        results.append({"id": seg_id, "kind": "timeline", "prompt": optimized})

    for reference_index, reference in enumerate(references):
        if not isinstance(reference, dict) or not reference.get("selected", True):
            continue
        ref_id = str(reference.get("id") or reference.get("label") or "")
        generated_count += 1
        instruction = build_reference_caption_instruction(reference, reference_prompt_template)

        if spec.backend == "fallback":
            status(
                f"Generating fallback reference caption {generated_count} of {selected_total}...",
                generated_count,
                selected_total,
            )
            optimized = fallback_reference_caption(reference)
            status(f"Completed reference caption {generated_count} of {selected_total}.", generated_count, selected_total)
        else:
            status(f"Preparing reference image context {generated_count} of {selected_total}...", generated_count, selected_total)
            if spec.backend == "qwen":
                images = _reference_context_images(reference)
                if not images and not reference_direction_text(reference):
                    optimized = fallback_reference_caption(reference)
                else:
                    prompt_optimizer_vram_preflight(status)
                    loaded = _load_qwen_model(spec, path, status)  # type: ignore[arg-type]
                    status(f"Generating reference caption {generated_count} of {selected_total}...", generated_count, selected_total)
                    optimized = _generate_qwen(spec, path, images, instruction, _noop_status, loaded=loaded)  # type: ignore[arg-type]
            elif spec.backend == "florence":
                image = _reference_context_image(reference)
                if image is None:
                    optimized = fallback_reference_caption(reference)
                else:
                    prompt_optimizer_vram_preflight(status)
                    loaded = _load_florence_model(spec, path, status)  # type: ignore[arg-type]
                    status(f"Generating reference caption {generated_count} of {selected_total}...", generated_count, selected_total)
                    optimized = _generate_florence(spec, path, image, instruction, _noop_status, loaded=loaded)  # type: ignore[arg-type]
            elif spec.backend == "llama_cpp_vision":
                images = _reference_context_images(reference)
                if not images and not reference_direction_text(reference):
                    optimized = fallback_reference_caption(reference)
                else:
                    prompt_optimizer_vram_preflight(status)
                    loaded = _load_llama_cpp_vision_model(spec, path, status)  # type: ignore[arg-type]
                    status(f"Generating reference caption {generated_count} of {selected_total}...", generated_count, selected_total)
                    optimized = _generate_llama_cpp_vision(spec, path, images, instruction, _noop_status, loaded=loaded)  # type: ignore[arg-type]
            elif spec.backend == "gemma_safetensors":
                status(f"Generating reference caption {generated_count} of {selected_total}...", generated_count, selected_total)
                optimized = _generate_gemma_safetensors(spec, path, instruction, _noop_status)  # type: ignore[arg-type]
            else:
                raise PromptOptimizerError(f"Unsupported optimizer backend: {spec.backend}")
            status(f"Completed reference caption {generated_count} of {selected_total}.", generated_count, selected_total)
            status(f"Cleaning generated reference caption {generated_count} of {selected_total}...", generated_count, selected_total)
            optimized = clean_prompt_text(optimized)

        if not optimized:
            optimized = fallback_reference_caption(reference)
        results.append(
            {
                "id": ref_id,
                "kind": "reference",
                "label": clean_prompt_text(reference.get("label") or f"reference {reference_index + 1}"),
                "description": optimized,
            }
        )

    status(f"Done. Generated {len(results)} prompt{'s' if len(results) != 1 else ''}.", len(results), selected_total)
    return {
        "ok": True,
        "model": spec.alias,
        "mode": mode,
        "results": results,
    }


def _phase_for_message(message: str) -> str:
    lower = message.lower()
    if (
        lower.startswith("generating prompt")
        or lower.startswith("generating fallback prompt")
        or lower.startswith("generating reference caption")
        or lower.startswith("generating fallback reference caption")
    ):
        return "generating"
    if lower.startswith("completed prompt") or lower.startswith("completed reference caption"):
        return "completed_prompt"
    if lower.startswith("cleaning"):
        return "cleaning"
    if lower.startswith("preparing"):
        return "preparing"
    if lower.startswith("downloading"):
        return "downloading"
    if lower.startswith("loading"):
        return "loading"
    if lower.startswith("done"):
        return "completed"
    if lower.startswith("checking") or lower.startswith("using cached") or lower.startswith("downloaded"):
        return "setup"
    return "running"


def _job_average_seconds(job: dict[str, Any]) -> float | None:
    durations = []
    for value in job.get("prompt_durations") or []:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            durations.append(duration)
    if durations:
        return sum(durations) / len(durations)
    average = float(job.get("profile_average_seconds") or 0)
    return average if average > 0 else None


def _estimated_job_progress(job: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    progress = dict(job.get("progress") or {})
    current = progress.get("current")
    total = progress.get("total")
    phase = str(progress.get("phase") or "idle")
    elapsed = max(0.0, now - float(job.get("created_at") or now))
    prompt_elapsed = None
    percent = progress.get("percent")
    eta_seconds = progress.get("eta_seconds")
    estimated = bool(progress.get("estimated"))

    if job.get("state") == "completed":
        percent = 100.0
        eta_seconds = 0.0
        estimated = False
        phase = "completed"
    elif phase == "downloading":
        eta_seconds = None
        estimated = False
    elif isinstance(current, int) and isinstance(total, int) and total > 0:
        average = _job_average_seconds(job)
        completed = max(0, min(total, current - 1))
        if phase == "generating":
            started = float(job.get("prompt_started_at") or now)
            prompt_elapsed = max(0.0, now - started)
            if average:
                prompt_fraction = min(0.92, max(0.02, prompt_elapsed / average))
                eta_seconds = max(0.0, average - prompt_elapsed) + (max(total - current, 0) * average)
            else:
                prompt_fraction = min(0.35, max(0.02, prompt_elapsed / 45.0))
                eta_seconds = None
            percent = ((completed + prompt_fraction) / total) * 100.0
            estimated = True
        elif phase in {"completed_prompt", "cleaning", "completed"}:
            percent = (min(current, total) / total) * 100.0
            eta_seconds = (max(total - current, 0) * average) if average else None
            estimated = False
        else:
            percent = (completed / total) * 100.0
            eta_seconds = ((total - completed) * average) if average else None
            estimated = bool(average)

    progress.update(
        {
            "phase": phase,
            "percent": round(max(0.0, min(100.0, float(percent or 0.0))), 1),
            "eta_seconds": round(float(eta_seconds), 1) if eta_seconds is not None else None,
            "elapsed_seconds": round(elapsed, 1),
            "prompt_elapsed_seconds": round(prompt_elapsed, 1) if prompt_elapsed is not None else None,
            "estimated": estimated,
        }
    )
    return progress


def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "job_id": job["job_id"],
        "state": job["state"],
        "message": job["message"],
        "progress": _estimated_job_progress(job),
        "results": job.get("results") or [],
        "error": job.get("error") or "",
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


def _set_job_status(
    job_id: str,
    message: str,
    current: int | None = None,
    total: int | None = None,
    progress_details: dict[str, Any] | None = None,
) -> None:
    now = time.time()
    with _OPTIMIZER_JOBS_LOCK:
        job = _OPTIMIZER_JOBS.get(job_id)
        if not job:
            return
        phase = _phase_for_message(message)
        previous_phase = str((job.get("progress") or {}).get("phase") or "")
        if phase == "generating" and (previous_phase != "generating" or job.get("prompt_current") != current):
            job["prompt_started_at"] = now
            job["prompt_current"] = current
        elif phase == "completed_prompt":
            started = job.get("prompt_started_at")
            if started is not None and job.get("prompt_current") == current:
                duration = max(0.001, now - float(started))
                job.setdefault("prompt_durations", []).append(duration)
                spec = job.get("model_spec")
                if isinstance(spec, OptimizerModelSpec):
                    record_prompt_timing(spec, duration)
            job["completed_prompts"] = current
            job["prompt_started_at"] = None
        job["message"] = message
        progress = _progress(current, total, phase=phase)
        if isinstance(progress_details, dict):
            progress.update(progress_details)
            progress["phase"] = phase
        job["progress"] = progress
        job["updated_at"] = now


def start_optimizer_job(payload: dict[str, Any]) -> str:
    job_id = uuid.uuid4().hex
    now = time.time()
    with _OPTIMIZER_JOBS_LOCK:
        _OPTIMIZER_JOBS[job_id] = {
            "job_id": job_id,
            "state": "queued",
            "message": "Queued prompt optimization...",
            "progress": _progress(phase="queued", percent=0.0),
            "results": [],
            "error": "",
            "created_at": now,
            "updated_at": now,
            "prompt_durations": [],
        }

    thread = threading.Thread(target=_run_optimizer_job, args=(job_id, payload), daemon=True)
    thread.start()
    return job_id


def _run_optimizer_job(job_id: str, payload: dict[str, Any]) -> None:
    try:
        spec = resolve_model(payload.get("model"))
        profile_average = timing_profile_average(model_timing_key(spec))
    except Exception:
        spec = None
        profile_average = None
    with _OPTIMIZER_JOBS_LOCK:
        job = _OPTIMIZER_JOBS.get(job_id)
        if job:
            job["state"] = "running"
            job["message"] = "Starting prompt optimization..."
            job["progress"] = _progress(phase="setup", percent=0.0)
            job["model_spec"] = spec
            job["model_key"] = model_timing_key(spec) if isinstance(spec, OptimizerModelSpec) else ""
            job["profile_average_seconds"] = profile_average
            job["updated_at"] = time.time()
    try:
        result = optimize_segments(
            payload,
            lambda message, current=None, total=None, progress=None: _set_job_status(
                job_id, message, current, total, progress
            ),
        )
        with _OPTIMIZER_JOBS_LOCK:
            job = _OPTIMIZER_JOBS.get(job_id)
            if job:
                job["state"] = "completed"
                job["message"] = f"Done. Generated {len(result.get('results') or [])} prompt{'s' if len(result.get('results') or []) != 1 else ''}."
                job["progress"] = _progress(
                    len(result.get("results") or []),
                    len(result.get("results") or []),
                    phase="completed",
                    percent=100.0,
                    eta_seconds=0.0,
                    estimated=False,
                )
                job["results"] = result.get("results") or []
                job["error"] = ""
                job["updated_at"] = time.time()
    except Exception as exc:  # noqa: BLE001 - route polls should see readable errors.
        with _OPTIMIZER_JOBS_LOCK:
            job = _OPTIMIZER_JOBS.get(job_id)
            if job:
                job["state"] = "failed"
                job["message"] = "Prompt optimization failed."
                job["error"] = str(exc)
                progress = dict(job.get("progress") or {})
                progress["phase"] = "failed"
                job["progress"] = progress
                job["updated_at"] = time.time()


def get_optimizer_job_status(job_id: str) -> dict[str, Any]:
    with _OPTIMIZER_JOBS_LOCK:
        job = _OPTIMIZER_JOBS.get(str(job_id or ""))
        if not job:
            raise PromptOptimizerError(f"Unknown optimizer job: {job_id}")
        return _job_snapshot(job)
