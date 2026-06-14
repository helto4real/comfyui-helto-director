
You are a senior ComfyUI nodepack engineer and local AI video workflow architect. You are implementing a new generic ComfyUI nodepack for video timeline authoring and model-specific planning/runtime execution.

You must follow the locked design decisions below exactly. Do not redesign the product unless explicitly instructed. If you find a technical constraint that conflicts with the design, stop and propose a minimal adjustment before coding.

# Project Goal

Build a generic ComfyUI video timeline nodepack with this high-level flow:

Video Timeline Director
→ VIDEO_TIMELINE
→ Model Timeline Planner
→ MODEL_TIMELINE_PLAN
→ Model Timeline Runtime

The Video Timeline Director is generic. Model-specific logic belongs downstream in model-specific Config, Planner, and Runtime nodes.

Primary supported model families:

* LTX 2.3
* WAN 2.2

The codebase must be modular internally, but the visible ComfyUI graph should stay simple.

# Hard Rules

Do not violate these rules:

1. The Video Timeline Director must not contain LTX/WAN-specific logic.
2. The Video Timeline Director must not have fixed image/video/audio input slots in V1.
3. Images, video, and audio are attached directly to sections/clips through the timeline UI.
4. V1 has no visible Asset Bin or Media Library.
5. VIDEO_TIMELINE.assets exists only as an internal serialization/detail layer.
6. Do not embed full image/video/audio data, thumbnails, or waveform data in workflow JSON.
7. V1 does not copy selected media. Media stays in its original location.
8. Thumbnails and waveform cache live in ComfyUI temp and are regenerated on demand.
9. Audio clips must show waveform in V1.
10. Privacy Mode V1 hides previews/prompts in UI. Encrypted preview/waveform cache is future-ready but not required.
11. Planner nodes output serializable plans, not tensors.
12. Runtime nodes produce actual ComfyUI runtime objects such as conditioning, latents, guide data, audio, and source frames.
13. Prefer one main Runtime node per model family.
14. Debug output is diagnostic only and must never be required for generation.
15. Keep the nodepack loadable after every phase.

# Repository Context

Use the existing WhatDreamsCost-ComfyUI implementation on branch helto-changes as inspiration, especially for:

* image picker concept
* audio picker concept
* video picker pattern where practical
* timeline canvas interaction ideas
* audio waveform drawing concept
* LTX Director guide/reference ideas
* LTX identity/reference helper nodes

Do not copy the old architecture blindly. The old LTX Director is an all-in-one node. The new architecture separates generic Director, model Planner, and model Runtime.

# Locked Product Model

## Main Timeline

The Director node owns one Main Timeline.

The Main Timeline contains:

* one Director Track
* Audio Tracks below it

## Director Track

The Director Track contains sequential sections:

* Image Section
* Text Section
* Video Section

Director Track rules:

* Sections cannot overlap.
* Gaps are allowed.
* Gaps mean “No Guidance”.
* Project Duration is a hard boundary.
* Remove Section leaves a gap.
* Duplicate Section places copy after original or nearest valid gap.
* Move Section only allows valid non-overlapping placement.
* Split at Playhead copies section settings.
* Video Section split divides source_in/source_out proportionally.
* Boundary edit uses Trim Neighbor.
* Ripple Edit moves following sections but cannot exceed Project Duration.

## Image Section

Image Section fields:

* image: required
* prompt: optional, may be empty
* guide_strength: 0.0–1.0, default 1.0
* crop_mode: Project Default / Crop / Pad / Stretch to Fit / Keep Aspect Ratio

Empty prompt means image-only guidance.

## Text Section

Text Section fields:

* prompt: required, non-empty

Text Section has no media, no guide_strength, and no crop_mode.

## Video Section

Video Section fields:

