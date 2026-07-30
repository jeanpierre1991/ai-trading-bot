"""Kill-switch readers for the bounded paper operator (Milestone 12.1).

Checked before every operator cycle, including before the first ``run_once``.
File presence and/or a truthy environment variable engage the switch.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


_TRUTHY_ENV = frozenset({"1", "true", "yes", "on"})


class KillSwitch(Protocol):
    """Minimal kill-switch contract used by ``PaperOperator``."""

    def is_engaged(self) -> bool:
        """Return True when the operator must refuse or stop new cycles."""


class FileEnvKillSwitch:
    """Engage when a kill file exists and/or an env var is truthy.

    Parameters
    ----------
    path:
        Filesystem path inspected with ``Path.exists()``. Missing path → not
        engaged via file. Any existing file (including empty) engages.
    env_var:
        Environment variable name. Engaged when its stripped lowercased value
        is one of ``1`` / ``true`` / ``yes`` / ``on``. Unset or other values
        do not engage via env.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        env_var: str = "PAPER_OPERATOR_KILL",
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._env_var = env_var

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def env_var(self) -> str:
        return self._env_var

    def is_engaged(self) -> bool:
        if self._env_engaged():
            return True
        if self._path is not None and self._path.exists():
            return True
        return False

    def _env_engaged(self) -> bool:
        raw = os.environ.get(self._env_var)
        if raw is None:
            return False
        return raw.strip().lower() in _TRUTHY_ENV
