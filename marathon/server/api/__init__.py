"""Versioned API routers. Add new routers to api_routers."""

from marathon.server.api import detail, read, report, system

api_prefix = "/api/v1"
api_routers = (system.router, read.router, detail.router, report.router)
