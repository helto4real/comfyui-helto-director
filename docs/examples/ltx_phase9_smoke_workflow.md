# LTX 2.3 Timeline Runtime Smoke Workflow

This smoke recipe verifies the Phase 9 graph path from the generic Director through
the LTX planner and runtime. It is intentionally small and uses normal ComfyUI LTX
loader/sampler nodes around the Helto Director nodes.

## Minimal Graph

Wire the Helto nodes in this order:

1. `Video Timeline Director`
2. `LTX 2.3 Timeline Config`
3. `LTX 2.3 Timeline Planner`
4. `LTX 2.3 Timeline Runtime`

Connect:

- Director `VIDEO_TIMELINE` to Planner `VIDEO_TIMELINE`.
- Config `LTX_TIMELINE_CONFIG` to Planner `LTX_TIMELINE_CONFIG`.
- Planner `LTX_TIMELINE_PLAN` to Runtime `LTX_TIMELINE_PLAN`.
- LTX model loader outputs to Runtime `model`, `clip`, and `vae`.
- Optional Audio VAE loader output to Runtime `audio_vae` when provided-audio or native-audio latents are needed.
- Optional custom negative conditioning to Runtime `negative`; leave it disconnected to use the runtime's internal zeroed negative conditioning.

## Timeline Setup

Use a short project first:

- Duration: `1.0` to `2.0` seconds.
- Frame Rate: `24`.
- Quality Preset: `Quick Draft`.
- Add one text or image section covering the whole duration.
- For image smoke testing, attach one local image and set a nonzero guide strength.
- For provided-audio smoke testing, attach one local WAV/audio clip and keep `Use Native Audio` off.
- For native-audio smoke testing, turn `Use Native Audio` on and use an LTX audio-video model that supports native audio.

## Expected Runtime Outputs

- `model`: patched for Prompt Relay when Prompt Relay is enabled.
- `positive`: encoded timeline prompt conditioning.
- `negative`: connected negative conditioning, or zeroed positive conditioning when no negative is connected.
- `video_latent`: LTX-shaped latent for the resolved frame count and quality preset.
- `combined_audio`: mixed timeline audio when `Use Native Audio` is off.
- `audio_latent`: encoded/placeholder LTX audio latent depending on `audio_vae` and audio mode.
- `guide_data`: image guide metadata when image sections are present; zero-strength dummy guide data for text-only timelines.
- `runtime_debug`: summary counts for sections, guides, audio clips, latent shape, and diagnostics.

## Expected Failures

- Missing media files should fail with `Media file not found: ...`.
- Invalid planner validation should fail before runtime materialization.
- `Use Native Audio` with a non-native-audio LTX model should fail with a clear unsupported-model error.
- Missing `audio_vae` should not block provided-audio mixing, but the runtime returns an empty audio latent placeholder and records a diagnostic.

Source-video sections are the next Phase 9 follow-up target and are not required for this smoke recipe.
