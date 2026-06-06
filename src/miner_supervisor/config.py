from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import yaml

from .models import AppConfig


def _load_raw(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    with path.open("r", encoding="utf-8") as handle:
        if suffix == ".json":
            return json.load(handle)
        if suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(handle) or {}
            if not isinstance(data, dict):
                raise ValueError("YAML config must contain a mapping at top level")
            return data
    raise ValueError(f"Unsupported config extension: {suffix}")


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    raw = _load_raw(config_path)
    return AppConfig.model_validate(raw)
