import json

from m2.agent.runner import load_config


def test_load_config(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps({"storages": {"media": "./media"}, "tags": ["local"]}))
    assert load_config(path) == {"storages": {"media": "./media"}, "tags": ["local"]}
