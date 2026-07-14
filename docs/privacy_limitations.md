# Managed Privacy Boundaries

Privacy Mode is enabled by default for new Director projects and is governed by
the verified shared Helto privacy suite.

## Protected by the Director profile

- Timeline state is saved and queued through the managed workflow snapshot;
  locked or unreadable private state is preserved byte-for-byte and never
  replaced with defaults.
- Project and character library data uses managed records with minimal locked
  shells and authorized preview/use operations.
- Folder settings, capture state, take metadata, sidecars, thumbnails,
  waveforms, and timeline segment spills use declared singleton, operation, or
  artifact resources with the global Director privacy mode as their floor.
- Browser media uses opaque references and short-lived shared leases. Raw paths
  and private names are not placed in thumbnail, waveform, or view URLs.
- Private prompts, labels, media, and diagnostics are concealed or redacted by
  the managed browser adapters until the shared session permits their intended
  product view.
- Privacy-mode transitions include all declared Director participants and
  either complete with verified read-back or leave the prior mode authoritative.

## Outside this profile

- Original user media remains in its existing location. Director validates
  allowed roots but does not move or encrypt the original file.
- Other ComfyUI node packs are protected only by their own profiles in the same
  exact suite.
- Model weights, ordinary ComfyUI outputs, GPU memory, and third-party node
  behavior are outside the Director profile.
- A trusted local ComfyUI process and OS account remain part of the threat
  boundary. Do not expose an unencrypted, unauthenticated ComfyUI endpoint to
  an untrusted network.

## Fail-closed behavior

A missing or mismatched shared package, inactive suite, incomplete adapter set,
locked session, transition in progress, invalid reference, or failed managed
write blocks the operation. The supported Director candidate has no positive
local privacy fallback and never intentionally serializes private timeline
content as plaintext after a privacy failure.

Existing workflows explicitly saved in public mode remain public until an
authorized complete transition changes their authoritative mode.
