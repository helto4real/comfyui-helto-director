# Current Limitations

This nodepack is usable for LTX Timeline workflows and early WAN planning, but it is still intentionally scoped.

## LTX

- LTX 2.3 has Config, Planner, Runtime, prompt optimizer, source-video guidance, audio mixing/native-audio gating, and identity/reference helper nodes.
- LTX runtime examples still require the user's installed LTX model, CLIP/text encoder, VAE, sampler, and output nodes.
- Source-video stitching helpers beyond the current trimmed source outputs remain future work.

## WAN

- WAN 2.2 currently has Config and Planner nodes only.
- WAN plans preserve prompt, media, and audio metadata, but Image, Video, and Audio timeline features are warning-only in the skeleton.
- There is no WAN Runtime node yet, so WAN examples stop at planning/debug inspection.

## Workflow JSON Examples

The workflow examples under `docs/workflows/` are UI-importable starting points. Replace placeholder model and media filenames with files installed in your ComfyUI setup before queueing.
