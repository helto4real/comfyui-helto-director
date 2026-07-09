# Shot, Take, And Sequence Workflow

This guide describes the current practical workflow for generating clips shot by
shot, keeping takes, and assembling an accepted sequence. The Director timeline
stays generic; LTX and WAN model behavior remains in their planner/runtime
nodes.

For the full node list, see [Node Reference](node_reference.md).

## Shot-Based Generation

The timeline has one active `sequence` named `main`. A sequence contains shots,
boundaries, and audio tracks. Existing `director_track.sections` remain the
planner-facing prompt/media/timing bridge.

To generate one shot:

1. Create or select a compatible shot.
2. Add Text, Image, or Video Sections while that shot is selected.
3. Use the LTX or WAN planner's `Generation Mode` control to choose the target.
4. Run the existing planner/runtime path for that model.
5. Register the generated output as an asset and attach it as a take.
6. Accept the best take when it is ready for assembly.

`Missing Only` is the default. It targets the selected shot when that shot is
not assembly-ready, otherwise the earliest generatable shot that is not ready;
it skips generation when every shot is ready. `Force Selected` regenerates the
currently selected generatable shot, including one that is already ready.
`Force Full Timeline` bypasses shot targeting and plans the complete timeline.

The removed `shot_id` planner argument is accepted only for compatibility with
older programmatic callers and saved widget values. A legacy ID takes
precedence, produces `GENERATION_LEGACY_SHOT_ID_DEPRECATED`, and should not be
used in new workflows.

## Shot-Local Extraction

When generation policy targets one shot, the shared shot extraction helper
builds a temporary shot-local timeline:

- project duration becomes the selected shot duration;
- selected shot sections are shifted to local time starting at `0`;
- project settings, assets, privacy settings, character references, global
  prompt, audio settings, and model LoRAs are preserved;
- the local sequence contains only the selected shot;
- generic `shot_context` records the original time range, duration, section IDs,
  shot LoRA overrides, and boundary context.

Invalid legacy shot IDs do not crash the planner or silently fall back to
another mode. They block generation with a normal-shaped empty generation plan
and a `GENERATION_LEGACY_SHOT_NOT_FOUND` validation error.

## Boundary Context

Boundary context is generic metadata exposed to planners. It does not make the
Director runtime model-specific.

The context includes:

- previous and next shot IDs;
- incoming and outgoing boundary summaries;
- adjacent accepted take and clip asset references when available;
- continuity policy;
- `tail_frames` and `blend_frames` preferences.

Current boundary behavior:

- `Hard Cut` means no continuity intent.
- `Continuous Shot` exposes continuity intent and adjacent clip references when
  available.
- `Blend Seam` preserves blend preferences for assembly when compatible.
- `Transition` preserves transition metadata but does not invent unsupported
  model behavior.

Continuous or blended boundaries across different LoRA stacks can create visible
style or identity seams. The UI reports LoRA mismatch warnings from validation.

## Takes And Generated Assets

A take is one generated attempt for a shot. Takes are saved under their owning
shot and can be `Candidate`, `Accepted`, or `Rejected`.

Generated media is represented as a project asset whose `source_kind` is
`Generated`. Workflow JSON stores only asset references and metadata. It must
not embed video bytes, image bytes, thumbnails, waveform arrays, blobs, or data
URLs.

Take metadata can include:

- asset ID;
- model family and version;
- seed;
- prompt and plan hashes;
- resolved LoRA snapshot;
- runtime/settings metadata.

Runtime and executor context payloads may produce take-registration metadata.
Attaching generated output to the timeline is currently manual or
semi-automatic through UI/helper flows rather than automatic hidden mutation of
the Director node after execution.

## Accepting And Rejecting Takes

Accepting a video take:

- sets `shot.accepted_take_id`;
- marks that take as `Accepted`;
- restores any previously accepted take to `Candidate`;
- sets `shot.clip_instance` to the accepted video asset so sequence assembly can
  use it;
- preserves the shot type, so accepting a generated take does not turn the shot
  into an `Imported` shot.

Rejecting an accepted take clears the accepted state and removes the matching
clip instance. Restoring a take to `Candidate` keeps the asset and take metadata
available for later review.

## Imported Clip Shots

Imported shots use the same clip-instance mechanism as accepted generated
takes, but they are still semantically different. Assigning an existing video
asset as an imported shot clip sets the shot type to `Imported`.

Imported clips can participate in sequence assembly even when they have no
generated takes.

## Sequence Assembly

Sequence assembly is separate from generation:

- generation creates takes;
- assembly stitches accepted takes and imported clips.

The shared backend assembly helper reads the normalized timeline and returns:

- assembled frames;
- mixed audio metadata/output;
- frame rate;
- debug info.

Assembly currently supports hard-cut concatenation, imported clip instances, and
accepted generated video takes. It records debug summaries for included clips,
missing accepted takes, missing assets, resolution policy, boundary behavior,
and audio mixing.

When no accepted generated take or imported clip is ready, the Timeline Sequence
Assembler blocks its media outputs silently instead of producing a black
placeholder. Multi-shot sequences also block until every generated, edited, or
extended shot has an accepted take; imported shots count as ready when they have
an enabled imported video clip. This prevents partial sequence output while you
render or review one not-yet-accepted shot.

Blend seams can blend frames when clip shapes are compatible. Unsupported or
advanced transition behavior falls back to hard-cut style assembly with warnings
or preserved metadata rather than claiming model-specific transition support.

## Known Limitations

- Only one sequence is supported for now.
- Full-timeline generation remains supported and is still useful.
- Shot-based generation is additive.
- Take registration may be manual or semi-automatic in the current workflow.
- Automatic mutation of the Director timeline after runtime execution is
  deferred.
- Advanced transitions are deferred.
- Section-level LoRAs are deferred.
- Per-shot LoRAs are reliable when generating shots separately or when segmented
  execution can apply per-shot stacks.
- Continuous boundaries with changed LoRA stacks may cause visible seams.
- Assembly may require compatible frame sizes and frame rates, or use the
  documented fallback behavior.
- Privacy mode encrypts private timeline state, and UI summaries should avoid
  revealing media names, prompt text, paths, and LoRA names unless private data
  is intentionally revealed.
