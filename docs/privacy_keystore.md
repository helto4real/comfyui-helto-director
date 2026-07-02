# Privacy Keystore

The privacy keystore replaces the per-repo plaintext `config/privacy_key.json`
with a single password-protected key file shared by all Helto node packs.

## How it works

- **At rest** — data keys are wrapped with AES-256-GCM under a key derived
  from your password with scrypt and stored in
  `~/.config/helto/privacy_keystore.json` (override with
  `HELTO_PRIVACY_KEYSTORE`). The file is useless without the password.
- **While unlocked** — the plain keys and a session token are cached in
  `$XDG_RUNTIME_DIR/helto/` (per-user tmpfs, mode 0600). The cache survives
  browser refreshes and ComfyUI restarts, and the OS wipes it on
  reboot/logout — that is when you re-enter the password.
- **HTTP protection** — once a keystore exists, `/helto_director/privacy/encrypt`
  and `/decrypt` require the `X-Helto-Privacy-Token` header issued by
  `/unlock`, and privacy-mode previews (`/media/thumbnail?privacy=1`,
  `/media/waveform?privacy=1`, media-browser `/thumb?privacy=1`) require the
  same token. The Director UI stores it in `localStorage` for fetch calls and
  in a `helto_privacy_token` cookie for `<img>`/media elements, which cannot
  send custom headers. A browser that never unlocked (or any other client on
  the network) cannot use these routes.

## Using it

Open the Director's **Global Settings** window:

- **Set Password** — creates the keystore. Your existing plaintext key is
  imported (old workflows stay readable) and the old file is renamed to
  `privacy_key.json.migrated`; delete it once you trust the keystore.
- **Unlock** — after a reboot, or when a locked timeline shows the unlock
  prompt.
- **Lock** / **Change Password** — available while unlocked.

Endpoints for scripting: `POST /helto_director/privacy/keystore/init`,
`/unlock`, `/lock`, `/keystore/change_password` (JSON bodies with
`password` / `current_password` + `new_password`).

## Sharing with other Helto packs

The shared implementation lives in
[`helto-privacy`](https://github.com/helto4real/helto-privacy). Other Helto
node packs can depend on the tagged package and use its
`PrivacyEnvelopeCodec("<pack schema>")` with their own envelope schema while
resolving the same keystore and session paths. One unlock covers every pack on
the same ComfyUI origin.

This repo keeps `shared/privacy_keystore.py` as a compatibility shim with a
vendored fallback for offline/manual installs. The fallback preserves existing
Timeline Director behavior; new cross-pack migrations should use the package
APIs (`initialize_keystore_with_legacy_migration`, `add_keys_to_keystore`, and
`rotate_primary_key`).

## Threat model

Gained: stolen disks, backups, and synced dotfiles cannot decrypt anything;
other network clients cannot use the decrypt route without unlocking.
Not gained: malware running as your user while unlocked can read the session
cache — the same limitation as an unlocked OS keyring. Use full-disk
encryption and encrypted swap for the layers below this one.
