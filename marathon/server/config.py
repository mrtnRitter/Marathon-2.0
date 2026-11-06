"""Server settings from config/server.toml."""

# IMPORTS
import tomllib
from dataclasses import dataclass
from pathlib import Path

# CONFIG
default_config_file = Path(__file__).resolve().parents[2] / "config" / "server.toml"


# FUNC
@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080


class ConfigError(Exception):
    pass


def load_server_config(config_file: Path = default_config_file) -> ServerConfig:
    """Read and validate the server config; missing keys fall back to defaults."""
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
    return config
