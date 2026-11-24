"""Create emergency authentication codes for configured administrators."""

# IMPORTS
import argparse
import sys
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from marathon.server.auth.codes import AuthError, create_admin_code
from marathon.server.config import (
    ConfigError,
    default_config_file,
    load_server_config,
    normalize_email,
    pg_password_file,
)
from marathon.server.db.engine import create_db_engine, read_password


# CONFIG
app_name = "marathon-auth"
app_version = "2.0.0"


# FUNC


# MAIN
def main(
    argv: list[str] | None = None,
    config_file: Path = default_config_file,
    engine: Engine | None = None,
) -> int:
    """Create and print a one-time admin code; return the process exit code."""
    parser = argparse.ArgumentParser(prog="python -m marathon.server.auth")
    subparsers = parser.add_subparsers(dest="command", required=True)
    admin_code_parser = subparsers.add_parser("admin-code", help="Notfall-Anmeldecode für einen Admin ausgeben")
    admin_code_parser.add_argument("email")
    arguments = parser.parse_args(argv)

    owned_engine = None
    try:
        server_config = load_server_config(config_file)
        active_engine = engine
        if active_engine is None:
            active_engine = create_db_engine(server_config.database, read_password(pg_password_file))
            owned_engine = active_engine
        with active_engine.begin() as connection:
            code = create_admin_code(connection, arguments.email, server_config.auth)
    except AuthError as error:
        message = "Bitte eine gültige E-Mail-Adresse eingeben" if error.status_code == 422 else error.detail
        print(f"Fehler: {message}", file=sys.stderr)
        return 2 if error.status_code == 422 else 1
    except ConfigError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    except OperationalError:
        print("Fehler: Datenbank nicht erreichbar – zuerst python run.py starten", file=sys.stderr)
        return 1
    finally:
        if owned_engine is not None:
            owned_engine.dispose()

    email = normalize_email(arguments.email)
    print(f"Anmeldecode für {email}: {code} (gültig {server_config.auth.code_ttl_minutes} Minuten)")
    return 0


# EXEC
if __name__ == "__main__":
    sys.exit(main())
