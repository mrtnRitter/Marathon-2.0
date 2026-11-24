"""Server settings from config/marathon.toml."""

# IMPORTS
import ipaddress
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

# CONFIG
root_dir = Path(__file__).resolve().parents[2]
default_config_file = root_dir / "config" / "marathon.toml"
data_dir = root_dir / ".data"
pg_dir = root_dir / ".tools" / "postgres"
pg_bin_dir = pg_dir / "bin"
pg_data_dir = data_dir / "postgres"
pg_password_file = data_dir / "postgres.pass"
pg_log_file = data_dir / "postgres.log"
cert_dir = root_dir / ".cert"
caddy_dir = root_dir / ".tools" / "caddy"
caddy_exe = caddy_dir / ("caddy.exe" if os.name == "nt" else "caddy")
caddy_storage_dir = cert_dir / "caddy"
caddy_root_cert = caddy_storage_dir / "pki" / "authorities" / "local" / "root.crt"
caddyfile_path = cert_dir / "Caddyfile"
caddy_log_file = data_dir / "caddy.log"
public_url_error = "Öffentliche Adresse: nur https://-Adresse ohne Pfad, z. B. https://laptop-martin-r"
trusted_proxies_error = "Proxy-Adressen: nur IP-Adressen, z. B. 10.0.77.20"
trusted_proxies_required_error = "Proxy-Adressen: mindestens eine Adresse eintragen, wenn der mitgelieferte Proxy aus ist"
email_error = "Bitte eine gültige E-Mail-Adresse eingeben"
domains_error = "E-Mail-Domains: nur Domains ohne Adresse, z. B. looks.film"
_public_url_host_pattern = re.compile(r"^[a-z0-9.-]{1,253}$")
_database_identifier = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_server_address_pattern = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
_skill_id_pattern = re.compile(r"^[a-z0-9_]{1,64}$")
_domain_pattern = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


# FUNC
def normalize_public_url(value: object) -> str:
    """Validate and canonicalize the public HTTPS origin."""
    if not isinstance(value, str):
        raise ValueError(public_url_error)
    try:
        url = value.strip()
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError(public_url_error) from error
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or host is None
        or not _public_url_host_pattern.fullmatch(host)
        or (port is not None and not 1 <= port <= 65535)
        or parsed.path not in ("", "/")
        or "?" in url
        or "#" in url
    ):
        raise ValueError(public_url_error)
    return f"https://{host}" + (f":{port}" if port is not None and port != 443 else "")


def normalize_trusted_proxies(values: object) -> tuple[str, ...]:
    """Validate and deduplicate trusted proxy IP addresses in input order."""
    if not isinstance(values, (list, tuple)):
        raise ValueError(trusted_proxies_error)
    normalized = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(trusted_proxies_error)
        try:
            address = str(ipaddress.ip_address(value.strip()))
        except ValueError as error:
            raise ValueError(trusted_proxies_error) from error
        if address not in normalized:
            normalized.append(address)
    return tuple(normalized)


def normalize_email(value: object) -> str:
    """Normalize an email address and validate its local part and domain."""
    if not isinstance(value, str):
        raise ValueError(email_error)
    normalized = value.strip().casefold()
    if normalized.count("@") != 1:
        raise ValueError(email_error)
    local, domain = normalized.split("@")
    if (
        not 1 <= len(local) <= 64
        or any(character.isspace() or character == "@" for character in local)
        or not _domain_pattern.fullmatch(domain)
    ):
        raise ValueError(email_error)
    return normalized


def normalize_domains(values: object) -> tuple[str, ...]:
    """Normalize, validate, and stably deduplicate email domains."""
    if not isinstance(values, (list, tuple)):
        raise ValueError(domains_error)
    normalized = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(domains_error)
        domain = value.strip().casefold().removeprefix("@")
        if not domain:
            continue
        if not _domain_pattern.fullmatch(domain):
            raise ValueError(domains_error)
        if domain not in normalized:
            normalized.append(domain)
    return tuple(normalized)


def check_proxy(builtin: bool, trusted_proxies: tuple[str, ...]) -> None:
    """Require trusted proxy addresses when the built-in proxy is disabled."""
    if not builtin and not trusted_proxies:
        raise ValueError(trusted_proxies_required_error)


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
class FolderDialogConfig:
    server_address: str = ""


@dataclass(frozen=True)
class ServerWorkerConfig:
    enabled: bool = True
    skills: tuple[str, ...] = ("copy_file", "move_file", "rename_file")


@dataclass(frozen=True)
class ProxyConfig:
    public_url: str = "https://localhost"
    builtin: bool = True
    trusted_proxies: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthConfig:
    admin_emails: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    session_days: int = 180
    code_ttl_minutes: int = 15


