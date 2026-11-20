from __future__ import annotations

import importlib.metadata

import fishfinder as m


def test_version() -> None:
    assert importlib.metadata.version("fishfinder") == m.__version__
