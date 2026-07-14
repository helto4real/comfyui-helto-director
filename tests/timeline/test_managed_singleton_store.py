from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import stat
from pathlib import Path

import pytest
from helto_privacy import SingletonSnapshot

import shared.timeline.managed_singleton_store as managed_singleton_store
from shared.timeline.managed_singleton_store import (
    DirectorManagedSingletonStore,
    DirectorManagedSingletonStoreError,
)


SINGLETON_ID = "media-folder-settings"
ALTERNATE_SINGLETON_ID = "capture-index"
ALLOWED_IDS = (SINGLETON_ID, ALTERNATE_SINGLETON_ID, "take-deletion-journal")
CANARY = "SYNTHETIC_SINGLETON_PLAINTEXT_CANARY"


def _protected(label: str) -> dict[str, object]:
    return {
        "algorithm": "AES-256-GCM",
        "ciphertext": label,
        "encrypted": True,
        "keyId": "synthetic-key",
        "nonce": "synthetic-nonce",
        "schema": "helto.director.synthetic",
        "version": 1,
    }


def _store(path: Path) -> DirectorManagedSingletonStore:
    return DirectorManagedSingletonStore(path, ALLOWED_IDS)


def _multiprocess_commit(
    path: str,
    label: str,
    ready,
    start,
    results,
) -> None:
    try:
        store = DirectorManagedSingletonStore(path, ALLOWED_IDS)
        transaction = store.begin_singleton_replace(
            SINGLETON_ID,
            0,
            SingletonSnapshot(1, _protected(label)),
        )
        ready.put(True)
        start.wait()
        results.put((label, transaction.commit()))
    except BaseException as error:
        results.put((label, type(error).__name__))


class _KnownPostCommitFaultStore(DirectorManagedSingletonStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, ALLOWED_IDS)
        self.fail_after_commit = True

    def _validate_after_commit(self) -> None:
        super()._validate_after_commit()
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError(CANARY)


class _PreCommitFaultStore(DirectorManagedSingletonStore):
    def _commit_connection(self, connection: sqlite3.Connection) -> None:
        raise OSError(CANARY)


class _UnknownCommitStore(DirectorManagedSingletonStore):
    def _commit_connection(self, connection: sqlite3.Connection) -> None:
        super()._commit_connection(connection)
        raise OSError(CANARY)


class _ReadbackFaultStore(DirectorManagedSingletonStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, ALLOWED_IDS)
        self.fail_next_read = False

    def read_singleton(self, singleton_id: str) -> SingletonSnapshot:
        if self.fail_next_read:
            self.fail_next_read = False
            raise DirectorManagedSingletonStoreError()
        return super().read_singleton(singleton_id)


def test_create_read_replace_and_revisioned_tombstone(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    store = _store(path)
    assert store.read_singleton(SINGLETON_ID) == SingletonSnapshot(0)
    assert not path.exists()

    first = SingletonSnapshot(1, _protected("first"))
    transaction = store.begin_singleton_replace(SINGLETON_ID, 0, first)
    assert transaction.commit() is True
    assert transaction.read_back() == first
    assert store.read_singleton(SINGLETON_ID) == first

    tombstone = SingletonSnapshot(2, None)
    assert store.begin_singleton_replace(SINGLETON_ID, 1, tombstone).commit() is True
    assert store.read_singleton(SINGLETON_ID) == tombstone

    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT singleton_id, revision, protected_json "
            "FROM helto_director_singletons"
        ).fetchall()
        tables = connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [(SINGLETON_ID, 2, None)]
    assert tables == [("helto_director_singletons",)]


