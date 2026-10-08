"""Process-scoped admission lock with durable recovery metadata."""

import fcntl
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import BinaryIO


class AdmissionStateError(RuntimeError):
    """Persisted admission state cannot safely be interpreted."""


class AdmissionController:
    """Serialize admission; preserve state until its owner confirms completion.

    Acquiring the OS lock does not prove downstream processing has stopped.
    A new owner must reconcile any existing snapshot before accepting work.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.directory / "admission.lock"
        self._state_path = self.directory / "admission.json"
        self._mutex = RLock()
        self._lock_file: BinaryIO | None = None

    @property
    def lock_fd(self) -> int | None:
        """Descriptor optionally inherited by a managed conversion subprocess."""
        with self._mutex:
            return self._lock_file.fileno() if self._lock_file is not None else None

    def try_acquire(self) -> bool:
        """Claim the lock without waiting; malformed state fails closed."""
        with self._mutex:
            if self._lock_file is not None:
                return False
            lock_file = self._lock_path.open("a+b")
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock_file.close()
                return False
            except BaseException:
                lock_file.close()
                raise
            self._lock_file = lock_file
            try:
                self.snapshot()
            except BaseException:
                self.release(clear=False)
                raise
            return True

    def is_locked(self) -> bool:
        """Probe OS ownership, including other processes and inherited FDs."""
        with self._mutex, self._lock_path.open("a+b") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False

    def snapshot(self) -> dict | None:
        """Read an atomic snapshot, also when another process owns the lock."""
        with self._mutex:
            try:
                content = self._state_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return None
            except UnicodeError as exc:
                raise AdmissionStateError("Invalid persisted admission state") from exc
            try:
                state = json.loads(content)
            except (ValueError, UnicodeError) as exc:
                raise AdmissionStateError("Invalid persisted admission state") from exc
            if not isinstance(state, dict):
                raise AdmissionStateError("Admission state must be an object")
            return state

    def update(self, **fields) -> None:
        """Merge recovery/progress fields and durably publish one JSON object."""
        with self._mutex:
            if self._lock_file is None:
                raise RuntimeError("Admission lock must be held to update state")
            state = self.snapshot() or {}
            state.update(fields)
            state["updated_at"] = datetime.now(UTC).isoformat()
            # Serialize first so invalid fields cannot change the current state.
            content = json.dumps(state, ensure_ascii=False, allow_nan=False)
            descriptor, name = tempfile.mkstemp(
                prefix=".admission-", suffix=".json", dir=self.directory
            )
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self._state_path)
                self._sync_directory()
            finally:
                temporary.unlink(missing_ok=True)

    def release(self, clear: bool = True) -> None:
        """Release a held claim; clear only after confirmed completion/cleanup."""
        with self._mutex:
            if self._lock_file is None:
                return
            if clear:
                self._state_path.unlink(missing_ok=True)
                self._sync_directory()
            lock_file = self._lock_file
            self._lock_file = None
            # Closing releases flock and prevents leaked ownership descriptors.
            # Never unlink admission.lock: all processes must lock the same inode.
            lock_file.close()

    def close(self) -> None:
        """Close during shutdown while retaining state for recovery."""
        self.release(clear=False)

    def _sync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
