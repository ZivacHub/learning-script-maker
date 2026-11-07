"""Shared pytest setup: golden tests need the real sample folder and are skipped without it."""

import os
from pathlib import Path

import pytest


def sample_dir() -> Path | None:
    raw = os.environ.get("SM_SAMPLE_DIR")
    if raw and Path(raw).is_dir():
        return Path(raw)
    return None


def pytest_collection_modifyitems(config, items):
    if sample_dir() is not None:
        return
    skip = pytest.mark.skip(reason="golden test: set SM_SAMPLE_DIR to the Sample_Folder folder")
    for item in items:
        if "golden" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def real_sample() -> Path:
    path = sample_dir()
    if path is None:  # pragma: no cover - guarded by the collection hook
        pytest.skip("SM_SAMPLE_DIR not set")
    return path
