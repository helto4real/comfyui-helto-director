# WAN 2.2 Timeline Support

For first-run installation and node discovery, start with
[Getting Started](getting_started.md). For a grouped list of WAN and non-WAN
nodes, see [Node Reference](node_reference.md).

WAN 2.2 support uses this node path:

`Video Timeline Director -> WAN 2.2 Timeline Config -> WAN 2.2 Timeline Planner -> WAN 2.2 Timeline Runtime`

The Director stays generic. WAN-specific behavior lives in the WAN Config, Planner, and Runtime nodes.

## Defaults

- Model Mode: `I2V-A14B`
- Prompt Routing: `Prompt Relay`
- Bernini Task Prompt: `Auto`
- Visual Conditioning Mode: `Timed Keyframes`
- Runtime Backend Profile: `Plan Only`

`Plan Only` is the safe default. It validates the WAN plan, reports backend compatibility, and returns runtime debug without attempting WAN tensor conditioning. Switch to `ComfyUI Core` when you want the runtime to materialize supported WAN conditioning and latent outputs.

## Backend Profiles

- `Plan Only`: diagnostic mode. It preserves prompt, keyframe, and audio metadata and explains what would or would not run.
- `Auto`: resolves to `ComfyUI Core` only when CLIP, VAE, and at least one high/low WAN model phase are connected; otherwise it resolves to `Plan Only`.
- `ComfyUI Core`: executable mode for the supported core path. It builds prompt conditioning, patches connected model phases for Prompt Relay, calls ComfyUI Core WAN image/latent helpers, creates a WAN latent, and applies supported Start/End image conditioning.
- `WanVideoWrapper`: reserved profile. Selecting it fails clearly because this nodepack does not currently provide that backend.

## Runtime Model Wiring

WAN 2.2 workflows commonly use separate high-noise and low-noise model phases. The Runtime therefore exposes:

- `high_noise_model`
- `low_noise_model`

These model sockets are optional so `Plan Only` can run without model loaders. In `ComfyUI Core` mode, the core WAN image/latent helpers use `clip` and `vae` for conditioning; the model sockets are used for Prompt Relay patching and pass-through.

When Prompt Relay is enabled:

- both connected model phases are patched independently,
- one connected phase is patched and the missing phase is reported as a warning,
- no connected model phase is an error in `ComfyUI Core`,
- `Auto` resolves to `ComfyUI Core` only when `clip`, `vae`, and at least one model phase are connected; otherwise it resolves to `Plan Only`.

The runtime builds the output latent from the connected model latent format when a model phase is present. This matters for WanMoe high/low workflows, which commonly expect 16-channel WAN latents and a matching WAN VAE. If image keyframe conditioning is enabled and the VAE encodes into a different latent format than the connected model expects, the runtime fails with `WAN_RUNTIME_LATENT_FORMAT_MISMATCH` before the sampler runs. For 48-channel WAN 2.2 VAE/model wiring, the Core path can use the WAN 2.2 image-to-video latent helper and reports that helper choice in `runtime_debug.media_decisions`.

## Prompt Relay

Text Sections become temporal Prompt Relay segments. The Project Global Prompt is stored as the WAN global prompt. Section prompts become local prompt segments, and the planner records:

- `video_frame_count`
- `latent_chunk_count`
- `segment_lengths`
- `section_to_latent_mapping`

WAN uses a 4-frame temporal stride. Planner segment lengths always sum to `latent_chunk_count`.

## Bernini-A14B Mode

Select `Bernini-A14B` in WAN Model Mode to use Bernini task prompting with the supported ComfyUI Core conditioning path.

Bernini Task Prompt controls the trained task system prompt prepended before T5 text:

- `Auto`: choose `t2v`, `i2v`, or `v2v` from timeline media.
- `Off`: run Bernini mode without adding a Bernini system prompt.
- `t2v`, `i2v`, `v2v`, `r2v`, `rv2v`: force a supported task prompt.

Auto rules are conservative:

- text-only timelines use `t2v`.
- one or more Image Sections use `i2v`; multiple images remain timeline keyframes/storyboard beats, not `r2v` references.
- any Video Section uses `v2v`; the first usable video is passed as Bernini `source_video`.
- prompt-tagged Director character references use `r2v` when no timeline media is present.
- prompt-tagged Director character references plus Image/Video Sections use `rv2v`, with the first timeline image/video as source/background context.

