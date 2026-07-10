from __future__ import annotations

from vendor_dd.engine.events import ReportEvent


def to_sse_frame(event: ReportEvent) -> dict[str, str]:
    """Map a ReportEvent to an sse-starlette frame: the event name + JSON payload.
    EventSourceResponse writes this as `event: <type>\\ndata: <json>\\n\\n`."""
    return {"event": event.type, "data": event.model_dump_json()}
