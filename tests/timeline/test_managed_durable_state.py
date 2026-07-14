from __future__ import annotations

import copy

import pytest
from helto_privacy import SingletonSnapshot

from shared.timeline.managed_durable_state import (
    CAPTURE_INDEX_ID,
    CAPTURE_INDEX_SCHEMA,
    DURABLE_SINGLETON_IDS,
    DURABLE_STATE_RESOURCE_ID,
    DURABLE_STATE_STORE_ADAPTER_ID,
    MEDIA_FOLDER_SETTINGS_ID,
    MEDIA_FOLDER_SETTINGS_SCHEMA,
    TAKE_DELETION_JOURNAL_ID,
    TAKE_DELETION_JOURNAL_SCHEMA,
    DirectorDurableStateError,
    build_director_durable_state_privacy_profile,
    build_director_durable_state_server_adapters,
    media_folder_settings_view,
    normalize_capture_index,
    normalize_media_folder_settings,
    normalize_take_deletion_journal,
)


CANARY = "SYNTHETIC_DURABLE_PRIVATE_CANARY"
CAPTURE_ID = "hp-owner-" + "c" * 32
TRANSACTION_ID = "hp-operation-" + "t" * 32
REFERENCE = {
    "schema": "helto.private-artifact-reference",
    "version": 1,
    "id": "hp-art-" + "s" * 32,
}


def _folders():
    return {
        "schema": MEDIA_FOLDER_SETTINGS_SCHEMA,
        "version": 1,
        "folders": {
            "image": [{"alias": "images", "path": "/media/images", "enabled": True}],
            "video": [{"alias": "videos", "path": "/media/videos", "enabled": False}],
            "audio": [],
        },
    }


def _capture(phase="sidecar-pending"):
    return {
        "phase": phase,
        "externalTransactionId": TRANSACTION_ID,
        "artifactOwnerId": CAPTURE_ID,
        "sidecarReference": copy.deepcopy(REFERENCE),
    }


def _capture_index(entry=None):
    return {
        "schema": CAPTURE_INDEX_SCHEMA,
        "version": 1,
        "captures": {CAPTURE_ID: _capture() if entry is None else entry},
    }


def _target(relative_path=None):
    return {
        "rootDevice": 10,
        "rootInode": 20,
        "targetDevice": 10,
        "targetInode": 21,
        "relativePath": relative_path or ["takes", "synthetic.mp4"],
    }


def _journal():
    return {
        "schema": TAKE_DELETION_JOURNAL_SCHEMA,
        "version": 1,
        "intents": {
            "2" * 32: {
                "phase": "delete-intent",
                "externalTransactionId": TRANSACTION_ID,
                "captureId": CAPTURE_ID,
                "targets": [_target()],
            }
        },
    }


def test_durable_profile_composes_one_store_and_three_field_singletons(tmp_path):
    profile = build_director_durable_state_privacy_profile()
    resource = next(item for item in profile.resources if item.id == DURABLE_STATE_RESOURCE_ID)
    assert resource.kind.value == "singleton"
    assert resource.adapter_slots == (DURABLE_STATE_STORE_ADAPTER_ID,)
    assert {item.id for item in profile.singletons} == set(DURABLE_SINGLETON_IDS)
    assert {item.payload_kind.value for item in profile.singletons} == {"field"}
    assert {item.scope_id for item in profile.singletons} == {"director-global"}
    assert {item.store_adapter for item in profile.singletons} == {
        DURABLE_STATE_STORE_ADAPTER_ID
    }

    adapters = build_director_durable_state_server_adapters(tmp_path / "singletons.sqlite3")
    assert set(adapters) == {DURABLE_STATE_STORE_ADAPTER_ID}
    store = adapters[DURABLE_STATE_STORE_ADAPTER_ID]
    for method in profile.server_adapter_contracts[DURABLE_STATE_STORE_ADAPTER_ID]:
        assert callable(getattr(store, method, None))
    assert store.read_singleton(CAPTURE_INDEX_ID) == SingletonSnapshot(0)