def test_cross_instance_cas_has_one_winner_and_stale_writer_is_non_mutating(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    first_store = _store(path)
    second_store = _store(path)
    first = first_store.begin_singleton_replace(
        SINGLETON_ID,
        0,
        SingletonSnapshot(1, _protected("first")),
    )
    stale = second_store.begin_singleton_replace(
        SINGLETON_ID,
        0,
        SingletonSnapshot(1, _protected("stale")),
    )

    assert first.commit() is True
    assert stale.commit() is False
    assert second_store.read_singleton(SINGLETON_ID) == SingletonSnapshot(
        1,
        _protected("first"),
    )

    newer = second_store.begin_singleton_replace(
        SINGLETON_ID,
        1,
        SingletonSnapshot(2, _protected("newer")),
    )
    assert newer.commit() is True
    with pytest.raises(DirectorManagedSingletonStoreError):
        newer.commit()


def test_precommit_rollback_does_not_claim_identical_concurrent_write(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    replacement = SingletonSnapshot(1, _protected("identical"))
    prepared = _store(path).begin_singleton_replace(
        SINGLETON_ID,
        0,
        replacement,
    )
    winner = _store(path).begin_singleton_replace(
        SINGLETON_ID,
        0,
        replacement,
    )

    assert winner.commit() is True
    prepared.rollback()
    assert _store(path).read_singleton(SINGLETON_ID) == replacement


def test_known_cas_loser_rollback_does_not_claim_identical_winner(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    replacement = SingletonSnapshot(1, _protected("identical"))
    winner = _store(path).begin_singleton_replace(
        SINGLETON_ID,
        0,
        replacement,
    )
    loser = _store(path).begin_singleton_replace(
        SINGLETON_ID,
        0,
        replacement,
    )

    assert winner.commit() is True
    assert loser.commit() is False
    loser.rollback()
    assert _store(path).read_singleton(SINGLETON_ID) == replacement


def test_cross_process_begin_immediate_cas_has_exactly_one_winner(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    results = context.Queue()
    start = context.Event()
    processes = [
        context.Process(
            target=_multiprocess_commit,
            args=(str(path), label, ready, start, results),
        )
        for label in ("first", "second")
    ]
    try:
        for process in processes:
            process.start()
        assert ready.get(timeout=10) is True
        assert ready.get(timeout=10) is True
        start.set()
        outcomes = [results.get(timeout=10), results.get(timeout=10)]
        for process in processes:
            process.join(timeout=10)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert all(process.exitcode == 0 for process in processes)
    assert sorted(value for _label, value in outcomes) == [False, True]
    winner = next(label for label, value in outcomes if value is True)
    assert _store(path).read_singleton(SINGLETON_ID) == SingletonSnapshot(
        1,
        _protected(winner),
    )


def test_readback_failure_can_roll_back_exact_committed_snapshot(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    store = _ReadbackFaultStore(path)
    replacement = SingletonSnapshot(1, _protected("replacement"))
    transaction = store.begin_singleton_replace(SINGLETON_ID, 0, replacement)
    assert transaction.commit() is True
    store.fail_next_read = True
    with pytest.raises(DirectorManagedSingletonStoreError):
        transaction.read_back()

    transaction.rollback()
    assert store.read_singleton(SINGLETON_ID) == SingletonSnapshot(2, None)


def test_known_post_commit_fault_is_classifiable_and_exactly_rollbackable(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    store = _KnownPostCommitFaultStore(path)
    replacement = SingletonSnapshot(1, _protected("committed"))
    transaction = store.begin_singleton_replace(SINGLETON_ID, 0, replacement)

    with pytest.raises(DirectorManagedSingletonStoreError) as failure:
        transaction.commit()
    assert CANARY not in str(failure.value)
    assert transaction.read_back() == replacement
    transaction.rollback()
    assert store.read_singleton(SINGLETON_ID) == SingletonSnapshot(2, None)


def test_precommit_failure_cannot_rollback_identical_concurrent_winner(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    replacement = SingletonSnapshot(1, _protected("identical"))
    failed = _PreCommitFaultStore(path, ALLOWED_IDS).begin_singleton_replace(
        SINGLETON_ID,
        0,
        replacement,
    )

    with pytest.raises(DirectorManagedSingletonStoreError) as failure:
        failed.commit()
    assert CANARY not in str(failure.value)
    assert _store(path).begin_singleton_replace(
        SINGLETON_ID,
        0,
        replacement,
    ).commit()

    failed.rollback()
    assert _store(path).read_singleton(SINGLETON_ID) == replacement


def test_truly_unknown_commit_outcome_does_not_auto_rollback(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    replacement = SingletonSnapshot(1, _protected("possibly-committed"))
    store = _UnknownCommitStore(path, ALLOWED_IDS)
    transaction = store.begin_singleton_replace(SINGLETON_ID, 0, replacement)

    with pytest.raises(DirectorManagedSingletonStoreError) as failure:
        transaction.commit()
    assert CANARY not in str(failure.value)
    assert transaction.read_back() == replacement
    transaction.rollback()
    assert store.read_singleton(SINGLETON_ID) == replacement


@pytest.mark.parametrize("mutation", ("json", "format", "unknown", "schema"))
def test_malformed_database_fails_closed_with_sanitized_error(
    tmp_path,
    mutation,
):
    path = tmp_path / "state" / "singletons.sqlite3"
    store = _store(path)
    assert store.begin_singleton_replace(
        SINGLETON_ID,
        0,
        SingletonSnapshot(1, _protected("valid")),
    ).commit()

    connection = sqlite3.connect(path)
    try:
        if mutation == "json":
            connection.execute(
                "UPDATE helto_director_singletons SET protected_json = ?",
                (f'{{"secret":"{CANARY}"}} ',),
            )
        elif mutation == "format":
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE helto_director_singletons SET format = ?",
                (CANARY,),
            )
        elif mutation == "unknown":
            connection.execute(
                "INSERT INTO helto_director_singletons "
                "(singleton_id, format, revision, protected_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    "undeclared-singleton",
                    "helto.director.singleton-store-v1",
                    1,
                    json.dumps(
                        _protected(CANARY),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
        else:
            connection.execute(
                "ALTER TABLE helto_director_singletons ADD COLUMN unexpected TEXT"
            )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DirectorManagedSingletonStoreError) as failure:
        store.read_singleton(SINGLETON_ID)
    assert str(path) not in str(failure.value)
    assert CANARY not in str(failure.value)
    assert repr(failure.value) == "DirectorManagedSingletonStoreError()"


def test_rollback_full_snapshot_cas_preserves_conflicting_newer_writer(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    store = _store(path)
    replacement = SingletonSnapshot(1, _protected("replacement"))
    transaction = store.begin_singleton_replace(SINGLETON_ID, 0, replacement)
    assert transaction.commit() is True

    concurrent = SingletonSnapshot(2, _protected("concurrent"))
    assert store.begin_singleton_replace(SINGLETON_ID, 1, concurrent).commit() is True
    with pytest.raises(DirectorManagedSingletonStoreError):
        transaction.rollback()
    assert store.read_singleton(SINGLETON_ID) == concurrent

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE helto_director_singletons SET revision = 1, protected_json = ?",
            (
                json.dumps(
                    _protected("same-revision-other-value"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    assert store.rollback_singleton_replace(
        SINGLETON_ID,
        replacement,
        SingletonSnapshot(2, None),
    ) is False
    assert store.read_singleton(SINGLETON_ID) == SingletonSnapshot(
        1,
        _protected("same-revision-other-value"),
    )


def test_store_enforces_private_permissions_and_has_no_plaintext_alternate(tmp_path):
    parent = tmp_path / "state"
    parent.mkdir(mode=0o755)
    os.chmod(parent, 0o755)
    path = parent / "singletons.sqlite3"
    store = _store(path)
    protected = _protected("opaque-ciphertext-without-the-secret")
    plaintext_source = {"secret": CANARY}
    assert store.begin_singleton_replace(
        SINGLETON_ID,
        0,
        SingletonSnapshot(1, protected),
    ).commit()

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.with_name(path.name + "-journal").exists()
    assert CANARY.encode() not in path.read_bytes()
    assert plaintext_source == {"secret": CANARY}
    assert str(path) not in repr(store)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        connection.close()


def test_existing_valid_database_mode_is_repaired_to_owner_only(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    snapshot = SingletonSnapshot(1, _protected("persisted"))
    assert _store(path).begin_singleton_replace(
        SINGLETON_ID,
        0,
        snapshot,
    ).commit()
    os.chmod(path, 0o644)

    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert _store(path).read_singleton(SINGLETON_ID) == snapshot
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_closed_ids_and_invalid_replacements_fail_without_creating_storage(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    store = _store(path)
    with pytest.raises(DirectorManagedSingletonStoreError):
        store.read_singleton("undeclared-singleton")
    with pytest.raises(DirectorManagedSingletonStoreError):
        store.read_singleton([])  # type: ignore[arg-type]
    with pytest.raises(DirectorManagedSingletonStoreError):
        store.begin_singleton_replace(
            SINGLETON_ID,
            0,
            SingletonSnapshot(2, _protected("skipped-revision")),
        )
    with pytest.raises(DirectorManagedSingletonStoreError):
        store.begin_singleton_replace(
            SINGLETON_ID,
            0,
            SingletonSnapshot(1, {"not-json": (CANARY,)}),
        )
    assert not path.exists()


def test_cyclic_and_excessively_deep_protected_values_fail_sanitized(tmp_path):
    path = tmp_path / "state" / "singletons.sqlite3"
    store = _store(path)
    cyclic: list[object] = []
    cyclic.append(cyclic)
    deep: object = "leaf"
    for _ in range(65):
        deep = [deep]

    for protected in (cyclic, deep):
        with pytest.raises(DirectorManagedSingletonStoreError) as failure:
            store.begin_singleton_replace(
                SINGLETON_ID,
                0,
                SingletonSnapshot(1, protected),
            )
        assert repr(failure.value) == "DirectorManagedSingletonStoreError()"
        assert str(path) not in str(failure.value)
    assert not path.exists()


def test_over_budget_container_is_rejected_before_child_copy(tmp_path, monkeypatch):
    path = tmp_path / "state" / "singletons.sqlite3"
    copied = False

    def reject_copy(_value):
        nonlocal copied
        copied = True
        raise AssertionError("over-budget container was copied")

    monkeypatch.setattr(managed_singleton_store, "_MAX_PROTECTED_ITEMS", 4)
    monkeypatch.setattr(
        managed_singleton_store,
        "tuple",
        reject_copy,
        raising=False,
    )

    with pytest.raises(DirectorManagedSingletonStoreError):
        _store(path).begin_singleton_replace(
            SINGLETON_ID,
            0,
            SingletonSnapshot(1, [None, None, None, None]),
        )
    assert copied is False
    assert not path.exists()


def test_dangling_database_symlink_is_invalid_not_absent(tmp_path):
    parent = tmp_path / "state"
    parent.mkdir()
    path = parent / "singletons.sqlite3"
    try:
        path.symlink_to(parent / "missing.sqlite3")
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(DirectorManagedSingletonStoreError):
        _store(path).read_singleton(SINGLETON_ID)
