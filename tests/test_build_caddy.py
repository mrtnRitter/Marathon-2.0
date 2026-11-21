import hashlib
import io
import os
import tarfile
import zipfile

import pytest

import build


def _patch_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "download_dir", tmp_path / "downloads")
    monkeypatch.setattr(build, "build_dir", tmp_path / "build")
    monkeypatch.setattr(build, "tools_dir", tmp_path / "tools")
    monkeypatch.setattr(build, "caddy_dir", tmp_path / "tools" / "caddy")
    monkeypatch.setattr(build, "caddy_exe", tmp_path / "tools" / "caddy" / "caddy")


def _copy_archive(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _tar_archive(path, members):
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return path


def test_caddy_packages_pinned():
    assert build.caddy_version == "2.11.9"
    assert build.caddy_packages == {
        ("linux", "x64"): (
            "linux_amd64.tar.gz",
            "5b8cb7faf8726a2028ad96a66e54e6e81e8f361bc6175e5d22bbd997cc5177fb",
        ),
        ("win", "x64"): (
            "windows_amd64.zip",
            "ad93112d335ee687100c4c9f11bced16ca590377976b7fe7883882b9e797e472",
        ),
    }


def test_install_caddy_unknown_platform(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "platform_key", lambda: ("darwin", "arm64"))
    downloads = []
    monkeypatch.setattr(build, "download", lambda *args: downloads.append(args))

    with pytest.raises(build.BuildError, match="^Keine Caddy-Version für dieses System hinterlegt"):
        build.install_caddy()

    assert downloads == []


def test_install_caddy_discards_bad_download(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "platform_key", lambda: ("linux", "x64"))
    monkeypatch.setattr(build, "download", lambda url, target: (target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b"junk")))

    with pytest.raises(build.BuildError, match="Prüfsumme"):
        build.install_caddy()

    assert not (tmp_path / "downloads" / f"caddy_{build.caddy_version}_linux_amd64.tar.gz").exists()
    assert build.stamp_matches("caddy", build.caddy_version) is False


def test_install_caddy_extracts_tar_binary(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "platform_key", lambda: ("linux", "x64"))
    source = _tar_archive(tmp_path / "caddy.tar.gz", [("caddy", b"#!bin"), ("LICENSE", b"license")])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(build, "caddy_packages", {("linux", "x64"): ("linux_amd64.tar.gz", digest)})
    monkeypatch.setattr(build, "download", lambda url, target: _copy_archive(source, target))

    build.install_caddy()

    assert build.caddy_exe.read_bytes() == b"#!bin"
    if not build.is_windows:
        assert os.access(build.caddy_exe, os.X_OK)
    assert not (build.caddy_dir / "LICENSE").exists()
    assert build.stamp_matches("caddy", build.caddy_version)


def test_install_caddy_extracts_zip_binary(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "platform_key", lambda: ("win", "x64"))
    monkeypatch.setattr(build, "is_windows", True)
    monkeypatch.setattr(build, "caddy_exe", tmp_path / "tools" / "caddy" / "caddy.exe")
    source = tmp_path / "caddy.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("caddy.exe", b"binary")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(build, "caddy_packages", {("win", "x64"): ("windows_amd64.zip", digest)})
    monkeypatch.setattr(build, "download", lambda url, target: _copy_archive(source, target))

    build.install_caddy()

    assert build.caddy_exe.read_bytes() == b"binary"


def test_install_caddy_rejects_archive_without_binary(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "platform_key", lambda: ("linux", "x64"))
    source = _tar_archive(tmp_path / "caddy.tar.gz", [("LICENSE", b"license")])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(build, "caddy_packages", {("linux", "x64"): ("linux_amd64.tar.gz", digest)})
    monkeypatch.setattr(build, "download", lambda url, target: _copy_archive(source, target))

    with pytest.raises(build.BuildError, match=r"Caddy-Archiv ist unvollständig: caddy fehlt\."):
        build.install_caddy()


def test_install_caddy_skips_when_present(tmp_path, monkeypatch, capsys):
    _patch_paths(tmp_path, monkeypatch)
    build.caddy_dir.mkdir(parents=True)
    build.caddy_exe.write_bytes(b"binary")
    build.write_stamp("caddy", build.caddy_version)
    monkeypatch.setattr(build, "download", lambda *args: pytest.fail("download must not run"))

    build.install_caddy()

    assert "übersprungen" in capsys.readouterr().out


def test_steps_include_caddy_after_postgres():
    titles = [title for title, _ in build.steps]
    postgres_index = next(i for i, title in enumerate(titles) if title.startswith("PostgreSQL "))

    assert f"Caddy {build.caddy_version} (.tools/caddy)" in titles
    assert titles.index(f"Caddy {build.caddy_version} (.tools/caddy)") == postgres_index + 1
