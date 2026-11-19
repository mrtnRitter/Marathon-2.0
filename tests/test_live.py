import asyncio
from dataclasses import dataclass
import threading
import time
from types import SimpleNamespace

import pytest

from marathon.server import live


async def _flush_loop(loop: asyncio.AbstractEventLoop) -> None:
    flushed = loop.create_future()
    loop.call_soon(flushed.set_result, None)
    await flushed


def test_hub_thread_publish_coalesces_changes_and_full_queue_resyncs() -> None:
    async def scenario() -> None:
        hub = live.LiveHub()
        sub = hub.subscribe()
        publisher = threading.Thread(target=lambda: (hub.publish({"assets"}), hub.publish({"jobs"})))
        publisher.start()
        publisher.join()
        assert await sub.next(0.5) == frozenset({"assets", "jobs"})
        sub.close()
        sub.close()
        assert hub.subscriber_count == 0

        full_hub = live.LiveHub(max_queue=2)
        full_sub = full_hub.subscribe()
        for table in ("assets", "jobs", "projects", "users", "tasks"):
            full_hub.publish({table})
        await _flush_loop(asyncio.get_running_loop())
        assert await full_sub.next(0.5) == frozenset({"*"})
        full_sub.close()

    asyncio.run(scenario())


def test_hub_timeout_close_order_and_subscription_after_close() -> None:
    async def scenario() -> None:
        hub = live.LiveHub()
        sub = hub.subscribe()
        assert await sub.next(0.05) is None
        hub.publish({"assets"})
        hub.close_streams()
        await _flush_loop(asyncio.get_running_loop())
        assert await sub.next(0.5) == frozenset({"assets"})
        with pytest.raises(live.LiveClosed):
            await sub.next(0.5)
        with pytest.raises(live.LiveClosed):
            await sub.next(0.5)
        assert hub.subscriber_count == 0

        closed_sub = hub.subscribe()
        with pytest.raises(live.LiveClosed):
            await closed_sub.next(0.5)
        hub.close_streams()

    asyncio.run(scenario())


def test_hub_queue_full_close_raises_after_queued_changes() -> None:
    async def scenario() -> None:
        hub = live.LiveHub(max_queue=2)
        subscription = hub.subscribe()
        hub.publish({"assets"})
        hub.publish({"jobs"})
        await _flush_loop(asyncio.get_running_loop())
        hub.close_streams()
        await _flush_loop(asyncio.get_running_loop())
        assert await subscription.next(0.5) == frozenset({"assets", "jobs"})
        with pytest.raises(live.LiveClosed):
            await subscription.next(0.5)

    asyncio.run(scenario())


def test_hub_close_wakes_waiting_next_promptly() -> None:
    async def scenario() -> None:
        hub = live.LiveHub()
        sub = hub.subscribe()
        waiting = asyncio.create_task(sub.next(10.0))
        await asyncio.sleep(0)
        closer = threading.Thread(target=hub.close_streams)
        started = time.monotonic()
        closer.start()
        with pytest.raises(live.LiveClosed):
            await asyncio.wait_for(waiting, timeout=0.5)
        closer.join()
        assert time.monotonic() - started < 0.5

    asyncio.run(scenario())


def test_subscription_close_wakes_waiting_next_promptly() -> None:
    async def scenario() -> None:
        subscription = live.LiveHub().subscribe()
        waiting = asyncio.create_task(subscription.next(10.0))
        await asyncio.sleep(0)
        started = time.monotonic()
        subscription.close()
        with pytest.raises(live.LiveClosed):
            await asyncio.wait_for(waiting, timeout=0.5)
        assert time.monotonic() - started < 0.5

    asyncio.run(scenario())


def test_hub_publish_after_subscriber_loop_closes_is_ignored() -> None:
    hub = live.LiveHub()
    loop = asyncio.new_event_loop()

    async def subscribe() -> live.LiveSubscription:
        return hub.subscribe()

    loop.run_until_complete(subscribe())
    loop.close()
    hub.publish({"assets"})
    assert hub.subscriber_count == 0


