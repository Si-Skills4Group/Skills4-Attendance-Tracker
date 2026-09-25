"""Stage 2, item 6: admin-only reverse-presence exceptions for previously-
linked, internally active learners whose linked Bud learning-plan row has
gone missing from the source. See bud_missing_source_lib.py's module
docstring.

Stage 2 verification pass, item 1: GET is read-only -- it never triggers
detection. Detection only ever runs via the explicit, admin-triggered POST
/refresh endpoint (this app has no cron/background worker at all, by
established design -- see main.py's own "lazy check" comments -- so an
explicit admin action is the consistent choice here too, not a new
pattern invented for this endpoint)."""
from fastapi import APIRouter, Depends, Request

from ..allocation_reconciliation_lib import fetch_source_info
from ..auth import require_admin
from ..bud_missing_source_lib import (
    get_missing_source_refresh_status,
    list_missing_source_exceptions,
    refresh_missing_source_exceptions,
)
from ..db import get_cursor

router = APIRouter(tags=["bud-missing-source"])


@router.get("/bud-missing-source/exceptions")
def get_missing_source_exceptions(status: str | None = "open", _session: dict = Depends(require_admin)):
    """Read-only: lists whatever the last refresh detected, alongside
    source-freshness/completeness signals (fetch_source_info -- this app
    cannot verify the external Bud extract was complete or up to date, so
    that limitation must be visible here too) and the refresh status
    itself (when detection last ran, and whether it last succeeded).
    Performs NO writes -- verified by test_get_exceptions_performs_no_writes."""
    with get_cursor() as cur:
        items = list_missing_source_exceptions(cur, status)
        source_info = fetch_source_info(cur)
        refresh_status = get_missing_source_refresh_status(cur)
        return {"items": items, "sourceInfo": source_info, "refreshStatus": refresh_status}


@router.post("/bud-missing-source/refresh")
def post_missing_source_refresh(request: Request, session: dict = Depends(require_admin)):
    """Runs detection now (see refresh_missing_source_exceptions) and
    returns the updated refresh status. A failure here is reported to the
    caller but leaves the last successful results, and the exceptions
    themselves, completely untouched."""
    with get_cursor() as cur:
        refresh_missing_source_exceptions(cur, request, session)
        return get_missing_source_refresh_status(cur)
