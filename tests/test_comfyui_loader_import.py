import asyncio
import importlib.util
import sys
from pathlib import Path


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
