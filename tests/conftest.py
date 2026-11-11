"""Fixtures for isolated PostgreSQL migration tests."""

# IMPORTS
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from marathon.server.config import DatabaseConfig, pg_bin_dir


# FUNC
def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a local PostgreSQL utility with a bounded wait."""
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=True)


@pytest.fixture(scope="session")
def pg_server(tmp_path_factory: pytest.TempPathFactory) -> tuple[DatabaseConfig, str]:
    """Start a temporary PostgreSQL server for the test session."""
    bin_dir = Path(os.environ.get("MARATHON_TEST_PG_BIN", pg_bin_dir))
    initdb = bin_dir / ("initdb.exe" if os.name == "nt" else "initdb")
    pg_ctl = bin_dir / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")
    if not initdb.is_file():
        pytest.skip("PostgreSQL-Programme fehlen: python build.py ausführen")

    data_dir = tmp_path_factory.mktemp("marathon-postgres") / "data"
    password_file = data_dir.parent / "postgres.pass"
    password_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    password = password_file.read_text(encoding="utf-8")
    initdb_command = [
        str(initdb), "-D", str(data_dir), "-U", "marathon", "-E", "UTF8",
        "--locale=C", "--auth=scram-sha-256", f"--pwfile={password_file}", "--no-instructions",
    ]
    _run(initdb_command, timeout=120)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log_file = data_dir.parent / "postgres.log"
    socket_dir = tempfile.mkdtemp(prefix="m2pg-") if os.name == "posix" else None
    options = f"-p {port} -h 127.0.0.1"
    if socket_dir:
        options += f" -k {socket_dir}"
    start_command = [
        str(pg_ctl), "start", "-D", str(data_dir), "-w", "-t", "30", "-l", str(log_file), "-o", options,
    ]
    stop_base = [str(pg_ctl), "stop", "-D", str(data_dir), "-w", "-t", "30"]
    try:
        _run(start_command, timeout=40)
        yield DatabaseConfig(host="127.0.0.1", port=port, name="marathon", user="marathon"), password
    finally:
        try:
            subprocess.run([*stop_base, "-m", "fast"], capture_output=True, text=True, timeout=35, check=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            try:
                subprocess.run([*stop_base, "-m", "immediate"], capture_output=True, text=True, timeout=35, check=False)
            except subprocess.TimeoutExpired:
                pass
        if socket_dir:
            shutil.rmtree(socket_dir, ignore_errors=True)


@pytest.fixture
def db_config(pg_server: tuple[DatabaseConfig, str]) -> tuple[DatabaseConfig, str]:
    """Provide a fresh database name and drop it after the test."""
    server_config, password = pg_server
    config = DatabaseConfig(
        host=server_config.host,
        port=server_config.port,
        name=f"t_{uuid4().hex[:12]}",
        user=server_config.user,
    )
    yield config, password
    from marathon.server.db.engine import create_db_engine

    admin_engine = create_db_engine(config, password, database="postgres").execution_options(
        isolation_level="AUTOCOMMIT"
    )
    try:
        preparer = admin_engine.dialect.identifier_preparer
        with admin_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {preparer.quote(config.name)} WITH (FORCE)"))
    finally:
        admin_engine.dispose()


@pytest.fixture

def db_engine(db_config: tuple[DatabaseConfig, str]):
    """Create and migrate a fresh test database."""
    from marathon.server.db.engine import create_db_engine, ensure_database, upgrade_database

    config, password = db_config
    ensure_database(config, password)
    engine = create_db_engine(config, password)
    try:
        upgrade_database(engine)
        yield engine
    finally:
        engine.dispose()
