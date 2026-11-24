"""Versioned API routers. Add new routers to api_routers."""

from marathon.server.api import assets, auth, detail, events, fs, read, report, settings, system, worker, workers, workflows

api_prefix = "/api/v1"
api_routers = (system.router, read.router, detail.router, report.router, workflows.router, assets.router, workers.router, worker.router, fs.router, events.router, settings.router, auth.router)
