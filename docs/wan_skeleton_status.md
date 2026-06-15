# WAN 2.2 Status

WAN 2.2 support has moved beyond the Phase 11 planner skeleton into Phase 16 ComfyUI Core runtime execution.

Implemented:

- `WAN 2.2 Timeline Config`
- `WAN 2.2 Timeline Planner`
- `WAN 2.2 Timeline Runtime`
- `WAN_TIMELINE_CONFIG`
- `WAN_TIMELINE_PLAN`
- I2V-A14B as the default model mode
- Prompt Relay planning with latent chunk segment lengths
- all Image Sections preserved as requested visual keyframe candidates
- Plan Only runtime debug mode
- ComfyUI Core runtime execution for supported WAN prompt conditioning, Prompt Relay model patching, Start/End image conditioning, and WAN latent creation
- explicit `WAN_REQUIRED_IMAGE_CONDITIONING_MISSING` failures for default I2V-A14B execution without a Start image
- dual high-noise and low-noise model sockets for Prompt Relay patching
- Auto backend resolution to Plan Only or ComfyUI Core
- runtime compatibility reports under `runtime_debug.backend`
- compact user-facing runtime summaries under `runtime_debug.status`
- importable WAN workflow examples for text-only, first-image I2V, timed keyframe planning, and audio final-mix metadata
- warnings for unsupported Timed keyframes, Video Sections, and audio conditioning
- runtime debug fields for selected primary image, output payload type, media/helper decisions, and known limitations

Still limited:

- Timed visual keyframes are planned but not applied by the ComfyUI Core backend.
- ComfyUI Core mode requires CLIP and VAE, and Prompt Relay requires at least one connected WAN model phase.
- Default I2V-A14B ComfyUI Core execution requires at least one Image Section.
- WanVideoWrapper integration is not implemented.
- WAN Video Section conditioning is not implemented.
- WAN audio conditioning, S2V, Animate, and reference library behavior are not implemented.

See [WAN 2.2 Timeline Support](WAN22_SUPPORT.md) for the current workflow and debug fields, and [WAN 2.2 Manual Test Checklist](WAN22_MANUAL_TEST_CHECKLIST.md) for hands-on verification.
