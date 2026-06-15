# Workflow Examples

These files are UI-importable ComfyUI workflow examples. They are starting points, not bundled runnable assets.

Before queueing:

1. Replace placeholder checkpoint, VAE, text encoder, or media filenames with files installed in your ComfyUI setup.
2. Open the Director node and adjust timeline media through the pickers when needed.
3. Keep Privacy Mode off for examples unless you are testing encrypted timeline serialization locally.

Examples:

- `ltx_text_only_workflow.json`: minimal Director -> LTX Config -> Planner -> Runtime -> sampler path.
- `ltx_image_video_audio_workflow.json`: timeline JSON with placeholder image, video, and audio asset references.
- `ltx_identity_reference_workflow.json`: LTX runtime with identity anchor and guide-data helper wiring.
- `wan_planner_skeleton_workflow.json`: Director -> WAN Config -> WAN Planner -> WAN Runtime in Plan Only mode.
