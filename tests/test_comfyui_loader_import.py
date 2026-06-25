import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import torch
from comfy_execution.graph_utils import ExecutionBlocker

from shared.contracts.video_timeline import ASSET_TYPE_VIDEO, SECTION_TYPE_TEXT
from shared.timeline import create_default_video_timeline, validate_video_timeline


def load_nodepack_like_comfyui():
    module_path = Path(__file__).resolve().parents[1]
    sys_module_name = str(module_path).replace(".", "_x_")
    spec = importlib.util.spec_from_file_location(
        sys_module_name,
        module_path / "__init__.py",
    )
    module = importlib.util.module_from_spec(spec)

    previous = sys.modules.get(sys_module_name)
    previous_path = list(sys.path)
    sys.modules[sys_module_name] = module
    try:
        sys.path = [
            path
            for path in sys.path
            if Path(path or ".").resolve() != module_path
        ]
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path = previous_path
        if previous is None:
            sys.modules.pop(sys_module_name, None)
        else:
            sys.modules[sys_module_name] = previous


def get_video_timeline_director():
    module = load_nodepack_like_comfyui()
    extension = asyncio.run(module.comfy_entrypoint())
    return asyncio.run(extension.get_node_list())[0]


def get_node_list():
    module = load_nodepack_like_comfyui()
    extension = asyncio.run(module.comfy_entrypoint())
    return asyncio.run(extension.get_node_list())


def test_comfyui_style_custom_node_loader_imports_package():
    node = get_video_timeline_director()
    schema = node.define_schema()

    assert schema.node_id == "HeltoVideoTimelineDirector"
    assert [output.io_type for output in schema.outputs] == [
        "VIDEO_TIMELINE",
        "TIMELINE_VALIDATION",
        "FLOAT",
    ]


def test_comfyui_style_loader_includes_phase10_timeline_identity_nodes():
    node_ids = [node.define_schema().node_id for node in get_node_list()]

    assert "HeltoLTX23TimelineCropReferenceTail" in node_ids
    assert "HeltoLTX23TimelineReferenceImageSelector" in node_ids
    assert "HeltoLTX23TimelineIdentityAnchorLatentAware" in node_ids
    assert "HeltoLTX23TimelineIdentityAnchorFace" in node_ids
    assert "HeltoLTX23TimelineIdentityAnchorCombine" in node_ids
    assert "HeltoLTX23TimelineApplyIdentityAnchor" in node_ids


def test_comfyui_style_loader_includes_phase11_wan_nodes():
    node_ids = [node.define_schema().node_id for node in get_node_list()]

    assert "HeltoWAN22TimelineConfig" in node_ids
    assert "HeltoWAN22TimelinePlanner" in node_ids
    assert "HeltoWAN22TimelineRuntime" in node_ids


def test_classic_loader_mapping_includes_timeline_lora_node():
    module = load_nodepack_like_comfyui()

    assert "HeltoTimelineLoraConfiguration" in module.NODE_CLASS_MAPPINGS
    assert module.NODE_CLASS_MAPPINGS["HeltoTimelineLoraConfiguration"].RETURN_TYPES == ("HELTO_LORA_CONFIG",)


def test_comfyui_style_loader_includes_timeline_take_capture_node():
    nodes = get_node_list()
    schemas = {node.define_schema().node_id: node.define_schema() for node in nodes}
    schema = schemas["HeltoTimelineTakeCapture"]

    assert schema.display_name == "Timeline Take Capture"
    assert [output.io_type for output in schema.outputs] == [
        "VIDEO_TIMELINE",
        "VIDEO",
        "STRING",
        "STRING",
        "DEBUG_INFO",
    ]
    assert [input.id for input in schema.inputs] == [
        "video_timeline",
        "runtime_debug",
        "video",
        "images",
        "audio",
        "frame_rate",
        "take_registration_json",
        "generated_asset_path",
        "shot_id_override",
        "filename_prefix",
        "accept",
        "update_clip_instance",
    ]


def test_comfyui_style_loader_includes_timeline_sequence_assembler_node():
    nodes = get_node_list()
    schemas = {node.define_schema().node_id: node.define_schema() for node in nodes}
    schema = schemas["HeltoTimelineSequenceAssembler"]

    assert schema.display_name == "Timeline Sequence Assembler"
    assert [output.io_type for output in schema.outputs] == [
        "VIDEO",
        "IMAGE",
        "AUDIO",
        "FLOAT",
        "DEBUG_INFO",
        "BOOLEAN",
    ]
    assert [output.id for output in schema.outputs] == [
        "video",
        "images",
        "audio",
        "frame_rate",
        "debug_info",
        "has_assembled_video",
    ]
    assert [input.id for input in schema.inputs] == [
        "video_timeline",
        "missing_take_policy",
        "bit_depth",
    ]


