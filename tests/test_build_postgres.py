import hashlib
import io
import shutil
import sys
import tarfile
import zipfile

import pytest

import build
from marathon.server.config import ConfigError, load_server_config


@pytest.mark.parametrize(
    ("system", "machine", "expected", "package"),
    [
        ("win32", "AMD64", ("win", "x64"), ("windows-amd64", "b7ef2d03588e0439f0d0a2250f43905f7464f1e96ace47703ceaf79094198150")),
        ("linux", "x86_64", ("linux", "x64"), ("linux-amd64", "008ee189eaa7ca2b58bd01dd950291e638b998392cf982297acd4779ec898ac1")),
    ],
)
def test_platform_key_and_postgres_packages(monkeypatch, system, machine, expected, package):
    monkeypatch.setattr(build.sys, "platform", system)
    monkeypatch.setattr(build.platform, "machine", lambda: machine)

    assert build.platform_key() == expected
    assert build.pg_packages[expected] == package


def test_unknown_platform_raises_build_error(monkeypatch):
    monkeypatch.setattr(build.sys, "platform", "freebsd")
    monkeypatch.setattr(build.platform, "machine", lambda: "x86_64")

    with pytest.raises(build.BuildError, match="Unbekanntes System: freebsd/x86_64"):
        build.platform_key()


def test_node_archive_name_supports_darwin_arm64(monkeypatch):
    monkeypatch.setattr(build.sys, "platform", "darwin")
    monkeypatch.setattr(build.platform, "machine", lambda: "arm64")

    assert build.node_archive_name() == "node-v24.22.2-darwin-arm64.tar.gz"


