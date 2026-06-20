"""Runtime LoRA application helpers."""

from __future__ import annotations

from typing import Any

try:
    from .config import normalize_lora_config
except ImportError:  # pragma: no cover - direct test imports
    from shared.lora.config import normalize_lora_config


def apply_lora_config(
    *,
    model: Any,
    clip: Any,
    lora_config: dict[str, Any] | None,
) -> tuple[Any, Any, list[dict[str, Any]]]:
    normalized = normalize_lora_config(lora_config)
    loras = normalized["loras"]
    if not loras:
        return model, clip, []

    import nodes  # type: ignore

    loader = nodes.LoraLoader()
    applied: list[dict[str, Any]] = []
    for lora in loras:
        model, clip = loader.load_lora(
            model,
            clip,
            lora["name"],
            lora["strength_model"],
            lora["strength_clip"],
        )
        applied.append(dict(lora))
    return model, clip, applied


def apply_lora_config_model_only(
    *,
    model: Any,
    lora_config: dict[str, Any] | None,
) -> tuple[Any, list[dict[str, Any]]]:
    normalized = normalize_lora_config(lora_config)
    loras = normalized["loras"]
    if not loras:
        return model, []

    import nodes  # type: ignore

    loader = nodes.LoraLoaderModelOnly()
    applied: list[dict[str, Any]] = []
    for lora in loras:
        (model,) = loader.load_lora_model_only(
            model,
            lora["name"],
            lora["strength_model"],
        )
        applied.append(dict(lora))
    return model, applied
