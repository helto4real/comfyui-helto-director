import asyncio
import importlib.util
import sys
from pathlib import Path


def test_comfyui_style_custom_node_loader_imports_package():
    module_path = Path(__file__).resolve().parents[1]
    sys_module_name = str(module_path).replace(".", "_x_")
    spec = importlib.util.spec_from_file_location(
        sys_module_name,
        module_path / "__init__.py",
    )
    module = importlib.util.module_from_spec(spec)

    previous = sys.modules.get(sys_module_name)
    sys.modules[sys_module_name] = module
    try:
        spec.loader.exec_module(module)
        extension = asyncio.run(module.comfy_entrypoint())
        node = asyncio.run(extension.get_node_list())[0]
        schema = node.define_schema()
    finally:
        if previous is None:
            sys.modules.pop(sys_module_name, None)
        else:
            sys.modules[sys_module_name] = previous

    assert schema.node_id == "HeltoVideoTimelineDirector"
    assert [output.io_type for output in schema.outputs] == [
        "VIDEO_TIMELINE",
        "TIMELINE_VALIDATION",
    ]
