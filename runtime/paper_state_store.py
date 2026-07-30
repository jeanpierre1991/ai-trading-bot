"""Atomic JSON persistence for paper-operator state (Milestone 12.2)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

from core.exceptions import ConfigurationError
from runtime.paper_state import OperatorState


class PaperStateStore(Protocol):
    """Load/save contract for durable paper-operator state."""

    def exists(self) -> bool:
        """Return True when a state file is present at the configured path."""

    def ensure_parent_writable(self) -> None:
        """Create parent dirs and verify writability. Fail closed on OSError."""

    def load(self) -> OperatorState:
        """Load and validate state. Raises ConfigurationError on failure."""

    def save(self, state: OperatorState) -> None:
        """Atomically persist state."""


class JsonPaperStateStore:
    """JSON file store using temp-file write + ``os.replace``."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.is_file()

    def ensure_parent_writable(self) -> None:
        """Create the state parent directory and prove it is writable (M12.4 S1b).

        Called at operator start before any ``run_once`` so unattended runs fail
        closed early. Does not create, repair, or truncate the state file itself.
        """
        parent = self._path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                f"failed to create state directory {parent}: {exc}"
            ) from exc

        probe_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.writecheck.",
                suffix=".tmp",
                dir=str(parent),
            )
            probe_path = Path(tmp_name)
            os.close(fd)
            probe_path.unlink(missing_ok=True)
            probe_path = None
        except OSError as exc:
            raise ConfigurationError(
                f"operator state directory is not writable: {parent}: {exc}"
            ) from exc
        finally:
            if probe_path is not None:
                try:
                    probe_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def load(self) -> OperatorState:
        if not self.exists():
            raise ConfigurationError(
                f"operator state file not found: {self._path}"
            )
        try:
            raw_text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(
                f"failed to read operator state file {self._path}: {exc}"
            ) from exc
        try:
            payload: Any = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"corrupt operator state JSON at {self._path}: {exc}"
            ) from exc
        try:
            return OperatorState.from_json_dict(payload)
        except ConfigurationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ConfigurationError(
                f"invalid operator state at {self._path}: {exc}"
            ) from exc

    def save(self, state: OperatorState) -> None:
        payload = state.to_json_dict()
        parent = self._path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                f"failed to create state directory {parent}: {exc}"
            ) from exc

        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=str(parent),
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self._path)
            tmp_path = None
        except ConfigurationError:
            raise
        except OSError as exc:
            raise ConfigurationError(
                f"failed to atomically save operator state to {self._path}: {exc}"
            ) from exc
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
