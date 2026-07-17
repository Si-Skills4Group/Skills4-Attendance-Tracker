"""Per-row attendance business rules (Phase 7): status-specific field
requirements, excess-hours override permission, and historical-edit-reason
detection. Kept separate from session_register_lib.py (session/register
lifecycle) since this is squarely about individual attendance row content,
not the session/register itself.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, TypedDict

AttendanceStatus = Literal[
    "present", "absent_authorised", "absent_unauthorised", "late", "not_expected", "withdrawn"
]

# Statuses where the learner did not accrue attendance time -- hours and
# minutes-late must be exactly zero, never left to a stray nonzero value.
ZERO_HOURS_STATUSES = {"absent_authorised", "absent_unauthorised", "not_expected", "withdrawn"}

_TRACKED_FIELDS = ("status", "hoursAttended", "minutesLate", "notes")
_MATERIAL_FIELDS = {"status", "hoursAttended"}


class RowError(TypedDict):
    learnerId: int
    field: str
    message: str


def validate_entry(
    *,
    learner_id: int,
    status: str,
    hours_attended: float,
    minutes_late: int,
    override_reason: str | None,
    planned_hours: float,
    is_admin: bool,
    check_override: bool = True,
) -> list[RowError]:
    """Returns the list of row errors (empty if the entry is valid). Never
    raises -- callers collect errors across every row in the register and
    decide how to respond as a whole (atomic all-or-nothing save).

    check_override=False skips the excess-hours-needs-admin-approval branch
    -- that gate belongs at write time (when new hours are being submitted),
    not when revalidating data that was already legitimately written and
    audited (e.g. register-completion revalidation), since an admin-approved
    excess-hours value stored in the database is not itself invalid."""
    errors: list[RowError] = []

    def err(field: str, message: str) -> None:
        errors.append({"learnerId": learner_id, "field": field, "message": message})

    if status == "late" and minutes_late <= 0:
        err("minutesLate", "Minutes late is required when status is Late")

    if status in ZERO_HOURS_STATUSES:
        if hours_attended != 0:
            err("hoursAttended", f"Hours attended must be zero when status is {status}")
        if minutes_late != 0:
            err("minutesLate", f"Minutes late must be zero when status is {status}")
    elif check_override:
        # Excess-hours/minutes override only makes sense for statuses where
        # nonzero values are legitimate in the first place (present/late).
        planned_minutes = planned_hours * 60
        exceeds_hours = hours_attended > planned_hours
        exceeds_minutes = status == "late" and minutes_late > planned_minutes
        if exceeds_hours or exceeds_minutes:
            if not is_admin:
                err(
                    "hoursAttended" if exceeds_hours else "minutesLate",
                    "Only an Administrator can approve hours or minutes exceeding the planned session duration",
                )
            elif not override_reason or not override_reason.strip():
                err(
                    "overrideReason",
                    "An override reason is required when hours attended exceeds the planned session duration",
                )

    return errors


def is_historical_save(register_status: str, session_date: date, today: date | None = None) -> bool:
    """A save is "historical" if the register is already completed/locked,
    or the session itself is in the past -- either way, requirement 13's
    change-reason requirement applies."""
    if today is None:
        today = date.today()
    return register_status in ("completed", "locked") or session_date < today


def diff_entry(existing: dict | None, new_values: dict) -> dict:
    """existing/new_values are both keyed by status/hoursAttended/minutesLate/notes.
    Returns {field: {before, after}} for every field that changed. A learner
    with no prior attendance_records row (existing=None) returns an empty
    diff -- recording someone's first-ever attendance isn't a "change" to
    require a reason for, it's just filling in a gap (e.g. after a
    register-refresh added them); callers track first-time rows separately."""
    if existing is None:
        return {}
    changes: dict = {}
    for field in _TRACKED_FIELDS:
        before = existing.get(field)
        after = new_values.get(field)
        if field == "hoursAttended" and before is not None:
            before = float(before)
        if before != after:
            changes[field] = {"before": before, "after": after}
    return changes


def requires_change_reason(diff: dict) -> bool:
    return bool(_MATERIAL_FIELDS & diff.keys())
