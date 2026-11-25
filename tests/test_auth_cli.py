import re
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from marathon.server.auth.__main__ import main
from marathon.server.auth.codes import confirm_admin_code, is_locked
from marathon.server.config import AuthConfig


def _config_file(tmp_path):
    path = tmp_path / "marathon.toml"
    path.write_text("", encoding="utf-8")
    return path


def test_admin_code_creates_emergency_login_code(tmp_path, db_engine, capsys):
    config_file = _config_file(tmp_path)
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO auth_locks (email, locked_until) VALUES ('', now() + interval '1 hour')")
        )

    assert main(["admin-code"], config_file=config_file, engine=db_engine) == 0
    output = capsys.readouterr().out
    match = re.fullmatch(
        r"Admin-Code: (\d{6}) \(gültig 15 Minuten\) – auf der Anmeldeseite unter „Mit Admin-Code anmelden“ eingeben\n",
        output,
    )
    assert match

    with db_engine.begin() as connection:
        result = confirm_admin_code(connection, match.group(1), AuthConfig())
        unlocked = not is_locked(connection, "")
    assert result.status_code == 200
    assert unlocked


def test_admin_code_rejects_extra_argument(tmp_path):
    try:
        main(["admin-code", "a@b.de"], config_file=_config_file(tmp_path))
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("expected argparse to reject an extra argument")


def test_admin_code_database_unreachable(tmp_path, capsys):
    class UnreachableEngine:
        @contextmanager
        def begin(self):
            raise OperationalError("connect", {}, RuntimeError("password must stay private"))
            yield

    assert main(["admin-code"], config_file=_config_file(tmp_path), engine=UnreachableEngine()) == 1
    error = capsys.readouterr().err
    assert error == "Fehler: Datenbank nicht erreichbar – zuerst python run.py starten\n"
    assert "password" not in error
