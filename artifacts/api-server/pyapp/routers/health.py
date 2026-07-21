"""Liveness/readiness endpoints. Neither leaks secrets, connection details,
schema internals, or raw exception output -- readiness reports a plain
"unavailable" on DB failure, nothing more specific."""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..bud_progress import get_bud_sync_health
from ..db import get_cursor

logger = logging.getLogger("skills4attendance-api.health")

router = APIRouter()


@router.get("/health/live")
@router.get("/healthz")  # legacy alias -- no Container App probe currently points at this path
def health_live():
    """The process is up and can respond -- no dependency checks. Always
    fast; must not block on the database."""
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready():
    """Can this instance actually serve traffic? Database connectivity is
    the one hard requirement -- Bud (public.learner_progress) is reported
    as a separate, non-blocking "degraded" signal: stale or unreachable Bud
    data must never take core attendance functionality offline."""
    checks: dict[str, str] = {}
    ready = True

    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, never re-raised to the client
        logger.warning("Readiness check: database unavailable: %s", exc)
        checks["database"] = "unavailable"
        ready = False

    try:
        with get_cursor() as cur:
            bud = get_bud_sync_health(cur)
        checks["bud"] = "ok" if bud.get("totalSynced") else "no_data"
    except Exception as exc:  # noqa: BLE001
        logger.info("Readiness check: Bud sync status unavailable (non-blocking): %s", exc)
        checks["bud"] = "degraded"
        # Bud is optional context, not a core dependency -- never fails readiness.

    status_code = 200 if ready else 503
    return JSONResponse(status_code=status_code, content={"status": "ok" if ready else "unavailable", "checks": checks})
