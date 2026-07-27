"""Faithful port of lib/attendance-calc.ts -- core attendance math."""
from typing import TypedDict


class AttendanceTotals(TypedDict):
    scheduledHours: float
    attendedHours: float
    authorisedAbsenceHours: float
    unauthorisedAbsenceHours: float
    lateCount: int
    sessionCount: int
    attendancePercentage: float


# A record only counts toward the learner's schedule when they were expected
# to be there. "not_expected" (e.g. not yet allocated to the cohort),
# "withdrawn" (recorded after the learner left the programme), and "bil"
# (a formal break in learning) are excluded.
def _is_scheduled(status: str) -> bool:
    return status not in ("not_expected", "withdrawn", "bil")


def compute_attendance_totals(records: list[dict]) -> AttendanceTotals:
    totals: AttendanceTotals = {
        "scheduledHours": 0.0,
        "attendedHours": 0.0,
        "authorisedAbsenceHours": 0.0,
        "unauthorisedAbsenceHours": 0.0,
        "lateCount": 0,
        "sessionCount": 0,
        "attendancePercentage": 0.0,
    }

    for record in records:
        status = record["status"]
        if not _is_scheduled(status):
            continue

        planned = float(record["plannedDurationHours"])
        hours_attended = float(record["hoursAttended"])

        totals["sessionCount"] += 1
        totals["scheduledHours"] += planned

        if status in ("present", "late"):
            totals["attendedHours"] += hours_attended
        if status == "late":
            totals["lateCount"] += 1
        if status == "absent_authorised":
            totals["authorisedAbsenceHours"] += planned
        if status == "absent_unauthorised":
            totals["unauthorisedAbsenceHours"] += planned

    if totals["scheduledHours"] > 0:
        totals["attendancePercentage"] = (
            round((totals["attendedHours"] / totals["scheduledHours"]) * 1000) / 10
        )

    return totals
