# Agent Guide

This file is the fast context map for this repo. Keep it short and focused on
agent routing, current code boundaries, and validation commands.

## Always-On Rules

- Keep the Video Timeline Director generic. No LTX/WAN-specific logic in the
  Director.
- Do not add fixed image/video/audio input slots to the Director.
- Media attaches through the timeline UI and is stored as internal `assets[]`
  records. Do not embed media, thumbnails, or waveform data in workflow JSON.
- Media files stay in their original location; previews/waveforms are cache data.
- Keep the nodepack loadable after every change.
- Do not commit unless the user explicitly asks for a commit.
- Current UI preference: compact icon buttons/menu controls where practical,
  matching the timeline toolbar style. Keep full English labels in tooltips,
  aria labels, and dropdown items.

## Source Of Truth

- `README.md`: user-facing overview and links to current documentation.
- `docs/current_limitations.md`: current supported and unsupported behavior.
- `docs/WAN22_SUPPORT.md`: WAN 2.2 workflow, defaults, backend profiles, and
  debug fields.
- Local ComfyUI checkout: `/home/thhel/git/ComfyUI/`. Use it for ComfyUI API
  source checks and Python test imports.
- WhatDreamsCost reference repo: `/home/thhel/git/WhatDreamsCost-ComfyUI`.
  Use only as inspiration for picker/timeline concepts; do not copy its old
  all-in-one architecture blindly.

## Execution Model

The main thread is the orchestrator and planner. It decides whether work is
UI-only, backend-only, or cross-layer; owns shared contract changes; delegates
focused worker threads; performs final integration review; and runs final
validation.

Worker delegation is for meaningful implementation slices, not ceremony. The
main thread may make tightly scoped edits directly when delegation would add
coordination noise, especially for docs-only edits, test expectation updates,
one-line fixes, or follow-up corrections to work it just integrated. If worker
tools are unavailable, or the user explicitly asks for single-threaded
execution, state that before making implementation changes and include the
reason in the final response.

Main thread responsibilities:

- Define the shared contract before spawning implementation workers. This
  includes route shapes, API stubs, JSON/schema fields, hidden widget payload
  changes, validation codes, debug fields, migration behavior, and fallback
  rules.
- Keep contract edits small and explicit so workers can code against them
  without guessing.
- Review child-agent work, integrate it, resolve inconsistencies, fix errors,
  and run final validation.
- Avoid delegating a task while also editing the same files or behavior in
  parallel. If the main thread edits while a worker is running, use a disjoint
  write scope and review carefully before integration.

Worker responsibilities:

- UI worker: receives the shared contract plus frontend context, implements all
  assigned UI code, and runs focused JavaScript tests for that UI slice.
- Backend worker: receives the shared contract plus backend context, implements
  all assigned backend code, and runs focused Python tests for that backend
  slice.
- Cross-layer implementation: the main thread defines and lands or clearly
  sketches the shared contract first, then splits UI and backend work against
  that contract.
- Advice-only, review-only, planning-only, docs-only, and tiny follow-up turns
  do not need a worker unless the task grows into a meaningful implementation
  slice.

Backend workers load backend paths, Python tests, and only the minimum shared
contract files needed for integration. UI workers load frontend paths,
JavaScript tests, and only the minimum shared contract files needed for
integration.

Each worker report should list files read, files changed, contract assumptions,
tests run, and open handoff risks. Do not broaden context because it might be
interesting; broaden only when current evidence shows the boundary is wrong.

## Shared Contract Boundary

UI and backend work communicate through the shared contract, not through each
other's implementation details. Shared contract surfaces are:

- `shared/contracts/`
- `shared/timeline/`
- `web/timeline/schema.js`
- `web/timeline/migration.js`
- Hidden widget payload: `video_timeline_json`
- Serialized `VIDEO_TIMELINE` data
- Media browser/cache route request and response shapes
- Validation errors, warning codes, fallback behavior, and debug fields

If UI behavior depends on a backend detail, promote that detail into an explicit
schema field, route response field, validation code, debug field, or documented
fallback behavior. Cross-layer work starts with the orchestrator defining the
contract change before UI or backend implementation begins.

Backend workers may inspect frontend schema/migration files only to confirm the
serialized contract. UI workers may inspect backend contract or route
definitions only to confirm payload shapes.

## Subsystem Context Maps

### Director Backend

Start here for Director node execution, visible widgets, hidden
`video_timeline_json`, validation output, and ComfyUI node registration:

