"""Director LoRA configuration node copied from AIO LoRA Configuration."""

from __future__ import annotations

from typing import Any

try:
    from ...shared.lora.dynamic_inputs import ANY_TYPE, FlexibleOptionalInputType
    from ...shared.lora.config import normalize_lora_config
except ImportError:  # pragma: no cover - direct test imports
    from shared.lora.dynamic_inputs import ANY_TYPE, FlexibleOptionalInputType
    from shared.lora.config import normalize_lora_config


class HeltoTimelineLoraConfiguration:
    CATEGORY = "timeline/director"
    RETURN_TYPES = ("HELTO_LORA_CONFIG",)
    RETURN_NAMES = ("lora_config",)
    FUNCTION = "configure"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "show_strengths": (
                    ["single", "separate"],
                    {
                        "default": "single",
                        "tooltip": "Choose one shared LoRA strength or separate model and CLIP strengths per row.",
                    },
                ),
                "match": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "Optional regular expression used to filter the LoRA chooser list.",
                    },
                ),
            },
            "optional": FlexibleOptionalInputType(ANY_TYPE),
            "hidden": {},
        }

    def configure(self, show_strengths: str = "single", match: str = "", **kwargs: Any):
        payload = dict(kwargs)
        payload["show_strengths"] = show_strengths
        payload["match"] = match
        return (normalize_lora_config(payload),)