@dataclass(frozen=True)
class SmtpConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    use_tls: bool = True
    from_address: str = ""
    from_name: str = "Marathon"

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_address)


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    server_worker: ServerWorkerConfig = field(default_factory=ServerWorkerConfig)
    folder_dialog: FolderDialogConfig = field(default_factory=FolderDialogConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    smtp: SmtpConfig = field(default_factory=SmtpConfig)


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
    folder_dialog_data = data.get("folder_dialog", {})
    if not isinstance(folder_dialog_data, dict):
        raise ConfigError(f"[folder_dialog] in {config_file} muss eine Tabelle sein")
    server_address = folder_dialog_data.get("server_address", "")
    if not isinstance(server_address, str) or (server_address and not _server_address_pattern.fullmatch(server_address)):
        raise ConfigError(
            f"[folder_dialog] server_address in {config_file} muss ein Rechnername oder eine IP-Adresse sein"
        )
    folder_dialog = FolderDialogConfig(server_address)

    worker_data = data.get("server_worker", {})
    if not isinstance(worker_data, dict):
        raise ConfigError(f"[server_worker] in {config_file} muss eine Tabelle sein")
    enabled = worker_data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"[server_worker] enabled in {config_file} muss true oder false sein")
    skills = worker_data.get("skills", ["copy_file", "move_file", "rename_file"])
    if (
        not isinstance(skills, list)
        or any(not isinstance(skill, str) or not _skill_id_pattern.fullmatch(skill) for skill in skills)
        or len(set(skills)) != len(skills)
    ):
        raise ConfigError(
            f"[server_worker] skills in {config_file} muss eine Liste von Modultyp-IDs ohne Doppelte sein"
        )
    server_worker = ServerWorkerConfig(enabled=enabled, skills=tuple(skills))

    proxy_data = data.get("proxy", {})
    if not isinstance(proxy_data, dict):
        raise ConfigError(f"[proxy] in {config_file} muss eine Tabelle sein")
    builtin = proxy_data.get("builtin", True)
    if not isinstance(builtin, bool):
        raise ConfigError(f"[proxy] builtin in {config_file} muss true oder false sein")
    try:
        public_url = normalize_public_url(proxy_data.get("public_url", "https://localhost"))
    except ValueError as error:
        raise ConfigError(f"[proxy] public_url in {config_file}: {error}") from error
    try:
        trusted_proxies = normalize_trusted_proxies(proxy_data.get("trusted_proxies", []))
    except ValueError as error:
        raise ConfigError(f"[proxy] trusted_proxies in {config_file}: {error}") from error
    try:
        check_proxy(builtin, trusted_proxies)
    except ValueError as error:
        raise ConfigError(f"[proxy] trusted_proxies in {config_file}: {error}") from error
    proxy = ProxyConfig(public_url, builtin, trusted_proxies)

    auth_data = data.get("auth", {})
    if not isinstance(auth_data, dict):
        raise ConfigError(f"[auth] in {config_file} muss eine Tabelle sein")
    admin_emails_data = auth_data.get("admin_emails", [])
    if not isinstance(admin_emails_data, list) or any(not isinstance(value, str) for value in admin_emails_data):
        raise ConfigError(f"[auth] admin_emails in {config_file} muss eine Liste gültiger E-Mail-Adressen sein")
    try:
        admin_emails = tuple(dict.fromkeys(normalize_email(value) for value in admin_emails_data))
    except ValueError as error:
        raise ConfigError(
            f"[auth] admin_emails in {config_file} muss eine Liste gültiger E-Mail-Adressen sein"
        ) from error
    try:
        allowed_domains = normalize_domains(auth_data.get("allowed_domains", []))
    except ValueError as error:
        raise ConfigError(f"[auth] allowed_domains in {config_file}: {domains_error}") from error
    auth = AuthConfig(
        admin_emails=admin_emails,
        allowed_domains=allowed_domains,
        session_days=_validated_value(auth_data, "auth", "session_days", 180, 1, 365, config_file, integer=True),
        code_ttl_minutes=_validated_value(
            auth_data, "auth", "code_ttl_minutes", 15, 1, 60, config_file, integer=True
        ),
    )

    smtp_data = data.get("smtp", {})
    if not isinstance(smtp_data, dict):
        raise ConfigError(f"[smtp] in {config_file} muss eine Tabelle sein")
    smtp_host = smtp_data.get("host", "")
    if not isinstance(smtp_host, str):
        raise ConfigError(f"[smtp] host in {config_file} muss ein Text sein")
    smtp_username = smtp_data.get("username", "")
    if not isinstance(smtp_username, str):
        raise ConfigError(f"[smtp] username in {config_file} muss ein Text sein")
    smtp_use_tls = smtp_data.get("use_tls", True)
    if not isinstance(smtp_use_tls, bool):
        raise ConfigError(f"[smtp] use_tls in {config_file} muss true oder false sein")
    smtp_from_name = smtp_data.get("from_name", "Marathon")
    if not isinstance(smtp_from_name, str):
        raise ConfigError(f"[smtp] from_name in {config_file} muss ein Text sein")
    smtp_from_address_data = smtp_data.get("from_address", "")
    if not isinstance(smtp_from_address_data, str):
        raise ConfigError(f"[smtp] from_address in {config_file} muss leer oder eine gültige E-Mail-Adresse sein")
    if smtp_from_address_data:
        try:
            smtp_from_address = normalize_email(smtp_from_address_data)
        except ValueError as error:
            raise ConfigError(
                f"[smtp] from_address in {config_file} muss leer oder eine gültige E-Mail-Adresse sein"
            ) from error
    else:
        smtp_from_address = ""
    smtp = SmtpConfig(
        host=smtp_host.strip(),
        port=_validated_value(smtp_data, "smtp", "port", 587, 1, 65535, config_file, integer=True),
        username=smtp_username.strip(),
        use_tls=smtp_use_tls,
        from_address=smtp_from_address,
        from_name=smtp_from_name,
    )
    if smtp.host and not smtp.from_address:
        raise ConfigError(
            f"[smtp] from_address in {config_file} fehlt: Absenderadresse ist nötig, wenn host gesetzt ist"
        )
    return ServerConfig(config.host, config.port, database, engine, server_worker, folder_dialog, proxy, auth, smtp)
