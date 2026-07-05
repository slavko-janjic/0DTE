from pathlib import Path

import yaml

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"


def load_settings(path: Path | str = _SETTINGS_PATH) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)
