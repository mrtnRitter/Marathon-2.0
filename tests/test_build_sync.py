"""build.py, README and repo must stay in sync."""

import json
import re
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_dir))

import build  # noqa: E402
from marathon.common.version import app_version  # noqa: E402


def test_required_files_exist():
    missing = [path for path in build.required_files if not path.is_file()]
    assert not missing, f"build.py expects missing files: {missing}"


def test_paths_point_into_repo():
    for path in (build.venv_dir, build.tools_dir, build.build_dir, build.ui_dir, build.static_dir):
        assert path.is_relative_to(root_dir)


def test_ui_package_matches_build():
    package = json.loads(build.ui_package_file.read_text(encoding="utf-8"))
    assert {"dev", "build"} <= package["scripts"].keys()
    assert package["version"] == app_version


def test_ui_versions_are_pinned():
    package = json.loads(build.ui_package_file.read_text(encoding="utf-8"))
    versions = {**package["dependencies"], **package["devDependencies"]}
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", version) for version in versions.values()), versions


def test_python_requirements_are_pinned():
    lines = [line.strip() for line in build.requirements_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    assert lines and all(re.fullmatch(r"[A-Za-z0-9_.\-\[\]]+==[\w.]+", line) for line in lines), lines


def test_vite_writes_to_static_dir():
    config = (build.ui_dir / "vite.config.ts").read_text(encoding="utf-8")
    assert "'../marathon/server/static'" in config
    assert (build.ui_dir / "../marathon/server/static").resolve() == build.static_dir


def test_gitignore_covers_generated_dirs():
    ignored = set((root_dir / ".gitignore").read_text(encoding="utf-8").split())
    for path in (build.venv_dir, build.tools_dir, build.build_dir, build.ui_modules_dir, build.static_dir):
        assert path.relative_to(root_dir).as_posix() in ignored


def test_readme_mentions_real_commands_and_paths():
    readme = (root_dir / "README.md").read_text(encoding="utf-8")
    for text in ("python build.py", "python run.py", "python run.py --dev", "config/server.toml", "requirements/server.txt", "ui/package-lock.json"):
        assert text in readme
