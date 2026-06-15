# WAN 2.2 Status

WAN 2.2 support has moved beyond the Phase 11 planner skeleton into Phase 13 runtime planning and Plan Only execution support.

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
- ComfyUI Core runtime adapter for Start and End image conditioning
- warnings for unsupported Timed keyframes, Video Sections, and audio conditioning

Still limited:

- Timed visual keyframes are planned but not applied by the ComfyUI Core backend.
- WanVideoWrapper integration is not implemented.
- WAN Video Section conditioning is not implemented.
- WAN audio conditioning, S2V, Animate, and reference library behavior are not implemented.

See [WAN 2.2 Timeline Support](WAN22_SUPPORT.md) for the current workflow and debug fields.