@dataclass
class _Notification:
    payload: str


class _FakeConnection:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: list[_Notification] = []
        self._errors: list[Exception] = []
        self._closed = False
        self.listen_commands: list[str] = []
        self.notifies_entered = threading.Event()
        self.closed_event = threading.Event()

    def execute(self, statement: str) -> None:
        self.listen_commands.append(statement)

    def notifies(self, timeout: float) -> list[_Notification]:
        self.notifies_entered.set()
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._pending and not self._errors and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)
            if self._errors:
                raise self._errors.pop(0)
            batch, self._pending = self._pending, []
            return batch

    def send(self, *payloads: str) -> None:
        with self._condition:
            self._pending.extend(_Notification(payload) for payload in payloads)
            self._condition.notify_all()

    def fail(self, error: Exception) -> None:
        with self._condition:
            self._errors.append(error)
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self.closed_event.set()


class _Recorder:
    def __init__(self, fail_first: bool = False) -> None:
        self._condition = threading.Condition()
        self.calls: list[set[str]] = []
        self._fail_first = fail_first

    def __call__(self, tables: set[str]) -> None:
        with self._condition:
            self.calls.append(set(tables))
            self._condition.notify_all()
            fail = self._fail_first
            self._fail_first = False
        if fail:
            raise RuntimeError("callback failure")

    def wait_for_count(self, count: int, timeout_s: float = 1.0) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: len(self.calls) >= count, timeout_s)

    def snapshot(self) -> list[set[str]]:
        with self._condition:
            return [set(call) for call in self.calls]


def test_listener_resync_batches_valid_payloads_and_ignores_invalid() -> None:
    connection = _FakeConnection()
    recorder = _Recorder()
    listener = live.NotifyListener(lambda: connection, recorder, batch_s=0.1)
    listener.start()
    assert recorder.wait_for_count(1)
    assert recorder.snapshot()[0] == {"*"}
    assert connection.notifies_entered.wait(1.0)
    connection.send("assets", "jobs", "INVALID", "bad-name")
    assert recorder.wait_for_count(2)
    assert recorder.snapshot()[1] == {"assets", "jobs"}
    assert connection.listen_commands == [f"LISTEN {live.live_channel}"]
    assert listener.stop(timeout_s=1.0)
    assert connection.closed_event.is_set()


def test_listener_reconnects_after_notify_error_and_resyncs() -> None:
    first = _FakeConnection()
    second = _FakeConnection()
    recorder = _Recorder()
    connections = iter((first, second))
    listener = live.NotifyListener(lambda: next(connections), recorder, batch_s=0.05, retry_delays_s=(0.01,))
    listener.start()
    assert recorder.wait_for_count(1)
    assert first.notifies_entered.wait(1.0)
    first.fail(RuntimeError("listen failed"))
    assert recorder.wait_for_count(2)
    assert recorder.snapshot()[:2] == [{"*"}, {"*"}]
    assert second.notifies_entered.wait(1.0)
    assert listener.stop(timeout_s=1.0)
    assert first.closed_event.is_set()
    assert second.closed_event.is_set()


def test_listener_retry_delay_progresses_when_listen_fails() -> None:
    class StopAfterWait:
        def __init__(self) -> None:
            self.stopped = False
            self.delays: list[float] = []

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, delay: float) -> bool:
            self.delays.append(delay)
            if len(self.delays) == 3:
                self.stopped = True
                return True
            return False

        def set(self) -> None:
            self.stopped = True

    class FailedListen:
        def execute(self, statement: str) -> None:
            raise RuntimeError("LISTEN failed")

        def close(self) -> None:
            pass

    listener = live.NotifyListener(
        lambda: FailedListen(), lambda tables: None, retry_delays_s=(1.0, 2.0, 5.0)
    )
    stop_event = StopAfterWait()
    listener._stop_event = stop_event  # type: ignore[assignment]
    listener._run()

    assert stop_event.delays == [1.0, 2.0, 5.0]