* video: required
* prompt: optional, may be empty
* guide_strength: 0.0–1.0, default 1.0
* crop_mode: Project Default / Crop / Pad / Stretch to Fit / Keep Aspect Ratio
* source_in
* source_out
* timing_mode

timing_mode values:

* Fit to Section
* Use Source Timing
* Loop
* Freeze Last Frame

## Audio Clips

Audio Clips live below Director Track.

Audio rules:

* Audio Clips may overlap across lanes.
* Audio Clips cannot overlap within the same lane.
* Overlapping clips auto-stack to new lanes.
* Empty audio lanes are cleaned up automatically.
* Left resize trims source_in.
* Right resize trims source_out.
* Moving an audio clip preserves source_in/source_out.
* Volume default is 100.
* If audio normalization is enabled, Volume 100 means normalized level.

Audio Clip fields:

* audio: required
* start_time
* end_time
* source_in
* source_out
* volume
* normalization metadata
* fade_in
* fade_out
* enabled
* locked
* name

# Locked Node Properties and Toolbar Controls

Visible ComfyUI node properties on Video Timeline Director:

* Duration
* Frame Rate
* Aspect Ratio
* Orientation
* Quality Preset
* Zoom Level

Quality Preset values:

* Quick Draft
* Draft
* Standard
* High
* Native Resolution

Do not expose raw width/height on the Director node. Model config/planner nodes resolve width/height from aspect ratio, orientation, and quality preset.

Toolbar icon/toggle controls:

* Timeline Display Mode
* Section Edit Mode
* Use Global Prompt
* Snap Mode: None / Seconds / Frames
* Zoom to Fit
* Project Settings

Zoom Level is a slider on the node.

Default values:

* Allow Gaps: true
* Auto Close Gaps: false
* Section Edit Mode: Trim Neighbor
* Snap Mode: Frames
* Default Image Guide Strength: 1.0
* Default Video Guide Strength: 1.0
* Default Video Timing Mode: Fit to Section
* Default Audio Volume: 100
* Default Audio Fade In: 0.0
* Default Audio Fade Out: 0.0

# Project Settings Modal

Project Settings modal contains:

* Default Crop Mode
* Show Resolved Model Output
* Allow Gaps
* Auto Close Gaps
* Minimum Section Duration
* Global Prompt
* Global Prompt Position
* Show Effective Prompt
* Always Normalize Audio
* Audio Normalization Mode
* Target LUFS
* True Peak Limit
* Default Audio Volume
* Default Audio Fade In
* Default Audio Fade Out
* Privacy Mode
* Hide Media Previews
* Hide Text Prompts
* Encrypt Previews
* Show Section Labels
* Show Thumbnails
* Show Audio Waveforms

All UI concepts/settings must use English names.

# VIDEO_TIMELINE V1 Contract

The Video Timeline Director outputs VIDEO_TIMELINE and TIMELINE_VALIDATION.

Top-level VIDEO_TIMELINE structure:

* schema_version
* type = "VIDEO_TIMELINE"
* project
* ui_state
* assets
* director_track
* audio_tracks
* model_outputs
* validation

project contains:

* duration_seconds
* frame_rate
* aspect_ratio
* orientation
* quality_preset
* default_crop_mode
* settings
* global_prompt
* audio
* privacy
* display

project.settings contains:

* allow_gaps
* auto_close_gaps
* minimum_section_duration_seconds
* show_resolved_model_output

project.global_prompt contains:

* enabled
* prompt
* position
* show_effective_prompt

project.audio contains:

* always_normalize
* normalization_mode
* target_lufs
* true_peak_limit_db
* default_volume
* default_fade_in_seconds
* default_fade_out_seconds

project.privacy contains:

* mode
* hide_media_previews
* hide_text_prompts
* encrypt_previews

project.display contains:

* show_section_labels
* show_thumbnails
* show_audio_waveforms

ui_state contains:

* timeline_display_mode
* section_edit_mode
* snap_mode
* zoom_level
* scroll_x
* selected_item_id
* state_revision

