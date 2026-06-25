# Media Picker Setup

The Director timeline stores media as file references in `assets[]`. It does not copy media into the workflow and it does not embed image, video, audio, thumbnail, or waveform payloads.

## Folder Sources

Use the image, video, and audio picker buttons in the Director toolbar to choose media from configured browser folders or ComfyUI-accessible input locations. Picked files become timeline assets with:

- `asset_id`
- media type
- source kind
- file path or browser metadata
- display name

Sections and audio clips reference those assets by `asset_id`.

## Expected Behavior

- Image and Video Sections use the picker for Choose, Replace, and Clear workflows.
- Audio clips use the audio picker and keep their timing, volume, fade, and lane fields in the timeline.
- Thumbnails and waveforms are preview cache data under ComfyUI temp, not workflow state.
- Moving or renaming source files after choosing them can make runtime media loading fail until the media is selected again.

## Privacy Mode

Privacy Mode is enabled by default for new Director projects. While it is enabled, picker thumbnails and audio filenames are masked while the cursor is outside the picker panel, and preview cache files are encrypted by this nodepack.
