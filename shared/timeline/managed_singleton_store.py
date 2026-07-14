"""Director-owned SQLite storage for shared protected singleton snapshots.

The store persists only the opaque representation produced by ``helto_privacy``.
It has no plaintext codec, fallback file, or product-state interpretation.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
from collections.abc import Callable, Iterable
from pathlib import Path

from helto_privacy import SingletonSnapshot


_STABLE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_TABLE = "helto_director_singletons"
_STORE_FORMAT = "helto.director.singleton-store-v1"
_SCHEMA_SQL = (
    f"CREATE TABLE {_TABLE} ("
    "singleton_id TEXT PRIMARY KEY NOT NULL, "
    f"format TEXT NOT NULL CHECK (format = '{_STORE_FORMAT}'), "
    "revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision > 0), "
    "protected_json TEXT CHECK ("
    "protected_json IS NULL OR typeof(protected_json) = 'text'"
    ")"
    ") STRICT, WITHOUT ROWID"
)
_CREATE_SCHEMA_SQL = _SCHEMA_SQL.replace(
    "CREATE TABLE ",
    "CREATE TABLE IF NOT EXISTS ",
    1,
)
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_CREATE_FLAGS = (
    os.O_CREAT
    | os.O_EXCL
    | os.O_RDWR
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_MAX_PROTECTED_DEPTH = 64
_MAX_PROTECTED_ITEMS = 100_000
_MAX_PROTECTED_BYTES = 16 * 1024 * 1024


class DirectorManagedSingletonStoreError(RuntimeError):
    """Product-data-free failure for every singleton-store operation."""

    def __init__(self) -> None:
        super().__init__("Director protected singleton persistence failed.")

    def __repr__(self) -> str:
        return "DirectorManagedSingletonStoreError()"


class DirectorManagedSingletonStore:
    """One strict SQLite store for a closed set of singleton identifiers."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        allowed_singleton_ids: Iterable[str],
    ) -> None:
        try:
            if isinstance(allowed_singleton_ids, (str, bytes)):
                raise ValueError
            normalized = frozenset(allowed_singleton_ids)
            if (
                not normalized
                or any(
                    not isinstance(value, str) or _STABLE_ID.fullmatch(value) is None
                    for value in normalized
                )
            ):
                raise ValueError
            raw_path = os.path.expanduser(os.fspath(path))
            absolute = Path(os.path.abspath(raw_path))
            if not absolute.name or "\x00" in str(absolute):
                raise ValueError
        except (TypeError, ValueError, OSError):
            raise DirectorManagedSingletonStoreError() from None
        self._path = absolute
        self._allowed_singleton_ids = normalized

    def read_singleton(self, singleton_id: str) -> SingletonSnapshot:
        """Return one exact revision, or revision zero when the store is absent."""

        self._require_singleton_id(singleton_id)
        try:
            os.lstat(self._path)
        except FileNotFoundError:
            return SingletonSnapshot(0)
        except OSError:
            raise DirectorManagedSingletonStoreError() from None
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect(create=False)
            self._ensure_schema(connection)
            self._validate_all_rows(connection)
            return self._read_row(connection, singleton_id)
        except DirectorManagedSingletonStoreError:
            raise
        except Exception:
            raise DirectorManagedSingletonStoreError() from None
        finally:
            if connection is not None:
                connection.close()

    def begin_singleton_replace(
        self,
        singleton_id: str,
        expected_revision: int,
        replacement: SingletonSnapshot,
    ) -> "DirectorSingletonStoreTransaction":
        """Prepare one revision-CAS transaction without changing storage."""

        self._require_singleton_id(singleton_id)
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
            or not isinstance(replacement, SingletonSnapshot)
            or replacement.revision != expected_revision + 1
        ):
            raise DirectorManagedSingletonStoreError()
        _encoded_protected(replacement.protected)
        original = self.read_singleton(singleton_id)
        return DirectorSingletonStoreTransaction(
            self,
            singleton_id,
            expected_revision,
            replacement,
            original,
        )

    def rollback_singleton_replace(
        self,
        singleton_id: str,
        expected: SingletonSnapshot,
        replacement: SingletonSnapshot,
    ) -> bool:
        """Replace an exact committed snapshot with one newer rollback snapshot."""

        self._require_singleton_id(singleton_id)
        if (
            not isinstance(expected, SingletonSnapshot)
            or not isinstance(replacement, SingletonSnapshot)
            or replacement.revision != expected.revision + 1
        ):
            raise DirectorManagedSingletonStoreError()
        _encoded_protected(expected.protected)
        _encoded_protected(replacement.protected)
        return self._replace(
            singleton_id,
            expected_revision=expected.revision,
            expected_snapshot=expected,
            replacement=replacement,
        )

    def _replace_revision(
        self,
        singleton_id: str,
        expected_revision: int,
        replacement: SingletonSnapshot,
        on_committed: Callable[[], None],
    ) -> bool:
        return self._replace(
            singleton_id,
            expected_revision=expected_revision,
            expected_snapshot=None,
            replacement=replacement,
            on_committed=on_committed,
        )

    def _replace(
        self,
        singleton_id: str,
        *,
        expected_revision: int,
        expected_snapshot: SingletonSnapshot | None,
        replacement: SingletonSnapshot,
        on_committed: Callable[[], None] | None = None,
    ) -> bool:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect(create=True)
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_schema(connection)
            self._validate_all_rows(connection)
            current = self._read_row(connection, singleton_id)
            if current.revision != expected_revision or (
                expected_snapshot is not None
                and not _snapshots_equal(current, expected_snapshot)
            ):
                connection.rollback()
                return False
            self._write_row(connection, singleton_id, replacement)
            if not _snapshots_equal(
                self._read_row(connection, singleton_id),
                replacement,
            ):
                raise DirectorManagedSingletonStoreError()
            self._commit_connection(connection)
            if on_committed is not None:
                on_committed()
            self._validate_after_commit()
            return True
        except DirectorManagedSingletonStoreError:
            if connection is not None:
                _rollback_silent(connection)
            raise
        except Exception:
            if connection is not None:
                _rollback_silent(connection)
            raise DirectorManagedSingletonStoreError() from None
        finally:
            if connection is not None:
                connection.close()

    def _commit_connection(self, connection: sqlite3.Connection) -> None:
        connection.commit()

    def _validate_after_commit(self) -> None:
        self._validate_database_file()

    def _connect(self, *, create: bool) -> sqlite3.Connection:
        if create:
            self._prepare_database_path()
        else:
            self._validate_parent_directory()
            self._validate_database_file()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                str(self._path),
                timeout=5.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            secure_delete = connection.execute("PRAGMA secure_delete=ON").fetchone()
            journal_mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA busy_timeout=5000")
            if (
                secure_delete is None
                or secure_delete[0] != 1
                or journal_mode is None
                or str(journal_mode[0]).lower() != "delete"
            ):
                raise DirectorManagedSingletonStoreError()
            self._validate_database_file()
            return connection
        except DirectorManagedSingletonStoreError:
            if connection is not None:
                connection.close()
            raise
        except Exception:
            if connection is not None:
                connection.close()
            raise DirectorManagedSingletonStoreError() from None

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(_CREATE_SCHEMA_SQL)
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        if len(rows) != 1:
            raise DirectorManagedSingletonStoreError()
        row = rows[0]
        if (
            row["type"] != "table"
            or row["name"] != _TABLE
            or row["tbl_name"] != _TABLE
            or row["sql"] != _SCHEMA_SQL
        ):
            raise DirectorManagedSingletonStoreError()

    def _validate_all_rows(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            f"SELECT singleton_id, format, revision, protected_json FROM {_TABLE}"
        ).fetchall()
        if len(rows) > len(self._allowed_singleton_ids):
            raise DirectorManagedSingletonStoreError()
        for row in rows:
            singleton_id = row["singleton_id"]
            revision = row["revision"]
            protected_json = row["protected_json"]
            if (
                singleton_id not in self._allowed_singleton_ids
                or row["format"] != _STORE_FORMAT
                or not isinstance(revision, int)
                or isinstance(revision, bool)
                or revision < 1
                or (protected_json is not None and not isinstance(protected_json, str))
            ):
                raise DirectorManagedSingletonStoreError()
            if protected_json is not None:
                _decoded_protected(protected_json)

    def _read_row(
        self,
        connection: sqlite3.Connection,
        singleton_id: str,
    ) -> SingletonSnapshot:
        row = connection.execute(
            f"SELECT revision, protected_json FROM {_TABLE} WHERE singleton_id = ?",
            (singleton_id,),
        ).fetchone()
        if row is None:
            return SingletonSnapshot(0)
        protected_json = row["protected_json"]
        protected = (
            None if protected_json is None else _decoded_protected(protected_json)
        )
        try:
            return SingletonSnapshot(row["revision"], protected)
        except (TypeError, ValueError):
            raise DirectorManagedSingletonStoreError() from None

    @staticmethod
    def _write_row(
        connection: sqlite3.Connection,
        singleton_id: str,
        replacement: SingletonSnapshot,
    ) -> None:
        protected_json = (
            None
            if replacement.protected is None
            else _encoded_protected(replacement.protected)
        )
        connection.execute(
            f"""
            INSERT INTO {_TABLE}
                (singleton_id, format, revision, protected_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                format=excluded.format,
                revision=excluded.revision,
                protected_json=excluded.protected_json
            """,
            (
                singleton_id,
                _STORE_FORMAT,
                replacement.revision,
                protected_json,
            ),
        )

    def _require_singleton_id(self, singleton_id: object) -> None:
        if (
            not isinstance(singleton_id, str)
            or _STABLE_ID.fullmatch(singleton_id) is None
            or singleton_id not in self._allowed_singleton_ids
        ):
            raise DirectorManagedSingletonStoreError()

    def _prepare_database_path(self) -> None:
        parent = self._path.parent
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._validate_parent_directory()
            try:
                descriptor = os.open(self._path, _FILE_CREATE_FLAGS, 0o600)
            except FileExistsError:
                descriptor = None
            if descriptor is not None:
                try:
                    os.fchmod(descriptor, 0o600)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            self._validate_database_file()
        except DirectorManagedSingletonStoreError:
            raise
        except OSError:
            raise DirectorManagedSingletonStoreError() from None

    def _validate_parent_directory(self) -> None:
        descriptor: int | None = None
        try:
            info = os.lstat(self._path.parent)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                raise DirectorManagedSingletonStoreError()
            descriptor = os.open(self._path.parent, _DIRECTORY_FLAGS)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise DirectorManagedSingletonStoreError()
            os.fchmod(descriptor, 0o700)
            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
                raise DirectorManagedSingletonStoreError()
        except DirectorManagedSingletonStoreError:
            raise
        except OSError:
            raise DirectorManagedSingletonStoreError() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _validate_database_file(self) -> None:
        try:
            info = os.lstat(self._path)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
            ):
                raise DirectorManagedSingletonStoreError()
            os.chmod(self._path, 0o600, follow_symlinks=False)
            current = os.lstat(self._path)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_uid != os.geteuid()
                or current.st_nlink != 1
                or stat.S_IMODE(current.st_mode) != 0o600
                or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise DirectorManagedSingletonStoreError()
        except DirectorManagedSingletonStoreError:
            raise
        except OSError:
            raise DirectorManagedSingletonStoreError() from None


