# IMPORTS
import argparse
import logging
import os
from pathlib import Path
import signal
import sys
import threading

from marathon.common.version import app_version as package_version
from marathon.modules._job import ToolMissing
from marathon.worker.runtime import WorkerRuntime, worker_capabilities
from marathon.worker.tls import create_ssl_context


# CONFIG
app_name = "marathon-worker"
app_version = package_version


# MAIN
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=app_name)
    parser.add_argument("--server", required=True)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--exit-on-stdin-eof", action="store_true")
    parser.add_argument("--tools-dir", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not args.server.casefold().startswith("https://"):
        logging.getLogger("marathon.worker").error("Server-Adresse muss mit https:// beginnen")
        return 2
    token = os.environ.get("MARATHON_WORKER_TOKEN")
    if not token:
        logging.getLogger("marathon.worker").error("MARATHON_WORKER_TOKEN fehlt")
        return 2
    stop = threading.Event()
    previous_handlers: dict[int, object] = {}

    def request_stop(_: int, __: object) -> None:
        stop.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, signal_name, None)
        if signum is None:
            continue
        try:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        except ValueError:
            continue

    if args.exit_on_stdin_eof:
        def watch_stdin() -> None:
            try:
                sys.stdin.read()
            finally:
                stop.set()

        threading.Thread(target=watch_stdin, name="marathon-worker-stdin", daemon=True).start()

    try:
        ssl_context = create_ssl_context(args.ca_file)
        def resolve_tool(name: str) -> Path:
            if args.tools_dir is None or not args.tools_dir.is_dir():
                raise ToolMissing(f"Zusatzprogramm fehlt: {name}")
            return args.tools_dir / name

        runtime = WorkerRuntime(
            args.server,
            token,
            args.worker_id,
            args.name,
            worker_capabilities(),
            ssl_context=ssl_context,
            tool_path=resolve_tool if args.tools_dir is not None else None,
        )
        return runtime.run(stop)
    except ValueError as error:
        logging.getLogger("marathon.worker").error("Ungültige Worker-Konfiguration: %s", error)
        return 2
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except ValueError:
                pass


# EXEC
if __name__ == "__main__":
    raise SystemExit(main())
