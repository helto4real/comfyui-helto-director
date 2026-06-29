# Director Improvement Plans

These plans were selected from a read-only comparison against
`/home/thhel/git/WhatDreamsCost-ComfyUI` LTX Director 2.0. Keep the Director
generic: backend-specific behavior belongs in LTX/WAN config, planner, or
runtime layers.

## Plan 1: Multi-Select And Group Editing

Implement multi-select and group editing for Director timeline items.

Scope:
- Extend the shared timeline UI state with `selected_item_ids` while preserving
  backward compatibility with the existing `selected_item_id`.
- Support multi-select for Director sections and audio clips using standard
  editor interactions such as Ctrl/Cmd-click, Shift-click range selection where
  practical, and Escape to collapse/clear selection.
- Add group move, group delete, and group duplicate behavior.
- Keep current single-item inspector behavior unless a clear grouped inspector
  can be added without UI churn.
- Preserve timeline constraints: Director sections must not overlap unless the
  existing mode permits it, audio clips may keep lane stacking, and locked audio
  clips must not move.
- Keep UI compact and toolbar-consistent with icon buttons, tooltips, and aria
  labels.

Suggested implementation route:
- Update `web/timeline/schema.js`, `web/timeline/migration.js`, and
  `shared/contracts/` only if the selected-id list must serialize.
- Update `web/timeline/operations.js` with pure operations for group delete,
  duplicate, and movement.
- Update `web/timeline/renderer.js` for selection interactions and selected
  styling.
- Add or update focused JS tests in `tests/js/timeline_operations.test.mjs` and
  a UI-level test if needed.

Validation:
- Run `npm run test:js`.
- Run `git diff --check`.

## Plan 2: Mark In/Out Range And Split At Playhead

Add editor-style mark in/out controls plus playhead-aware split and trim
commands.

Scope:
- Add generic UI state for `mark_in_time`, `mark_out_time`, and playhead-aware
  commands.
- Add compact toolbar/menu controls for setting/clearing marks and splitting at
  the playhead.
- Make Split use the playhead when the playhead is inside the selected section,
  falling back to current midpoint behavior only when no usable playhead split
  exists.
- Add operations for trimming selected items to mark range where it is safe and
  generic.
- Keep timeline math in seconds and respect snap mode.
- Avoid model-specific behavior in the Director.

Suggested implementation route:
- Update `web/timeline/schema.js` and `web/timeline/migration.js` for mark
  fields in `ui_state`.
- Update `web/timeline/operations.js` with `splitSelectedSectionAtPlayhead`,
  mark setters/clearers, and safe range helpers.
- Update `web/timeline/renderer.js` to render mark indicators on the ruler and
  compact controls in the toolbar.
- Add focused JS tests for split-at-playhead, invalid playhead fallback, and
  mark normalization.

Validation:
- Run `npm run test:js`.
- Run `git diff --check`.

## Plan 3: Generic Control/Motion Lane For IC-LoRA-Style Video Guides

Add a generic auxiliary control lane that can carry video guide clips for
model-specific runtimes such as LTX IC-LoRA, without making the Director
LTX-specific.

Scope:
- Introduce a generic serialized track, for example `control_tracks[]` or a
  single `motion_track`, whose clips reference existing `assets[]` video media.
- Support adding, replacing, moving, trimming, and deleting control clips in the
  timeline UI.
- Keep media references lightweight: no embedded media, thumbnails, waveforms,
  or decoded frames in workflow JSON.
- Add a model-specific LTX planner/runtime interpretation that converts control
  clips into an explicit motion/IC-LoRA guide payload.
- Preserve the existing `IC_LORA_PARAMETERS` runtime input and use the new lane
  only as timeline timing/media guidance.
- Add debug fields that show requested control clips, applied clips, ignored
  clips, and why clips were ignored.

Suggested implementation route:
- Define the shared contract first in `shared/contracts/`,
  `shared/timeline/`, `web/timeline/schema.js`, and
  `web/timeline/migration.js`.
- Implement UI in `web/timeline/operations.js` and `web/timeline/renderer.js`.
- Extend `shared/ltx/planner.py` to preserve the generic control lane in
  `LTX_TIMELINE_PLAN`.
- Extend `shared/ltx/runtime/` to emit or consume an LTX-specific motion guide
  payload while keeping Director generic.
- Update docs under `docs/current_limitations.md` and the LTX workflow guide.

Validation:
- Run focused JS timeline tests plus `npm run test:js`.
- Run focused LTX Python tests such as
  `tests/ltx/test_runtime_media_audio_loras.py` and relevant
  media/runtime tests.
- Run `git diff --check`.

## Plan 4: Protected Region / Retake Workflow

Add a generic protected-region workflow that model-specific runtimes can use for
retake-style generation.

Scope:
- Add generic timeline contract fields for protected/regenerate regions, for
  example a region with start time, duration, prompt, strength, and optional
  base video media reference.
- Keep the Director UI generic: present this as protected/regenerate regions,
  not as an LTX-only Retake Mode.
- Preserve normal timeline prompts and global prompt behavior outside the
  selected regenerate region.
- Add planner debug showing before/protected/after ranges and how they map to
  generated frames.
- Add LTX runtime support only where the current LTX guide/latent/mask path can
  represent the behavior safely.
- If WAN cannot apply the behavior yet, preserve it as plan/debug metadata and
  report unsupported runtime behavior clearly.

Suggested implementation route:
- Define the shared region contract in `shared/contracts/`,
  `shared/timeline/`, `web/timeline/schema.js`, and migration code.
- Add minimal UI for creating/selecting/editing a region in
  `web/timeline/renderer.js`.
- Add validation entries for invalid region ranges or missing base media.
- Extend LTX planner/runtime with protected-region handling and debug output.
- Document runtime support and unsupported backend behavior.

Validation:
- Run `npm run test:js`.
- Run focused LTX planner/runtime tests.
- Run focused WAN planner/runtime tests if WAN debug metadata changes.
- Run `git diff --check`.

## Plan 5: Explicit Audio Gap Policy And Generated-Audio Fill

Make timeline audio gap handling explicit and debuggable.

Scope:
- Add a model-specific LTX audio policy that distinguishes at least:
  `Silence Gaps`, `Mix Timeline Audio`, `Source Video Fallback`, and
  `Native Generated Fill` where supported.
- Keep generic Director audio clips unchanged; the policy belongs in LTX config
  and runtime, not in the Director timeline itself.
- Preserve existing audio mix behavior as the default unless the user chooses a
  different policy.
- Use connected `audio_vae` and model capability checks before attempting native
  generated audio.
- Add clear runtime debug fields for selected policy, applied policy, fallback
  reason, decoded source-video audio, generated/native audio, and final returned
  audio.
- Update docs so users understand when imported audio is preserved, when gaps
  are silent, and when native/generated audio can fill gaps.

Suggested implementation route:
- Extend `shared/ltx/config.py` and `nodes/ltx_timeline_config/node.py` with
  the explicit audio policy option.
- Extend `shared/ltx/runtime/audio.py`, `shared/ltx/runtime/runtime.py`, and
  segmented execution where needed.
- Keep the Director toolbar `Use Native Audio` behavior compatible or migrate
  it into the new policy without breaking old timelines.
- Add Python tests for each policy and fallback path.
- Update `docs/examples/ltx_timeline_workflow_guide.md` and
  `docs/current_limitations.md`.

Validation:
- Run focused LTX audio/runtime tests.
- Run segmented executor tests if segmented audio stitching changes.
- Run `git diff --check`.
