# Current Limitations

This nodepack is usable for LTX Timeline workflows and the supported WAN 2.2 ComfyUI Core runtime path, but it is still intentionally scoped.

For installation and first-run workflow setup, see [Getting Started](getting_started.md).
For the current node list, see [Node Reference](node_reference.md).

## Timeline Contract Terms

- Project: the root timeline settings, assets, privacy state, global prompt, audio defaults, metadata, and model LoRA defaults.
- Timeline / Sequence: the active editing timeline. Only one sequence is supported for now, saved as `sequence_id: "main"`.
- Shot: a timeline-level creative unit such as `Generated`, `Imported`, `Extended`, `Edited`, or `Placeholder`. Shots group one or more planner-facing sections and can carry takes, accepted output state, and shot LoRA overrides.
- Boundary: the relationship between two adjacent shots. Supported modes are `Hard Cut`, `Continuous Shot`, `Blend Seam`, and `Transition`.
- Section: the planner-facing prompt/media/timing item in `director_track.sections`. Sections remain the compatibility bridge used by LTX/WAN planners for now.
- Take: candidate generated-output metadata for a shot. Takes may initially be metadata only unless they reference an existing generated asset.
- Asset: a project-level media reference for an image, video, audio file, uploaded file, generated output, or ComfyUI input. Workflow JSON stores references and metadata, not embedded media payloads.
- Clip Instance: an optional reference from an imported shot to a video asset plus trim/speed metadata. A separate Clip Slot concept is intentionally not required yet.
- Project LoRAs: project-level model-targeted default LoRA stacks.
- Shot LoRA Overrides: optional per-shot LoRA intent that can inherit, add to, replace, or disable the project LoRA stacks.
- Take LoRA Snapshot: the resolved LoRA state recorded with a take when runtime/planner data can identify what was used.
- Model LoRA Target: the model-specific target a LoRA stack applies to. LTX 2.3 has `main`; WAN 2.2 has `high_noise` and `low_noise`.

## Timeline And LoRA Boundaries

- `director_track.sections` remains saved and valid as compatibility/planner-facing data. The newer `sequence.shots` structure groups those sections but does not replace the planner bridge yet.
- Boundary-aware runtime behavior may be partial depending on backend. Hard cuts are safest; continuous/blended/transition boundaries may still depend on the selected planner/runtime path.
- Per-shot LoRA execution may require shot-level generation or segmented execution. If one runtime generation spans shots with different LoRA stacks, the planner/runtime may defer exact per-shot switching.
- Section-level LoRAs are intentionally deferred. LoRAs are project-level, shot-level, take snapshot, or model-runtime targeted in the current contract.
- Legacy `lora_config_hi` and `lora_config_low` timeline workflows are no longer migrated. Old LoRA choices require manual reconfiguration into the model-targeted project/shot stacks.
- LoRA changes across continuous, blended, or transition boundaries can cause visible style or identity seams.

## Shot Generation, Takes, And Assembly

- Shot-based generation is additive. Leaving planner `shot_id` empty keeps the existing full-timeline workflow.
- Selecting a compatible shot in the timeline UI lets new Text/Image/Video Sections attach to that shot instead of creating a second wrapper shot.
- The LTX and WAN planners can receive a selected `shot_id` and plan against a shot-local timeline with generic boundary context.
- Take registration is currently manual or semi-automatic. Runtime/debug metadata can describe a take, but the Director timeline is not automatically mutated after graph execution.
- Generated outputs are represented as `Generated` assets and nested shot takes. Workflow JSON stores asset references and metadata only.
- Accepting a video take updates the shot's accepted take and clip-instance state without changing a generated shot into an imported shot.
- Imported shots can reference existing video assets through clip instances and can participate in sequence assembly without generated takes.
- Sequence assembly is a backend helper path for accepted generated takes and imported clips. Advanced transition rendering is still deferred or preserved as metadata/fallback behavior.
- Assembly may need compatible clip sizes and frame rates, or it will rely on the documented fallback/resizing behavior.

## LTX

- LTX 2.3 has Config, Planner, Runtime, prompt optimizer, source-video guidance, audio mixing/native-audio gating, and identity/reference helper nodes.
- LTX runtime examples still require the user's installed LTX model, CLIP/text encoder, VAE, sampler, and output nodes.
- Source-video stitching helpers beyond the current trimmed source outputs are not currently supported.
- LTX LoRAs use the single `main` model LoRA target.

## WAN

- WAN 2.2 has Config, Planner, and Runtime nodes.
- I2V-A14B is the default WAN mode.
- Prompt Relay planning and all Image Section visual keyframe candidates are preserved in `WAN_TIMELINE_PLAN`.
- The default Runtime Backend Profile is `Plan Only`, with `runtime_context.backend` and `runtime_context.status` intended for workflow inspection.
- The Runtime uses separate optional `high_noise_model` and `low_noise_model` sockets for WAN 2.2 Prompt Relay patching.
- The `ComfyUI Core` backend can execute the supported core path: prompt conditioning, Prompt Relay patching for compatible high/low models, WAN latent creation, and Start/End image conditioning.
- Default `I2V-A14B` ComfyUI Core execution requires at least one Image Section; text-only execution should use `Plan Only` or an explicit text-capable model mode.
- Timed keyframes are preserved in debug output but are not applied as conditioning.
- `ComfyUI Core` requires CLIP and VAE, and Prompt Relay requires at least one connected WAN model phase.
- WAN LoRAs use separate `high_noise` and `low_noise` model LoRA targets.
- Video Sections, WAN audio conditioning, S2V, Animate, reference library support, arbitrary Timed keyframe execution, and WanVideoWrapper integration are not currently supported.

## Workflow JSON Examples

The workflow examples under `docs/workflows/` are UI-importable starting points. Replace placeholder model and media filenames with files installed in your ComfyUI setup before queueing.
