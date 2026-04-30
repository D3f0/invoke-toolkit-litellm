"""Shared fixtures for invoke-toolkit-litellm tests."""

import pathlib

import pytest


@pytest.fixture
def tmp_config(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a temporary directory suitable for config file tests."""
    return tmp_path
