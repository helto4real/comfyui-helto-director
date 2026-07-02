import json

import pytest

import shared.privacy as privacy
import shared.privacy_keystore as keystore
from shared.privacy import (
    PrivacyError,
    crypto_status,
    decrypt_state,
    encrypt_state,
    initialize_privacy_keystore,
)
from shared.privacy_keystore import (
    KEYSTORE_CRYPTO_AVAILABLE,
    PrivacyKeystoreError,
)

pytestmark = pytest.mark.skipif(
    not KEYSTORE_CRYPTO_AVAILABLE,
    reason="cryptography package is required for privacy keystore tests",
)

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def isolated_keystore(tmp_path, monkeypatch):
    monkeypatch.setenv(keystore.KEYSTORE_ENV, str(tmp_path / "keystore" / "privacy_keystore.json"))
    monkeypatch.setenv(keystore.SESSION_DIR_ENV, str(tmp_path / "session"))
    # Interactive scrypt cost is deliberately high; keep unit tests fast.
    monkeypatch.setattr(keystore, "SCRYPT_N", 2**12)
    # Keep the legacy key file away from the repo's real config directory.
    monkeypatch.setattr(privacy, "config_dir", lambda: tmp_path / "legacy_config")


def test_initialize_unlock_lock_lifecycle():
    status = crypto_status()
    assert status["keystoreInitialized"] is False

    result = initialize_privacy_keystore(PASSWORD)
    assert result["token"]
    assert result["keystoreInitialized"] is True
    assert result["keystoreLocked"] is False
    assert keystore.session_token() == result["token"]

    envelope = encrypt_state({"secret": "prompt"})
    assert decrypt_state(envelope) == {"secret": "prompt"}

    keystore.lock_keystore()
    assert crypto_status()["keystoreLocked"] is True
    assert keystore.session_token() is None
    with pytest.raises(PrivacyError, match="PRIVACY_LOCKED"):
        decrypt_state(envelope)
    with pytest.raises(PrivacyError, match="PRIVACY_LOCKED"):
        encrypt_state({"secret": "prompt"})

    unlocked = keystore.unlock_keystore(PASSWORD)
    assert unlocked["token"]
    assert unlocked["token"] != result["token"]
    assert decrypt_state(envelope) == {"secret": "prompt"}


def test_unlock_rejects_wrong_password():
    initialize_privacy_keystore(PASSWORD)
    keystore.lock_keystore()
    with pytest.raises(PrivacyKeystoreError, match="PRIVACY_PASSWORD_INVALID"):
        keystore.unlock_keystore("not the password")
    assert crypto_status()["keystoreLocked"] is True


def test_initialize_requires_minimum_password_length():
    with pytest.raises(PrivacyError, match="PRIVACY_PASSWORD_TOO_SHORT"):
        initialize_privacy_keystore("short")
    assert crypto_status()["keystoreInitialized"] is False


def test_initialize_twice_is_rejected():
    initialize_privacy_keystore(PASSWORD)
    with pytest.raises(PrivacyError, match="PRIVACY_KEYSTORE_EXISTS"):
        initialize_privacy_keystore(PASSWORD)


def test_legacy_key_is_imported_and_retired():
    legacy_envelope = encrypt_state({"old": "workflow"})
    legacy_path = privacy.key_path()
    assert legacy_path.exists()

    initialize_privacy_keystore(PASSWORD)

    assert not legacy_path.exists()
    assert legacy_path.with_name(legacy_path.name + ".migrated").exists()
    # Envelopes written by the legacy key stay readable while unlocked...
    assert decrypt_state(legacy_envelope) == {"old": "workflow"}
    # ...and new envelopes use the fresh primary key, not the imported one.
    new_envelope = encrypt_state({"new": "workflow"})
    assert new_envelope["keyId"] != legacy_envelope["keyId"]
    assert decrypt_state(new_envelope) == {"new": "workflow"}


def test_change_password_rewraps_keys():
    initialize_privacy_keystore(PASSWORD)
    envelope = encrypt_state({"secret": "prompt"})

    keystore.change_keystore_password(PASSWORD, "new password 123")
    keystore.lock_keystore()
    with pytest.raises(PrivacyKeystoreError, match="PRIVACY_PASSWORD_INVALID"):
        keystore.unlock_keystore(PASSWORD)
    keystore.unlock_keystore("new password 123")
    assert decrypt_state(envelope) == {"secret": "prompt"}


def test_keystore_file_contains_no_usable_key_material():
    initialize_privacy_keystore(PASSWORD)
    envelope = encrypt_state({"secret": "prompt"})
    session = keystore._read_session()
    raw = json.loads(keystore.keystore_path().read_text(encoding="utf-8"))

    assert raw["schema"] == keystore.KEYSTORE_SCHEMA
    assert raw["kdf"]["name"] == "scrypt"
    keystore_text = json.dumps(raw)
    for key in session["keys"].values():
        assert keystore._b64url_encode(key) not in keystore_text
    assert envelope["keyId"] in {entry["keyId"] for entry in raw["keys"]}


def test_session_cache_survives_module_state_reset():
    """A fresh process (simulated by re-reading the session file) stays unlocked."""
    result = initialize_privacy_keystore(PASSWORD)
    envelope = encrypt_state({"secret": "prompt"})

    session = keystore._read_session()
    assert session is not None
    assert session["token"] == result["token"]
    assert decrypt_state(envelope) == {"secret": "prompt"}


def test_explicit_base_dir_keeps_legacy_behavior(tmp_path):
    initialize_privacy_keystore(PASSWORD)
    keystore.lock_keystore()
    # Callers that pass base_dir (tests, tools) bypass the keystore entirely.
    envelope = encrypt_state({"secret": "prompt"}, base_dir=tmp_path / "standalone")
    assert decrypt_state(envelope, base_dir=tmp_path / "standalone") == {"secret": "prompt"}


class _FakeRequest:
    def __init__(self, header_token=None, cookie_token=None):
        self.headers = {}
        self.cookies = {}
        if header_token is not None:
            self.headers["X-Helto-Privacy-Token"] = header_token
        if cookie_token is not None:
            self.cookies["helto_privacy_token"] = cookie_token


def test_check_privacy_token_gates_by_keystore_state():
    from routes.privacy import check_privacy_token

    # No keystore: legacy open behavior.
    assert check_privacy_token(_FakeRequest()) is None

    result = initialize_privacy_keystore(PASSWORD)
    token = result["token"]

    assert check_privacy_token(_FakeRequest(header_token=token)) is None
    assert check_privacy_token(_FakeRequest(cookie_token=token)) is None

    missing = check_privacy_token(_FakeRequest())
    assert missing is not None and missing.status == 401
    wrong = check_privacy_token(_FakeRequest(header_token="not-the-token"))
    assert wrong is not None and wrong.status == 401

    keystore.lock_keystore()
    locked = check_privacy_token(_FakeRequest(header_token=token))
    assert locked is not None and locked.status == 401
