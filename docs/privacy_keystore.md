# Managed Privacy Suite

Helto Director uses the shared `helto-privacy` runtime. It does not own a
second keystore, unlock dialog, browser token client, privacy route family, or
fallback codec.

## Installation contract

Director is one member of an exact coordinated Helto privacy suite. The shared
runtime verifies the suite manifest, Director profile fingerprint, adapter
set, and browser assets before any privacy-bearing operation is available. A
missing, stale, mixed, or verification-only suite is blocked; Director never
falls back to plaintext or to a pack-local privacy implementation.

Install Director through its declared package metadata and install
`requirements.txt` before starting ComfyUI. Do not replace the exact
`helto-privacy` pin with a floating branch, version range, editable checkout,
or unrelated local package.

## Keystore and session

- The password-protected shared keystore is
  `~/.config/helto/privacy_keystore.json` by default. Tests and isolated
  installations may override it with `HELTO_PRIVACY_KEYSTORE`.
- The unlocked session lives under `$XDG_RUNTIME_DIR/helto/` by default and can
  be redirected with `HELTO_PRIVACY_SESSION_DIR`.
- The shared browser runtime owns the session header/cookie, bounded unlock
  retry, setup, lock, password change, and recovery UI. Consumer JavaScript
  cannot read or construct the token.
- One successful unlock covers every profile in the same verified suite and
  ComfyUI origin.

Use the shared Helto Privacy surface presented by ComfyUI to initialize,
unlock, lock, or change the password. Director exposes no
`/helto_director/privacy/*` compatibility endpoints.

## Existing Director data

The Director profile declares continuity for the existing
`helto.timeline-director` envelope and a verified import for the historical
Director JSON key. Migration is performed by the shared suite as a declared,
audited operation. It verifies read-back before retiring plaintext key
material and does not create a `.migrated` plaintext backup.

Do not manually move, rewrite, delete, or prune keys while preparing or testing
the candidate. Key pruning is a separate irreversible operation that requires
its own evidence and explicit authorization.

## Threat boundary

The suite protects saved workflows, private records and settings, managed
artifacts, and browser disclosure paths at rest and while locked. It cannot
protect against malware already running as the same OS user during an unlocked
session. Use full-disk encryption, encrypted swap, and a trusted local ComfyUI
deployment for those layers.
