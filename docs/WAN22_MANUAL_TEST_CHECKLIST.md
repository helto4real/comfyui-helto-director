# WAN 2.2 Manual Test Checklist

Use this checklist inside ComfyUI when verifying WAN Timeline workflows.

- [ ] Load ComfyUI and confirm the nodepack loads without import errors.
- [ ] Create a `Video Timeline Director` node.
- [ ] Add `WAN 2.2 Timeline Config`.
- [ ] Add `WAN 2.2 Timeline Planner`.
- [ ] Add `WAN 2.2 Timeline Runtime`.
- [ ] Create a text-only timeline.
- [ ] Verify `prompt_relay` appears in Planner `DEBUG_INFO`.
- [ ] Run Runtime in `Plan Only`.
- [ ] Verify `runtime_debug.backend` and `runtime_debug.status` explain that no execution backend ran.
- [ ] Create a timeline with one Image Section.
- [ ] Verify one `Start` keyframe candidate appears in `requested_keyframes`.
- [ ] Create a timeline with four or more Image Sections.
- [ ] Verify all Image Sections appear in `requested_keyframes`.
- [ ] Verify unsupported Timed keyframes are visible with reasons when the backend cannot apply them.
- [ ] Add an Audio Clip.
- [ ] Verify planner validation reports audio as final mix only.
- [ ] Verify runtime summary reports audio as final mix only.
- [ ] Switch Runtime Backend Profile to `Auto`.
- [ ] Verify Auto resolves to `Plan Only` when CLIP, VAE, and model phases are not connected.
- [ ] Connect CLIP, VAE, and at least one WAN high/low model phase.
- [ ] Verify Auto resolves to `ComfyUI Core`.
- [ ] With default `I2V-A14B`, verify ComfyUI Core execution fails clearly with `WAN_REQUIRED_IMAGE_CONDITIONING_MISSING` if no Image Section is present.
- [ ] Add one Image Section and verify Runtime produces `positive`, `negative`, and `video_latent` outputs with `output_payload_type` set to `COMFYUI_CORE_CONDITIONING_LATENT`.
- [ ] Add Start and End Image Sections and verify Runtime reports two applied keyframes.
- [ ] Connect both high-noise and low-noise model phases.
- [ ] Verify Prompt Relay patch status reports both phases patched.
- [ ] Keep notes on any sampler/model/VAE mismatch errors for follow-up backend hardening.
