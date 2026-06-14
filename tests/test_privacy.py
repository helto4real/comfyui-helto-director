import json

import pytest

from shared.privacy import CRYPTO_AVAILABLE, decrypt_state, encrypt_state, is_encrypted_payload
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
