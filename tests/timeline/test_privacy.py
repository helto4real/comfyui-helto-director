import json

import pytest

import shared.privacy as privacy
from shared.privacy import (
    BYTE_CHUNKED_ENVELOPE_SCHEMA,
    CRYPTO_AVAILABLE,
    PrivacyError,
    decrypt_bytes,
    decrypt_state,
    encrypt_bytes,
    encrypt_state,
    is_encrypted_payload,
)
from shared.timeline import create_default_video_timeline


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for privacy encryption tests")
def test_timeline_privacy_envelope_round_trips_without_clear_text():
    timeline = create_default_video_timeline()
    timeline["project"]["privacy"]["mode"] = True
    timeline["project"]["global_prompt"]["prompt"] = "private global"
    timeline["assets"].append(
        {
            "asset_id": "asset_001",
            "type": "Image",
            "source_kind": "FilePath",
            "path": "/private/reference.png",
            "name": "reference.png",
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": "Image",
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "private prompt",
            "image": {"asset_id": "asset_001"},
        }
    )

    envelope = encrypt_state({"timeline": timeline})
    serialized = json.dumps(envelope)
    decrypted = decrypt_state(envelope)

    assert is_encrypted_payload(envelope)
    assert "private prompt" not in serialized
    assert "private global" not in serialized
    assert "reference.png" not in serialized
    assert decrypted["timeline"]["director_track"]["sections"][0]["prompt"] == "private prompt"


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for privacy encryption tests")
def test_byte_privacy_chunked_envelope_round_trips_without_clear_text(monkeypatch, tmp_path):
    monkeypatch.setattr(privacy, "BYTE_CHUNK_SIZE", 9)
    data = b"chunk-secret-" * 5

    envelope = encrypt_bytes(data, "test-bytes", base_dir=tmp_path)
    serialized = json.dumps(envelope)
    decrypted = decrypt_bytes(envelope, "test-bytes", base_dir=tmp_path)

    assert envelope["schema"] == BYTE_CHUNKED_ENVELOPE_SCHEMA
    assert envelope["chunkSize"] == 9
    assert envelope["plaintextSize"] == len(data)
    assert len(envelope["chunks"]) > 1
    assert "chunk-secret" not in serialized
    assert decrypted == data


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for privacy encryption tests")
def test_byte_privacy_chunked_envelope_rejects_tampered_ciphertext(monkeypatch, tmp_path):
    monkeypatch.setattr(privacy, "BYTE_CHUNK_SIZE", 7)
    envelope = encrypt_bytes(b"private-data-for-tamper-test", "test-bytes", base_dir=tmp_path)
    ciphertext = envelope["chunks"][0]["ciphertext"]
    envelope["chunks"][0]["ciphertext"] = ("A" if ciphertext[:1] != "A" else "B") + ciphertext[1:]

    with pytest.raises(PrivacyError, match="Could not decrypt chunked byte payload"):
        decrypt_bytes(envelope, "test-bytes", base_dir=tmp_path)
