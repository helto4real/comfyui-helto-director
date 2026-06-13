# ComfyUI Helto Director

Generic ComfyUI nodepack for video timeline authoring and downstream model-specific planning/runtime execution.

## Phase Status

- The nodepack loads as a ComfyUI V3 extension.
- `WEB_DIRECTORY` points to `web/`.
- Custom socket type names are defined in `shared/contracts/socket_types.py`.
- `Video Timeline Director` exposes generic project widgets and custom outputs.
- Phase 1 shared timeline contracts are available under `shared/timeline/`.
- Phase 2 Director backend parsing, normalization, visible-property application, and validation are wired.
- Phase 3 frontend state shell mounts on the Director node and syncs `video_timeline_json`.
- Phase 4 timeline renderer/interactions cover sections, gaps, playhead, selection, split/duplicate/delete, snapping, zoom-to-fit, and audio lane stacking.

Media picking, LTX 2.3 planning/runtime, and WAN 2.2 planning/runtime are intentionally left for later phases.
