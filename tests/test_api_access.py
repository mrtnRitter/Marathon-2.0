"""API-wide session and administrator access checks."""

import re
from threading import Timer

from fastapi.testclient import TestClient

from auth_helpers import sign_in
from marathon.server.app import create_app
from marathon.server.auth.sessions import AuthUser


_EXCEPTIONS = {
    ("GET", "/api/v1/client"),
    ("GET", "/api/v1/client/download"),
    ("POST", "/api/v1/worker/control-link"),
    ("POST", "/api/v1/worker-control/exchange"),
    ("GET", "/api/v1/worker-control/self"),
    ("PUT", "/api/v1/worker-control/self/availability"),
    ("POST", "/api/v1/worker-control/self/start"),
    ("POST", "/api/v1/worker-control/self/stop-drain"),
    ("POST", "/api/v1/worker-control/self/stop-now"),
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/auth/request-code"),
    ("POST", "/api/v1/auth/confirm"),
    ("POST", "/api/v1/auth/admin-code"),
    ("POST", "/api/v1/auth/logout"),
}


def _route_calls(app):
    def api_routes(routes, prefix=""):
        for route in routes:
            context = getattr(route, "include_context", None)
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                nested_prefix = prefix + context.prefix
                yield from api_routes(original_router.routes, nested_prefix)
            elif hasattr(route, "path"):
                yield route, prefix.rstrip("/") + "/" + route.path.lstrip("/")

    for route, route_path in api_routes(app.routes):
        if route_path == "/api/{path:path}":
            route_path = "/api/v1/fallback-test"
        if not route_path.startswith("/api/v1/") or route_path.startswith("/api/v1/worker/"):
            continue
        test_path = re.sub(r"\{[^}]+\}", "test-value", route_path)
        methods = set(getattr(route, "methods", ()) or ()) | {"HEAD", "OPTIONS"}
        for method in sorted(methods):
            if (method, route_path) not in _EXCEPTIONS:
                yield method, test_path


def _request(client, method, path, app):
    timer = None
    if path == "/api/v1/events":
        timer = Timer(0.2, app.state.live_hub.close_streams)
        timer.start()
    try:
        if method in {"POST", "PUT", "PATCH"}:
            return client.request(method, path, json={})
        return client.request(method, path)
    finally:
        if timer is not None:
            timer.cancel()
            timer.join(timeout=1.0)
            assert not timer.is_alive()


def test_every_non_exempt_api_route_requires_a_session(db_engine):
    app = create_app(engine=db_engine)
    with TestClient(app) as client:
        for method, path in _route_calls(app):
            response = _request(client, method, path, app)
            assert response.status_code == 401, f"{method} {path}: {response.status_code} {response.text}"
            if method != "HEAD":
                assert response.json() == {"detail": "Nicht angemeldet"}


def test_public_exceptions_do_not_require_a_session(db_engine):
    app = create_app(engine=db_engine)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code != 401
        assert client.post("/api/v1/auth/request-code", json={"email": "test@example.com"}).status_code != 401
        assert client.post("/api/v1/auth/confirm", json={"email": "test@example.com", "code": "000000"}).status_code != 401
        assert client.post("/api/v1/auth/admin-code", json={"code": "000000"}).status_code != 401
        assert client.post("/api/v1/auth/logout").status_code != 401


def test_admin_routes_reject_regular_users_and_reads_remain_available(db_engine):
    app = create_app(engine=db_engine)
    sign_in(app, AuthUser("test-user", "user@test.local", "Test User", "user", "user-session"))
    with TestClient(app) as client:
        settings = client.get("/api/v1/settings")
        assert settings.status_code == 200
        worker_write_routes = {
            (method, path)
            for method, path in _route_calls(app)
            if path.startswith("/api/v1/workers/") and method in {"POST", "PUT", "DELETE"}
        }
        expected_worker_routes = {
            ("POST", "/api/v1/workers/test-value/stop-drain"),
            ("POST", "/api/v1/workers/test-value/stop-now"),
            ("POST", "/api/v1/workers/test-value/approve"),
            ("PUT", "/api/v1/workers/test-value/availability"),
            ("DELETE", "/api/v1/workers/test-value"),
        }
        assert expected_worker_routes <= worker_write_routes
        assert ("POST", "/api/v1/workers/test-value/stop") not in worker_write_routes

        protected_routes = [
            ("PUT", "/api/v1/settings", settings.json()),
            ("POST", "/api/v1/server/restart", None),
        ]
        protected_routes.extend(
            (method, path, {} if method in {"POST", "PUT"} else None)
            for method, path in sorted(worker_write_routes)
        )
        for method, path, body in protected_routes:
            response = client.request(method, path, json=body) if body is not None else client.request(method, path)
            assert response.status_code == 403, f"{method} {path}: {response.status_code} {response.text}"
            assert response.json() == {"detail": "Nur für Admins"}

        for method, path in _route_calls(app):
            if method not in {"GET", "PUT", "DELETE"} or not path.startswith("/api/v1/users/"):
                continue
            if path == "/api/v1/users/directory":
                continue
            body = {"role": "user"} if method == "PUT" else None
            response = client.request(method, path, json=body) if body is not None else client.request(method, path)
            assert response.status_code == 403, f"{method} {path}: {response.status_code} {response.text}"
            assert response.json() == {"detail": "Nur für Admins"}

        assert client.get("/api/v1/users").status_code == 403
        assert client.get("/api/v1/workers").status_code == 200
        assert client.get("/api/v1/users/directory").status_code == 200


def test_events_require_a_session(db_engine):
    app = create_app(engine=db_engine)
    with TestClient(app) as client:
        response = _request(client, "GET", "/api/v1/events", app)
    assert response.status_code == 401
    assert response.json() == {"detail": "Nicht angemeldet"}
