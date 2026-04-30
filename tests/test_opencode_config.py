"""Tests for _get_opencode_config handling of empty and non-existent files."""

import json
import pathlib

import pytest

from invoke_toolkit_litellm.tasks import _get_opencode_config


def test_missing_file_raises_by_default(tmp_config: pathlib.Path):
    """When create_if_missing is False (default), a missing file raises."""
    missing = tmp_config / "does-not-exist.json"
    with pytest.raises(FileNotFoundError, match="OpenCode config not found"):
        _get_opencode_config(str(missing))


def test_missing_file_returns_empty_dict_when_create_if_missing(
    tmp_config: pathlib.Path,
):
    """When create_if_missing is True, a missing file returns an empty dict."""
    missing = tmp_config / "opencode.json"
    cfg_path, config = _get_opencode_config(str(missing), create_if_missing=True)
    assert cfg_path == missing
    assert config == {}


def test_missing_file_creates_parent_dirs(tmp_config: pathlib.Path):
    """create_if_missing should create intermediate directories."""
    nested = tmp_config / "a" / "b" / "opencode.json"
    cfg_path, config = _get_opencode_config(str(nested), create_if_missing=True)
    assert cfg_path == nested
    assert config == {}
    assert nested.parent.is_dir()


def test_empty_file_returns_empty_dict(tmp_config: pathlib.Path):
    """An empty config file should be treated as an empty JSON object."""
    cfg_file = tmp_config / "opencode.json"
    cfg_file.write_text("", encoding="utf-8")
    cfg_path, config = _get_opencode_config(str(cfg_file))
    assert cfg_path == cfg_file
    assert config == {}


def test_whitespace_only_file_returns_empty_dict(tmp_config: pathlib.Path):
    """A file containing only whitespace should be treated as empty."""
    cfg_file = tmp_config / "opencode.json"
    cfg_file.write_text("   \n  \n  ", encoding="utf-8")
    cfg_path, config = _get_opencode_config(str(cfg_file))
    assert cfg_path == cfg_file
    assert config == {}


def test_valid_json_is_parsed(tmp_config: pathlib.Path):
    """A valid JSON file should be parsed correctly."""
    cfg_file = tmp_config / "opencode.json"
    data = {"provider": {"my-provider": {"models": {"m1": {"name": "m1"}}}}}
    cfg_file.write_text(json.dumps(data), encoding="utf-8")
    cfg_path, config = _get_opencode_config(str(cfg_file))
    assert cfg_path == cfg_file
    assert config == data


def test_empty_json_object_is_parsed(tmp_config: pathlib.Path):
    """A file containing '{}' should return an empty dict."""
    cfg_file = tmp_config / "opencode.json"
    cfg_file.write_text("{}", encoding="utf-8")
    cfg_path, config = _get_opencode_config(str(cfg_file))
    assert cfg_path == cfg_file
    assert config == {}


def test_json_with_comments_is_parsed(tmp_config: pathlib.Path):
    """jsonclark should handle JSON-with-comments correctly."""
    cfg_file = tmp_config / "opencode.json"
    cfg_file.write_text(
        '{\n  // this is a comment\n  "provider": {}\n}\n', encoding="utf-8"
    )
    cfg_path, config = _get_opencode_config(str(cfg_file))
    assert cfg_path == cfg_file
    assert config == {"provider": {}}