assets are internal serialization records and can be referenced by sections/clips via asset_id.

Asset types:

* Image
* Video
* Audio

Asset source kinds:

* FilePath
* UploadedFile
* Generated
* ComfyUIInput, future only

In V1, prefer FilePath references to original media locations. Do not copy media.

# State Sync and Serialization

Frontend keeps one in-memory TimelineState matching VIDEO_TIMELINE.

The primary hidden widget is:

* video_timeline_json

It contains the complete serialized VIDEO_TIMELINE.

Visible node properties are authoritative for matching project fields.

All timeline mutations must go through:

* commitTimelineChange(reason, options)

commitTimelineChange must:

1. normalize state
2. apply visible node property values
3. validate state
4. serialize VIDEO_TIMELINE to video_timeline_json
5. update hidden widget value
6. mark ComfyUI graph dirty
7. rerender if needed
8. refresh async thumbnails/waveforms if needed

Commit timing:

* Dragging/resizing: commit on mouseup
* Prompt typing: debounced
* Media choose/replace: immediate commit
* Settings changes: immediate commit

Frontend and backend must both validate the timeline.

Schema migration must be built into load/parse.

Local timeline undo/redo stack is required for timeline edits.

Undo/redo commit boundaries include:

* add
* delete
* split
* duplicate
* replace media
* drag end
* resize end
* settings change

# Validation Contract

Validation has three severities:

* Error
* Warning
* Info

Validation object:

{
"is_valid": true,
"errors": [],
"warnings": [],
"info": []
}

Entry format:

{
"code": "TEXT_SECTION_EMPTY_PROMPT",
"severity": "Error",
"source": "Director",
"scope": "Section",
"item_id": "section_002",
"message": "Text Section requires a non-empty prompt.",
"hint": "Add a prompt or remove the Text Section.",
"details": {}
}

Rules:

* is_valid is false only when Error entries exist.
* Director validation is generic and preserves user data.
* Director gaps are Info, never errors.
* Planner validation is model-specific and may upgrade gaps/unsupported features to warnings/errors.
* Runtime validation is strict and may fail fast with clear ValueError messages.
* Director always outputs VIDEO_TIMELINE + TIMELINE_VALIDATION even if invalid.
* Planner should avoid hard crashes and return validation when possible.
* Runtime may raise clear ValueError when execution is impossible.
* Normalization may repair harmless defaults but must not delete, move, or rewrite user content silently.

# Custom Socket Types

Define and use these custom socket types:

* VIDEO_TIMELINE
* TIMELINE_VALIDATION
* DEBUG_INFO
* LTX_TIMELINE_CONFIG
* LTX_TIMELINE_PLAN
* WAN_TIMELINE_CONFIG
* WAN_TIMELINE_PLAN

Future/helper types:

* GUIDE_DATA
* LTX_IDENTITY_ANCHOR

# Node Graph Architecture

Visible nodes:

* Video Timeline Director
* LTX 2.3 Timeline Config
* LTX 2.3 Timeline Planner
* LTX 2.3 Timeline Runtime
* WAN 2.2 Timeline Config
* WAN 2.2 Timeline Planner
* WAN 2.2 Timeline Runtime

Future/advanced helper nodes:

* LTX Timeline Crop Reference Tail
* LTX Reference Image Selector
* LTX Identity Anchor: Latent Aware
* LTX Identity Anchor: Face
* LTX Identity Anchor: Combine
* LTX Apply Identity Anchor

## Video Timeline Director

Inputs:

* none

Outputs:

* VIDEO_TIMELINE
* TIMELINE_VALIDATION

## LTX 2.3 Timeline Config

Inputs:

* none

Outputs:

* LTX_TIMELINE_CONFIG

Owns LTX policy:

