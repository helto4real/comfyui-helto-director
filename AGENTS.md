# Agent Guide

This file is the fast context map for this repo. Keep it short. The full original
product plan and phase roadmap live in `PLAN.md`; read that only when you need
locked product decisions, schema details, or roadmap acceptance criteria.

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

- `PLAN.md`: full locked design, original roadmap, schema contract, and phase
  acceptance criteria.
- `phase_status.md`: current implemented phases, current focus, and active UI
  preferences.
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

- UI-only work: assign a UI worker with frontend context and JavaScript tests.
- Backend-only work: assign a backend worker with backend context and Python
  tests.
- Cross-layer work: define the shared contract change first, then split UI and
  backend work against that contract.

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
Director changes, check `PLAN.md` first and keep the Director generic.

### WAN 2.2 Config, Planner, And Runtime

WAN is planned but may not be scaffolded yet. When working on WAN, use the
planned paths from `PLAN.md` and keep WAN-specific logic out of the Director:

- Future node paths: `nodes/wan_timeline_config/`,
  `nodes/wan_timeline_planner/`, `nodes/wan_timeline_runtime/`
- Future shared path: `shared/wan/`
- Planned internal structure in `PLAN.md`: `wan22/config/`, `wan22/planner/`,
  `wan22/runtime/`, and `wan22/shared/`

If the current repo does not contain these paths, create the minimal structure
needed for the requested WAN phase and add focused tests alongside it.

### Tests And Docs

- Python tests live in `tests/`.
- JavaScript tests live in `tests/js/`.
- User-facing overview lives in `README.md`.
- Keep this file focused on agent routing. Put long product detail in
  `PLAN.md`, and current progress in `phase_status.md`.

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
