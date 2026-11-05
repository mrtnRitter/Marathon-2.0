import importlib.util
import json
from pathlib import Path

import pytest

from m2.agent import ModuleContext


ROOT = Path(__file__).parents[1]


def load_module(name):
    path = ROOT / "m2" / "modules" / name / "module.py"
    spec = importlib.util.spec_from_file_location(f"test_module_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_storage_path_mapping(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    source = root / "source.mxf"
    source.write_text("clip")
    ctx = ModuleContext({"media": root})
    clip = {"job": "job_42", "files": [{"storage": "media"}]}

    assert ctx.job_dir(clip) == root / "job_42"
    assert ctx.resolve({"storage": "media", "path": "source.mxf"}) == source
    with pytest.raises(ValueError):
        ctx.resolve({"storage": "media", "path": "../outside.mxf"})
    with pytest.raises(ValueError):
        ctx.job_dir({"job": "../outside", "files": []})


def test_copy_file_and_cleanup_flat_job_folder(tmp_path):
    root = tmp_path / "media"
    source = root / "incoming" / "clip.mxf"
    source.parent.mkdir(parents=True)
    source.write_text("video")
    ctx = ModuleContext({"media": root})
    clip = {
        "id": "clip-1",
        "job": "job_1",
        "files": [{"storage": "media", "path": "incoming/clip.mxf", "role": "source"}],
    }

    copied = load_module("copy_file").run(ctx, [clip], {})[0]
    assert copied["status"] == "success"
    assert (root / "job_1" / "clip.mxf").read_text() == "video"
    assert copied["files_add"] == [{"storage": "media", "path": "job_1/clip.mxf", "role": "source"}]

    cleaned = load_module("cleanup").run(ctx, [clip], {})[0]
    assert cleaned["status"] == "success"
    assert not (root / "job_1").exists()


def test_all_manifests_have_object_params_schema():
    manifests = list((ROOT / "m2" / "modules").glob("*/manifest.json"))
    assert {path.parent.name for path in manifests} == {"manual_source", "wait", "copy_file", "cleanup"}
    for path in manifests:
        manifest = json.loads(path.read_text())
        schema = manifest["params_schema"]
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)
        assert manifest["name"] == path.parent.name
