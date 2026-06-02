"""Shared fixtures for live integration tests.

These tests hit the real Tension API and are skipped automatically unless
``TENSION_USERNAME`` and ``TENSION_PASSWORD`` are available in the environment
(or in a ``.env`` file at the repository root). They are intentionally kept
separate from the fast, network-free unit tests in ``tests/``.
"""

from __future__ import annotations

import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Load ``.env`` from the repo root into ``os.environ`` if present.

    Uses python-dotenv when installed, otherwise falls back to a tiny parser so
    the suite has no hard test dependency. Existing environment variables always
    win over ``.env`` values.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return

    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


@pytest.fixture(scope="session")
def tension_credentials() -> tuple[str, str]:
    username = os.environ.get("TENSION_USERNAME")
    password = os.environ.get("TENSION_PASSWORD")
    if not username or not password:
        pytest.skip(
            "Set TENSION_USERNAME and TENSION_PASSWORD (env or .env) to run live "
            "Tension integration tests."
        )
    return username, password


@pytest.fixture(scope="session")
def board() -> str:
    return os.environ.get("TENSION_BOARD", "tension")
