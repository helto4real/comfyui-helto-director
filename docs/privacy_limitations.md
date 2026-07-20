# Privacy Mode Limitations

Privacy Mode is enabled by default for new Director projects. It is a local workflow-state and preview-cache protection feature for this nodepack.

## Protected

- The hidden `video_timeline_json` widget is encrypted before workflow serialization.
- Timeline prompts, media references, filenames, assets, and picker-derived timeline metadata are inside that encrypted payload.
- Director timeline content is visually masked when the cursor is outside the node.
- Picker and prompt optimizer media/text content is visually masked outside their panels.
- When global Privacy Mode is enabled, Director-generated thumbnails and waveforms are stored encrypted in ComfyUI temp under `helto_timeline_director`. Media requests may opt into this protection, but cannot opt out of the global setting.
- Enabling global Privacy Mode removes existing plaintext Director thumbnail and waveform caches before the setting is saved. If cleanup fails, Privacy Mode is not changed.

## Not Protected

- Original media files remain in their original locations and are not encrypted or moved.
- Other ComfyUI nodes and their widget values are not encrypted by this nodepack.
- Public Director widgets such as duration, frame rate, aspect ratio, orientation, and quality preset remain clear text.
- Runtime tensors and normal ComfyUI execution artifacts are outside this feature's scope.
- Without a privacy keystore (see `docs/privacy_keystore.md`), anyone who can reach the ComfyUI HTTP port can call `POST /helto_director/privacy/decrypt` and recover the plaintext of any envelope, because ComfyUI has no authentication and the key lives on the server. With a keystore, those routes additionally require the session token issued at unlock. Privacy Mode protects data at rest (saved workflows, shared JSON, preview caches) — it assumes the ComfyUI server itself is trusted and reachable only by you (localhost, or a network you control). Do not expose the server with `--listen` on untrusted networks and expect Privacy Mode to hold; passwords and tokens travel over the plain HTTP connection.

If privacy encryption or decryption fails, the Director should fail clearly rather than silently saving private timeline content as clear text.

Existing workflows that explicitly saved Privacy Mode off remain public until the user turns it back on.