def test_install_postgres_rejects_darwin_arm64_without_download(tmp_path, monkeypatch):
    monkeypatch.setattr(build.sys, "platform", "darwin")
    monkeypatch.setattr(build.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(build, "build_dir", tmp_path / "build")
    monkeypatch.setattr(build, "download_dir", tmp_path / "downloads")
    monkeypatch.setattr(build, "pg_bin_dir", tmp_path / "tools" / "postgres" / "bin")
    download_calls = []
    monkeypatch.setattr(build, "download", lambda *args: download_calls.append(args))

    with pytest.raises(build.BuildError, match="Keine PostgreSQL-Version für dieses System"):
        build.install_postgres()

    assert download_calls == []


def test_install_postgres_discards_bad_download(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "platform_key", lambda: ("linux", "x64"))
    monkeypatch.setattr(build, "download_dir", tmp_path / "downloads")
    monkeypatch.setattr(build, "build_dir", tmp_path / "build")
    monkeypatch.setattr(build, "tools_dir", tmp_path / "tools")
    monkeypatch.setattr(build, "pg_dir", tmp_path / "tools" / "postgres")
    monkeypatch.setattr(build, "pg_bin_dir", tmp_path / "tools" / "postgres" / "bin")
    build.write_stamp("postgres", build.pg_version)

    def write_bad_download(url, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"not a PostgreSQL archive")

    monkeypatch.setattr(build, "download", write_bad_download)
    with pytest.raises(build.BuildError):
        build.install_postgres()

    archive_name = "embedded-postgres-binaries-linux-amd64-18.6.0.jar"
    assert not (tmp_path / "downloads" / archive_name).exists()
    assert not build.stamp_matches("postgres", build.pg_version)


def test_init_database_rejects_existing_data_without_password(tmp_path, monkeypatch):
    init_database = getattr(build, "init_database", None)
    assert callable(init_database)
    data_dir = tmp_path / ".data"
    pg_data_dir = data_dir / "postgres"
    pg_data_dir.mkdir(parents=True)
    (pg_data_dir / "PG_VERSION").write_text("18\n", encoding="utf-8")
    monkeypatch.setattr(build, "data_dir", data_dir)
    monkeypatch.setattr(build, "pg_data_dir", pg_data_dir)
    monkeypatch.setattr(build, "pg_password_file", data_dir / "postgres.pass")
    monkeypatch.setattr(build, "load_server_config", lambda: object())

    with pytest.raises(build.BuildError):
        init_database()


def test_init_database_skips_existing_data_with_password(tmp_path, monkeypatch):
    init_database = getattr(build, "init_database", None)
    assert callable(init_database)
    data_dir = tmp_path / ".data"
    pg_data_dir = data_dir / "postgres"
    pg_data_dir.mkdir(parents=True)
    (pg_data_dir / "PG_VERSION").write_text("18\n", encoding="utf-8")
    password_file = data_dir / "postgres.pass"
    password_file.write_text("x" * 43, encoding="utf-8")
    monkeypatch.setattr(build, "data_dir", data_dir)
    monkeypatch.setattr(build, "pg_data_dir", pg_data_dir)
    monkeypatch.setattr(build, "pg_password_file", password_file)
    monkeypatch.setattr(build, "load_server_config", lambda: object())
    calls = []
    monkeypatch.setattr(build, "run", lambda *args, **kwargs: calls.append(args))

    init_database()

    assert calls == []


def test_config_database_defaults_when_table_is_missing(tmp_path):
    config_file = tmp_path / "server.toml"
    config_file.write_text('host = "127.0.0.1"\nport = 8080\n', encoding="utf-8")

    config = load_server_config(config_file)

    assert (config.database.host, config.database.port, config.database.name, config.database.user) == (
        "127.0.0.1", 5433, "marathon", "marathon"
    )


def test_config_rejects_invalid_database_port(tmp_path):
    config_file = tmp_path / "server.toml"
    config_file.write_text('[database]\nport = 0\n', encoding="utf-8")

    with pytest.raises(ConfigError):
        load_server_config(config_file)


def test_config_rejects_invalid_database_name(tmp_path):
    config_file = tmp_path / "server.toml"
    config_file.write_text('[database]\nname = "Bad-Name"\n', encoding="utf-8")

    with pytest.raises(ConfigError):
        load_server_config(config_file)


def test_install_postgres_unpacks_verified_archive(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    (source_dir / "postgres" / "bin").mkdir(parents=True)
    for name in ("initdb", "pg_ctl", "postgres"):
        (source_dir / "postgres" / "bin" / name).write_text("test executable", encoding="utf-8")
    txz_file = tmp_path / "postgres.txz"
    with tarfile.open(txz_file, "w:xz") as tar_file:
        tar_file.add(source_dir / "postgres", arcname="postgres")
    jar_file = tmp_path / "bundle.jar"
    with zipfile.ZipFile(jar_file, "w") as zip_file:
        zip_file.write(txz_file, "postgres.txz")
    digest = hashlib.sha256(jar_file.read_bytes()).hexdigest()
    monkeypatch.setattr(build, "platform_key", lambda: ("linux", "x64"))
    monkeypatch.setattr(build, "pg_packages", {("linux", "x64"): ("fixture", digest)})
    monkeypatch.setattr(build, "download_dir", tmp_path / "downloads")
    monkeypatch.setattr(build, "build_dir", tmp_path / "build")
    monkeypatch.setattr(build, "tools_dir", tmp_path / "tools")
    monkeypatch.setattr(build, "pg_dir", tmp_path / "tools" / "postgres")
    monkeypatch.setattr(build, "pg_bin_dir", tmp_path / "tools" / "postgres" / "bin")
    monkeypatch.setattr(build, "download", lambda url, target: (target.parent.mkdir(parents=True, exist_ok=True), shutil.copyfile(jar_file, target)))

    build.install_postgres()

    assert all((build.pg_bin_dir / name).is_file() for name in ("initdb", "pg_ctl", "postgres"))
    assert build.stamp_matches("postgres", build.pg_version)


def test_init_database_cleans_partial_data_after_initdb_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace

    data_dir = tmp_path / ".data"
    pg_data_dir = data_dir / "postgres"
    password_file = data_dir / "postgres.pass"
    monkeypatch.setattr(build, "data_dir", data_dir)
    monkeypatch.setattr(build, "pg_data_dir", pg_data_dir)
    monkeypatch.setattr(build, "pg_password_file", password_file)
    monkeypatch.setattr(build, "pg_bin_dir", tmp_path / "tools" / "postgres" / "bin")
    monkeypatch.setattr(build, "load_server_config", lambda: SimpleNamespace(database=SimpleNamespace(user="marathon")))

    def fail_initdb(*args, **kwargs):
        pg_data_dir.mkdir(parents=True)
        (pg_data_dir / "partial").write_text("partial", encoding="utf-8")
        raise build.BuildError("initdb failed")

    monkeypatch.setattr(build, "run", fail_initdb)
    with pytest.raises(build.BuildError, match="initdb failed"):
        build.init_database()

    assert not pg_data_dir.exists()
    assert not password_file.exists()


def test_run_aborts_timed_out_subprocess():
    with pytest.raises(build.BuildError, match="Zeitlimit"):
        build.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)


def test_check_python_requires_3114(monkeypatch):
    monkeypatch.setattr(build.sys, "version_info", (3, 11, 3))
    with pytest.raises(build.BuildError, match=r"Python 3\.11\.4 oder neuer nötig, gefunden:"):
        build.check_python()

    monkeypatch.setattr(build.sys, "version_info", (3, 11, 4))
    build.check_python()


def test_postgres_tar_rejects_parent_path(tmp_path, monkeypatch):
    txz_file = tmp_path / "malicious.txz"
    payload = b"outside target"
    member = tarfile.TarInfo("../evil.txt")
    member.size = len(payload)
    with tarfile.open(txz_file, "w:xz") as tar_file:
        tar_file.addfile(member, io.BytesIO(payload))
    jar_file = tmp_path / "bundle.jar"
    with zipfile.ZipFile(jar_file, "w") as zip_file:
        zip_file.write(txz_file, "postgres.txz")
    digest = hashlib.sha256(jar_file.read_bytes()).hexdigest()
    tools_dir = tmp_path / "tools"
    monkeypatch.setattr(build, "platform_key", lambda: ("linux", "x64"))
    monkeypatch.setattr(build, "pg_packages", {("linux", "x64"): ("fixture", digest)})
    monkeypatch.setattr(build, "download_dir", tmp_path / "downloads")
    monkeypatch.setattr(build, "build_dir", tmp_path / "build")
    monkeypatch.setattr(build, "tools_dir", tools_dir)
    monkeypatch.setattr(build, "pg_dir", tools_dir / "postgres")
    monkeypatch.setattr(build, "pg_bin_dir", tools_dir / "postgres" / "bin")
    monkeypatch.setattr(build, "download", lambda url, target: (target.parent.mkdir(parents=True, exist_ok=True), shutil.copyfile(jar_file, target)))
    monkeypatch.delattr(build.tarfile, "data_filter")
    evil_file = tools_dir / "evil.txt"

    try:
        with pytest.raises(build.BuildError):
            build.install_postgres()
        assert not evil_file.exists()
    finally:
        evil_file.unlink(missing_ok=True)