def test_timeline_take_capture_skips_media_when_runtime_reports_no_generation(tmp_path):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineTakeCapture"]
    timeline_input = _timeline_with_shot()
    timeline_input["project"]["storage"]["asset_root_directory"] = str(tmp_path / "takes")
    runtime_debug = {
        "type": "DEBUG_INFO",
        "source": "LTX Runtime",
        "summary": {
            "generation_required": False,
            "generation_status": "skipped",
            "generation_skip_reason": "all_shots_ready",
            "generation_mode": "Missing Only",
        },
        "generation_policy": {
            "status": "skipped",
            "skip_reason": "all_shots_ready",
            "mode": "Missing Only",
        },
    }

    assert node.check_lazy_status(timeline_input, runtime_debug=None, video=None, images=None) == ["runtime_debug"]
    assert node.check_lazy_status(timeline_input, runtime_debug=runtime_debug, video=None, images=None) == []

    output = node.execute(
        timeline_input,
        runtime_debug=runtime_debug,
        video=FakeVideo(),
        filename_prefix="should_not_write/%shot_id%/%take_id%",
    )

    timeline = output[0]
    assert output[1] is None
    assert output[2] == ""
    assert output[3] == ""
    assert output[4]["code"] == "TAKE_CAPTURE_SKIPPED_NO_GENERATION_REQUIRED"
    assert output[4]["summary"]["storage_action"] == "skipped"
    assert timeline["assets"] == []
    assert timeline["sequence"]["shots"][0].get("takes", []) == []
    assert not (tmp_path / "takes").exists()


def test_timeline_take_capture_skips_non_ready_runtime_registration_without_writing(tmp_path):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineTakeCapture"]
    timeline_input = _timeline_with_shot()
    timeline_input["project"]["storage"]["asset_root_directory"] = str(tmp_path / "takes")
    runtime_debug = {
        "type": "DEBUG_INFO",
        "source": "LTX Runtime",
        "summary": {
            "take_registration_ready": False,
            "take_registration_shot_ids": ["shot_001", "shot_002"],
            "generation_status": "targeted",
            "generation_mode": "Force Full Timeline",
        },
        "take_registration": {
            "type": "TAKE_REGISTRATION_ENVELOPE",
            "shot_id": None,
            "shot_ids": ["shot_001", "shot_002"],
            "registration_ready": False,
            "capture_blockers": ["TAKE_CAPTURE_MULTIPLE_SHOTS"],
            "asset": {"type": ASSET_TYPE_VIDEO},
            "take": {"take_id": "take_multi"},
        },
    }

    assert node.check_lazy_status(timeline_input, runtime_debug=runtime_debug, video=None, images=None) == []

    output = node.execute(
        timeline_input,
        runtime_debug=runtime_debug,
        video=FakeVideo(),
        filename_prefix="should_not_write/%shot_id%/%take_id%",
    )

    timeline = output[0]
    assert output[1] is None
    assert output[2] == ""
    assert output[3] == ""
    assert output[4]["code"] == "TAKE_CAPTURE_SKIPPED_REGISTRATION_NOT_READY"
    assert output[4]["summary"]["capture_blockers"] == ["TAKE_CAPTURE_MULTIPLE_SHOTS"]
    assert output[4]["summary"]["shot_ids"] == ["shot_001", "shot_002"]
    assert output[4]["summary"]["storage_action"] == "skipped"
    assert timeline["assets"] == []
    assert timeline["sequence"]["shots"][0].get("takes", []) == []
    assert not (tmp_path / "takes").exists()


