# Privacy Mode Limitations

Privacy Mode is a local workflow-state and preview-cache protection feature for this nodepack.

## Protected

- The hidden `video_timeline_json` widget is encrypted before workflow serialization.
- Timeline prompts, media references, filenames, assets, and picker-derived timeline metadata are inside that encrypted payload.
- Director timeline content is visually masked when the cursor is outside the node.
- Picker and prompt optimizer media/text content is visually masked outside their panels.
- Director-generated thumbnails and waveforms are stored encrypted in ComfyUI temp under `helto_timeline_director`.

## Not Protected

- Original media files remain in their original locations and are not encrypted or moved.
- Other ComfyUI nodes and their widget values are not encrypted by this nodepack.
- Public Director widgets such as duration, frame rate, aspect ratio, orientation, and quality preset remain clear text.
- Runtime tensors and normal ComfyUI execution artifacts are outside this feature's scope.

If privacy encryption or decryption fails, the Director should fail clearly rather than silently saving private timeline content as clear text.
