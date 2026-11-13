"""Versioned API routers. Add new routers to api_routers."""

from marathon.server.api import assets, detail, fs, read, report, system, workflows

api_prefix = "/api/v1"
api_routers = (system.router, read.router, detail.router, report.router, workflows.router, assets.router, fs.router)
