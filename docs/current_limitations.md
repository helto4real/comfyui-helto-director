# Current Limitations

This nodepack is usable for LTX Timeline workflows and early WAN 2.2 planning/runtime debug, but it is still intentionally scoped.

## LTX

- LTX 2.3 has Config, Planner, Runtime, prompt optimizer, source-video guidance, audio mixing/native-audio gating, and identity/reference helper nodes.
- LTX runtime examples still require the user's installed LTX model, CLIP/text encoder, VAE, sampler, and output nodes.
- Source-video stitching helpers beyond the current trimmed source outputs remain future work.

## WAN

- WAN 2.2 has Config, Planner, and Runtime nodes.
- I2V-A14B is the default WAN mode.
- Prompt Relay planning and all Image Section visual keyframe candidates are preserved in `WAN_TIMELINE_PLAN`.
- The default Runtime Backend Profile is `Plan Only`, with `runtime_debug.backend` and `runtime_debug.status` intended for workflow inspection.
- The Runtime uses separate optional `high_noise_model` and `low_noise_model` sockets for WAN 2.2 Prompt Relay patching.
- The `ComfyUI Core` backend can apply Start and End image conditioning, but Timed keyframes are planned/debug-visible only.
- `ComfyUI Core` requires CLIP and VAE, and Prompt Relay requires at least one connected WAN model phase.
- Video Sections, WAN audio conditioning, S2V, Animate, reference library support, arbitrary Timed keyframe execution, and WanVideoWrapper integration remain future work.

## Workflow JSON Examples

The workflow examples under `docs/workflows/` are UI-importable starting points. Replace placeholder model and media filenames with files installed in your ComfyUI setup before queueing.
