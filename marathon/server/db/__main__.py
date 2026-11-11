"""Manage the Marathon database with Alembic."""

# IMPORTS
import argparse
import sys
from pathlib import Path

from sqlalchemy.exc import OperationalError

from marathon.server.config import ConfigError, default_config_file, load_server_config, pg_password_file
from marathon.server.db.demo import load_demo_data
from marathon.server.db.engine import create_db_engine, ensure_database, read_password, upgrade_database


# CONFIG
app_name = "marathon-database"
app_version = "2.0.0"


# FUNC
def main(
    argv: list[str] | None = None,
    config_file: Path = default_config_file,
    password_file: Path = pg_password_file,
) -> int:
    """Run a database subcommand and return its process exit code."""
    parser = argparse.ArgumentParser(prog="python -m marathon.server.db")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("upgrade", help="Datenbank bis zur neuesten Migration aktualisieren")
    subparsers.add_parser("demo-data", help="Demo-Daten in eine leere Datenbank laden")
    arguments = parser.parse_args(argv)

    try:
        server_config = load_server_config(config_file)
        password = read_password(password_file)
        ensure_database(server_config.database, password)
        engine = create_db_engine(server_config.database, password)
        try:
            revision = upgrade_database(engine)
            demo_loaded = load_demo_data(engine) if arguments.command == "demo-data" else None
        finally:
            engine.dispose()
    except ConfigError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    except OperationalError as error:
        cause = str(error.orig).splitlines()[0] if str(error.orig) else str(error).splitlines()[0]
        if "password" in locals():
            cause = cause.replace(password, "[geschützt]")
        database_config = server_config.database
        print(
            f"Fehler: Datenbank nicht erreichbar ({database_config.host}:{database_config.port}): {cause}",
            file=sys.stderr,
        )
        return 1

    if arguments.command == "demo-data":
        print("Demo-Daten geladen" if demo_loaded else "Datenbank enthält bereits Daten, Demo-Daten übersprungen")
        return 0
    if arguments.command == "upgrade":
        print(f"Datenbank {server_config.database.name} aktuell (Revision {revision})")
        return 0
    return 2


# EXEC
if __name__ == "__main__":
    sys.exit(main())
