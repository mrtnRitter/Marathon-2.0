import threading
import types

import pytest

from marathon.client.tray import TrayController, status_text


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"message": "Anmeldung erforderlich", "connected": False}, "Anmeldung erforderlich"),
        ({"message": "", "connected": False}, "Keine Verbindung zum Server"),
        ({"connected": False}, "Keine Verbindung zum Server"),
        ({"connected": True, "status": None}, "Verbinde …"),
        ({"status": "idle", "approved": False}, "Wartet auf Freigabe"),
        ({"status": "stopped", "approved": True}, "Gestoppt"),
        ({"status": "stopping", "approved": True}, "Wird sofort gestoppt"),
        ({"status": "draining", "approved": True}, "Wird gestoppt"),
        ({"status": "ready", "approved": True, "available": True}, "Bereit"),
        ({"status": "ready", "approved": True, "available": True, "running": 1}, "Bereit · 1 Job läuft"),
        ({"status": "ready", "approved": True, "available": True, "running": 3}, "Bereit · 3 Jobs laufen"),
        ({"status": "ready", "approved": True, "available": False, "reason": "CPU-Last zu hoch"}, "Bereit · CPU-Last zu hoch"),
        ({"status": "ready", "approved": True, "available": False}, "Bereit · nicht verfügbar"),
    ],
)
def test_status_text_rules(state, expected):
    assert status_text(state) == expected


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


class FakeMenuItem:
    def __init__(self, text, action=None, **kwargs):
        self.text = text
        self.action = action
        self.kwargs = kwargs


FakePystray = types.ModuleType("pystray")
FakePystray.Menu = FakeMenu
FakePystray.MenuItem = FakeMenuItem
FakePystray.Icon = object


class FakeIcon:
    def __init__(self, name, icon, title, menu):
        self.name = name
        self.icon = icon
        self.title = title
        self.menu = menu
        self.stopped = False
        self.run_called = False
        self.update_count = 0
        self.run_started = threading.Event()
        self.run_release = threading.Event()

    def run(self):
        self.run_called = True
        self.run_started.set()
        self.run_release.wait(5)

    def stop(self):
        self.stopped = True
        self.run_release.set()

    def update_menu(self):
        self.update_count += 1


def test_tray_menu_status_tooltip_and_quit(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pystray", FakePystray)
    opened, quit_called = [], []
    quit_finished = threading.Event()
    icons = []
    icon_created = threading.Event()

    def factory(name, image, title, menu):
        icon = FakeIcon(name, image, title, menu)
        icons.append(icon)
        icon_created.set()
        return icon

    def on_quit():
        quit_called.append(True)
        quit_finished.set()

    controller = TrayController(lambda: opened.append(True), on_quit, factory)
    controller.set_status({"status": "ready", "approved": True, "available": True})
    run_thread = threading.Thread(target=controller.run)
    run_thread.start()
    assert icon_created.wait(1)
    icon = icons[0]
    assert icon.run_started.wait(1)
    assert icon.title == "Marathon-Worker – Bereit"
    assert icon.icon.size == (64, 64)
    assert icon.icon.getpixel((32, 32)) == (40, 155, 75, 255)
    assert icon.menu.items[0].text == "Worker-Einstellungen öffnen"
    assert icon.menu.items[0].kwargs["default"] is True
    assert icon.menu.items[1].text(icon.menu.items[1]) == "Status: Bereit"
    assert icon.menu.items[1].kwargs["enabled"] is False
    assert icon.menu.items[2] is FakeMenu.SEPARATOR
    assert icon.menu.items[3].text == "Beenden"

    controller.set_status({"connected": False})
    assert icon.title == "Marathon-Worker – Keine Verbindung zum Server"
    assert icon.menu.items[1].text(icon.menu.items[1]) == "Status: Keine Verbindung zum Server"
    assert icon.icon.getpixel((32, 32)) == (205, 48, 48, 255)

    for state, color in (
        ({"status": "ready", "approved": True, "available": False}, (232, 145, 35, 255)),
        ({"status": "draining", "approved": True}, (232, 145, 35, 255)),
        ({"status": "stopped", "approved": True}, (128, 128, 128, 255)),
        ({"status": "ready", "approved": False}, (128, 128, 128, 255)),
    ):
        controller.set_status(state)
        assert icon.icon.getpixel((32, 32)) == color

    icon.menu.items[3].action(icon, icon.menu.items[3])
    assert quit_finished.wait(1)
    assert quit_called == [True]
    assert icon.stopped
    previous_title = icon.title
    controller.set_status({"status": "ready", "approved": True, "available": True})
    assert icon.title == previous_title
    run_thread.join(1)
    assert not run_thread.is_alive()


def test_open_settings_uses_one_daemon_thread_and_ignores_duplicate_click(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pystray", FakePystray)
    started = threading.Event()
    release = threading.Event()
    calls = []
    icon_box = []
    icon_created = threading.Event()

    def factory(name, image, title, menu):
        icon = FakeIcon(name, image, title, menu)
        icon_box.append(icon)
        icon_created.set()
        return icon

    def open_settings():
        calls.append(threading.current_thread())
        started.set()
        release.wait(2)

    controller = TrayController(open_settings, lambda: None, factory)
    run_thread = threading.Thread(target=controller.run)
    run_thread.start()
    assert icon_created.wait(1)
    assert icon_box[0].run_started.wait(1)
    item = icon_box[0].menu.items[0]
    item.action(icon_box[0], item)
    assert started.wait(1)
    item.action(icon_box[0], item)
    assert len(calls) == 1
    assert calls[0] is not threading.current_thread()
    assert calls[0].daemon
    release.set()
    calls[0].join(1)
    controller.stop()
    run_thread.join(1)
    assert not run_thread.is_alive()


def test_quit_action_returns_while_shutdown_callback_is_waiting():
    release = threading.Event()
    callback_started = threading.Event()
    callback_threads = []

    def on_quit():
        callback_threads.append(threading.current_thread())
        callback_started.set()
        release.wait(2)

    controller = TrayController(lambda: None, on_quit)
    action = threading.Thread(target=controller._quit, args=(None, None))
    action.start()
    try:
        action.join(0.1)
        assert not action.is_alive()
        assert callback_started.wait(1)
        assert callback_threads[0] is not action
        assert callback_threads[0].daemon
    finally:
        release.set()
        action.join(1)
        if callback_threads:
            callback_threads[0].join(1)
