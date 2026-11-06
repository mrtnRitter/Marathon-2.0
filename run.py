"""Start the Marathon server or the live UI preview."""

# IMPORTS
import argparse
import subprocess
import sys

from build import BuildError, node_env, node_exe, ui_command, root_dir, static_dir, ui_dir, ui_modules_dir, venv_python

# CONFIG
app_name = "marathon-run"
app_version = "2.0.0"

setup_hint = "Zuerst python build.py ausführen."


# FUNC
def start_server() -> int:
    if not venv_python().is_file() or not (static_dir / "index.html").is_file():
        raise BuildError(f"Server ist nicht eingerichtet. {setup_hint}")
    return subprocess.call([str(venv_python()), "-m", "marathon.server"], cwd=root_dir)


def start_dev() -> int:
    if not node_exe().is_file() or not ui_modules_dir.is_dir():
        raise BuildError(f"Node.js oder UI-Bibliotheken fehlen. {setup_hint}")
    return subprocess.call(ui_command("vite"), cwd=ui_dir, env=node_env(), shell=False)


# MAIN
def main() -> int:
    parser = argparse.ArgumentParser(description="Startet den Marathon-Server (Standard) oder die Live-Vorschau der Oberfläche.")
    parser.add_argument("--dev", action="store_true", help="Live-Vorschau der Oberfläche auf http://127.0.0.1:5173")
    args = parser.parse_args()
    try:
        return start_dev() if args.dev else start_server()
    except BuildError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


# EXEC
if __name__ == "__main__":
    sys.exit(main())
