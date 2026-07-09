"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from config.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings()
