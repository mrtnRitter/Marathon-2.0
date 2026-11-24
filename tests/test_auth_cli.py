import re
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from marathon.server.auth.__main__ import main
from marathon.server.auth.codes import confirm_code
from marathon.server.config import load_server_config


def _config_file(tmp_path, admin_emails='["admin@looks.film"]'):
    path = tmp_path / "marathon.toml"
    path.write_text(f"[auth]\nadmin_emails = {admin_emails}\n", encoding="utf-8")
    return path


def test_admin_code_for_config_admin(tmp_path, db_engine, capsys):
    config_file = _config_file(tmp_path)

    assert main(["admin-code", "Admin@Looks.Film"], config_file=config_file, engine=db_engine) == 0
    output = capsys.readouterr().out
    match = re.fullmatch(r"Anmeldecode für admin@looks\.film: (\d{6}) \(gültig 15 Minuten\)\n", output)
    assert match

    with db_engine.begin() as connection:
        result = confirm_code(connection, "admin@looks.film", match.group(1), load_server_config(config_file).auth)
    assert result.status_code == 200


def test_admin_code_rejects_non_admin(tmp_path, db_engine, capsys):
    assert main(["admin-code", "user@looks.film"], config_file=_config_file(tmp_path), engine=db_engine) == 1
    assert capsys.readouterr().err == "Fehler: user@looks.film ist kein Admin\n"


def test_admin_code_invalid_email(tmp_path, db_engine, capsys):
    assert main(["admin-code", "kein-mail"], config_file=_config_file(tmp_path), engine=db_engine) == 2
    assert capsys.readouterr().err == "Fehler: Bitte eine gültige E-Mail-Adresse eingeben\n"


def test_admin_code_lifts_lock(tmp_path, db_engine):
    config_file = _config_file(tmp_path)
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO auth_locks (email, locked_until) VALUES ('admin@looks.film', now() + interval '1 hour')")
        )

    assert main(["admin-code", "admin@looks.film"], config_file=config_file, engine=db_engine) == 0
    with db_engine.connect() as connection:
        assert connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM auth_locks WHERE email = 'admin@looks.film')")
        ).scalar_one() is False


def test_admin_code_database_unreachable(tmp_path, capsys):
    class UnreachableEngine:
        @contextmanager
        def begin(self):
            raise OperationalError("connect", {}, RuntimeError("password must stay private"))
            yield

    assert main(["admin-code", "admin@looks.film"], config_file=_config_file(tmp_path), engine=UnreachableEngine()) == 1
    error = capsys.readouterr().err
    assert error == "Fehler: Datenbank nicht erreichbar – zuerst python run.py starten\n"
    assert "password" not in error
