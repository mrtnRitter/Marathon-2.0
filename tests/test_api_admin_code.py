from fastapi.testclient import TestClient

from marathon.server.app import create_app
from marathon.server.auth.codes import create_admin_code, invalid_code_text, locked_text
from marathon.server.auth.sessions import cookie_name
from marathon.server.config import AuthConfig


EMERGENCY_USER = {
    "id": "emergency",
    "email": "",
    "display_name": "Notfallzugang",
    "role": "admin",
}


def test_admin_code_login_sets_session_and_me_returns_emergency_user(db_engine, tmp_path):
    app = create_app(static_dir=tmp_path, engine=db_engine)
    with db_engine.begin() as connection:
        code = create_admin_code(connection, AuthConfig())

    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/v1/auth/admin-code", json={"code": code})
        cookie = response.headers["set-cookie"].casefold()
        me = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json() == EMERGENCY_USER
    assert cookie_name.casefold() in cookie
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert me.status_code == 200
    assert me.json() == EMERGENCY_USER


def test_admin_code_failures_lock_emergency_account(db_engine, tmp_path):
    app = create_app(static_dir=tmp_path, engine=db_engine)
    with TestClient(app, base_url="https://testserver") as client:
        responses = [client.post("/api/v1/auth/admin-code", json={"code": "wrong"}) for _ in range(3)]

    assert [response.status_code for response in responses] == [400, 400, 429]
    assert responses[0].json() == {"detail": invalid_code_text}
    assert responses[1].json() == {"detail": invalid_code_text}
    assert responses[2].json() == {"detail": locked_text}


def test_admin_code_rejects_non_digit_code(db_engine, tmp_path):
    app = create_app(static_dir=tmp_path, engine=db_engine)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/v1/auth/admin-code", json={"code": "abc"})

    assert response.status_code == 400
    assert response.json() == {"detail": invalid_code_text}
