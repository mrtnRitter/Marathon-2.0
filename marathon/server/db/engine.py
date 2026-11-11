"""Database engine and Alembic helpers."""

# IMPORTS
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, URL

from marathon.server.config import ConfigError, DatabaseConfig, pg_password_file


# CONFIG
migrations_dir = Path(__file__).resolve().parent / "migrations"


# FUNC
def read_password(path: Path = pg_password_file) -> str:
    """Read the PostgreSQL password without surrounding whitespace."""
    if not path.is_file():
        raise ConfigError(f"Passwortdatei fehlt: {path}. Zuerst python build.py ausführen.")
    return path.read_text(encoding="utf-8").strip()


def database_url(config: DatabaseConfig, password: str, database: str | None = None) -> URL:
    """Build a SQLAlchemy URL for the selected database."""
    return URL.create(
        "postgresql+psycopg",
        username=config.user,
        password=password,
        host=config.host,
        port=config.port,
        database=database or config.name,
    )


def create_db_engine(
    config: DatabaseConfig,
    password: str | None = None,
    database: str | None = None,
) -> Engine:
    """Create a pooled PostgreSQL engine with bounded connection waits."""
    secret = password if password is not None else read_password()
    return create_engine(
        database_url(config, secret, database),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
        connect_args={"connect_timeout": 5},
    )


def ensure_database(config: DatabaseConfig, password: str) -> bool:
    """Create the configured database if it does not already exist."""
    engine = create_db_engine(config, password, database="postgres")
    try:
        with engine.connect() as connection:
            connection = connection.execution_options(isolation_level="AUTOCOMMIT")
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": config.name},
            ).scalar_one_or_none()
            if exists is not None:
                return False
            quoted_name = engine.dialect.identifier_preparer.quote(config.name)
            connection.execute(
                text(f"CREATE DATABASE {quoted_name} ENCODING 'UTF8' TEMPLATE template0")
            )
            return True
    finally:
        engine.dispose()


def current_revision(engine: Engine) -> str | None:
    """Return the installed Alembic revision, if migrations have been applied."""
    if not inspect(engine).has_table("alembic_version"):
        return None
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


def upgrade_database(engine: Engine) -> str:
    """Apply all available migrations and return the current revision."""
    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    revision = current_revision(engine)
    if revision is None:
        raise RuntimeError("Alembic hat nach dem Upgrade keine Revision eingetragen.")
    return revision