class DirectorSingletonStoreTransaction:
    """One-use revision CAS with exact, monotonic rollback semantics."""

    def __init__(
        self,
        store: DirectorManagedSingletonStore,
        singleton_id: str,
        expected_revision: int,
        replacement: SingletonSnapshot,
        original: SingletonSnapshot,
    ) -> None:
        self._store = store
        self._singleton_id = singleton_id
        self._expected_revision = expected_revision
        self._replacement = SingletonSnapshot(
            replacement.revision,
            replacement.protected,
        )
        self._original = SingletonSnapshot(original.revision, original.protected)
        self._attempted = False
        self._commit_outcome: bool | None = None

    def commit(self) -> bool:
        if self._attempted:
            raise DirectorManagedSingletonStoreError()
        self._attempted = True
        outcome = self._store._replace_revision(
            self._singleton_id,
            self._expected_revision,
            self._replacement,
            self._mark_committed,
        )
        self._commit_outcome = outcome
        return outcome

    def read_back(self) -> SingletonSnapshot:
        return self._store.read_singleton(self._singleton_id)

    def rollback(self) -> None:
        # Only a commit that returned from SQLite owns the replacement. A
        # prepared, losing, pre-commit-failed, or truly ambiguous transaction
        # cannot distinguish an identical concurrent winner without adding
        # forbidden durable transaction metadata.
        if self._commit_outcome is not True:
            return
        current = self._store.read_singleton(self._singleton_id)
        if _snapshots_equal(current, self._original):
            return
        if not _snapshots_equal(current, self._replacement):
            raise DirectorManagedSingletonStoreError()
        restored = SingletonSnapshot(
            current.revision + 1,
            self._original.protected,
        )
        if not self._store.rollback_singleton_replace(
            self._singleton_id,
            current,
            restored,
        ):
            raise DirectorManagedSingletonStoreError()
        if not _snapshots_equal(
            self._store.read_singleton(self._singleton_id),
            restored,
        ):
            raise DirectorManagedSingletonStoreError()

    def _mark_committed(self) -> None:
        self._commit_outcome = True


