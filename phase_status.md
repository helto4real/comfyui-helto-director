
## Phase Status

- The nodepack loads as a ComfyUI V3 extension.
- `WEB_DIRECTORY` points to `web/`.
- Custom socket type names are defined in `shared/contracts/socket_types.py`.
- `Video Timeline Director` exposes generic project widgets and custom outputs.
- Phase 1 shared timeline contracts are available under `shared/timeline/`.
- Phase 2 Director backend parsing, normalization, visible-property application, and validation are wired.
- Phase 3 frontend state shell mounts on the Director node and syncs `video_timeline_json`.
- Phase 4 timeline renderer/interactions cover sections, gaps, playhead, selection, split/duplicate/delete, snapping, zoom-to-fit, and audio lane stacking.
- Phase 5 media attachment foundations store file/source asset records, attach media by `asset_id`, reject embedded media payloads, and render audio waveform placeholders.
- Zoom-to-fit now measures the timeline viewport and syncs the visible Zoom Level widget so state commits preserve the fitted zoom.
- Phase 7 media cache routes generate thumbnail `.webp` files and audio waveform peak JSON under ComfyUI temp, with frontend cache hydration for previews/waveforms.
- Phase 8 adds LTX 2.3 Timeline Config and Planner nodes that output serializable LTX plans with frame mapping, resolved dimensions, prompt/media/audio plans, validation, and debug info.

We are now refining the UI before moving phase 9 LTX 2.3 runtime and WAN 2.2 planning/runtime into later phases. The current focus is on improving the timeline interactions, media attachment UX, and cache performance to ensure a solid foundation for the more complex planning and runtime execution in the next phases.

LTX 2.3 runtime and WAN 2.2 planning/runtime are intentionally left for later phases.
