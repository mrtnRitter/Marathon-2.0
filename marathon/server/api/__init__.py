"""Versioned API routers. Add new routers to api_routers."""

from marathon.server.api import system

api_prefix = "/api/v1"
api_routers = (system.router,)