def test_media_folder_settings_are_strict_bounded_and_do_not_persist_defaults():
    source = _folders()
    normalized = normalize_media_folder_settings(source)
    assert normalized == source
    assert normalized is not source
    assert normalized["folders"]["image"][0] is not source["folders"]["image"][0]

    defaults = {
        "image": [
            {"alias": "input", "path": "/comfy/input", "enabled": True},
            {"alias": "images", "path": "/default/overridden", "enabled": True},
        ],
        "video": [],
        "audio": [{"alias": "audio", "path": "/comfy/audio", "enabled": True}],
    }
    view = media_folder_settings_view(source, defaults)
    assert [item["alias"] for item in view["folders"]["image"]] == [
        "input", "images",
    ]
    assert view["folders"]["image"][1]["path"] == "/media/images"
    assert view["folders"]["audio"][0]["alias"] == "audio"
    assert normalize_media_folder_settings(source)["folders"]["audio"] == []


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.update({"unknown": CANARY}),
        lambda value: value["folders"].update({"document": []}),
        lambda value: value["folders"]["image"][0].update({"unknown": CANARY}),
        lambda value: value["folders"]["image"].append(
            {"alias": "images", "path": "/other", "enabled": True}
        ),
        lambda value: value["folders"]["image"].append(
            {"alias": "other", "path": "/media/images", "enabled": True}
        ),
        lambda value: value["folders"]["image"][0].update(path="relative/private"),
        lambda value: value["folders"]["image"][0].update(enabled=1),
    ),
)
def test_media_folder_settings_reject_unknown_duplicate_or_unsafe_values(mutate):
    value = _folders()
    mutate(value)
    with pytest.raises(DirectorDurableStateError) as failure:
        normalize_media_folder_settings(value)
    assert CANARY not in str(failure.value)
    assert CANARY not in repr(failure.value)


def test_media_folder_settings_reject_more_than_256_without_eviction():
    value = _folders()
    value["folders"]["image"] = [
        {"alias": f"folder-{index}", "path": f"/media/{index}", "enabled": True}
        for index in range(257)
    ]
    with pytest.raises(DirectorDurableStateError):
        normalize_media_folder_settings(value)


def test_media_folder_settings_reject_invalid_unicode_as_domain_error():
    value = _folders()
    value["folders"]["image"][0]["path"] = "/media/\ud800"
    with pytest.raises(DirectorDurableStateError):
        normalize_media_folder_settings(value)


def test_capture_index_normalizes_pending_and_committed_entries_deterministically():
    alternate = "hp-owner-" + "d" * 32
    value = _capture_index(_capture("committed"))
    value["captures"][alternate] = {
        **_capture(),
        "artifactOwnerId": alternate,
        "externalTransactionId": "hp-operation-" + "u" * 32,
    }
    normalized = normalize_capture_index(value)
    assert list(normalized["captures"]) == sorted((CAPTURE_ID, alternate))
    assert normalized["captures"][CAPTURE_ID]["sidecarReference"] == REFERENCE
    assert normalized["captures"][alternate]["sidecarReference"] == REFERENCE


@pytest.mark.parametrize(
    "forbidden",
    (
        "timeline",
        "path",
        "project",
        "shot",
        "take",
        "registration",
        "prompt",
        "model",
        "timestamp",
    ),
)
def test_capture_index_rejects_every_forbidden_sensitive_field(forbidden):
    value = _capture_index()
    value["captures"][CAPTURE_ID][forbidden] = CANARY
    with pytest.raises(DirectorDurableStateError) as failure:
        normalize_capture_index(value)
    assert CANARY not in str(failure.value)


@pytest.mark.parametrize(
    "entry",
    (
        {**_capture(), "artifactOwnerId": "hp-owner-" + "x" * 32},
        {**_capture(), "externalTransactionId": "not-a-transaction"},
        {**_capture(), "sidecarMac": "a" * 64},
        {**_capture(), "timelineMac": "b" * 64},
        {**_capture("committed"), "sidecarReference": None},
    ),
)
def test_capture_index_rejects_inconsistent_or_unkeyed_entries(entry):
    with pytest.raises(DirectorDurableStateError):
        normalize_capture_index(_capture_index(entry))


