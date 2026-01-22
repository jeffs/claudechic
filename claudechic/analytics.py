"""PostHog analytics for claudechic - fire-and-forget event tracking."""

import os
import platform
import shutil
import uuid as uuid_mod
from datetime import datetime, timezone

import httpx

from claudechic.config import get_analytics_enabled, get_analytics_id

VERSION = "0.1.0"  # Keep in sync with __init__.py
SESSION_ID = str(uuid_mod.uuid4())  # Unique per process

POSTHOG_HOST = "https://us.i.posthog.com"
POSTHOG_API_KEY = "phc_M0LMkbSaDsaXi5LeYE5A95Kz8hTHgsJ4POlqucehsse"


async def capture(event: str, **properties: str | int | float | bool) -> None:
    """Capture an analytics event to PostHog.

    Fire-and-forget: failures are silently ignored.
    Respects analytics opt-out setting.
    """
    if not get_analytics_enabled():
        return

    # Build properties - session_id on all events, context only on app_started
    props: dict = {"$session_id": SESSION_ID, **properties}

    if event in ("app_started", "app_installed"):
        # Include version and environment context on session start and install
        props["claudechic_version"] = VERSION
        try:
            term_size = os.get_terminal_size()
            props["term_width"] = term_size.columns
            props["term_height"] = term_size.lines
        except OSError:
            pass
        props["term_program"] = os.environ.get("TERM_PROGRAM", "unknown")
        props["os"] = platform.system()
        props["has_uv"] = shutil.which("uv") is not None
        props["has_conda"] = shutil.which("conda") is not None

    payload = {
        "api_key": POSTHOG_API_KEY,
        "event": event,
        "distinct_id": get_analytics_id(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "properties": props,
    }

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{POSTHOG_HOST}/capture/",
                json=payload,
                timeout=5.0,
            )
    except Exception:
        pass  # Silent failure - analytics should never impact user experience