def test_timeline_take_capture_shot_override_allows_manual_capture_when_runtime_registration_is_not_ready(tmp_path):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineTakeCapture"]
    timeline_input = _timeline_with_shot()
    timeline_input["project"]["storage"]["asset_root_directory"] = str(tmp_path / "takes")
    runtime_debug = {
        "type": "DEBUG_INFO",
        "source": "WAN Runtime",
        "summary": {"take_registration_ready": False},
        "take_registration": {
            "type": "TAKE_REGISTRATION_ENVELOPE",
            "shot_id": None,
            "registration_ready": False,
            "capture_blockers": ["TAKE_CAPTURE_NO_SHOT_ID"],
            "asset": {"type": ASSET_TYPE_VIDEO},
            "take": {"take_id": "take_manual"},
        },
    }

    assert node.check_lazy_status(
        timeline_input,
        runtime_debug=runtime_debug,
        shot_id_override="shot_001",
        video=None,
        images=None,
    ) == ["video", "images"]

    output = node.execute(
        timeline_input,
        runtime_debug=runtime_debug,
        shot_id_override="shot_001",
        video=FakeVideo(),
        filename_prefix="manual/%shot_id%/%take_id%",
    )

    timeline = output[0]
    saved_path = Path(timeline["assets"][0]["path"])
    assert output[2] == "asset_generated_001"
    assert output[3] == "take_001"
    assert saved_path.is_file()
    assert saved_path.with_suffix(".helto_take.json").is_file()
    assert timeline["sequence"]["shots"][0]["takes"][0]["take_id"] == "take_001"


def test_timeline_sequence_assembler_node_returns_video_components(monkeypatch):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineSequenceAssembler"]
    node_module = sys.modules[node.__module__]
    frames = torch.zeros((3, 4, 5, 3), dtype=torch.float32)
    audio = {"waveform": torch.zeros((1, 1, 400), dtype=torch.float32), "sample_rate": 16000}
    debug = {
        "type": "DEBUG_INFO",
        "source": "test",
        "summary": {"status": "assembled", "included_clip_count": 1},
    }

    def fake_assemble(video_timeline, *, missing_take_policy):
        assert video_timeline == {"type": "VIDEO_TIMELINE"}
        assert missing_take_policy == "error"
        return frames, audio, 12.5, debug

    monkeypatch.setattr(node_module, "assemble_timeline_sequence", fake_assemble)

    output = node.execute(
        {"type": "VIDEO_TIMELINE"},
        missing_take_policy="error",
        bit_depth=10,
    )

    video = output[0]
    components = video.get_components()
    assert components.images is frames
    assert components.audio is audio
    assert float(components.frame_rate) == 12.5
    assert video.get_bit_depth() == 10
    assert output[1] is frames
    assert output[2] is audio
    assert output[3] == 12.5
    assert output[4] is debug
    assert output[5] is True


def test_timeline_sequence_assembler_node_blocks_media_when_not_assembled(monkeypatch):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineSequenceAssembler"]
    node_module = sys.modules[node.__module__]
    frames = torch.zeros((1, 16, 16, 3), dtype=torch.float32)
    audio = {"waveform": torch.zeros((1, 1, 1), dtype=torch.float32), "sample_rate": 16000}
    debug = {
        "type": "DEBUG_INFO",
        "source": "test",
        "summary": {"status": "not_built", "included_clip_count": 0},
    }

    def fake_assemble(video_timeline, *, missing_take_policy):
        assert missing_take_policy == "warning"
        return frames, audio, 24.0, debug

    monkeypatch.setattr(node_module, "assemble_timeline_sequence", fake_assemble)

    output = node.execute({"type": "VIDEO_TIMELINE"})

    assert isinstance(output[0], ExecutionBlocker)
    assert output[0].message is None
    assert isinstance(output[1], ExecutionBlocker)
    assert output[1].message is None
    assert isinstance(output[2], ExecutionBlocker)
    assert output[2].message is None
    assert output[3] == 24.0
    assert output[4] is debug
    assert output[5] is False


def test_timeline_sequence_assembler_node_preserves_error_policy(monkeypatch):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineSequenceAssembler"]
    node_module = sys.modules[node.__module__]

    def fake_assemble(video_timeline, *, missing_take_policy):
        assert video_timeline == {"type": "VIDEO_TIMELINE"}
        assert missing_take_policy == "error"
        raise ValueError("SEQUENCE_ASSEMBLY_ACCEPTED_TAKE_MISSING")

    monkeypatch.setattr(node_module, "assemble_timeline_sequence", fake_assemble)

    with pytest.raises(ValueError, match="SEQUENCE_ASSEMBLY_ACCEPTED_TAKE_MISSING"):
        node.execute({"type": "VIDEO_TIMELINE"}, missing_take_policy="error")


