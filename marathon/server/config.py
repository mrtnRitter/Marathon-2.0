"""Server settings from config/marathon.toml."""

# IMPORTS
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# CONFIG
root_dir = Path(__file__).resolve().parents[2]
default_config_file = root_dir / "config" / "marathon.toml"
data_dir = root_dir / ".data"
pg_dir = root_dir / ".tools" / "postgres"
pg_bin_dir = pg_dir / "bin"
pg_data_dir = data_dir / "postgres"
pg_password_file = data_dir / "postgres.pass"
pg_log_file = data_dir / "postgres.log"
_database_identifier = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


# FUNC
class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class DatabaseConfig:
    host: str = "127.0.0.1"
    port: int = 5433
    name: str = "marathon"
    user: str = "marathon"


@dataclass(frozen=True)
class EngineConfig:
    tick_interval_s: float = 0.5
    poll_timeout_s: float = 25.0
    worker_timeout_s: float = 30.0
    pickup_timeout_s: float = 10.0
    max_attempts: int = 3


@dataclass(frozen=True)
class ServerWorkerConfig:
    enabled: bool = True
    max_parallel: int = 1


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    server_worker: ServerWorkerConfig = field(default_factory=ServerWorkerConfig)


def _validated_value(
    values: dict,
    section: str,
    key: str,
    default: int | float,
    minimum: int | float,
    maximum: int | float,
    config_file: Path,
    *,
    integer: bool = False,
) -> int | float:
    value = values.get(key, default)
    valid_type = type(value) is int if integer else isinstance(value, (int, float)) and not isinstance(value, bool)
    if not valid_type or not minimum <= value <= maximum:
        if integer:
            message = f"[{section}] {key} in {config_file} muss eine ganze Zahl zwischen {minimum} und {maximum} sein"
        else:
            message = f"[{section}] {key} in {config_file} muss eine Zahl zwischen {minimum:g} und {maximum:g} sein"
        raise ConfigError(message)
    return value if integer else float(value)


def load_server_config(config_file: Path = default_config_file) -> ServerConfig:
    """Read and validate server and database settings from TOML."""
    if not config_file.is_file():
        raise ConfigError(f"Konfigurationsdatei fehlt: {config_file}")
    try:
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{config_file} ist kein gültiges TOML: {error}") from error
    config = ServerConfig(**{key: data[key] for key in ("host", "port") if key in data})
    if not isinstance(config.host, str) or not config.host:
        raise ConfigError(f"'host' in {config_file} muss ein nicht leerer Text sein")
    if not isinstance(config.port, int) or not 1 <= config.port <= 65535:
        raise ConfigError(f"'port' in {config_file} muss eine Zahl zwischen 1 und 65535 sein")
    database_data = data.get("database", {})
    if not isinstance(database_data, dict):
        raise ConfigError(f"[database] in {config_file} muss eine Tabelle sein")
    database = DatabaseConfig(
        **{key: database_data[key] for key in ("host", "port", "name", "user") if key in database_data}
    )
    if not isinstance(database.host, str) or not database.host.strip():
        raise ConfigError(f"[database] host in {config_file} muss ein nicht leerer Text sein")
    if type(database.port) is not int or not 1 <= database.port <= 65535:
        raise ConfigError(f"[database] port in {config_file} muss eine Zahl zwischen 1 und 65535 sein")
    for key in ("name", "user"):
        if not isinstance(getattr(database, key), str) or not _database_identifier.fullmatch(getattr(database, key)):
            raise ConfigError(f"[database] {key} in {config_file} muss ein gültiger PostgreSQL-Bezeichner sein")

    engine_data = data.get("engine", {})
    if not isinstance(engine_data, dict):
        raise ConfigError(f"[engine] in {config_file} muss eine Tabelle sein")
    engine = EngineConfig(
        tick_interval_s=_validated_value(engine_data, "engine", "tick_interval_s", 0.5, 0.05, 10, config_file),
        poll_timeout_s=_validated_value(engine_data, "engine", "poll_timeout_s", 25.0, 1, 60, config_file),
        worker_timeout_s=_validated_value(engine_data, "engine", "worker_timeout_s", 30.0, 10, 600, config_file),
        pickup_timeout_s=_validated_value(engine_data, "engine", "pickup_timeout_s", 10.0, 1, 600, config_file),
        max_attempts=_validated_value(engine_data, "engine", "max_attempts", 3, 1, 10, config_file, integer=True),
    )
    worker_data = data.get("server_worker", {})
    if not isinstance(worker_data, dict):
        raise ConfigError(f"[server_worker] in {config_file} muss eine Tabelle sein")
    enabled = worker_data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"[server_worker] enabled in {config_file} muss true oder false sein")
    max_parallel = _validated_value(
        worker_data, "server_worker", "max_parallel", 1, 1, 64, config_file, integer=True
    )
    server_worker = ServerWorkerConfig(enabled, max_parallel)
    return ServerConfig(config.host, config.port, database, engine, server_worker)
