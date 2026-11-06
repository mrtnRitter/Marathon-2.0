"""Start the Marathon server: python -m marathon.server"""

# IMPORTS
import sys

import uvicorn

from marathon.common.version import app_name, app_version
from marathon.server.app import create_app
from marathon.server.config import ConfigError, load_server_config
from marathon.server.ui import default_static_dir


# MAIN
def main() -> int:
    try:
        config = load_server_config()
    except ConfigError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    if not (default_static_dir / "index.html").is_file():
        print("Fehler: Oberfläche ist nicht gebaut. Zuerst python build.py ausführen.", file=sys.stderr)
        return 1
    print(f"{app_name} {app_version} läuft auf http://{config.host}:{config.port}  (Beenden mit Strg+C)")
    uvicorn.run(create_app(), host=config.host, port=config.port, log_level="warning")
    return 0


# EXEC
if __name__ == "__main__":
    sys.exit(main())