The first-pass runtime passes only one timeline source into Bernini:

- first Image Section -> single-frame Bernini `source_video` context.
- first Video Section -> decoded source-video frames.

Image-only `i2v` uses a compatibility mapping because ComfyUI Core does not expose a dedicated Bernini start-image input. The runtime keeps the Bernini `i2v` system prompt and passes the first timeline image as single-frame `source_video`, producing Bernini `context_latents`. It does not pass normal timeline images into `reference_images`, because that implies Bernini reference-token tasks such as `r2v`.

Director character references are the Bernini subject-reference path. Add them from the Director reference manager and mention them in section prompts with tags such as `@image1:character` or `@image1:character[0.8]`. Tagged, enabled references are loaded as Bernini `reference_images`; the tag text is replaced with the reference description before WAN prompt encoding. Strength overrides are preserved in debug, but ComfyUI Core `BerniniConditioning` does not expose per-reference strength control.

Only prompt-tagged references are passed to Bernini. Untagged active references stay available in the Director UI but do not influence WAN generation. Bernini accepts up to eight subject reference images through the current ComfyUI Core autogrow input; extra tagged references are reported and ignored.

Additional timeline images/videos are preserved in planner/runtime debug as unavailable media for the selected backend. Timeline media is never reclassified as a Bernini subject reference; timeline images/videos are source/background context, and Director references are subject references. `ads2v`, `vi2v`, `vrc2v`, and `mv2v` are reported as unavailable task types.

Bernini system prompts are prefixes only. ComfyUI Core execution fails with `BERNINI_NO_USER_CONDITIONING` if Bernini receives no user prompt text and no timeline image/video media, because generating from only the trained task prefix usually indicates an unconnected or unserialized Director timeline.

## Visual Keyframes

Every enabled Image Section with a valid asset is preserved as a requested visual keyframe candidate. The planner does not drop middle images.

Roles are assigned by timeline order:

- one image: `Start`
- two images: `Start`, `End`
- three or more images: `Start`, `Timed`, ..., `End`

Runtime backend capabilities decide what can be applied:

- `requested_keyframes`: all image sections requested by the timeline
- `applied_keyframes`: keyframes applied by the selected backend
- `unsupported_keyframes`: requested keyframes the backend cannot apply

The `ComfyUI Core` backend currently applies Start and End image conditioning. Timed keyframes stay visible in debug, but are reported unsupported.

For default `I2V-A14B` execution, at least one usable Image Section is required. If the timeline has no Start image keyframe, `ComfyUI Core` mode fails clearly with `WAN_REQUIRED_IMAGE_CONDITIONING_MISSING` instead of silently producing a text-only fallback. Use `Plan Only` for inspection, add an Image Section, or select a text-capable model mode when you intentionally want no image guidance.

## Video, Audio, And References

- Video Sections are prompt-only fallback for vanilla WAN unless policy is set to error; Bernini can use the first Video Section as `source_video`.
- Audio clips are preserved as final-mix metadata only; WAN generation is not audio-conditioned.
- Bernini subject references come from Director character references. Vanilla WAN A14B subject reference images, Animate, S2V, arbitrary Timed keyframe tensor application, and WanVideoWrapper runtime integration are not currently supported.

## Debugging

Set Debug Mode to `Summary` or `Full` on the WAN Config node. Inspect the Planner `DEBUG_INFO` for prompt/keyframe planning, and Runtime `runtime_debug` for:

- `backend`: requested/resolved backend profile, availability, missing requirements, unsupported features, and recommended next action.
- `status`: compact user-facing summary of execution, Prompt Relay, visual keyframes, audio policy, and validation counts.
- `visual_conditioning`: requested, applied, and unsupported keyframes.
- `output_payload_type`: the runtime output family, such as `COMFYUI_CORE_CONDITIONING_LATENT`.
- `prompt_relay`: full prompt and token/range debug.
- `model_patch_status`: high/low model phase patch status.
- `known_limitations`: limitations that still apply to the resolved backend.

Use [WAN 2.2 Manual Test Checklist](WAN22_MANUAL_TEST_CHECKLIST.md) for hands-on verification steps.
