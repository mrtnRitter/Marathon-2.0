import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import build
from marathon.server.db.__main__ import main as db_main


def _patch_paths(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    build_dir = root / ".build"
    client_venv_dir = build_dir / "client-venv"
    client_build_dir = build_dir / "client"
    client_package_dir = root / ".data" / "client"
    requirements_dir = root / "requirements"
    requirements_dir.mkdir()
    client_requirements_file = requirements_dir / "client.txt"
    client_build_requirements_file = requirements_dir / "client-build.txt"
    client_requirements_file.write_text("pystray==0.19.5\n", encoding="utf-8")
    client_build_requirements_file.write_text("pyinstaller==6.22.3\n", encoding="utf-8")
    cert_file = root / ".cert" / "root.crt"
    for name, value in {
        "root_dir": root,
        "build_dir": build_dir,
        "client_venv_dir": client_venv_dir,
        "client_build_dir": client_build_dir,
        "client_package_dir": client_package_dir,
        "client_requirements_file": client_requirements_file,
        "client_build_requirements_file": client_build_requirements_file,
        "caddy_root_cert": cert_file,
        "required_files": (),
    }.items():
        monkeypatch.setattr(build, name, value)
    monkeypatch.setattr(build, "is_windows", True)
    return SimpleNamespace(
        root=root,
        build_dir=build_dir,
        client_venv_dir=client_venv_dir,
        client_build_dir=client_build_dir,
        client_package_dir=client_package_dir,
        client_requirements_file=client_requirements_file,
        client_build_requirements_file=client_build_requirements_file,
        cert_file=cert_file,
    )


def _config(*, public_url="https://workers.example.test", builtin=False, sign_command=()):
    return SimpleNamespace(
        proxy=SimpleNamespace(public_url=public_url, builtin=builtin),
        client_build=SimpleNamespace(sign_command=sign_command),
    )


def _fake_client_run(paths, monkeypatch, *, proxy_info=None, sign_error=None):
    commands = []

    def fake_run(command, cwd=build.root_dir, env=None, timeout=build.command_timeout, capture_output=False):
        commands.append(command)
        if command[:3] == [sys.executable, "-m", "venv"]:
            python = build.client_venv_python()
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
            return None
        if command[-1:] == ["proxy-info"]:
            if isinstance(proxy_info, Exception):
                raise proxy_info
            return SimpleNamespace(stdout=json.dumps(proxy_info or {"public_url": "https://db.example.test", "builtin": False}))
        if "PyInstaller" in command:
            dist = Path(command[command.index("--distpath") + 1])
            dist.mkdir(parents=True, exist_ok=True)
            (dist / "MarathonWorker.exe").write_bytes(b"test executable")
        if command and command[0] == "sign-tool" and sign_error:
            raise build.BuildError(sign_error)
        return None

    monkeypatch.setattr(build, "run", fake_run)
    return commands



def test_new_client_venv_installs_requirements_even_when_stamp_matches(tmp_path, monkeypatch):
    paths = _patch_paths(tmp_path, monkeypatch)
    requirements_hash = build.client_requirements_hash()
    build.write_stamp("client-requirements", requirements_hash)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == [sys.executable, "-m", "venv"]:
            python = build.client_venv_python()
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()

    monkeypatch.setattr(build, "run", fake_run)
    build.setup_client_venv()

    assert commands[0][:3] == [sys.executable, "-m", "venv"]
    assert any("pip" in command and "install" in command for command in commands)
    assert build.stamp_matches("client-requirements", requirements_hash)


def test_full_build_skips_client_on_linux_and_still_finishes(tmp_path, monkeypatch, capsys):
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "is_windows", False)
    monkeypatch.setattr(build, "steps", (("Worker-Client (.data/client)", build.build_client),))

    monkeypatch.setattr(sys, "argv", ["build.py"])
    assert build.main() == 0

    output = capsys.readouterr().out
    assert "nur unter Windows, übersprungen" in output
    assert "Fertig. Starten mit: python run.py" in output


def test_client_only_fails_off_windows(tmp_path, monkeypatch, capsys):
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "is_windows", False)

    monkeypatch.setattr(sys, "argv", ["build.py", "--client"])
    assert build.main() == 1
    assert "Fehler: Der Worker-Client lässt sich nur unter Windows bauen." in capsys.readouterr().err


def test_missing_builtin_certificate_skips_or_fails_by_mode(tmp_path, monkeypatch, capsys):
    paths = _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "load_server_config", lambda: _config(builtin=True))
    message = f"Stammzertifikat fehlt ({paths.cert_file}). Zuerst python run.py einmal starten."
    monkeypatch.setattr(build, "run", lambda *args, **kwargs: (_ for _ in ()).throw(build.BuildError("DB unavailable")))

    build.build_client()
    assert message in capsys.readouterr().out
    with pytest.raises(build.BuildError, match="Stammzertifikat fehlt"):
        build.build_client(strict=True)