- `nodes/video_timeline_director/`
- `shared/timeline/`
- `shared/contracts/`
- Tests: `tests/test_director_phase2.py`, `tests/test_timeline_phase1.py`,
  `tests/test_comfyui_loader_import.py`

Do not load frontend files unless the task mentions UI behavior or serialization
from the browser.

### Timeline Frontend And UI

Start here for custom node UI, timeline rendering, inspector layout, toolbar,
dragging/resizing, range control, prompt editing, serialization, undo/redo, and
privacy display behavior:

- `web/video_timeline_director.js`
- `web/timeline/renderer.js`
- `web/timeline/state.js`
- `web/timeline/operations.js`
- `web/timeline/geometry.js`
- `web/timeline/undo.js`
- `web/timeline/validation.js`
- `web/timeline/schema.js`
- `web/timeline/migration.js`
- Tests: `tests/js/phase3_state.test.mjs`,
  `tests/js/phase4_operations.test.mjs`, `tests/js/phase6_zoom.test.mjs`,
  `tests/js/ui_timeline_preview.test.mjs`

Only open backend files if the UI change affects the serialized
`VIDEO_TIMELINE` contract.

### Media Picker, Thumbnails, And Waveforms

Start here for image/video/audio picker behavior, folder aliases, media asset
creation, thumbnail routes, waveform routes, and media cache hydration:

- `web/timeline/media_picker.js`
- `web/timeline/media_actions.js`
- `web/timeline/media_cache.js`
- `web/timeline/media.js`
- `routes/media_browser.py`
- `routes/media_cache.py`
- `shared/media_browser.py`
- `shared/media_cache.py`
- Tests: `tests/test_media_browser_phase9.py`,
  `tests/test_media_cache_phase7.py`, `tests/js/phase5_media.test.mjs`,
  `tests/js/phase7_media_cache.test.mjs`,
  `tests/js/phase9_media_picker.test.mjs`

Remember: workflow JSON stores file references and metadata only, never media
payloads, thumbnails, or waveform arrays.

### LTX 2.3 Config And Planner

Start here for LTX-specific config, resolution/frame policy, prompt/media/audio
planning, debug info, and LTX validation:

- `nodes/ltx_timeline_config/`
- `nodes/ltx_timeline_planner/`
- `shared/ltx/`
- Tests: `tests/test_ltx_phase8.py`

Keep all LTX-specific behavior in these LTX paths. If a change seems to require
Director changes, keep the Director generic and promote shared behavior through
the explicit contract surfaces above.

### WAN 2.2 Config, Planner, And Runtime

Start here for WAN-specific config, planning, runtime execution, prompt relay,
Bernini task prompting, visual conditioning, backend compatibility, and debug
output:

- `nodes/wan_timeline_config/`
- `nodes/wan_timeline_planner/`
- `nodes/wan_timeline_runtime/`
- `shared/wan/`
- `shared/wan/runtime/`
- Tests: `tests/test_wan_phase11.py`, `tests/test_wan_phase13.py`,
  `tests/test_wan_phase14.py`, `tests/test_wan_phase15.py`,
  `tests/test_wan_phase16.py`, `tests/test_wan_bernini.py`

Keep all WAN-specific behavior in these WAN paths. The Director remains generic,
and timeline media/reference semantics should cross the layer boundary through
the shared contract and debug fields.

### Tests And Docs

- Python tests live in `tests/`.
- JavaScript tests live in `tests/js/`.
- User-facing overview lives in `README.md`.
- Current user-facing details live under `docs/`.

## Commands

Use the ComfyUI Python environment/checkout when possible:

```bash
PYTHONPATH=/home/thhel/git/ComfyUI python -m pytest
```

Run focused Python tests:

```bash
PYTHONPATH=/home/thhel/git/ComfyUI python -m pytest tests/test_ltx_phase8.py
```

Compile changed Python files:

```bash
python -m py_compile path/to/file.py
```

Run all JavaScript tests:

```bash
npm run test:js
```

Run a focused JavaScript test:

```bash
node tests/js/ui_timeline_preview.test.mjs
```

Check whitespace/conflict-marker hygiene:

```bash
git diff --check
```

Useful combined validation for frontend-only changes:

```bash
npm run test:js
git diff --check
```

Useful combined validation for backend/schema changes:

```bash
PYTHONPATH=/home/thhel/git/ComfyUI python -m pytest
python -m py_compile path/to/changed_file.py
git diff --check
```
