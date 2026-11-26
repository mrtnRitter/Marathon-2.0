# IMPORTS
import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any


# CONFIG
_APP_NAME = "Marathon-Worker"


# INIT
main_log = logging.getLogger(__name__)


# FUNC
def status_text(state: Mapping[str, Any]) -> str:
    """Format the worker state for the tray menu and tooltip."""
    message = state.get("message")
    if message:
        return str(message)
    if state.get("connected") is False:
        return "Keine Verbindung zum Server"
    status = state.get("status")
    if status is None:
        return "Verbinde …"
    if state.get("approved") is False:
        return "Wartet auf Freigabe"
    if status == "stopped":
        return "Gestoppt"
    if status == "stopping":
        return "Wird sofort gestoppt"
    if status == "draining":
        return "Wird gestoppt"
    if status == "ready" and state.get("available"):
        running = int(state.get("running") or 0)
        if running > 0:
            noun = "Job läuft" if running == 1 else "Jobs laufen"
            return f"Bereit · {running} {noun}"
        return "Bereit"
    if status == "ready":
        reason = str(state.get("reason") or "").strip()
        return f"Bereit · {reason}" if reason else "Bereit · nicht verfügbar"
    return "Verbinde …"


def _status_color(state: Mapping[str, Any]) -> tuple[int, int, int]:
    if state.get("connected") is False:
        return (205, 48, 48)
    status = state.get("status")
    if status == "stopped" or state.get("approved") is False:
        return (128, 128, 128)
    if status in ("stopping", "draining"):
        return (232, 145, 35)
    if status == "ready":
        return (40, 155, 75) if state.get("available") else (232, 145, 35)
    return (128, 128, 128)


class TrayController:
    """Manage the worker tray icon and its thread-safe status updates."""

    def __init__(
        self,
        on_open_settings: Callable[[], None],
        on_quit: Callable[[], None],
        icon_factory: Callable[..., Any] | None = None,
    ) -> None:
        """Create a tray controller without importing optional UI dependencies."""
        self._on_open_settings = on_open_settings
        self._on_quit = on_quit
        self._icon_factory = icon_factory
        self._lock = threading.RLock()
        self._status: dict[str, Any] = {}
        self._icon: Any | None = None
        self._image_modules: tuple[Any, Any] | None = None
        self._stopped = False
        self._run_started = False
        self._settings_running = False
        self._quit_running = False

    def _tooltip(self) -> str:
        return f"{_APP_NAME} – {status_text(self._status)}"

    def _make_image(self, image_module: Any, draw_module: Any) -> Any:
        image = image_module.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw_module.Draw(image).ellipse((6, 6, 58, 58), fill=_status_color(self._status))
        return image

    def _open_settings(self, _icon: Any, _item: Any) -> None:
        with self._lock:
            if self._stopped or self._settings_running:
                return
            self._settings_running = True

        def invoke() -> None:
            try:
                self._on_open_settings()
            except Exception:
                main_log.exception("Unable to open worker settings")
            finally:
                with self._lock:
                    self._settings_running = False

        thread = threading.Thread(target=invoke, name="marathon-worker-settings", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            with self._lock:
                self._settings_running = False
            main_log.exception("Unable to start worker settings action")

    def _quit(self, _icon: Any, _item: Any) -> None:
        with self._lock:
            if self._stopped or self._quit_running:
                return
            self._quit_running = True
            self._stopped = True
            icon = self._icon

        if icon is not None:
            try:
                icon.stop()
            except Exception:
                main_log.exception("Unable to stop tray icon")

        def invoke() -> None:
            try:
                self._on_quit()
            except Exception:
                main_log.exception("Unable to stop worker")
            finally:
                with self._lock:
                    self._quit_running = False

        thread = threading.Thread(target=invoke, name="marathon-worker-quit", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            with self._lock:
                self._quit_running = False
            main_log.exception("Unable to start worker shutdown action")

    def run(self) -> None:
        """Run the tray icon on the calling thread until it is stopped."""
        import pystray
        from PIL import Image, ImageDraw

        with self._lock:
            if self._stopped or self._run_started:
                return
            self._run_started = True
            image_modules = (Image, ImageDraw)
            image = self._make_image(*image_modules)
            menu = pystray.Menu(
                pystray.MenuItem("Worker-Einstellungen öffnen", self._open_settings, default=True),
                pystray.MenuItem(
                    lambda _icon, _item: f"Status: {status_text(self._status)}",
                    None,
                    enabled=False,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Beenden", self._quit),
            )
            factory = self._icon_factory if self._icon_factory is not None else pystray.Icon
            icon = factory(_APP_NAME, image, self._tooltip(), menu)
            self._icon = icon
            self._image_modules = image_modules

        try:
            icon.run()
        finally:
            with self._lock:
                self._run_started = False
                self._stopped = True

    def set_status(self, state: Mapping[str, Any]) -> None:
        """Store a status and refresh a running icon unless it has been stopped."""
        with self._lock:
            if self._stopped:
                return
            self._status = dict(state)
            if self._icon is None or self._image_modules is None:
                return
            self._icon.title = self._tooltip()
            self._icon.icon = self._make_image(*self._image_modules)
            self._icon.update_menu()

    def stop(self) -> None:
        """Stop the icon and ignore any subsequent status updates."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            icon = self._icon
        if icon is not None:
            icon.stop()
