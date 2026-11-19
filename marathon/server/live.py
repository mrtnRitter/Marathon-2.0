"""Distribute live database changes to SSE subscribers."""

# IMPORTS
import asyncio
from collections.abc import Callable, Iterable
import logging
import re
from threading import Event, Lock, Thread, current_thread
from typing import Any

import psycopg
from sqlalchemy.engine import Engine


# CONFIG
live_channel = "marathon_changes"
resync_table = "*"
_logger = logging.getLogger("marathon.live")
_END = object()
_payload_pattern = re.compile(r"[a-z_]+")


# FUNC
class LiveClosed(Exception):
    """Raised when a live subscription has been closed."""


class LiveSubscription:
    """Receive coalesced table-change notifications from a live hub."""

    def __init__(self, hub: "LiveHub", loop: asyncio.AbstractEventLoop, max_queue: int) -> None:
        self._hub = hub
        self._loop = loop
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=max_queue)
        self._end_received = False
        self._end_consumed = False
        self._unsubscribed = False

    async def next(self, timeout_s: float) -> frozenset[str] | None:
        """Return queued table names, or None when the wait times out."""
        if self._end_consumed:
            raise LiveClosed()
        if self._queue.empty() and self._end_received:
            self._end_consumed = True
            raise LiveClosed()
        try:
            item = await asyncio.wait_for(self._queue.get(), timeout_s)
        except TimeoutError:
            if self._end_received and self._queue.empty():
                self._end_consumed = True
                raise LiveClosed() from None
            return None
        if item is _END:
            self._end_consumed = True
            raise LiveClosed()
        tables = set(item)
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is _END:
                self._end_consumed = True
                break
            tables.update(item)
        if self._end_received and self._queue.empty():
            self._end_consumed = True
        return frozenset(tables)

    def close(self) -> None:
        """Unregister this subscription; repeated calls have no effect."""
        self._hub._unsubscribe(self)

    def _enqueue(self, tables: frozenset[str]) -> None:
        if self._unsubscribed or self._end_received:
            return
        try:
            self._queue.put_nowait(tables)
        except asyncio.QueueFull:
            while True:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            self._queue.put_nowait(frozenset({resync_table}))

    def _receive_end(self) -> None:
        if self._end_received:
            return
        self._end_received = True
        try:
            self._queue.put_nowait(_END)
        except asyncio.QueueFull:
            pass


class LiveHub:
    """Manage thread-safe publication to asynchronous live subscriptions."""

    def __init__(self, max_queue: int = 100) -> None:
        if max_queue <= 0:
            raise ValueError("max_queue muss größer als 0 sein.")
        self._max_queue = max_queue
        self._lock = Lock()
        self._subscriptions: set[LiveSubscription] = set()
        self._closed = False

    def subscribe(self) -> LiveSubscription:
        """Subscribe from a running asyncio loop."""
        loop = asyncio.get_running_loop()
        subscription = LiveSubscription(self, loop, self._max_queue)
        with self._lock:
            if self._closed:
                subscription._receive_end()
            else:
                self._subscriptions.add(subscription)
        return subscription

    def publish(self, tables: Iterable[str]) -> None:
        """Publish table names without waiting for subscribers."""
        changes = frozenset(tables)
        if not changes:
            return
        with self._lock:
            if self._closed:
                return
            subscriptions = tuple(self._subscriptions)
        for subscription in subscriptions:
            try:
                subscription._loop.call_soon_threadsafe(subscription._enqueue, changes)
            except RuntimeError:
                self._unsubscribe(subscription)

    def close_streams(self) -> None:
        """Close current and future subscriptions without waiting for them."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = tuple(self._subscriptions)
            self._subscriptions.clear()
        for subscription in subscriptions:
            try:
                subscription._loop.call_soon_threadsafe(subscription._receive_end)
            except RuntimeError:
                self._unsubscribe(subscription)

    @property
    def subscriber_count(self) -> int:
        """Return the number of currently registered subscriptions."""
        with self._lock:
            return len(self._subscriptions)

    def _unsubscribe(self, subscription: LiveSubscription) -> None:
        with self._lock:
            if subscription._unsubscribed:
                return
            subscription._unsubscribed = True
            self._subscriptions.discard(subscription)
        try:
            subscription._loop.call_soon_threadsafe(subscription._receive_end)
        except RuntimeError:
            pass


def open_listen_connection(engine: Engine) -> psycopg.Connection:
    """Open a dedicated, bounded-time PostgreSQL LISTEN connection."""
    url = engine.url
    return psycopg.connect(
        host=url.host,
        port=url.port,
        dbname=url.database,
        user=url.username,
        password=url.password,
        autocommit=True,
        connect_timeout=5,
        keepalives=1,
        keepalives_idle=10,
        keepalives_interval=5,
        keepalives_count=3,
    )


class NotifyListener:
    """Forward PostgreSQL notifications from a stoppable daemon thread."""

    def __init__(
        self,
        connect: Callable[[], Any],
        on_tables: Callable[[set[str]], None],
        batch_s: float = 0.2,
        retry_delays_s: tuple[float, ...] = (1.0, 2.0, 5.0),
    ) -> None:
        if batch_s <= 0:
            raise ValueError("batch_s muss größer als 0 sein.")
        if not retry_delays_s or any(delay < 0 for delay in retry_delays_s):
            raise ValueError("retry_delays_s muss mindestens eine nichtnegative Wartezeit enthalten.")
        self._connect = connect
        self._on_tables = on_tables
        self._batch_s = batch_s
        self._retry_delays_s = retry_delays_s
        self._stop_event = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._started = False

    def start(self) -> None:
        """Start one daemon listener thread; repeated calls are ignored."""
        with self._lock:
            if self._started or self._stop_event.is_set():
                return
            self._started = True
            self._thread = Thread(target=self._run, name="marathon-live", daemon=True)
            self._thread.start()

    def stop(self, timeout_s: float = 6.0) -> bool:
        """Request shutdown and report whether the listener thread ended."""
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        if thread is current_thread():
            return False
        thread.join(timeout_s)
        return not thread.is_alive()

    def _report(self, tables: set[str]) -> None:
        if self._stop_event.is_set():
            return
        try:
            self._on_tables(tables)
        except Exception as error:
            _logger.warning("PostgreSQL-Änderung konnte nicht verteilt werden (%s)", type(error).__name__)

    def _run(self) -> None:
        retry_index = 0
        while not self._stop_event.is_set():
            connection = None
            error_name: str | None = None
            try:
                connection = self._connect()
                if self._stop_event.is_set():
                    return
                connection.execute(f"LISTEN {live_channel}")
                retry_index = 0
                self._report({resync_table})
                while not self._stop_event.is_set():
                    notifications = connection.notifies(timeout=self._batch_s)
                    if self._stop_event.is_set():
                        break
                    tables = {
                        notification.payload
                        for notification in notifications
                        if isinstance(getattr(notification, "payload", None), str)
                        and _payload_pattern.fullmatch(notification.payload)
                    }
                    if tables:
                        self._report(tables)
            except Exception as error:
                error_name = type(error).__name__
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
            if error_name is not None:
                if self._stop_event.is_set():
                    return
                _logger.warning("PostgreSQL-Listener fehlgeschlagen (%s)", error_name)
                delay = self._retry_delays_s[min(retry_index, len(self._retry_delays_s) - 1)]
                retry_index = min(retry_index + 1, len(self._retry_delays_s) - 1)
                if self._stop_event.wait(delay):
                    return
