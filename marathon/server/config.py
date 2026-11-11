"""Server settings from config/server.toml."""

# IMPORTS
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# CONFIG
root_dir = Path(__file__).resolve().parents[2]
default_config_file = root_dir / "config" / "server.toml"
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
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


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
    return ServerConfig(config.host, config.port, database)
