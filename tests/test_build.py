import importlib.util
import json
import zipfile
from pathlib import Path


BUILD_PATH = Path(__file__).resolve().parents[1] / "build.py"
spec = importlib.util.spec_from_file_location("project_build", BUILD_PATH)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def _project(root):
    (root / "requirements.txt").write_text("example==1.0\n", encoding="utf-8")
    (root / "marathon" / "client").mkdir(parents=True)
    (root / "marathon" / "__init__.py").write_text('__version__ = "9.8.7"\n', encoding="utf-8")
    for name in ("__init__.py", "starter.py", "runtime.py"):
        (root / "marathon" / "client" / name).write_text(f"# {name}\n", encoding="utf-8")


def _embedded_download(_url, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w") as archive:
        archive.writestr("python.exe", b"embedded-python")
        archive.writestr("python312.dll", b"runtime")
        archive.writestr("python312.zip", b"stdlib")
        archive.writestr("python312._pth", "python312.zip\n#import site\n")


def test_all_build_paths_are_project_local(tmp_path):
    paths = build._layout(tmp_path)
    for path in paths.values():
        assert path == tmp_path.resolve() or path.is_relative_to(tmp_path.resolve())


def test_client_zip_has_manifest_embedded_python_and_worker(tmp_path):
    _project(tmp_path)
    paths = build._layout(tmp_path)
    result = build._build_client_package(paths, build._version_from_init(tmp_path / "marathon" / "__init__.py"), _embedded_download)

    assert result == tmp_path / "dist" / "client" / "9.8.7.zip"
    with zipfile.ZipFile(result) as package:
        names = set(package.namelist())
        assert json.loads(package.read("manifest.json")) == {"version": "9.8.7"}
        assert {"python.exe", "python312.dll", "python312.zip"} <= names
        assert {
            "marathon/__init__.py",
            "marathon/client/__init__.py",
            "marathon/client/starter.py",
            "marathon/client/runtime.py",
        } <= names
        assert "." in package.read("python312._pth").decode("utf-8").splitlines()


def test_second_client_build_skips_download_and_preserves_archive(tmp_path):
    _project(tmp_path)
    paths = build._layout(tmp_path)
    first = build._build_client_package(paths, "9.8.7", _embedded_download)
    original = first.read_bytes()

    def unexpected_download(*_args):
        raise AssertionError("cached client package should not download again")

    second = build._build_client_package(paths, "9.8.7", unexpected_download)
    assert second == first
    assert second.read_bytes() == original


def _ui_project(root, with_lock):
    paths = build._layout(root)
    ui = paths["ui"]
    ui.mkdir(parents=True)
    (ui / "package.json").write_text('{"private":true}\n', encoding="utf-8")
    (ui / "index.html").write_text("<main>ui</main>\n", encoding="utf-8")
    if with_lock:
        (ui / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    return paths


def _ui_run(paths, commands):
    def run(args, cwd, _env):
        command = args[-1]
        commands.append(command)
        if command in {"install", "ci"}:
            (cwd / "node_modules").mkdir(exist_ok=True)
            (cwd / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
        elif command == "build":
            paths["ui_static"].mkdir(parents=True, exist_ok=True)
            (paths["ui_static"] / "index.html").write_text("<main>built</main>\n", encoding="utf-8")
    return run


def test_ui_build_installs_without_lock_and_uses_configured_static_dir(tmp_path):
    paths = _ui_project(tmp_path, with_lock=False)
    commands = []
    run = _ui_run(paths, commands)
    node = paths["node"] / "node"
    npm_cli = paths["node"] / "node_modules" / "npm" / "bin" / "npm-cli.js"

    build._build_ui(paths, node, npm_cli, run)

    assert commands == ["install", "build"]
    assert (paths["ui_static"] / "index.html").read_text(encoding="utf-8") == "<main>built</main>\n"
    assert not (paths["ui"] / "dist").exists()


def test_ui_build_uses_npm_ci_when_lock_exists(tmp_path):
    paths = _ui_project(tmp_path, with_lock=True)
    commands = []
    build._build_ui(
        paths,
        paths["node"] / "node",
        paths["node"] / "node_modules" / "npm" / "bin" / "npm-cli.js",
        _ui_run(paths, commands),
    )

    assert commands == ["ci", "build"]
    assert (paths["ui_static"] / "index.html").is_file()
