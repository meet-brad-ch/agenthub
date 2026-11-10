"""Shared fixtures: a fresh channel folder with the default hub.json."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agenthub.core import CONFIG_NAME, Hub, HubConfig  # noqa: E402


@pytest.fixture
def channel(tmp_path: Path) -> Path:
    (tmp_path / CONFIG_NAME).write_text(json.dumps(HubConfig.default_json()), encoding="utf-8")
    return tmp_path


@pytest.fixture
def hub(channel: Path) -> Hub:
    return Hub(channel)
