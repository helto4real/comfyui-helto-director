# WAN 2.2 Skeleton Status

WAN 2.2 support is planner-only in Phase 11.

Implemented:

- `WAN 2.2 Timeline Config`
- `WAN 2.2 Timeline Planner`
- `WAN_TIMELINE_CONFIG`
- `WAN_TIMELINE_PLAN`
- prompt ranges with global prompt merge
- gap ranges as No Guidance
- media and audio metadata preservation
- warnings for unsupported Image, Video, and Audio timeline features

Deferred:

- WAN Runtime
- WAN conditioning materialization
- WAN media guide semantics
- WAN sampler wiring
- WAN-specific source-video/audio behavior

Use the WAN skeleton to verify that a generic Director timeline can be planned for a second model family without adding WAN logic to the Director.
