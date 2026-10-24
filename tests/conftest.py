"""Shared pytest fixtures."""

import pytest

from posterize.config import CONFIG_ENV_VAR


@pytest.fixture(autouse=True)
def isolated_config(tmp_path_factory, monkeypatch):
    """Keep the user's real config file out of every test."""

    path = tmp_path_factory.mktemp("config") / "config.toml"
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    return path
