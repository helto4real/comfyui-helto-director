# WAN 2.2 Timeline Support

WAN 2.2 support is now in Phase 13. The path is:

`Video Timeline Director -> WAN 2.2 Timeline Config -> WAN 2.2 Timeline Planner -> WAN 2.2 Timeline Runtime`

The Director stays generic. WAN-specific behavior lives in the WAN Config, Planner, and Runtime nodes.

## Defaults

- Model Mode: `I2V-A14B`
- Prompt Routing: `Prompt Relay`
- Visual Conditioning Mode: `Timed Keyframes`
- Runtime Backend Profile: `Plan Only`

`Plan Only` is the safe default. It validates the WAN plan, resolves backend capabilities as unknown, and returns runtime debug without attempting WAN tensor conditioning.

## Prompt Relay

Text Sections become temporal Prompt Relay segments. The Project Global Prompt is stored as the WAN global prompt. Section prompts become local prompt segments, and the planner records:

- `video_frame_count`
- `latent_chunk_count`
- `segment_lengths`
- `section_to_latent_mapping`

WAN uses a 4-frame temporal stride. Planner segment lengths always sum to `latent_chunk_count`.

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

The `ComfyUI Core` backend currently applies Start and End image conditioning. Timed keyframes remain planned and visible in debug, but are reported unsupported.

## Video, Audio, And References

- Video Sections are prompt-only fallback in Phase 13 unless policy is set to error.
- Audio clips are preserved as final-mix metadata only; WAN generation is not audio-conditioned in Phase 13.
- Reference library, Animate, S2V, and WanVideoWrapper runtime integration are not implemented in Phase 13.

## Debugging

Set Debug Mode to `Summary` or `Full` on the WAN Config node. Inspect the Planner `DEBUG_INFO` for prompt/keyframe planning, and Runtime `runtime_debug` for backend capabilities plus applied/unsupported keyframes.