def test_timeline_take_capture_node_copies_asset_path_to_project_storage_and_writes_sidecar(tmp_path):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineTakeCapture"]
    media_path = tmp_path / "source" / "generated.mov"
    media_path.parent.mkdir()
    media_path.write_bytes(b"source video")
    timeline_input = _timeline_with_shot()
    capture_root = tmp_path / "takes"
    timeline_input["project"]["storage"]["asset_root_directory"] = str(capture_root)
    project_directory = capture_root / timeline_input["project"]["storage"]["project_directory_name"]

    output = node.execute(
        timeline_input,
        take_registration_json=json.dumps(
            {
                "type": "TAKE_REGISTRATION_ENVELOPE",
                "shot_id": "shot_001",
                "asset": {
                    "asset_id": "asset_capture_path",
                    "type": ASSET_TYPE_VIDEO,
                    "name": "generated.mp4",
                },
                "take": {
                    "take_id": "take_capture_path",
                    "seed": 123,
                },
            }
        ),
        generated_asset_path=str(media_path),
        filename_prefix="copied/%shot_id%/%take_id%",
        accept=True,
    )

    timeline = output[0]
    assert output[1] is None
    assert output[2] == "asset_capture_path"
    assert output[3] == "take_capture_path"
    assert output[4]["code"] == "TAKE_CAPTURE_REGISTERED"
    saved_path = Path(timeline["assets"][0]["path"])
    assert media_path.read_bytes() == b"source video"
    assert saved_path.parent == project_directory / "takes" / "shot_001" / "copied" / "shot_001"
    assert saved_path.name.startswith("take_capture_path_")
    assert saved_path.suffix == ".mov"
    assert saved_path.read_bytes() == b"source video"
    assert saved_path.with_suffix(".helto_take.json").is_file()
    assert output[4]["summary"]["storage_action"] == "copied"
    assert output[4]["summary"]["path"] == str(saved_path)
    assert timeline["sequence"]["shots"][0]["accepted_take_id"] == "take_capture_path"
    assert validate_video_timeline(timeline)["is_valid"] is True


def test_timeline_take_capture_node_saves_video_and_registers_sidecar(tmp_path, monkeypatch):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineTakeCapture"]
    node_module = sys.modules[node.__module__]
    monkeypatch.setattr(node_module.folder_paths, "get_output_directory", lambda: str(tmp_path))
    video = FakeVideo()

    output = node.execute(
        _timeline_with_shot(),
        take_registration_json=json.dumps(
            {
                "type": "TAKE_REGISTRATION_ENVELOPE",
                "shot_id": "shot_001",
                "asset": {
                    "asset_id": "asset_capture_video",
                    "type": ASSET_TYPE_VIDEO,
                    "name": "captured.mp4",
                },
                "take": {
                    "take_id": "take_capture_video",
                    "seed": 456,
                    "model_family": "LTX",
                    "model_version": "2.3",
                },
            }
        ),
        video=video,
        filename_prefix="helto_test/%shot_id%/%take_id%",
    )

    timeline = output[0]
    saved_path = Path(timeline["assets"][0]["path"])
    project_directory = tmp_path / "helto_director_projects" / timeline["project"]["storage"]["project_directory_name"]
    assert output[1] is video
    assert saved_path.is_file()
    assert saved_path.parent == project_directory / "takes" / "shot_001" / "helto_test" / "shot_001"
    assert saved_path.name.startswith("take_capture_video_")
    assert saved_path.with_suffix(".helto_take.json").is_file()
    assert output[4]["summary"]["sidecar_filename"] == saved_path.with_suffix(".helto_take.json").name
    assert output[4]["ui"]["filename"] == output.ui["images"][0]["filename"]
    assert output.ui["helto_take_capture_preview"] == [True]
    assert output.ui["helto_privacy_mode"] == [False]
    assert output.ui["animated"] == (True,)
    assert timeline["assets"][0]["metadata"]["frame_count"] == 12
    assert timeline["sequence"]["shots"][0]["takes"][0]["seed"] == 456
    assert validate_video_timeline(timeline)["is_valid"] is True