def _encoded_protected(value: object) -> str:
    _require_json_value(value)
    encoded = _encoded_from_decoded(value)
    if _encoded_from_decoded(_decoded_protected(encoded)) != encoded:
        raise DirectorManagedSingletonStoreError()
    return encoded


def _decoded_protected(value: str) -> object:
    def reject_constant(_value: str) -> object:
        raise ValueError

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError
            result[key] = item
        return result

    try:
        if len(value.encode("utf-8")) > _MAX_PROTECTED_BYTES:
            raise ValueError
        decoded = json.loads(
            value,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
        _require_json_value(decoded)
        if _encoded_from_decoded(decoded) != value:
            raise ValueError
        return decoded
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise DirectorManagedSingletonStoreError() from None


def _encoded_from_decoded(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > _MAX_PROTECTED_BYTES:
            raise ValueError
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise DirectorManagedSingletonStoreError() from None


def _require_json_value(value: object) -> None:
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active_containers: set[int] = set()
    scheduled_item_count = 1
    if scheduled_item_count > _MAX_PROTECTED_ITEMS:
        raise DirectorManagedSingletonStoreError()

    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active_containers.remove(id(current))
            continue

        if current is None or type(current) in {bool, str, int}:
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise DirectorManagedSingletonStoreError()
            continue
        if type(current) not in {list, dict} or depth >= _MAX_PROTECTED_DEPTH:
            raise DirectorManagedSingletonStoreError()
        child_count = len(current)
        if child_count > _MAX_PROTECTED_ITEMS - scheduled_item_count:
            raise DirectorManagedSingletonStoreError()
        scheduled_item_count += child_count

        identity = id(current)
        if identity in active_containers:
            raise DirectorManagedSingletonStoreError()
        active_containers.add(identity)
        stack.append((current, depth, True))

        if type(current) is dict:
            if any(not isinstance(key, str) for key in current):
                raise DirectorManagedSingletonStoreError()
            children = tuple(current.values())
        else:
            children = tuple(current)
        for child in reversed(children):
            stack.append((child, depth + 1, False))


def _snapshots_equal(left: object, right: SingletonSnapshot) -> bool:
    if not isinstance(left, SingletonSnapshot) or left.revision != right.revision:
        return False
    if left.protected is None or right.protected is None:
        return left.protected is None and right.protected is None
    try:
        return _encoded_protected(left.protected) == _encoded_protected(
            right.protected
        )
    except DirectorManagedSingletonStoreError:
        return False


def _rollback_silent(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        pass


__all__ = [
    "DirectorManagedSingletonStore",
    "DirectorManagedSingletonStoreError",
    "DirectorSingletonStoreTransaction",
]
