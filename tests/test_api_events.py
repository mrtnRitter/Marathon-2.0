"""Tests for the live server-sent events endpoint."""

# IMPORTS
import asyncio
import http.client
import logging
import socket
import signal
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
import uvicorn

from marathon.server import app as server_app
from marathon.server.api import events
from marathon.server.app import create_app
from marathon.server.live import LiveClosed, LiveHub, NotifyListener, open_listen_connection
from marathon.server.__main__ import _LiveServer


# TEST HELPERS
def _close_after_subscription(hub: LiveHub, tables: set[str] | None = None, delay_s: float = 0.0) -> threading.Thread:
    def close_stream() -> None:
        deadline = time.monotonic() + 2.0
        while hub.subscriber_count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        if hub.subscriber_count and tables:
            hub.publish(tables)
        if delay_s:
            time.sleep(delay_s)
        hub.close_streams()

    thread = threading.Thread(target=close_stream, name="test-live-close", daemon=True)
    thread.start()
    return thread


def _join_helper(thread: threading.Thread, hub: LiveHub) -> None:
    thread.join(timeout=3.0)
    hub.close_streams()
    assert not thread.is_alive()


def _get_stream_response(app, hub: LiveHub):
    responses = []
    failures = []
    completed = threading.Event()

    def request_stream() -> None:
        try:
            with TestClient(app) as client:
                responses.append(client.get("/api/v1/events"))
        except Exception as error:
            failures.append(error)
        finally:
            completed.set()

    thread = threading.Thread(target=request_stream, name="test-sse-request", daemon=True)
    thread.start()
    if not completed.wait(5.0):
        hub.close_streams()
        completed.wait(1.0)
    thread.join(timeout=1.0)
    assert completed.is_set() and not thread.is_alive()
    if failures:
        raise failures[0]
    assert responses
    return responses[0]


def _static_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text("<html></html>", encoding="utf-8")
    return path


# TESTS
def test_events_stream_sends_retry_and_changes(tmp_path):
    app = create_app(static_dir=_static_dir(tmp_path))
    hub = app.state.live_hub
    helper = _close_after_subscription(hub, {"jobs", "assets"})

    try:
        response = _get_stream_response(app, hub)
    finally:
        _join_helper(helper, hub)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("retry: 2000\n\n")
    assert 'data: {"tables": ["assets", "jobs"]}\n\n' in response.text
    assert hub.subscriber_count == 0


def test_events_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setattr(events, "heartbeat_s", 0.05)
    app = create_app(static_dir=_static_dir(tmp_path))
    hub = app.state.live_hub
    helper = _close_after_subscription(hub, delay_s=0.3)

    try:
        response = _get_stream_response(app, hub)
    finally:
        _join_helper(helper, hub)

    assert response.status_code == 200
    assert ": ping\n\n" in response.text


def test_events_end_immediately_when_hub_closed(tmp_path):
    app = create_app(static_dir=_static_dir(tmp_path))
    app.state.live_hub.close_streams()

    response = _get_stream_response(app, app.state.live_hub)

    assert response.status_code == 200
    assert response.text == "retry: 2000\n\n"


def test_events_subscribe_only_after_stream_starts(tmp_path):
    app = create_app(static_dir=_static_dir(tmp_path))
    hub = app.state.live_hub

    async def is_disconnected() -> bool:
        return False

    request = SimpleNamespace(app=app, is_disconnected=is_disconnected)
    response = asyncio.run(events.get_events(request))

    try:
        assert hub.subscriber_count == 0

        async def consume_stream() -> None:
            assert await anext(response.body_iterator) == "retry: 2000\n\n"
            assert hub.subscriber_count == 1
            hub.close_streams()
            with pytest.raises(StopAsyncIteration):
                await anext(response.body_iterator)

        asyncio.run(consume_stream())
    finally:
        hub.close_streams()