def test_capture_index_rejects_4097_entries_without_silent_eviction():
    captures = {}
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    for index in range(4097):
        suffix = "".join(
            alphabet[(index >> (offset * 6)) & 63] for offset in range(32)
        )
        capture_id = "hp-owner-" + suffix
        captures[capture_id] = {
            **_capture(),
            "artifactOwnerId": capture_id,
            "externalTransactionId": "hp-operation-" + suffix,
        }
    value = {
        "schema": CAPTURE_INDEX_SCHEMA,
        "version": 1,
        "captures": captures,
    }
    with pytest.raises(DirectorDurableStateError):
        normalize_capture_index(value)


def test_capture_index_rejects_non_string_keys_as_domain_error():
    value = _capture_index()
    value["captures"][1] = _capture()
    with pytest.raises(DirectorDurableStateError):
        normalize_capture_index(value)


def test_deletion_journal_binds_exact_root_target_and_path_and_sorts_targets():
    value = _journal()
    intent = value["intents"]["2" * 32]
    intent["targets"] = [
        _target(["takes", "z.mp4"]),
        {**_target(["takes", "a.mp4"]), "targetInode": 22},
    ]
    normalized = normalize_take_deletion_journal(value)
    targets = normalized["intents"]["2" * 32]["targets"]
    assert [item["relativePath"][-1] for item in targets] == ["a.mp4", "z.mp4"]
    assert targets[0]["rootDevice"] == 10
    assert targets[0]["rootInode"] == 20
    assert targets[0]["targetDevice"] == 10
    assert targets[0]["targetInode"] == 22


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.update({"unknown": CANARY}),
        lambda value: value["intents"]["2" * 32].update({"path": CANARY}),
        lambda value: value["intents"]["2" * 32].update(phase="deleted"),
        lambda value: value["intents"]["2" * 32]["targets"][0].update(rootInode=-1),
        lambda value: value["intents"]["2" * 32]["targets"][0].update(targetInode=0),
        lambda value: value["intents"]["2" * 32]["targets"][0].update(
            relativePath=["..", CANARY]
        ),
        lambda value: value["intents"]["2" * 32].update(targets=[]),
    ),
)
def test_deletion_journal_rejects_unknown_unbound_or_unsafe_intents(mutate):
    value = _journal()
    mutate(value)
    with pytest.raises(DirectorDurableStateError) as failure:
        normalize_take_deletion_journal(value)
    assert CANARY not in str(failure.value)
    assert CANARY not in repr(failure.value)


def test_deletion_journal_rejects_duplicate_and_unbounded_targets():
    duplicate = _journal()
    duplicate["intents"]["2" * 32]["targets"] *= 2
    with pytest.raises(DirectorDurableStateError):
        normalize_take_deletion_journal(duplicate)

    unbounded = _journal()
    unbounded["intents"]["2" * 32]["targets"] = [
        {**_target(["takes", f"{index}.mp4"]), "targetInode": index + 1}
        for index in range(17)
    ]
    with pytest.raises(DirectorDurableStateError):
        normalize_take_deletion_journal(unbounded)


def test_deletion_journal_rejects_hostile_keys_and_unicode_as_domain_errors():
    non_string_key = _journal()
    non_string_key["intents"][1] = non_string_key["intents"].pop("2" * 32)
    with pytest.raises(DirectorDurableStateError):
        normalize_take_deletion_journal(non_string_key)

    invalid_unicode = _journal()
    invalid_unicode["intents"]["2" * 32]["targets"][0]["relativePath"] = [
        "takes",
        "\ud800.mp4",
    ]
    with pytest.raises(DirectorDurableStateError):
        normalize_take_deletion_journal(invalid_unicode)


def test_singleton_identity_constants_remain_closed_and_product_specific():
    assert DURABLE_SINGLETON_IDS == (
        MEDIA_FOLDER_SETTINGS_ID,
        CAPTURE_INDEX_ID,
        TAKE_DELETION_JOURNAL_ID,
    )