* resolution profile
* Prompt Relay epsilon
* image guidance mode
* video section mode
* reference mode
* audio mode
* debug mode
* divisible_by = 32
* frame rule = 8n+1
* temporal stride = 8

## LTX 2.3 Timeline Planner

Inputs:

* VIDEO_TIMELINE
* LTX_TIMELINE_CONFIG

Outputs:

* LTX_TIMELINE_PLAN
* TIMELINE_VALIDATION
* DEBUG_INFO

## LTX 2.3 Timeline Runtime

Inputs:

* model
* clip
* negative
* vae
* LTX_TIMELINE_PLAN
* optional_latent optional
* audio_vae optional
* identity_anchor optional
* sigmas optional
* iclora_parameters optional

Outputs:

* model
* positive
* negative
* video_latent
* audio_latent
* combined_audio
* guide_data
* source_video_images
* source_video_audio
* source_video_frame_rate
* source_video_frame_count
* runtime_debug

## WAN 2.2 Timeline Config

Inputs:

* none

Outputs:

* WAN_TIMELINE_CONFIG

## WAN 2.2 Timeline Planner

Inputs:

* VIDEO_TIMELINE
* WAN_TIMELINE_CONFIG

Outputs:

* WAN_TIMELINE_PLAN
* TIMELINE_VALIDATION
* DEBUG_INFO

## WAN 2.2 Timeline Runtime

WAN runtime can remain minimal/skeletal until the exact WAN workflow is chosen.

# Planner Contract

Planner nodes output serializable model-specific plans, not final tensors.

Common planner envelope:

* schema_version
* type
* model_family
* model_version
* source_timeline_schema_version
* project
* resolved_output
* section_plan
* prompt_plan
* media_plan
* audio_plan
* model_specific
* validation

Rules:

* Section-to-frame mapping is explicit.
* Gaps may be represented as Gap / No Guidance entries.
* Prompt merging happens in the planner.
* Each section gets an effective_prompt.
* Media is mapped to model-specific guidance roles.
* Model-specific details live under model_specific.
* DEBUG_INFO is separate, optional, and human-oriented.

LTX-specific data goes under:

* model_specific.ltx

WAN-specific data goes under:

* model_specific.wan

# Runtime Contract

Each model family should prefer one main Runtime node.

Runtime nodes consume model-specific timeline plans.

Runtime nodes produce actual ComfyUI runtime objects.

Runtime implementation should be modular internally even if exposed as one node.

LTX runtime may internally handle:

* prompt relay encode
* media loading
* guide_data building
* guide application
* video latent creation/reuse
* audio mixing
* audio latent creation
* source video outputs
* runtime debug

# Media Picker and Cache

Reuse the picker concept from WhatDreamsCost-ComfyUI branch helto-changes.

Specifically:

* Image picker concept
* Audio picker concept
* Video picker should follow the same UX pattern where practical

Media selection rules:

* No visible Asset Bin
* No Media Library
* Choose / Replace / Clear on sections/clips
* Media attaches directly to Image Sections, Video Sections, and Audio Clips
* VIDEO_TIMELINE.assets remains hidden/internal
* Replace affects only selected section/clip by default

Persistence rules:

* V1 does not copy selected media.
* Media stays in original location.
* Workflow JSON stores references, not media.
* Runtime reads original media paths.
* Thumbnails are generated into ComfyUI temp cache.
* Audio waveforms are generated into ComfyUI temp cache.
* Temp previews/waveforms are disposable and regenerated on demand.
* Audio Clips must show waveform in V1.

# Privacy

Privacy Mode V1 is UI hiding only.

It must hide previews/prompts according to settings.

It must not claim to protect original media files.

Design cache abstraction so encrypted thumbnails/waveforms can be added later without replacing renderer code.

# Code Structure

Use model-specific folders.

Use Python-safe folder names:

* ltx23
* wan22

Do not use folder names with dots such as LTX2.3 or WAN2.2.

Recommended structure:

