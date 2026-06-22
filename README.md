# ComfyUI Helto Director

Generic ComfyUI nodepack for video timeline authoring and downstream model-specific planning/runtime execution.

## Installation

Install into your ComfyUI custom nodes folder:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/helto4real/comfyui-helto-director
```

Restart ComfyUI after cloning.

## Workflow Guide

See [LTX 2.3 Timeline Workflow Guide](docs/examples/ltx_timeline_workflow_guide.md) for practical graph wiring, source-video extension, prompt optimizer, identity/reference helpers, audio modes, and privacy mode behavior.

See [Shot, Take, and Sequence Workflow](docs/shot_take_sequence_workflow.md) for shot-based generation, take capture, accepted clips, imported shots, and sequence assembly.

## Quick Links

- [Workflow examples](docs/workflows/README.md)
- [Shot, take, and sequence workflow](docs/shot_take_sequence_workflow.md)
- [Media picker setup](docs/picker_setup.md)
- [Privacy mode limitations](docs/privacy_limitations.md)
- [Current limitations](docs/current_limitations.md)
- [WAN 2.2 Timeline support](docs/WAN22_SUPPORT.md)
- [WAN 2.2 manual test checklist](docs/WAN22_MANUAL_TEST_CHECKLIST.md)

## Developer Context

See [AGENTS.md](AGENTS.md) for the compact agent routing guide, code boundaries, and validation commands.