def test_server_shutdown_closes_open_stream(db_engine, tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    app = create_app(engine=db_engine, run_live=True, static_dir=_static_dir(tmp_path))
    server = _LiveServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            timeout_graceful_shutdown=5,
        ),
        app.state.live_hub.close_streams,
    )
    server_thread = threading.Thread(target=server.run, name="test-live-uvicorn", daemon=True)
    connection = None
    try:
        server_thread.start()
        deadline = time.monotonic() + 10
        while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/api/v1/events")
        response = connection.getresponse()
        assert response.status == 200
        assert response.readline() == b"retry: 2000\n"
        assert response.readline() == b"\n"

        started = time.monotonic()
        server.handle_exit(signal.SIGINT, None)
        server_thread.join(timeout=1.9)
        elapsed = time.monotonic() - started
        assert not server_thread.is_alive()
        assert elapsed < 2
    finally:
        if server_thread.is_alive():
            server.handle_exit(signal.SIGINT, None)
            server_thread.join(timeout=6)
        if connection is not None:
            connection.close()

    assert not any(thread.name == "marathon-live" and thread.is_alive() for thread in threading.enumerate())

def test_listener_delivers_database_change_to_hub(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf-events-listener', 'Initial')"))

    hub = LiveHub()
    listener = NotifyListener(lambda: open_listen_connection(db_engine), hub.publish, batch_s=0.05)
    listener.start()

    async def wait_for_workflow_change() -> bool:
        subscription = hub.subscribe()
        deadline = time.monotonic() + 5.0
        attempt = 0
        try:
            while time.monotonic() < deadline:
                with db_engine.begin() as connection:
                    connection.execute(
                        text("UPDATE workflows SET name = :name WHERE id = 'wf-events-listener'"),
                        {"name": f"Changed {attempt}"},
                    )
                attempt += 1
                remaining = deadline - time.monotonic()
                tables = await subscription.next(min(0.3, max(0.0, remaining)))
                if tables and "workflows" in tables:
                    return True
            return False
        finally:
            subscription.close()

    try:
        assert asyncio.run(wait_for_workflow_change())
    finally:
        hub.close_streams()
        stopped = listener.stop()

    assert stopped is True


def test_lifespan_runs_and_stops_live_listener(db_engine, tmp_path):
    app = create_app(engine=db_engine, run_live=True, static_dir=_static_dir(tmp_path))
    hub = app.state.live_hub

    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert any(thread.name == "marathon-live" and thread.is_alive() for thread in threading.enumerate())

    assert not any(thread.name == "marathon-live" and thread.is_alive() for thread in threading.enumerate())

    async def assert_closed() -> None:
        subscription = hub.subscribe()
        with pytest.raises(LiveClosed):
            await subscription.next(0.1)

    asyncio.run(assert_closed())


def test_lifespan_listener_start_failure_keeps_server_running(monkeypatch, db_engine, tmp_path, caplog):
    class FailingListener:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("listener startup failed")

        def stop(self):
            return True

    monkeypatch.setattr(server_app, "NotifyListener", FailingListener)
    app = create_app(engine=db_engine, run_live=True, static_dir=_static_dir(tmp_path))

    with caplog.at_level(logging.WARNING, logger="marathon.server"), TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert any(
        record.name == "marathon.server"
        and record.getMessage() == "Live-Listener konnte nicht gestartet werden (RuntimeError)"
        for record in caplog.records
    )


def test_live_server_handle_exit_closes_streams(tmp_path):
    app = create_app(static_dir=_static_dir(tmp_path))
    calls = []
    server = _LiveServer(uvicorn.Config(app), lambda: calls.append("closed"))

    server.handle_exit(signal.SIGINT, None)

    assert calls == ["closed"]
    assert server.should_exit is True

    def fail_to_close() -> None:
        raise RuntimeError("close failed")

    failing_server = _LiveServer(uvicorn.Config(app), fail_to_close)
    failing_server.handle_exit(signal.SIGINT, None)
    assert failing_server.should_exit is True


def test_lifespan_listener_stop_failure_is_logged(monkeypatch, db_engine, tmp_path, caplog):
    class FailingStopListener:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            return False

    monkeypatch.setattr(server_app, "NotifyListener", FailingStopListener)
    app = create_app(engine=db_engine, run_live=True, static_dir=_static_dir(tmp_path))

    with caplog.at_level(logging.WARNING, logger="marathon.server"), TestClient(app):
        pass

    assert any(
        record.name == "marathon.server"
        and record.getMessage() == "Live-Kanal konnte nicht gestoppt werden"
        for record in caplog.records
    )
