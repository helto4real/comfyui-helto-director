# ComfyUI Helto Director

Generic ComfyUI nodepack for video timeline authoring and downstream model-specific planning/runtime execution.

## Phase Status

- The nodepack loads as a ComfyUI V3 extension.
- `WEB_DIRECTORY` points to `web/`.
- Custom socket type names are defined in `shared/contracts/socket_types.py`.
- `Video Timeline Director` appears with no inputs and placeholder custom outputs.
- Phase 1 shared timeline contracts are available under `shared/timeline/`.

Director backend wiring, frontend editing, media picking, LTX 2.3 planning/runtime, and WAN 2.2 planning/runtime are intentionally left for later phases.
