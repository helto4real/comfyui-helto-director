from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / "docs" / "workflows"
DOC_PATHS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "examples" / "ltx_timeline_workflow_guide.md",
    REPO_ROOT / "docs" / "workflows" / "README.md",
    REPO_ROOT / "docs" / "picker_setup.md",
    REPO_ROOT / "docs" / "privacy_limitations.md",
    REPO_ROOT / "docs" / "current_limitations.md",
    REPO_ROOT / "docs" / "WAN22_SUPPORT.md",
    REPO_ROOT / "docs" / "WAN22_MANUAL_TEST_CHECKLIST.md",
    REPO_ROOT / "docs" / "IMPLEMENTATION_ROADMAP.md",
    REPO_ROOT / "docs" / "wan_skeleton_status.md",
]

EXPECTED_WORKFLOWS = {
    "ltx_text_only_workflow.json": {
        "HeltoVideoTimelineDirector",
        "HeltoLTX23TimelineConfig",
        "HeltoLTX23TimelinePlanner",
        "HeltoLTX23TimelineRuntime",
    },
    "ltx_image_video_audio_workflow.json": {
        "HeltoVideoTimelineDirector",
        "HeltoLTX23TimelineConfig",
        "HeltoLTX23TimelinePlanner",
        "HeltoLTX23TimelineRuntime",
    },
    "ltx_identity_reference_workflow.json": {
        "HeltoVideoTimelineDirector",
        "HeltoLTX23TimelineConfig",
        "HeltoLTX23TimelinePlanner",
        "HeltoLTX23TimelineRuntime",
        "HeltoLTX23TimelineIdentityAnchorLatentAware",
        "HeltoLTX23TimelineReferenceImageSelector",
        "HeltoLTX23TimelineCropReferenceTail",
    },
    "wan_planner_skeleton_workflow.json": {
        "HeltoVideoTimelineDirector",
        "HeltoWAN22TimelineConfig",
        "HeltoWAN22TimelinePlanner",
        "HeltoWAN22TimelineRuntime",
    },
    "wan_i2v_text_first_image_workflow.json": {
        "HeltoVideoTimelineDirector",
        "HeltoWAN22TimelineConfig",
        "HeltoWAN22TimelinePlanner",
        "HeltoWAN22TimelineRuntime",
    },
    "wan_timed_keyframes_workflow.json": {
        "HeltoVideoTimelineDirector",
        "HeltoWAN22TimelineConfig",
        "HeltoWAN22TimelinePlanner",
        "HeltoWAN22TimelineRuntime",
    },
    "wan_text_only_prompt_relay_workflow.json": {
        "HeltoVideoTimelineDirector",
        "HeltoWAN22TimelineConfig",
        "HeltoWAN22TimelinePlanner",
        "HeltoWAN22TimelineRuntime",
    },
    "wan_audio_final_mix_workflow.json": {
        "HeltoVideoTimelineDirector",
        "HeltoWAN22TimelineConfig",
        "HeltoWAN22TimelinePlanner",
        "HeltoWAN22TimelineRuntime",
    },
}

FORBIDDEN_PAYLOAD_PATTERNS = (
    "data:image",
    "data:video",
    "data:audio",
    ";base64",
    "\"thumbnail\"",
    "\"thumbnails\"",
    "\"waveform\"",
    "\"waveforms\"",
)


def test_workflow_examples_parse_and_contain_expected_nodes():
    workflow_files = sorted(path.name for path in WORKFLOW_DIR.glob("*.json"))
    assert workflow_files == sorted(EXPECTED_WORKFLOWS)

    for filename, expected_nodes in EXPECTED_WORKFLOWS.items():
        workflow = _load_workflow(filename)
        assert workflow["version"] == 0.4
        assert isinstance(workflow["nodes"], list)
        assert isinstance(workflow["links"], list)

        node_types = {node["type"] for node in workflow["nodes"]}
        assert expected_nodes.issubset(node_types)


def test_workflow_examples_do_not_embed_media_or_preview_payloads():
    for path in WORKFLOW_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        for pattern in FORBIDDEN_PAYLOAD_PATTERNS:
            assert pattern not in text, f"{path.name} contains forbidden payload marker {pattern}"

        workflow = json.loads(path.read_text(encoding="utf-8"))
        for value in _walk_values(workflow):
            if isinstance(value, str):
                assert len(value) < 20000, f"{path.name} contains an unexpectedly large string payload"


def test_workflow_links_reference_existing_nodes():
    for path in WORKFLOW_DIR.glob("*.json"):
        workflow = json.loads(path.read_text(encoding="utf-8"))
        node_ids = {node["id"] for node in workflow["nodes"]}
        for link in workflow["links"]:
            assert len(link) >= 5
            assert link[1] in node_ids, f"{path.name} link origin node does not exist: {link}"
            assert link[3] in node_ids, f"{path.name} link target node does not exist: {link}"


def test_wan_example_uses_plan_only_runtime():
    workflow = _load_workflow("wan_planner_skeleton_workflow.json")
    node_types = {node["type"] for node in workflow["nodes"]}

    assert "HeltoWAN22TimelineConfig" in node_types
    assert "HeltoWAN22TimelinePlanner" in node_types
    assert "HeltoWAN22TimelineRuntime" in node_types


def test_documentation_links_point_to_existing_files():
    for path in DOC_PATHS:
        text = path.read_text(encoding="utf-8")
        for target in _markdown_links(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if target.startswith("#"):
                continue
            target_path = target.split("#", 1)[0]
            if not target_path:
                continue
            resolved = (path.parent / target_path).resolve()
            assert resolved.exists(), f"{path.relative_to(REPO_ROOT)} links to missing file {target}"


def _load_workflow(filename: str) -> dict[str, Any]:
    return json.loads((WORKFLOW_DIR / filename).read_text(encoding="utf-8"))


def _walk_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def _markdown_links(text: str) -> list[str]:
    markdown_links = re.findall(r"!?\[[^\]]+\]\(([^)]+)\)", text)
    return [link.strip() for link in markdown_links]
