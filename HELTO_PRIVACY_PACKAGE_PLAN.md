# Plan: Extract `helto-privacy` into a shared GitHub package

Instructions for an implementing agent. Goal: move the privacy keystore out of
`comfyui-helto-director` into a standalone GitHub repo (`helto-privacy`) that
every Helto node pack can depend on, then switch this repo over to it.

## Background — what exists today (read these first)

All of this already works in `comfyui-helto-director` (commit `5b77759` and
later):

- `shared/privacy_keystore.py` — the keystore. **Deliberately standalone**
  (imports nothing from this repo). Password-wrapped data keys (scrypt KEK →
  AES-256-GCM wrap) in `~/.config/helto/privacy_keystore.json`; unlocked keys
  plus a bearer token cached in `$XDG_RUNTIME_DIR/helto/privacy_session.json`
  (survives ComfyUI restarts, wiped by the OS at reboot/logout).
- `shared/privacy.py` — the envelope layer (state envelopes, byte envelopes,
  chunking, AAD binding) plus the key-resolution order: explicit `base_dir`
  argument → keystore session → legacy plaintext `config/privacy_key.json`.
  Also `initialize_privacy_keystore()` (one-shot init that imports this
  repo's legacy key and renames it to `.migrated`).
- `routes/privacy.py` — `POST /helto_director/privacy/keystore/init`,
  `/unlock`, `/lock`, `/keystore/change_password`; `check_privacy_token()`
  guard enforcing the token (header `X-Helto-Privacy-Token` or cookie
  `helto_privacy_token`) on `/encrypt`, `/decrypt`, and privacy-mode media
  routes in `routes/media_cache.py` / `routes/media_browser.py`.
- Frontend: `web/timeline/privacy.js` (token storage: localStorage + cookie),
  `web/timeline/privacy_unlock.js` (unlock/setup/change dialogs),
  Global Settings row in `web/timeline/renderer.js`.
- Tests: `tests/timeline/test_privacy_keystore.py` (10 tests) and suite-wide
  isolation fixtures in `tests/conftest.py`.
- Docs: `docs/privacy_keystore.md`, `docs/privacy_limitations.md`.

## Hard compatibility constraints (do not violate)

The user's machine has a **live keystore** created with this code. Any change
to these byte-level details silently destroys access to encrypted data:

1. Keystore file: `schema` MUST stay `"helto.privacy-keystore"`, version 1
   layout as written by `privacy_keystore.py` today. KDF params are read from
   the file (never assume module constants when unlocking).
2. Key-wrap AAD MUST stay `f"{KEYSTORE_SCHEMA}|{KEYSTORE_VERSION}|{key_id}"`.
3. Envelope schemas and AADs in `shared/privacy.py` MUST stay identical:
   `helto.timeline-director`, `helto.timeline-director.bytes`,
   `helto.timeline-director.bytes.chunked`, and their AAD format strings.
4. Paths and env vars MUST stay:
   `~/.config/helto/privacy_keystore.json` (`HELTO_PRIVACY_KEYSTORE`),
   `$XDG_RUNTIME_DIR/helto/privacy_session.json` (`HELTO_PRIVACY_SESSION_DIR`).
5. Error-code prefixes are matched by the Director frontend — keep the exact
   strings: `PRIVACY_LOCKED`, `PRIVACY_TOKEN_REQUIRED`,
   `PRIVACY_KEYSTORE_UNINITIALIZED`, `PRIVACY_KEYSTORE_EXISTS`,
   `PRIVACY_PASSWORD_INVALID`, `PRIVACY_PASSWORD_TOO_SHORT`,
   `PRIVACY_KEYSTORE_INVALID`.
6. Header `X-Helto-Privacy-Token` and cookie `helto_privacy_token` names.
7. File hygiene: key/session files written 0600 (dirs 0700) via
   tmp-file + atomic replace; sessions are per-user; only dependency allowed
   is `cryptography>=42.0`.

## Testing rules (learned the hard way)

- Tests MUST NEVER read or write `~/.config/helto`, the real
  `XDG_RUNTIME_DIR`, or any repo `config/` directory. Always point
  `HELTO_PRIVACY_KEYSTORE` and `HELTO_PRIVACY_SESSION_DIR` at pytest tmp
  paths in an autouse fixture. (A test run once minted a real key file in the
  Director repo's `config/` and it nearly got committed.)
- Patch scrypt cost down in tests (`SCRYPT_N = 2**12`) — production cost is
  `2**17` and read-from-file on unlock, so this is safe.
- Never commit anything under `config/`, `*.json.migrated`, or key material
  in fixtures. Generate keys in-test.

---

## Phase 1 — Create the `helto-privacy` GitHub repo

Create a new GitHub repo `helto-privacy` (MIT license, matching
`comfyui-helto-director`'s license declaration).

### Layout

```
helto-privacy/
  pyproject.toml            # name = "helto-privacy", packages = ["helto_privacy"]
  README.md                 # what it is, file contract, quickstart, threat model
  LICENSE                   # MIT
  helto_privacy/
    __init__.py             # re-export public API from keystore/envelope/guard
    keystore.py             # moved from shared/privacy_keystore.py
    envelope.py             # generalized from shared/privacy.py
    guard.py                # aiohttp token guard (optional import of aiohttp)
  tests/
    conftest.py             # autouse env isolation + scrypt speedup
    test_keystore.py        # ported from tests/timeline/test_privacy_keystore.py
    test_envelope.py        # envelope round-trips, chunking, purpose/AAD checks
  .github/workflows/ci.yml  # pytest on Python 3.10–3.13, no ComfyUI needed
```

`pyproject.toml`: `requires-python = ">=3.10"`, `dependencies =
["cryptography>=42.0"]`. Version `0.1.0`, tag releases (`v0.1.0`) — consumers
pin by tag.

### `keystore.py`

Copy `shared/privacy_keystore.py` verbatim, then add two functions:

1. `add_keys_to_keystore(password, keys)` — `keys` is `list[tuple[key_id, key_bytes]]`.
   Verify the password by performing a full unlock; append the new keys as
   decrypt-only entries (skip duplicates by keyId); rewrite the keystore
   (same KDF salt/params are fine — reuse the derived KEK); refresh the
   session (new token, all keys). This is what lets a *second* node pack
   migrate its legacy key into an existing keystore.
2. `rotate_primary_key(password)` — same unlock-verify, then generate a fresh
   32-byte key, mark it `primary: true`, demote the old primary to
   decrypt-only, rewrite + refresh session. Return the new status/token dict.

Both must raise `PrivacyKeystoreError` with the established code prefixes
(reuse `PRIVACY_PASSWORD_INVALID`, etc.).

### `envelope.py`

Generalize `shared/privacy.py`'s crypto so each pack keeps its own envelope
identity while sharing keys. Suggested shape:

```python
class PrivacyEnvelopeCodec:
    def __init__(self, schema: str, *, key_provider=None): ...
    def encrypt_state(self, state, base_dir=None) -> dict: ...
    def decrypt_state(self, payload, base_dir=None) -> dict: ...
    def encrypt_bytes(self, data, purpose, base_dir=None) -> dict: ...
    def decrypt_bytes(self, payload, purpose, base_dir=None) -> bytes: ...
    def is_encrypted_payload(self, value) -> bool: ...
```

- `schema` parameterizes the envelope + AAD strings exactly as
  `shared/privacy.py` builds them today (byte schema = `f"{schema}.bytes"`,
  chunked = `f"{schema}.bytes.chunked"`; AAD formats unchanged).
- Default key resolution = the three-step order (explicit `base_dir` legacy
  file → keystore session → default-path legacy file), including multi-key
  decrypt by envelope `keyId` via `session_key_for()`. Port
  `_key_for_payload` logic.
- Keep the legacy per-directory key file support (`privacy_key.json` format,
  auto-create on encrypt) — the Director tests and tools rely on the
  `base_dir` escape hatch.
- Include `initialize_keystore_with_legacy_migration(password, legacy_dir)`
  mirroring `initialize_privacy_keystore()`: read `privacy_key.json` from
  `legacy_dir` if present, init-or-`add_keys` accordingly, rename the file to
  `.migrated` (never delete).
- `PrivacyError` stays the public exception; wrap `PrivacyKeystoreError`.

Verification for this step: a state envelope produced by
`comfyui-helto-director`'s current `shared/privacy.py` (schema
`helto.timeline-director`) MUST decrypt with
`PrivacyEnvelopeCodec("helto.timeline-director")` using the same key, and
vice versa. Write an explicit cross-compat test that constructs envelopes
with hardcoded expected JSON structure.

### `guard.py`

Port `check_privacy_token()` from `routes/privacy.py` minus aiohttp response
construction coupling — accept any request-like object with `.headers` and
`.cookies` mappings and return `None` (allowed) or a dict
`{"status": 401, "error": "..."}`; provide a thin
`aiohttp_check_privacy_token(request)` that wraps it into a
`web.json_response` when aiohttp is importable. Keeps the package importable
without aiohttp.

### CI

GitHub Actions: matrix over Python 3.10–3.13, `pip install -e . pytest`,
`pytest -q`. No ComfyUI dependency — the package must never import
`folder_paths`, `server`, or anything Comfy-specific.

---

## Phase 2 — Switch `comfyui-helto-director` to the package

Work in `/home/thhel/git/comfyui-helto-director`.

1. `requirements.txt`: add a pinned VCS dependency:
   `helto-privacy @ git+https://github.com/<OWNER>/helto-privacy.git@v0.1.0`
   (keep `cryptography>=42.0` listed too — ComfyUI installs without build
   isolation sometimes; belt and suspenders).
2. `shared/privacy_keystore.py`: turn into a shim that re-exports from the
   package with a vendored fallback:
   ```python
   try:
       from helto_privacy.keystore import *  # noqa: F401,F403
       from helto_privacy.keystore import PrivacyKeystoreError  # explicit
   except ImportError:
       # vendored fallback: previous file contents live in _vendored_keystore.py
       from ._vendored_keystore import *  # noqa: F401,F403
   ```
   Move the current implementation to `shared/_vendored_keystore.py`
   unchanged. This keeps offline / manual installs working. Plan to drop the
   vendored copy after one release cycle.
3. `shared/privacy.py`: EITHER keep as-is (it only imports
   `privacy_keystore`, which now resolves to the package) — this is the
   low-risk option and recommended for the first pass — OR replace its crypto
   internals with `PrivacyEnvelopeCodec("helto.timeline-director")` while
   keeping the module-level function API (`encrypt_state`, `decrypt_state`,
   `encrypt_bytes`, `decrypt_bytes`, `is_encrypted_payload`, `crypto_status`,
   `initialize_privacy_keystore`) byte-for-byte compatible. All callers
   (`shared/media_cache.py`, `shared/segmented_executor.py`,
   `routes/privacy.py`, node code, ~20 test files) use those functions.
4. `routes/privacy.py`: optionally swap `check_privacy_token` internals for
   `helto_privacy.guard`; keep the exported name — `routes/media_cache.py`
   and `routes/media_browser.py` import it.
5. Validation (all must pass, and `config/privacy_key.json` must NOT appear):
   ```
   PYTHONPATH=/home/thhel/git/ComfyUI /home/thhel/.pyenv/versions/3.13.2/bin/python -m pytest -q
   npm run test:js
   git diff --check
   ```
   Baseline: 427 passed. Also run the loader import test explicitly
   (`tests/integration/test_comfyui_loader_import.py`) — the pack is imported
   under a second module namespace (`comfyui_helto_director_runtime.*`) and
   shims/imports must survive that.
6. Update `docs/privacy_keystore.md` (sharing section → point at the GitHub
   repo) and remove the "standalone module" wording that says it lives only
   here.

## Phase 3 — Adoption recipe for other Helto packs (document in the package README)

For each other node pack:

1. Add the same `requirements.txt` line.
2. Replace its key loading with the three-step resolution via
   `PrivacyEnvelopeCodec("<that pack's schema>")`.
3. Migration on first password-protect action:
   keystore missing → `initialize_keystore_with_legacy_migration`;
   keystore exists → unlock, then `add_keys_to_keystore` with the pack's
   legacy key, then rename its key file to `.migrated`.
4. Gate its privacy routes with the guard helper.
5. UI: no unlock dialog needed — the browser token (localStorage + cookie,
   per origin) and the server session are already shared; on
   `PRIVACY_LOCKED`, show "Unlock via Timeline Director → Global Settings"
   or vendor the small dialog from `web/timeline/privacy_unlock.js`.

## Acceptance criteria

- [ ] `helto-privacy` repo on GitHub, CI green on 3.10–3.13, tagged `v0.1.0`.
- [ ] Package has no ComfyUI imports and only `cryptography` as dependency.
- [ ] Cross-compat test proves envelopes/keystore files interoperate with
      the pre-extraction Director implementation.
- [ ] `add_keys_to_keystore` and `rotate_primary_key` implemented + tested
      (wrong password, duplicate keyId, decrypt-after-rotate cases).
- [ ] Director repo: full pytest suite (≥427) + `npm run test:js` green with
      the package installed AND with it absent (vendored fallback path).
- [ ] No test run ever creates files under the repo `config/`,
      `~/.config/helto`, or the real `XDG_RUNTIME_DIR`.
- [ ] Nothing resembling key material is committed in either repo
      (check `git log -p` for `"key"`/`"wrapped_key"` values before pushing).
- [ ] Director commits follow repo style: single-line imperative subject.

## Out of scope (explicitly)

- Publishing to PyPI (GitHub pin is the chosen distribution).
- Kernel-keyring session backend, idle auto-lock timers.
- Re-encrypting existing envelopes/caches (multi-key decrypt covers them;
  caches regenerate on their own).