def test_listener_retries_connect_failures_until_success() -> None:
    recorder = _Recorder()
    connection = _FakeConnection()
    attempts = 0
    attempts_changed = threading.Condition()

    def connect() -> _FakeConnection:
        nonlocal attempts
        with attempts_changed:
            attempts += 1
            attempts_changed.notify_all()
            current = attempts
        if current < 3:
            raise ConnectionError("temporary")
        return connection

    listener = live.NotifyListener(connect, recorder, batch_s=0.05, retry_delays_s=(0.01,))
    listener.start()
    with attempts_changed:
        assert attempts_changed.wait_for(lambda: attempts >= 3, 1.0)
    assert recorder.wait_for_count(1)
    assert recorder.snapshot()[0] == {"*"}
    assert listener.stop(timeout_s=1.0)


def test_listener_continues_when_callback_raises() -> None:
    connection = _FakeConnection()
    recorder = _Recorder(fail_first=True)
    listener = live.NotifyListener(lambda: connection, recorder, batch_s=0.1)
    listener.start()
    assert recorder.wait_for_count(1)
    assert connection.notifies_entered.wait(1.0)
    connection.send("assets")
    assert recorder.wait_for_count(2)
    assert recorder.snapshot() == [{"*"}, {"assets"}]
    assert listener.stop(timeout_s=1.0)


def test_listener_stop_during_notifies_is_prompt_and_no_callback_follows() -> None:
    connection = _FakeConnection()
    recorder = _Recorder()
    listener = live.NotifyListener(lambda: connection, recorder, batch_s=0.4)
    listener.start()
    assert recorder.wait_for_count(1)
    assert connection.notifies_entered.wait(1.0)
    before = recorder.snapshot()
    started = time.monotonic()
    assert listener.stop(timeout_s=0.8)
    assert time.monotonic() - started < 1.0
    connection.send("assets")
    assert recorder.snapshot() == before
    assert connection.closed_event.is_set()


def test_listener_stop_during_retry_wait_is_prompt() -> None:
    attempts = 0
    attempted = threading.Event()
    recorder = _Recorder()

    def connect() -> None:
        nonlocal attempts
        attempts += 1
        attempted.set()
        raise ConnectionError("offline")

    listener = live.NotifyListener(connect, recorder, retry_delays_s=(10.0,))
    listener.start()
    assert attempted.wait(1.0)
    started = time.monotonic()
    assert listener.stop(timeout_s=0.8)
    assert time.monotonic() - started < 1.0
    assert attempts == 1
    assert recorder.snapshot() == []


def test_listener_stop_during_connect_closes_without_listen_or_callback() -> None:
    entered = threading.Event()
    release = threading.Event()
    connection = _FakeConnection()
    recorder = _Recorder()

    def connect() -> _FakeConnection:
        entered.set()
        assert release.wait(1.0)
        return connection

    listener = live.NotifyListener(connect, recorder, batch_s=0.05)
    listener.start()
    assert entered.wait(1.0)
    assert listener.stop(timeout_s=0.0) is False
    release.set()
    assert connection.closed_event.wait(1.0)
    assert listener.stop(timeout_s=1.0)
    assert connection.listen_commands == []
    assert recorder.snapshot() == []


def test_open_listen_connection_uses_engine_url_and_connection_options(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    expected = object()

    def connect(**kwargs: object) -> object:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(live.psycopg, "connect", connect)
    engine = SimpleNamespace(url=SimpleNamespace(host="db.example", port=5433, database="marathon", username="reader", password="secret"))
    assert live.open_listen_connection(engine) is expected
    assert captured == {
        "host": "db.example",
        "port": 5433,
        "dbname": "marathon",
        "user": "reader",
        "password": "secret",
        "autocommit": True,
        "connect_timeout": 5,
        "keepalives": 1,
        "keepalives_idle": 10,
        "keepalives_interval": 5,
        "keepalives_count": 3,
    }
