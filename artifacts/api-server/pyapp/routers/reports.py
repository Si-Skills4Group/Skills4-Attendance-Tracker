from fastapi import APIRouter, Depends, HTTPException

from ..attendance_calc import compute_attendance_totals
from ..attendance_data import (
    get_records_for_cohort,
    get_records_for_learner,
    get_records_for_organisation,
    get_records_for_tutor,
)
from ..auth import require_admin, require_auth
from ..csv_utils import stringify_rows_to_csv
from ..db import get_cursor
from ..learners_query import LEARNERS_WITH_NAMES_SELECT
from .cohorts import COHORT_SELECT
from .tutors import TUTOR_SELECT

router = APIRouter(tags=["reports"])


def _date_params(dateFrom: str | None, dateTo: str | None):
    return dateFrom, dateTo


@router.get("/reports/learner/{learner_id}")
def get_learner_report(learner_id: int, dateFrom: str | None = None, dateTo: str | None = None, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.id = %s", (learner_id,))
        learner = cur.fetchone()
    if not learner:
        raise HTTPException(status_code=404, detail="Learner not found")
    if session.get("role") == "tutor" and learner["tutorId"] != session.get("tutorId"):
        raise HTTPException(status_code=403, detail="Not allowed to view this learner")

    records = get_records_for_learner(learner_id, dateFrom, dateTo)
    return {"learner": learner, "totals": compute_attendance_totals(records)}


@router.get("/reports/cohort/{cohort_id}")
def get_cohort_report(cohort_id: int, dateFrom: str | None = None, dateTo: str | None = None, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        cur.execute(f"{COHORT_SELECT} WHERE c.id = %s", (cohort_id,))
        cohort = cur.fetchone()
        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found")
        if session.get("role") == "tutor" and cohort["tutorId"] != session.get("tutorId"):
            raise HTTPException(status_code=403, detail="Not allowed to view this cohort")

        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.cohort_id = %s", (cohort_id,))
        learners = cur.fetchall()

    records = get_records_for_cohort(cohort_id, dateFrom, dateTo)
    totals = compute_attendance_totals(records)

    breakdown = []
    for learner in learners:
        learner_records = get_records_for_learner(learner["id"], dateFrom, dateTo)
        breakdown.append(
            {
                "learnerId": learner["id"],
                "learnerName": f"{learner['firstName']} {learner['lastName']}",
                "learnerRef": learner["learnerRef"],
                "totals": compute_attendance_totals(learner_records),
            }
        )

    return {"cohort": cohort, "totals": totals, "learnerBreakdown": breakdown}


@router.get("/reports/tutor/{tutor_id}")
def get_tutor_report(tutor_id: int, dateFrom: str | None = None, dateTo: str | None = None, session: dict = Depends(require_auth)):
    if session.get("role") == "tutor" and session.get("tutorId") != tutor_id:
        raise HTTPException(status_code=403, detail="Not allowed to view this tutor")

    with get_cursor() as cur:
        cur.execute(f"{TUTOR_SELECT} WHERE id = %s", (tutor_id,))
        tutor = cur.fetchone()
        if not tutor:
            raise HTTPException(status_code=404, detail="Tutor not found")

        cur.execute(f"{COHORT_SELECT} WHERE c.tutor_id = %s", (tutor_id,))
        cohorts = cur.fetchall()

    records = get_records_for_tutor(tutor_id, dateFrom, dateTo)
    totals = compute_attendance_totals(records)

    breakdown = []
    for cohort in cohorts:
        cohort_records = get_records_for_cohort(cohort["id"], dateFrom, dateTo)
        breakdown.append(
            {"cohortId": cohort["id"], "cohortName": cohort["name"], "totals": compute_attendance_totals(cohort_records)}
        )

    return {"tutor": tutor, "totals": totals, "cohortBreakdown": breakdown}


@router.get("/reports/organisation")
def get_organisation_report(
    dateFrom: str | None = None,
    dateTo: str | None = None,
    programme: str | None = None,
    _session: dict = Depends(require_admin),
):
    records = get_records_for_organisation(dateFrom, dateTo, programme)
    totals = compute_attendance_totals(records)

    with get_cursor() as cur:
        cur.execute(f"{COHORT_SELECT}")
        cohorts = cur.fetchall()
        cur.execute("SELECT DISTINCT programme FROM cohorts")
        programmes = [r["programme"] for r in cur.fetchall()]

    programme_breakdown = []
    for programme in programmes:
        p_records = get_records_for_organisation(dateFrom, dateTo, programme)
        programme_breakdown.append({"programme": programme, "totals": compute_attendance_totals(p_records)})

    cohort_breakdown = []
    for cohort in cohorts:
        c_records = get_records_for_cohort(cohort["id"], dateFrom, dateTo)
        cohort_breakdown.append(
            {"cohortId": cohort["id"], "cohortName": cohort["name"], "totals": compute_attendance_totals(c_records)}
        )

    return {"totals": totals, "programmeBreakdown": programme_breakdown, "cohortBreakdown": cohort_breakdown}


@router.get("/reports/programme")
def get_programme_report(
    dateFrom: str | None = None, dateTo: str | None = None, _session: dict = Depends(require_admin)
):
    records = get_records_for_organisation(dateFrom, dateTo)
    programmes = sorted({r["programme"] for r in records})
    rows = []
    for programme in programmes:
        p_records = [r for r in records if r["programme"] == programme]
        rows.append({"programme": programme, "totals": compute_attendance_totals(p_records)})
    return rows


EXPORT_REPORT_TYPES = {"learner", "cohort", "tutor", "organisation", "programme", "allocation-history"}


@router.get("/reports/export")
def export_report(
    reportType: str,
    entityId: int | None = None,
    dateFrom: str | None = None,
    dateTo: str | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    programme: str | None = None,
    status: str | None = None,
    session: dict = Depends(require_auth),
):
    if reportType not in EXPORT_REPORT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"reportType must be one of: {', '.join(sorted(EXPORT_REPORT_TYPES))}",
        )

    # Tutors may only export their own tutor report or a cohort/learner that
    # belongs to them. Organisation-wide, programme, and allocation-history
    # exports remain admin-only.
    if session.get("role") == "tutor":
        if reportType == "tutor":
            if entityId != session.get("tutorId"):
                raise HTTPException(status_code=403, detail="Not allowed to export this tutor's report")
        elif reportType == "cohort" and entityId:
            with get_cursor() as cur:
                cur.execute("SELECT tutor_id AS \"tutorId\" FROM cohorts WHERE id = %s", (entityId,))
                cohort = cur.fetchone()
            if not cohort or cohort["tutorId"] != session.get("tutorId"):
                raise HTTPException(status_code=403, detail="Not allowed to export this cohort's report")
        elif reportType == "learner" and entityId:
            with get_cursor() as cur:
                cur.execute("SELECT tutor_id AS \"tutorId\" FROM learners WHERE id = %s", (entityId,))
                learner = cur.fetchone()
            if not learner or learner["tutorId"] != session.get("tutorId"):
                raise HTTPException(status_code=403, detail="Not allowed to export this learner's report")
        else:
            raise HTTPException(status_code=403, detail="Administrator access required")

    filename = f"{reportType}-report.csv"
    rows: list[dict] = []

    if reportType == "learner" and entityId:
        records = get_records_for_learner(entityId, dateFrom, dateTo)
        rows = [compute_attendance_totals(records)]
    elif reportType == "cohort" and entityId:
        records = get_records_for_cohort(entityId, dateFrom, dateTo)
        rows = [compute_attendance_totals(records)]
    elif reportType == "tutor" and entityId:
        records = get_records_for_tutor(entityId, dateFrom, dateTo)
        rows = [compute_attendance_totals(records)]
    elif reportType == "programme":
        records = get_records_for_organisation(dateFrom, dateTo)
        programmes = sorted({r["programme"] for r in records})
        rows = [
            {"programme": p, **compute_attendance_totals([r for r in records if r["programme"] == p])}
            for p in programmes
        ]
    else:
        records = get_records_for_organisation(dateFrom, dateTo, programme)
        rows = [compute_attendance_totals(records)]

    csv = stringify_rows_to_csv(rows, list(rows[0].keys()) if rows else [])
    return {"csv": csv, "filename": filename}