ComfyUI-TimelineDirector/
**init**.py
pyproject.toml
README.md

nodes/
video_timeline_director/
**init**.py
node.py
schema.py
validation.py

```
ltx23/
  __init__.py
  shared/
    __init__.py
    constants.py
    resolution.py
    frame_rules.py
    prompt_relay.py
    guide_data.py
    references.py
    validation.py
  config/
    __init__.py
    node.py
  planner/
    __init__.py
    node.py
    plan_builder.py
    prompt_plan.py
    guide_plan.py
    debug.py
  runtime/
    __init__.py
    node.py
    prompt_runtime.py
    media_runtime.py
    guide_runtime.py
    audio_runtime.py
    latent_runtime.py
    debug.py
  identity/
    __init__.py
    latent_aware.py
    face.py
    combine.py
    reference_selector.py
    apply_identity.py

wan22/
  __init__.py
  shared/
    __init__.py
    constants.py
    resolution.py
    frame_rules.py
    conditioning.py
    validation.py
  config/
    __init__.py
    node.py
  planner/
    __init__.py
    node.py
    plan_builder.py
    conditioning_plan.py
    debug.py
  runtime/
    __init__.py
    node.py
    media_runtime.py
    conditioning_runtime.py
    debug.py
```

shared/
contracts/
video_timeline.py
planner_contract.py
validation.py
debug_info.py
timeline/
defaults.py
normalize.py
validate.py
time_mapping.py
prompt_merge.py
gaps.py
media/
metadata.py
thumbnails.py
waveforms.py
cache.py
audio/
mix.py
normalize.py
waveform.py

routes/
picker_routes.py
media_routes.py
thumbnail_routes.py
waveform_routes.py
privacy_routes.py

web/
video_timeline_director.js
timeline/
state.js
schema.js
migration.js
validation.js
renderer.js
interactions.js
geometry.js
inspector.js
settings_modal.js
media_picker.js
thumbnails.js
waveforms.js
privacy.js
undo.js

# Implementation Roadmap

Implement in phases. Do not skip phases unless explicitly instructed.
See `phase_status.md` for current phase progress.

## Phase 0: Project scaffold

Deliver:

* Nodepack loads in ComfyUI.
* WEB_DIRECTORY points to web/.
* Custom socket types exist.
* Empty Video Timeline Director node appears.

Do not implement timeline logic yet.

## Phase 1: Shared contracts

Deliver:

* create_default_video_timeline()
* normalize_video_timeline()
* migrate_video_timeline()
* validate_video_timeline()
* validation helpers
* time mapping helpers
* gap detection
* prompt merge helpers

Acceptance:

* Empty VIDEO_TIMELINE can be created.
* Text Section empty prompt gives Error.
* Gap gives Info.
* Image Section missing image gives Error.
* Normalization fills safe defaults only.

## Phase 2: Director backend node

Deliver:

* Video Timeline Director Python node.
* Visible properties.
* hidden video_timeline_json.
* outputs VIDEO_TIMELINE and TIMELINE_VALIDATION.

Acceptance:

* no image/video/audio inputs
* can run without frontend state
* parses video_timeline_json
* applies visible node widgets as authoritative fields
* outputs validation even if invalid

## Phase 3: Frontend state shell

Deliver:

* frontend extension mounts on Director node
* TimelineState
* loadTimelineState()
* commitTimelineChange()
* undo/redo stack
* serialize to video_timeline_json

Acceptance:

* state updates hidden widget
* graph marked dirty
* Ctrl+Z/Ctrl+Y works
* prompt typing debounced
* drag/resize commit on mouseup

## Phase 4: Timeline renderer/interactions

Deliver:

* Director Track
* Audio Tracks
* No Guidance gaps
* ruler
* playhead
* selection
* resize handles
* move/split/duplicate/delete
* snap modes
* zoom level
* zoom to fit
* audio auto-lanes

Acceptance:

