"""Central Bud status_desc -> internal learners.status mapping (Phase 11
refinement). The single place Bud status strings are ever compared --
nothing else in this codebase should do a raw status_desc string check.

Confirmed real production status_desc values: 'In Progress', 'On Break',
'Withdrawn', 'Completed', 'Pending', 'In End Point Assessment',
'Break Requested', 'Break Return Requested', 'Withdrawal Requested'.
"BIL" (Break in Learning) is the Administrator's shorthand for 'On Break' --
not a literal Bud string.

Only statuses with a confirmed, agreed internal meaning are mapped. Nothing
here is invented -- status_desc values absent from BUD_STATUS_TO_LEARNER_STATUS
are deliberately treated as informational-only signals, never as a driver of
an actual learners.status change.
"""
from __future__ import annotations

from typing import Literal, TypedDict

LearnerStatus = Literal["active", "paused", "withdrawn", "completed"]

BUD_STATUS_TO_LEARNER_STATUS: dict[str, LearnerStatus] = {
    "In Progress": "active",
    "In End Point Assessment": "active",
    "On Break": "paused",
    "Withdrawn": "withdrawn",
    "Completed": "completed",
}

# Bud has reported an intent, but the change isn't effective yet -- Bud
# itself hasn't finalised it, so this trial never changes learners.status
# from these signals. Surfaced as an informational warning only.
INFORMATIONAL_STATUS_DESC = {"Pending", "Break Requested", "Break Return Requested", "Withdrawal Requested"}

# learners.status values that require an effective date the internal
# change_learner_status service will reject the change without -- Bud
# supplies neither withdrawalDate nor actualEndDate, so these transitions
# can never be classified "automatic".
REQUIRES_EFFECTIVE_DATE: dict[LearnerStatus, str] = {
    "withdrawn": "withdrawalDate",
    "completed": "actualEndDate",
}

TransitionKind = Literal["no_change", "automatic", "needs_date", "informational", "unrecognised"]


class StatusTransition(TypedDict):
    kind: TransitionKind
    targetStatus: LearnerStatus | None
    dateField: str | None


def classify_status_transition(current_learner_status: str, bud_status_desc: str | None) -> StatusTransition:
    """Pure, DB-free classification -- given a learner's current internal
    status and the Bud status_desc just observed, decide what (if anything)
    should happen. Never guesses an effective date; never invents business
    data Bud didn't supply."""
    if not bud_status_desc:
        return {"kind": "no_change", "targetStatus": None, "dateField": None}

    if bud_status_desc in INFORMATIONAL_STATUS_DESC:
        return {"kind": "informational", "targetStatus": None, "dateField": None}

    target = BUD_STATUS_TO_LEARNER_STATUS.get(bud_status_desc)
    if target is None:
        return {"kind": "unrecognised", "targetStatus": None, "dateField": None}

    if target == current_learner_status:
        return {"kind": "no_change", "targetStatus": target, "dateField": None}

    date_field = REQUIRES_EFFECTIVE_DATE.get(target)
    if date_field is not None:
        return {"kind": "needs_date", "targetStatus": target, "dateField": date_field}

    return {"kind": "automatic", "targetStatus": target, "dateField": None}
