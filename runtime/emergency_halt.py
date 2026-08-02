"""Durable emergency halt latch (Milestone 14.1).

Once engaged, remains engaged across process restarts until explicitly cleared.
Fail closed on corrupt latch state.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.exceptions import ConfigurationError


@dataclass(frozen=True)
class HaltState:
    engaged: bool
    incident_id: str | None = None
    reason: str | None = None
    trigger_source: str | None = None
    engaged_at: str | None = None


class DurableEmergencyHaltLatch:
    """Atomic JSON latch: engaged halt survives restarts."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._state = HaltState(engaged=False)
        self.ensure_ready()

    @property
    def path(self) -> Path:
        return self._path

    def ensure_ready(self) -> None:
        parent = self._path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                f"failed to create emergency halt directory {parent}: {exc}"
            ) from exc
        if self._path.is_file():
            self.load()
        else:
            self._persist(self._state)

    def load(self) -> HaltState:
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(
                f"failed to read emergency halt latch {self._path}: {exc}"
            ) from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"emergency halt latch is not valid JSON: {self._path}"
            ) from exc
        if not isinstance(payload, dict):
            raise ConfigurationError("emergency halt latch root must be an object")
        engaged = bool(payload.get("engaged", False))
        incident_id = _opt_str(payload.get("incident_id"))
        reason = _opt_str(payload.get("reason"))
        trigger_source = _opt_str(payload.get("trigger_source"))
        engaged_at = _opt_str(payload.get("engaged_at"))
        if engaged and not incident_id:
            raise ConfigurationError(
                "emergency halt latch engaged without incident_id (fail closed)"
            )
        self._state = HaltState(
            engaged=engaged,
            incident_id=incident_id,
            reason=reason,
            trigger_source=trigger_source,
            engaged_at=engaged_at,
        )
        return self._state

    def is_engaged(self) -> bool:
        return self._state.engaged

    @property
    def state(self) -> HaltState:
        return self._state

    def engage(
        self,
        *,
        incident_id: str,
        reason: str,
        trigger_source: str,
    ) -> HaltState:
        if self._state.engaged:
            return self._state
        cid = str(incident_id or "").strip()
        if not cid:
            raise ConfigurationError("incident_id is required to engage halt latch")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._state = HaltState(
            engaged=True,
            incident_id=cid,
            reason=str(reason or "emergency_stop"),
            trigger_source=str(trigger_source or "unknown"),
            engaged_at=now,
        )
        self._persist(self._state)
        return self._state

    def clear(self) -> HaltState:
        """Supervised clear — not used automatically by emergency stop."""
        self._state = HaltState(engaged=False)
        self._persist(self._state)
        return self._state

    def _persist(self, state: HaltState) -> None:
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "engaged": state.engaged,
            "incident_id": state.incident_id,
            "reason": state.reason,
            "trigger_source": state.trigger_source,
            "engaged_at": state.engaged_at,
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        fd: int | None = None
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=str(parent),
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self._path)
            tmp_path = None
        except OSError as exc:
            raise ConfigurationError(
                f"failed to persist emergency halt latch {self._path}: {exc}"
            ) from exc
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
