# IMPORTS
import argparse
import logging
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from marathon.client.app import is_client_running, run_client
from marathon.client.installer import install, is_installed_copy, uninstall, windows_ops
from marathon.client.local_state import bootstrap_file, client_dir, read_bootstrap
from marathon.client.starter import run_starter
from marathon.common.version import app_version as package_version


# CONFIG
app_name = "marathon-worker-client"
app_version = package_version


# INIT
main_log = logging.getLogger(__name__)


def _configure_logging(base: Path) -> None:
    log_dir = base / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "client.log",
            maxBytes=1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()], force=True)
        return
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


# FUNC
class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _bootstrap_path(args: argparse.Namespace, frozen: bool) -> Path:
    if frozen:
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir is None:
            raise ValueError("Client-Paket unvollständig: Bootstrap-Verzeichnis fehlt")
        return Path(bundle_dir) / bootstrap_file
    if args.bootstrap is None:
        raise ValueError("Bitte für den Entwicklungsstart --bootstrap PATH angeben")
    return Path(args.bootstrap)


def _spawn(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# MAIN
def main(argv: list[str] | None = None) -> int:
    """Run the client entry point and return its process exit code."""
    base = client_dir()
    _configure_logging(base)
    frozen = bool(getattr(sys, "frozen", False))
    parser = _ArgumentParser(prog=app_name)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--run", action="store_true", help="Client direkt starten")
    actions.add_argument("--uninstall", action="store_true", help="Client deinstallieren")
    parser.add_argument("--bootstrap", metavar="PATH", help="Bootstrap-Datei (nur Entwicklung)")
    try:
        args = parser.parse_args(argv)
    except ValueError as error:
        main_log.error("Ungültige Startoption: %s", error)
        return 2

    if args.uninstall:
        return uninstall(base, windows_ops(), is_client_running)
    if not frozen and not args.run:
        parser.print_help()
        return 2
    if frozen and args.bootstrap is not None:
        main_log.warning("--bootstrap wird in der eingefrorenen Anwendung ignoriert")

    try:
        bootstrap = read_bootstrap(_bootstrap_path(args, frozen))
    except (OSError, ValueError) as error:
        main_log.exception("Client-Bootstrap konnte nicht geladen werden")
        if frozen:
            windows_ops().message(app_name, str(error), error=True)
        return 2

    if args.run:
        return run_client(base, bootstrap)
    if not is_installed_copy(Path(sys.executable), base):
        return install(Path(sys.executable), bootstrap, base, windows_ops(), is_client_running)
    return run_starter(base, _spawn)


# EXEC
if __name__ == "__main__":
    raise SystemExit(main())