* sections cannot overlap
* gaps allowed
* duration hard boundary
* audio lanes auto-stack
* split/duplicate/delete work

## Phase 5: Media picker/cache/waveforms

Deliver:

* image picker concept copied from helto-changes
* audio picker concept copied from helto-changes
* video picker follows same UX pattern
* thumbnails in temp
* waveforms in temp
* Choose/Replace/Clear for image/video/audio

Acceptance:

* image thumbnail visible
* video thumbnail visible
* audio waveform visible
* media not copied
* no embedded media in workflow JSON

## Phase 6: Project Settings modal + privacy hooks

Deliver:

* settings modal
* global prompt handling
* effective prompt preview
* audio normalization settings
* privacy mode UI hiding
* cache abstraction ready for future encryption

Acceptance:

* settings persist in VIDEO_TIMELINE
* privacy hides previews/prompts
* encryption fields exist but do not overpromise real file protection

## Phase 7: LTX 2.3 Config node

Deliver:

* LTX_TIMELINE_CONFIG output
* LTX resolution/frame/prompt/guide/reference/audio/debug policies

Acceptance:

* serializable config
* no media/timeline data in config
* quality preset can resolve LTX dimensions downstream

## Phase 8: LTX 2.3 Planner

Deliver:

* LTX_TIMELINE_PLAN
* validation
* DEBUG_INFO
* resolved_output
* section_plan
* prompt_plan
* media_plan
* audio_plan
* model_specific.ltx

Acceptance:

* frame ranges explicit
* gaps handled
* global prompt merged
* prompt relay strings generated
* 8n+1 frame rule handled
* Debug Mode Off/Summary/Full works

## Phase 9: LTX 2.3 Runtime

Deliver:

* one LTX Runtime node
* reads LTX_TIMELINE_PLAN
* materializes runtime objects

Acceptance:

* text-only timeline works
* image sections produce guide_data
* audio clips mix into combined_audio
* runtime fails clearly on missing required runtime inputs/files
* runtime does not mutate VIDEO_TIMELINE

## Phase 10: LTX identity/reference helpers

Deliver or modernize:

* LTX Identity Anchor: Latent Aware
* LTX Identity Anchor: Face
* LTX Identity Anchor: Combine
* LTX Reference Image Selector
* LTX Apply Identity Anchor
* LTX Timeline Crop Reference Tail

Acceptance:

* identity helpers remain separate advanced LTX nodes
* Director remains generic

## Phase 11: WAN 2.2 skeleton

Deliver:

* WAN Config
* WAN Planner
* optional/minimal WAN Runtime skeleton

Acceptance:

* WAN config outputs valid config
* WAN planner consumes VIDEO_TIMELINE + WAN config
* WAN planner outputs WAN_TIMELINE_PLAN + validation + debug
* unsupported features produce warnings

## Phase 12: Tests/docs/examples

Deliver:

* tests for schema/defaults/validation/gaps/prompt merge/frame mapping/LTX resolution/LTX 8n+1
* example workflows
* README
* V1 limitations
* privacy limitations
* picker setup docs
* LTX guide
* WAN skeleton status

# Required Working Style

Work phase by phase.

At the start of each phase:

1. Restate the phase goal.
2. List files you will touch.
3. Confirm which locked decisions apply.
4. Implement only that phase.
5. Run or describe relevant checks.
6. Summarize changed files and acceptance status.

Do not proceed to the next phase unless instructed.

If a requested change conflicts with locked design decisions:

1. Stop.
2. Explain the conflict.
3. Propose the smallest design adjustment.
4. Wait for approval.

Keep every phase loadable in ComfyUI.

Do not add hidden model-specific behavior to the Video Timeline Director.

Do not add image/video/audio inputs to the Video Timeline Director.

Do not embed media, thumbnails, or waveform data in workflow JSON.

Keep the visible ComfyUI graph simple.
