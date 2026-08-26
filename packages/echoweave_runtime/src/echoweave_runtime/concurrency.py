from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import BinaryIO


class FileLockTimeoutError(TimeoutError):
    pass


class KeyedRLockPool:
    """Process-wide keyed reentrant locks shared by otherwise separate objects."""

    _instance: "KeyedRLockPool | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "KeyedRLockPool":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._locks_guard = threading.Lock()
                    instance._locks = {}
                    cls._instance = instance
        return cls._instance

    def lock_for(self, key: str | Path) -> threading.RLock:
        normalized = str(Path(key).expanduser().resolve()) if isinstance(key, Path) else str(key)
        with self._locks_guard:
            lock = self._locks.get(normalized)
            if lock is None:
                lock = threading.RLock()
                self._locks[normalized] = lock
            return lock


class InterProcessFileLock:
    """Small cross-platform exclusive lock backed by one byte in a sidecar file."""

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._try_lock(handle)
                self._handle = handle
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise FileLockTimeoutError(f"timed out locking {self.path}") from exc
                time.sleep(self.poll_interval_seconds)

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            self._unlock(handle)
        finally:
            self._handle = None
            handle.close()

    @staticmethod
    def _try_lock(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> "InterProcessFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.release()
        return False


__all__ = ["FileLockTimeoutError", "InterProcessFileLock", "KeyedRLockPool"]
