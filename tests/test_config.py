from pathlib import Path

import pytest
from pydantic import ValidationError

from miner_supervisor.config import load_config


def test_load_yaml_config() -> None:
    config = load_config(Path("/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/config/config.example.yaml"))
    assert config.rig_id == "rig-xmr-01"
    assert config.thresholds.min_hashrate == 5000
    assert config.api_bind_host == "127.0.0.1"


def test_invalid_config_rejected(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("rig_id: test\ncoin: xmr\nmode: xmrig\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(broken)
