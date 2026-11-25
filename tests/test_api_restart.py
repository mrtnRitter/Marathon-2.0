"""Server restart endpoint tests."""

from fastapi.testclient import TestClient

from auth_helpers import sign_in
from marathon.server.app import create_app
from marathon.server.auth.sessions import AuthUser


class ResponseSentMarker:
    def __init__(self, app, sent):
        self.app = app
        self.sent = sent

    async def __call__(self, scope, receive, send):
        async def mark_send(message):
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                self.sent.append(True)

        await self.app(scope, receive, mark_send)


def test_restart_returns_202_and_runs_after_response_body():
    app = create_app()
    sent = []
    callback_observed = []
    app.state.request_restart = lambda: callback_observed.append(bool(sent))
    sign_in(app)

    with TestClient(ResponseSentMarker(app, sent)) as client:
        response = client.post("/api/v1/server/restart")

    assert response.status_code == 202
    assert response.json() == {"detail": "Server wird neu gestartet"}
    assert sent == [True]
    assert callback_observed == [True]


def test_restart_requires_admin_and_available_callback():
    user_app = create_app()
    sign_in(user_app, AuthUser("test-user", "user@test.local", "Test", "user", "user-session"))
    with TestClient(user_app) as client:
        forbidden = client.post("/api/v1/server/restart")
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "Nur für Admins"}

    unavailable_app = create_app()
    sign_in(unavailable_app)
    with TestClient(unavailable_app) as client:
        unavailable = client.post("/api/v1/server/restart")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Neustart nicht verfügbar"}