def test_proxy_info_failure_uses_config_fallback(tmp_path, monkeypatch, capsys):
    paths = _patch_paths(tmp_path, monkeypatch)
    paths.cert_file.parent.mkdir(parents=True)
    paths.cert_file.write_text("root certificate\n", encoding="utf-8")
    monkeypatch.setattr(build, "load_server_config", lambda: _config(builtin=True))
    _fake_client_run(paths, monkeypatch, proxy_info=build.BuildError("database unavailable"))

    build.build_client()

    bootstrap = json.loads((paths.client_build_dir / "client_bootstrap.json").read_text(encoding="utf-8"))
    assert bootstrap == {
        "server_url": "https://workers.example.test",
        "root_ca_pem": "root certificate\n",
        "client_version": build.app_version,
    }
    assert "Server-Adresse aus config/marathon.toml (Datenbank nicht erreichbar)" in capsys.readouterr().out


def test_pyinstaller_command_bootstrap_signing_and_manifest(tmp_path, monkeypatch, capsys):
    paths = _patch_paths(tmp_path, monkeypatch)
    paths.cert_file.parent.mkdir(parents=True)
    paths.cert_file.write_text("root certificate\n", encoding="utf-8")
    monkeypatch.setattr(build, "load_server_config", lambda: _config(builtin=True, sign_command=("sign-tool", "--input", "{file}")))
    commands = _fake_client_run(paths, monkeypatch)

    build.build_client()

    command = next(command for command in commands if "PyInstaller" in command)
    assert command == [
        str(build.client_venv_python()), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "MarathonWorker", "--distpath", str(paths.client_build_dir / "dist"),
        "--workpath", str(paths.client_build_dir / "work"), "--specpath", str(paths.client_build_dir),
        "--add-data", f"{paths.client_build_dir / 'client_bootstrap.json'}{build.os.pathsep}.",
        "--paths", str(paths.root), "--collect-submodules", "marathon.modules", "--hidden-import", "pystray._win32",
        "--version-file", str(paths.client_build_dir / "version_info.txt"),
        str(paths.root / "marathon" / "client" / "__main__.py"),
    ]
    assert ["sign-tool", "--input", str(paths.client_build_dir / "dist" / "MarathonWorker.exe")] in commands
    version_info = (paths.client_build_dir / "version_info.txt").read_text(encoding="utf-8")
    for value in ("StringStruct('CompanyName', 'Marathon')", "StringStruct('ProductName', 'Marathon-Worker')",
                  "StringStruct('FileDescription', 'Marathon-Worker')",
                  f"StringStruct('FileVersion', '{build.app_version}')",
                  f"StringStruct('ProductVersion', '{build.app_version}')",
                  "StringStruct('OriginalFilename', 'MarathonWorker.exe')"):
        assert value in version_info
    assert "Worker-Client bereit: .data/client/MarathonWorker.exe" in capsys.readouterr().out
    exe = paths.client_package_dir / "MarathonWorker.exe"
    assert exe.read_bytes() == b"test executable"
    manifest = json.loads((paths.client_package_dir / "client.json").read_text(encoding="utf-8"))
    assert manifest == {
        "version": build.app_version,
        "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        "server_url": "https://db.example.test",
    }


def test_signing_failure_is_reported(tmp_path, monkeypatch):
    paths = _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "load_server_config", lambda: _config(sign_command=("sign-tool", "{file}")))
    _fake_client_run(paths, monkeypatch, sign_error="signer returned 2")

    with pytest.raises(build.BuildError, match="Signieren fehlgeschlagen: signer returned 2"):
        build.build_client()


def test_unsigned_client_prints_warning(tmp_path, monkeypatch, capsys):
    paths = _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "load_server_config", lambda: _config())
    _fake_client_run(paths, monkeypatch)

    build.build_client()

    assert "Hinweis: Worker-Client ist nicht signiert ([client_build] sign_command leer); Windows zeigt beim Start eine Warnung." in capsys.readouterr().out


def test_proxy_info_reads_persisted_settings_without_migration_or_seed(tmp_path, db_engine, db_config, capsys):
    config, password = db_config
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO settings (key, value) VALUES (:key, CAST(:value AS jsonb))"),
            [
                {"key": "proxy.public_url", "value": '"https://saved.example.test"'},
                {"key": "proxy.builtin", "value": "true"},
            ],
        )
    config_file = tmp_path / "server.toml"
    config_file.write_text(
        f'host = "127.0.0.1"\nport = 8080\n\n[database]\nhost = "{config.host}"\n'
        f'port = {config.port}\nname = "{config.name}"\nuser = "{config.user}"\n',
        encoding="utf-8",
    )
    password_file = tmp_path / "postgres.pass"
    password_file.write_text(password, encoding="utf-8")

    result = db_main(["proxy-info"], config_file=config_file, password_file=password_file)

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == '{"public_url": "https://saved.example.test", "builtin": true}\n'
    assert captured.err == ""
