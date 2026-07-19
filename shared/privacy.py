"""Local privacy encryption helpers for Helto Timeline Director."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Mapping

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    CRYPTO_AVAILABLE = True
    CRYPTO_IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - dependency may be absent in ComfyUI installs.
    AESGCM = None  # type: ignore[assignment]
    CRYPTO_AVAILABLE = False
    CRYPTO_IMPORT_ERROR = str(exc)

from . import privacy_keystore
from .atomic_write import atomic_write
from .privacy_keystore import PrivacyKeystoreError


ENVELOPE_SCHEMA = "helto.timeline-director"
BYTE_ENVELOPE_SCHEMA = "helto.timeline-director.bytes"
BYTE_CHUNKED_ENVELOPE_SCHEMA = "helto.timeline-director.bytes.chunked"
ENVELOPE_VERSION = 1
ALGORITHM = "AES-256-GCM"
KEY_FILE_NAME = "privacy_key.json"
BYTE_CHUNK_SIZE = 64 * 1024 * 1024
ERROR_KEY_MISSING = "PRIVACY_KEY_MISSING"
ERROR_KEY_INVALID = "PRIVACY_KEY_INVALID"
ERROR_KEY_MISMATCH = "PRIVACY_KEY_MISMATCH"
ERROR_DECRYPT_FAILED = "PRIVACY_DECRYPT_FAILED"
ERROR_PAYLOAD_INVALID = "PRIVACY_PAYLOAD_INVALID"
_LEGACY_KEY_LOCK = threading.Lock()


class PrivacyError(RuntimeError):
    """Raised when local privacy encryption cannot complete safely."""


def config_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "config"


def key_path(base_dir: str | os.PathLike[str] | None = None) -> Path:
    return Path(base_dir) / KEY_FILE_NAME if base_dir is not None else config_dir() / KEY_FILE_NAME


def crypto_status(base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = key_path(base_dir)
    return {
        "available": CRYPTO_AVAILABLE,
        "algorithm": ALGORITHM,
        "keyExists": path.exists(),
        "error": "" if CRYPTO_AVAILABLE else f"Python package 'cryptography' is required: {CRYPTO_IMPORT_ERROR}",
        **privacy_keystore.keystore_status(),
    }


def is_encrypted_payload(value: Any) -> bool:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return False
    return (
        isinstance(value, Mapping)
        and value.get("encrypted") is True
        and value.get("schema") == ENVELOPE_SCHEMA
        and value.get("algorithm") == ALGORITHM
    )


def encrypt_bytes(data: bytes, purpose: str, base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    key, key_id = _load_or_create_key(base_dir, create=True)
    chunk_size = _byte_chunk_size()
    if len(data) > chunk_size:
        return _encrypt_bytes_chunked(data, purpose, key, key_id, chunk_size)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, data, _bytes_aad(key_id, purpose))  # type: ignore[operator]
    return {
        "version": ENVELOPE_VERSION,
        "schema": BYTE_ENVELOPE_SCHEMA,
        "encrypted": True,
        "algorithm": ALGORITHM,
        "purpose": purpose,
        "keyId": key_id,
        "nonce": _b64url_encode(nonce),
        "ciphertext": _b64url_encode(ciphertext),
    }


def decrypt_bytes(payload: Any, purpose: str, base_dir: str | os.PathLike[str] | None = None) -> bytes:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception as exc:
            raise PrivacyError(f"Encrypted byte payload is not valid JSON: {exc}") from exc
    if not (
        isinstance(payload, Mapping)
        and payload.get("encrypted") is True
        and payload.get("algorithm") == ALGORITHM
    ):
        raise PrivacyError("Data is not an encrypted byte payload.")
    schema = payload.get("schema")
    if str(payload.get("purpose", "")) != purpose:
        raise PrivacyError("Encrypted byte payload was created for a different purpose.")
    key_id = str(payload.get("keyId", ""))
    key = _key_for_payload(
        key_id,
        base_dir,
        "Encrypted byte payload was created with a different local privacy key.",
    )
    if schema == BYTE_CHUNKED_ENVELOPE_SCHEMA:
        return _decrypt_bytes_chunked(payload, purpose, key, key_id)
    if schema != BYTE_ENVELOPE_SCHEMA:
        raise PrivacyError("Data is not an encrypted byte payload.")
    try:
        nonce = _b64url_decode(str(payload.get("nonce", "")))
        ciphertext = _b64url_decode(str(payload.get("ciphertext", "")))
        return AESGCM(key).decrypt(nonce, ciphertext, _bytes_aad(key_id, purpose))  # type: ignore[operator]
    except Exception as exc:  # noqa: BLE001 - auth/tag/key failures should be user-readable.
        raise PrivacyError(f"Could not decrypt byte payload: {exc}") from exc


def encrypt_state(state: Mapping[str, Any], base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    key, key_id = _load_or_create_key(base_dir, create=True)
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _aad(key_id))  # type: ignore[operator]
    return {
        "version": ENVELOPE_VERSION,
        "schema": ENVELOPE_SCHEMA,
        "encrypted": True,
        "algorithm": ALGORITHM,
        "keyId": key_id,
        "nonce": _b64url_encode(nonce),
        "ciphertext": _b64url_encode(ciphertext),
    }


def decrypt_state(payload: Any, base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception as exc:
            raise PrivacyError(
                f"{ERROR_PAYLOAD_INVALID}: Encrypted Timeline Director data is not valid JSON: {exc}"
            ) from exc
    if not is_encrypted_payload(payload):
        raise PrivacyError(
            f"{ERROR_PAYLOAD_INVALID}: Timeline Director data is not an encrypted privacy payload."
        )
    key_id = str(payload.get("keyId", ""))
    key = _key_for_payload(
        key_id,
        base_dir,
        "Encrypted Timeline Director data was created with a different local privacy key.",
    )
    try:
        nonce = _b64url_decode(str(payload.get("nonce", "")))
        ciphertext = _b64url_decode(str(payload.get("ciphertext", "")))
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(key_id))  # type: ignore[operator]
        loaded = json.loads(plaintext.decode("utf-8"))
    except PrivacyError:
        raise
    except Exception as exc:  # noqa: BLE001 - auth/tag/key failures should be user-readable.
        raise PrivacyError(
            f"{ERROR_DECRYPT_FAILED}: Could not decrypt Timeline Director data: {exc}"
        ) from exc
    if not isinstance(loaded, Mapping):
        raise PrivacyError(
            f"{ERROR_PAYLOAD_INVALID}: Encrypted Timeline Director data did not contain a state object."
        )
    return dict(loaded)


def _load_or_create_key(base_dir: str | os.PathLike[str] | None = None, create: bool = True) -> tuple[bytes, str]:
    if not CRYPTO_AVAILABLE:
        raise PrivacyError(f"Python package 'cryptography' is required for privacy mode: {CRYPTO_IMPORT_ERROR}")

    # Explicit base_dir callers are limited to migration tools and isolated
    # tests. Normal runtime encryption must use the password-protected shared
    # keystore and must never create a plaintext fallback key.
    if base_dir is None:
        if not privacy_keystore.keystore_exists():
            raise PrivacyError(
                f"{privacy_keystore.ERROR_UNINITIALIZED}: Privacy keystore has not been created yet. "
                "Open the Helto privacy dialog and set a privacy password."
            )
        try:
            return privacy_keystore.primary_session_key()
        except PrivacyKeystoreError as exc:
            raise PrivacyError(str(exc)) from exc

    path = key_path(base_dir)
    if path.exists():
        return _read_legacy_key(path)

    with _LEGACY_KEY_LOCK:
        # A keystore may have been initialized while this caller waited.
        if base_dir is None and privacy_keystore.keystore_exists():
            try:
                return privacy_keystore.primary_session_key()
            except PrivacyKeystoreError as exc:
                raise PrivacyError(str(exc)) from exc

        # Another thread may have created the legacy key while this caller
        # waited. Reuse that persisted key so every concurrent envelope stays
        # decryptable.
        if path.exists():
            return _read_legacy_key(path)
        if not create:
            raise PrivacyError(f"{ERROR_KEY_MISSING}: Privacy key file is missing: {path}")

        key = secrets.token_bytes(32)
        key_id = _b64url_encode(hashlib.sha256(key).digest()[:12])
        _write_private_json(
            path,
            {
                "version": 1,
                "algorithm": ALGORITHM,
                "keyId": key_id,
                "key": _b64url_encode(key),
            },
        )
        return key, key_id


def _read_legacy_key(path: Path) -> tuple[bytes, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        key = _b64url_decode(str(payload.get("key", "")))
        key_id = str(payload.get("keyId", "")).strip()
    except Exception as exc:  # noqa: BLE001 - bad local key should become a readable privacy error.
        raise PrivacyError(f"{ERROR_KEY_INVALID}: Could not read privacy key file '{path}': {exc}") from exc
    if len(key) != 32 or not key_id:
        raise PrivacyError(f"{ERROR_KEY_INVALID}: Privacy key file '{path}' is malformed.")
    return key, key_id


def _key_for_payload(payload_key_id: str, base_dir: str | os.PathLike[str] | None, mismatch_error: str) -> bytes:
    key, key_id = _load_or_create_key(base_dir, create=False)
    if payload_key_id == key_id:
        return key
    if base_dir is None and privacy_keystore.keystore_exists():
        # Imported legacy keys (and rotated-out primaries) stay decryptable
        # while the keystore is unlocked.
        alt = privacy_keystore.session_key_for(payload_key_id)
        if alt is not None:
            return alt
    raise PrivacyError(f"{ERROR_KEY_MISMATCH}: {mismatch_error}")


def initialize_privacy_keystore(password: str) -> dict[str, Any]:
    """Create the password-protected keystore, importing the legacy plaintext
    key file so existing envelopes stay readable, then retire that file."""
    if privacy_keystore.keystore_exists():
        raise PrivacyError(
            f"{privacy_keystore.ERROR_ALREADY_INITIALIZED}: Privacy keystore already exists: "
            f"{privacy_keystore.keystore_path()}"
        )
    legacy_keys: list[tuple[str, bytes]] = []
    path = key_path()
    if path.exists():
        try:
            legacy_key, legacy_key_id = _read_legacy_key(path)
            legacy_keys.append((legacy_key_id, legacy_key))
        except PrivacyError as exc:
            raise PrivacyError(
                f"Cannot migrate existing privacy key file '{path}': {exc}"
            ) from exc

    try:
        result = privacy_keystore.initialize_keystore(password, legacy_keys=legacy_keys)
    except PrivacyKeystoreError as exc:
        raise PrivacyError(str(exc)) from exc

    if legacy_keys:
        try:
            path.unlink(missing_ok=True)
            path.with_name(path.name + ".migrated").unlink(missing_ok=True)
        except OSError:
            pass
    return result


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    atomic_write(
        path,
        lambda temp_path: temp_path.write_text(text, encoding="utf-8"),
        mode=0o600,
    )


def _aad(key_id: str) -> bytes:
    return f"{ENVELOPE_SCHEMA}|{ENVELOPE_VERSION}|{ALGORITHM}|{key_id}".encode("utf-8")


def _bytes_aad(key_id: str, purpose: str) -> bytes:
    return f"{BYTE_ENVELOPE_SCHEMA}|{ENVELOPE_VERSION}|{ALGORITHM}|{key_id}|{purpose}".encode("utf-8")


def _chunk_bytes_aad(key_id: str, purpose: str, index: int, total_chunks: int, plaintext_size: int) -> bytes:
    return (
        f"{BYTE_CHUNKED_ENVELOPE_SCHEMA}|{ENVELOPE_VERSION}|{ALGORITHM}|{key_id}|{purpose}|"
        f"{int(index)}|{int(total_chunks)}|{int(plaintext_size)}"
    ).encode("utf-8")


def _byte_chunk_size() -> int:
    try:
        return max(1, int(BYTE_CHUNK_SIZE))
    except (TypeError, ValueError):
        return 64 * 1024 * 1024


def _encrypt_bytes_chunked(data: bytes, purpose: str, key: bytes, key_id: str, chunk_size: int) -> dict[str, Any]:
    plaintext_size = len(data)
    total_chunks = max(1, int(math.ceil(plaintext_size / chunk_size)))
    chunks = []
    for index, offset in enumerate(range(0, plaintext_size, chunk_size)):
        nonce = secrets.token_bytes(12)
        chunk = data[offset: offset + chunk_size]
        ciphertext = AESGCM(key).encrypt(
            nonce,
            chunk,
            _chunk_bytes_aad(key_id, purpose, index, total_chunks, plaintext_size),  # type: ignore[operator]
        )
        chunks.append({
            "index": index,
            "nonce": _b64url_encode(nonce),
            "ciphertext": _b64url_encode(ciphertext),
        })
    return {
        "version": ENVELOPE_VERSION,
        "schema": BYTE_CHUNKED_ENVELOPE_SCHEMA,
        "encrypted": True,
        "algorithm": ALGORITHM,
        "purpose": purpose,
        "keyId": key_id,
        "chunkSize": int(chunk_size),
        "plaintextSize": plaintext_size,
        "chunks": chunks,
    }


def _decrypt_bytes_chunked(payload: Mapping[str, Any], purpose: str, key: bytes, key_id: str) -> bytes:
    try:
        plaintext_size = int(payload.get("plaintextSize"))
        chunk_size = int(payload.get("chunkSize"))
    except (TypeError, ValueError) as exc:
        raise PrivacyError("Encrypted byte payload has invalid chunk metadata.") from exc
    if plaintext_size < 0 or chunk_size <= 0:
        raise PrivacyError("Encrypted byte payload has invalid chunk metadata.")
    chunks = payload.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise PrivacyError("Encrypted byte payload does not contain chunks.")
    total_chunks = len(chunks)
    expected_indexes = set(range(total_chunks))
    seen_indexes = set()
    plaintext_parts: list[bytes] = [b""] * total_chunks
    try:
        for entry in chunks:
            if not isinstance(entry, Mapping):
                raise PrivacyError("Encrypted byte payload contains an invalid chunk.")
            index = int(entry.get("index"))
            if index not in expected_indexes or index in seen_indexes:
                raise PrivacyError("Encrypted byte payload contains invalid chunk indexes.")
            nonce = _b64url_decode(str(entry.get("nonce", "")))
            ciphertext = _b64url_decode(str(entry.get("ciphertext", "")))
            plaintext_parts[index] = AESGCM(key).decrypt(  # type: ignore[operator]
                nonce,
                ciphertext,
                _chunk_bytes_aad(key_id, purpose, index, total_chunks, plaintext_size),
            )
            seen_indexes.add(index)
    except PrivacyError:
        raise
    except Exception as exc:  # noqa: BLE001 - auth/tag/key failures should be user-readable.
        raise PrivacyError(f"Could not decrypt chunked byte payload: {exc}") from exc
    if seen_indexes != expected_indexes:
        raise PrivacyError("Encrypted byte payload is missing chunks.")
    plaintext = b"".join(plaintext_parts)
    if len(plaintext) != plaintext_size:
        raise PrivacyError("Encrypted byte payload decrypted to an unexpected size.")
    return plaintext


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
