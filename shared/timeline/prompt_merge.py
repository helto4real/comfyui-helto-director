from __future__ import annotations

from ..contracts.video_timeline import (
    GLOBAL_PROMPT_POSITION_PREFIX,
    GLOBAL_PROMPT_POSITION_SUFFIX,
)


def merge_prompts(
    section_prompt: str | None,
    global_prompt: str | None,
    enabled: bool,
    position: str = GLOBAL_PROMPT_POSITION_PREFIX,
) -> str:
    section_text = (section_prompt or "").strip()
    global_text = (global_prompt or "").strip()
    if not enabled or not global_text:
        return section_text
    if not section_text:
        return global_text
    if position == GLOBAL_PROMPT_POSITION_SUFFIX:
        return f"{section_text}, {global_text}"
    return f"{global_text}, {section_text}"
