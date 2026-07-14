"""Synthetic historical Director values used only by migration tests."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


DIRECTOR_SCHEMA = "helto.timeline-director"
LEGACY_KEY_FILE_NAME = "privacy_key.json"


def director_legacy_fixture(
    root: Path,
    state: Mapping[str, object],
) -> tuple[dict[str, object], Path]:
    """Create deterministic non-user legacy bytes plus their import source."""

    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(b"synthetic Director migration fixture key").digest()
    key_id = _encode(hashlib.sha256(key).digest()[:12])
    plaintext = json.dumps(
        dict(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    nonce = hashlib.sha256(b"synthetic nonce\0" + plaintext).digest()[:12]
    aad = f"{DIRECTOR_SCHEMA}|1|AES-256-GCM|{key_id}".encode("utf-8")
    envelope = {
        "version": 1,
        "schema": DIRECTOR_SCHEMA,
        "encrypted": True,
        "algorithm": "AES-256-GCM",
        "keyId": key_id,
        "nonce": _encode(nonce),
        "ciphertext": _encode(AESGCM(key).encrypt(nonce, plaintext, aad)),
    }
    key_source = root / LEGACY_KEY_FILE_NAME
    key_source.write_text(
        json.dumps(
            {
                "version": 1,
                "algorithm": "AES-256-GCM",
                "keyId": key_id,
                "key": _encode(key),
            }
        ),
        encoding="utf-8",
    )
    return envelope, key_source


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
