"""Server-sent events for live database changes."""

# IMPORTS
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from marathon.server.live import LiveClosed


# CONFIG
heartbeat_s = 15.0
retry_ms = 2000
router = APIRouter()


# ROUTES
@router.get("/events")
async def get_events(request: Request) -> StreamingResponse:
    """Stream changed table names to the connected client."""
    async def stream():
        subscription = None
        try:
            subscription = request.app.state.live_hub.subscribe()
            yield f"retry: {retry_ms}\n\n"
            while not await request.is_disconnected():
                try:
                    tables = await subscription.next(heartbeat_s)
                except LiveClosed:
                    break
                if tables is None:
                    yield ": ping\n\n"
                else:
                    yield "data: " + json.dumps({"tables": sorted(tables)}) + "\n\n"
        finally:
            if subscription is not None:
                subscription.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
