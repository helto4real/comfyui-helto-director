
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
- Phase 9 adds the LTX 2.3 Timeline Runtime node with Prompt Relay model patching, image/video guide data/application, default tail-frame video guidance, provided-audio mixing, native-audio mode gating, optional negative conditioning, video/audio latents, trimmed source-video outputs, and runtime debug.
- Phase 10 adds LTX 2.3 Timeline identity/reference helper nodes, reference selection from runtime guide data, crop-reference-tail support, runtime identity anchor application, and third-party notices for bundled/adapted helper code.

Phase 10 is now being stabilized through practical ComfyUI graph usage before moving to WAN 2.2. The current LTX path covers text, image, video, provided audio, native-audio gating, identity/reference helpers, and clear failure scenarios.

UI preference for further edits: use compact icon buttons and icon menu controls for node buttons/selection controls where practical, matching the timeline toolbar style. Keep full English labels in tooltips, aria labels, and dropdown menu items rather than putting bulky text controls in the node body.

WAN 2.2 planning/runtime remains deferred until the LTX runtime path has been exercised end to end.
