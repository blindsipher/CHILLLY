import os
from pathlib import Path

import yaml

from topstepx_backend.config.profiles import BaseProfile


def test_yaml_config_settings_handles_complex_structures(tmp_path, monkeypatch):
    # Create a YAML file with nested dictionaries and lists
    data = {
        "username": "yamluser",
        "nested": {
            "items": [1, "two", {"more": ["a", "b"]}]
        },
    }
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(yaml.dump(data))

    # Point the loader to our YAML file
    monkeypatch.setenv("CONFIG_YAML", str(yaml_file))

    loaded = BaseProfile.yaml_config_settings(None)

    assert loaded["username"] == "yamluser"
    assert loaded["nested"]["items"][2]["more"][1] == "b"