def test_timeline_take_capture_private_preview_is_tagged_and_debug_redacted(tmp_path, monkeypatch):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineTakeCapture"]
    node_module = sys.modules[node.__module__]
    monkeypatch.setattr(node_module.folder_paths, "get_output_directory", lambda: str(tmp_path))
    timeline_input = _timeline_with_shot()
    timeline_input["project"]["privacy"]["mode"] = True
    video = FakeVideo()

    output = node.execute(
        timeline_input,
        take_registration_json=json.dumps(
            {
                "type": "TAKE_REGISTRATION_ENVELOPE",
                "shot_id": "shot_001",
                "asset": {
                    "asset_id": "asset_private_video",
                    "type": ASSET_TYPE_VIDEO,
                    "name": "private_capture.mp4",
                },
                "take": {
                    "take_id": "take_private_video",
                    "seed": 789,
                },
            }
        ),
        video=video,
        filename_prefix="private/%shot_id%/%take_id%",
    )

    timeline = output[0]
    saved_path = Path(timeline["assets"][0]["path"])
    assert output.ui["helto_take_capture_preview"] == [True]
    assert output.ui["helto_privacy_mode"] == [True]
    assert output.ui["images"][0]["filename"] == saved_path.name
    assert output.ui["images"][0]["subfolder"]
    assert output.ui["animated"] == (True,)
    assert output[4]["summary"]["filename"] == "Generated video"
    assert output[4]["summary"]["subfolder"] is None
    assert output[4]["summary"]["path"] == "Private path"
    assert output[4]["summary"]["sidecar_filename"] == "Private sidecar"
    assert output[4]["summary"]["project_directory"] == "Private path"
    assert output[4]["ui"] == {"private": True}
    assert saved_path.name not in json.dumps(output[4])
    assert validate_video_timeline(timeline)["is_valid"] is True


def test_timeline_take_capture_node_saves_video_to_absolute_project_root(tmp_path, monkeypatch):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineTakeCapture"]
    node_module = sys.modules[node.__module__]
    comfy_output = tmp_path / "comfy_output"
    capture_root = tmp_path / "external_takes"
    timeline_input = _timeline_with_shot()
    timeline_input["project"]["storage"]["asset_root_directory"] = str(capture_root)
    project_directory = capture_root / timeline_input["project"]["storage"]["project_directory_name"]
    monkeypatch.setattr(node_module.folder_paths, "get_output_directory", lambda: str(comfy_output))
    video = FakeVideo()

    output = node.execute(
        timeline_input,
        take_registration_json=json.dumps(
            {
                "type": "TAKE_REGISTRATION_ENVELOPE",
                "shot_id": "shot_001",
                "asset": {
                    "asset_id": "asset_capture_external",
                    "type": ASSET_TYPE_VIDEO,
                },
                "take": {
                    "take_id": "take_capture_external",
                },
            }
        ),
        video=video,
        filename_prefix="nested/%shot_id%/%take_id%",
    )

    timeline = output[0]
    saved_path = Path(timeline["assets"][0]["path"])
    assert saved_path.parent == project_directory / "takes" / "shot_001" / "nested" / "shot_001"
    assert saved_path.name.startswith("take_capture_external_")
    assert saved_path.suffix == ".mp4"
    assert saved_path.is_file()
    assert saved_path.with_suffix(".helto_take.json").is_file()
    assert output[4]["summary"]["storage_action"] == "saved"
    assert output[4]["summary"]["path"] == str(saved_path)
    assert output.ui is None


def test_timeline_take_capture_node_rejects_relative_project_asset_root(tmp_path):
    module = load_nodepack_like_comfyui()
    node = module.NODE_CLASS_MAPPINGS["HeltoTimelineTakeCapture"]
    timeline_input = _timeline_with_shot()
    timeline_input["project"]["storage"]["asset_root_directory"] = "relative/takes"
    video = FakeVideo()

    with pytest.raises(Exception, match="PROJECT_STORAGE_ROOT_NOT_ABSOLUTE"):
        node.execute(
            timeline_input,
            take_registration_json=json.dumps(
                {
                    "type": "TAKE_REGISTRATION_ENVELOPE",
                    "shot_id": "shot_001",
                    "take": {"take_id": "take_relative"},
                }
            ),
            video=video,
        )


class FakeVideo:
    def save_to(self, path, **_kwargs):
        Path(path).write_bytes(b"fake video")

    def get_dimensions(self):
        return 64, 32

    def get_frame_rate(self):
        return 24.0

    def get_frame_count(self):
        return 12

    def get_duration(self):
        return 0.5


def _timeline_with_shot() -> dict:
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["project"]["privacy"]["mode"] = False
    timeline["director_track"]["sections"] = [
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 2.0,
            "prompt": "generate a quiet establishing shot",
        }
    ]
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_001",
            "type": "Generated",
            "start_time": 0.0,
            "end_time": 2.0,
            "section_ids": ["section_001"],
        }
    ]
    return timeline
