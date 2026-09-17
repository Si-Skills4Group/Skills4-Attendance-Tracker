from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from ..auth import deny_object_access, require_admin, require_auth, require_cohort_access
from ..audit import write_audit_log
from ..db import get_cursor
from ..secondary_enrollment_lib import learner_in_cohort_now_sql
from ..session_register_lib import ensure_expected_learners_snapshots_bulk

router = APIRouter(tags=["cohorts"])

DeliveryDay = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MembershipType = Literal["primary", "secondary"]
CohortSubject = Literal["math", "english", "both"]


def _parse_time(value: str, field: str) -> datetime:
    # Accept both HH:MM and HH:MM:SS -- the frontend normalizes <input
    # type="time"> values to HH:MM:SS before submitting, and existing rows
    # may already store either form (session_start_time/end_time are plain
    # text columns with no format constraint at the DB level).
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail=f"{field} must be in HH:MM format") from None


def _validate_cohort_schedule(
    session_start_time: str | None,
    session_end_time: str | None,
    start_date: date | None,
    end_date: date | None,
) -> None:
    if session_start_time and session_end_time:
        start = _parse_time(session_start_time, "sessionStartTime")
        end = _parse_time(session_end_time, "sessionEndTime")
        if end <= start:
            raise HTTPException(status_code=400, detail="sessionEndTime must be after sessionStartTime")
    if start_date and end_date and end_date < start_date:
        raise HTTPException(status_code=400, detail="endDate cannot be before startDate")


def _check_subject_matches_membership_type(membership_type: str, subject: str | None) -> None:
    """subject only means anything for a Functional Skills ('secondary')
    cohort -- required there (so it can actually be reported on), and
    disallowed on an ordinary ('primary') cohort so a stale/leftover value
    can never linger on a cohort it doesn't apply to."""
    if membership_type == "secondary" and subject is None:
        raise HTTPException(
            status_code=400,
            detail="subject (Math, English, or Both) is required for a Functional Skills cohort",
        )
    if membership_type == "primary" and subject is not None:
        raise HTTPException(status_code=400, detail="subject only applies to a Functional Skills cohort")


def _ensure_tutor_active(cur, tutor_id: int | None) -> None:
    if tutor_id is None:
        return
    cur.execute("SELECT active FROM tutors WHERE id = %s", (tutor_id,))
    tutor = cur.fetchone()
    if not tutor:
        raise HTTPException(status_code=400, detail="Tutor not found")
    if not tutor["active"]:
        raise HTTPException(status_code=400, detail="Cannot assign an inactive tutor to a cohort")

COHORT_SELECT = """
    SELECT c.id, c.name, c.programme, c.level, c.tutor_id AS "tutorId",
           c.delivery_day AS "deliveryDay", c.session_start_time AS "sessionStartTime",
           c.session_end_time AS "sessionEndTime", c.start_date AS "startDate",
           c.end_date AS "endDate", c.active, c.external_system_id AS "externalSystemId",
           c.membership_type AS "membershipType", c.subject,
           c.created_at AS "createdAt", c.updated_at AS "updatedAt",
           CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName"
    FROM cohorts c
    LEFT JOIN tutors t ON c.tutor_id = t.id
"""


class CohortInput(BaseModel):
    name: str = Field(min_length=1)
    programme: str = Field(min_length=1)
    level: str = Field(min_length=1)
    tutorId: int | None = None
    deliveryDay: DeliveryDay
    sessionStartTime: str = Field(min_length=1)
    sessionEndTime: str = Field(min_length=1)
    startDate: date
    endDate: date | None = None
    active: bool = True
    externalSystemId: str | None = None
    membershipType: MembershipType = "primary"
    subject: CohortSubject | None = None

    @model_validator(mode="after")
    def _check_schedule(self) -> "CohortInput":
        _validate_cohort_schedule(self.sessionStartTime, self.sessionEndTime, self.startDate, self.endDate)
        _check_subject_matches_membership_type(self.membershipType, self.subject)
        return self


class CohortUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    programme: str | None = Field(default=None, min_length=1)
    level: str | None = Field(default=None, min_length=1)
    tutorId: int | None = None
    deliveryDay: DeliveryDay | None = None
    sessionStartTime: str | None = Field(default=None, min_length=1)
    sessionEndTime: str | None = Field(default=None, min_length=1)
    startDate: date | None = None
    endDate: date | None = None
    active: bool | None = None
    externalSystemId: str | None = None
    membershipType: MembershipType | None = None
    subject: CohortSubject | None = None


@router.get("/cohorts")
def list_cohorts(
    tutorId: int | None = None,
    active: bool | None = None,
    programme: str | None = None,
    level: str | None = None,
    session: dict = Depends(require_auth),
):
    clauses = ["c.deleted_at IS NULL"]
    params: list = []
    if session.get("role") == "tutor" and session.get("tutorId"):
        clauses.append("c.tutor_id = %s")
        params.append(session["tutorId"])
    elif tutorId is not None:
        clauses.append("c.tutor_id = %s")
        params.append(tutorId)
    if active is not None:
        clauses.append("c.active = %s")
        params.append(active)
    if programme:
        clauses.append("c.programme = %s")
        params.append(programme)
    if level:
        clauses.append("c.level = %s")
        params.append(level)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_cursor() as cur:
        cur.execute(f"{COHORT_SELECT} {where}", params)
        return cur.fetchall()


# Registered before /cohorts/{cohort_id} -- FastAPI/Starlette match routes in
# registration order, and "summary" would otherwise be swallowed as a
# cohort_id path parameter (and fail int-parsing) if that route came first.
@router.get("/cohorts/summary")
def list_cohort_summary(
    tutorId: int | None = None,
    active: bool | None = None,
    programme: str | None = None,
    level: str | None = None,
    session: dict = Depends(require_auth),
):
    """One row per cohort plus summary aggregates for the cohort-cards
    ("Level 1") view -- computed as a handful of grouped queries over the
    filtered cohort_ids, not per-cohort, so this stays O(1) queries
    regardless of how many cohorts are returned."""
    clauses = ["c.deleted_at IS NULL"]
    params: list = []
    if session.get("role") == "tutor" and session.get("tutorId"):
        clauses.append("c.tutor_id = %s")
        params.append(session["tutorId"])
    elif tutorId is not None:
        clauses.append("c.tutor_id = %s")
        params.append(tutorId)
    if active is not None:
        clauses.append("c.active = %s")
        params.append(active)
    if programme:
        clauses.append("c.programme = %s")
        params.append(programme)
    if level:
        clauses.append("c.level = %s")
        params.append(level)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_cursor() as cur:
        cur.execute(f"{COHORT_SELECT} {where}", params)
        cohorts = cur.fetchall()
        cohort_ids = [c["id"] for c in cohorts]

        active_counts: dict[int, int] = {}
        upcoming_counts: dict[int, int] = {}
        outstanding_counts: dict[int, int] = {}

        if cohort_ids:
            # unnest(%s) rather than "cohort_id = ANY(%s)" -- a learner's
            # active count must be attributed per specific cohort (their
            # home cohort, or one of possibly several secondary
            # enrollments), so each cohort_id in the filter list is checked
            # against every learner individually via learner_in_cohort_now_sql.
            cur.execute(
                f"""
                SELECT cid AS "cohortId", count(*)::int AS count
                FROM unnest(%s) AS cid
                JOIN learners ON learners.status = 'active' AND learners.deleted_at IS NULL
                    AND {learner_in_cohort_now_sql("learners", "cid")}
                GROUP BY cid
                """,
                (cohort_ids,),
            )
            active_counts = {row["cohortId"]: row["count"] for row in cur.fetchall()}

            cur.execute(
                """
                SELECT cohort_id AS "cohortId", count(*)::int AS count
                FROM attendance_sessions
                WHERE cohort_id = ANY(%s) AND session_date >= CURRENT_DATE AND status != 'cancelled' AND deleted_at IS NULL
                GROUP BY cohort_id
                """,
                (cohort_ids,),
            )
            upcoming_counts = {row["cohortId"]: row["count"] for row in cur.fetchall()}

            # Ensure every past-or-today session in these cohorts has its
            # expected-learners snapshot generated before counting against
            # it, so "outstanding" reads the same frozen roster the session's
            # own register page shows (rather than a live-recomputed one
            # that could disagree after a backdated allocation correction).
            cur.execute(
                "SELECT id FROM attendance_sessions WHERE cohort_id = ANY(%s) AND session_date <= CURRENT_DATE AND deleted_at IS NULL",
                (cohort_ids,),
            )
            ensure_expected_learners_snapshots_bulk(cur, [row["id"] for row in cur.fetchall()])

            cur.execute(
                """
                SELECT s.cohort_id AS "cohortId", count(*)::int AS count
                FROM attendance_sessions s
                WHERE s.cohort_id = ANY(%s) AND s.session_date <= CURRENT_DATE AND s.status != 'cancelled' AND s.deleted_at IS NULL
                  AND (SELECT count(*) FROM attendance_records WHERE session_id = s.id)
                      < (SELECT count(*) FROM session_expected_learners WHERE session_id = s.id)
                GROUP BY s.cohort_id
                """,
                (cohort_ids,),
            )
            outstanding_counts = {row["cohortId"]: row["count"] for row in cur.fetchall()}

    return [
        {
            **cohort,
            "activeLearnerCount": active_counts.get(cohort["id"], 0),
            "upcomingSessionCount": upcoming_counts.get(cohort["id"], 0),
            "outstandingRegisterCount": outstanding_counts.get(cohort["id"], 0),
        }
        for cohort in cohorts
    ]


def _create_cohort(cur, payload: CohortInput, request: Request, session: dict) -> dict:
    """Cursor-accepting internal, callable from within another caller's own
    transaction (e.g. the Bud sync trial's commit step) as well as from the
    plain admin route below -- mirrors the _create_learner/_create_tutor
    pattern already used for CSV import."""
    _ensure_tutor_active(cur, payload.tutorId)
    cur.execute(
        """
        INSERT INTO cohorts (name, programme, level, tutor_id, delivery_day, session_start_time,
                              session_end_time, start_date, end_date, active, external_system_id, membership_type,
                              subject)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, *
        """,
        (
            payload.name,
            payload.programme,
            payload.level,
            payload.tutorId,
            payload.deliveryDay,
            payload.sessionStartTime,
            payload.sessionEndTime,
            payload.startDate,
            payload.endDate,
            payload.active,
            payload.externalSystemId,
            payload.membershipType,
            payload.subject,
        ),
    )
    created = cur.fetchone()
    cur.execute(f"{COHORT_SELECT} WHERE c.id = %s", (created["id"],))
    full = cur.fetchone()

    write_audit_log(request, action="create", entity_type="cohort", entity_id=created["id"], new_value=created, cur=cur)
    return full


@router.post("/cohorts", status_code=201)
def create_cohort(payload: CohortInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        full = _create_cohort(cur, payload, request, session)
    return full


@router.get("/cohorts/{cohort_id}")
def get_cohort(cohort_id: int, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        cur.execute(f"{COHORT_SELECT} WHERE c.id = %s AND c.deleted_at IS NULL", (cohort_id,))
        cohort = cur.fetchone()
        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found")
        if session.get("role") == "tutor" and cohort["tutorId"] != session.get("tutorId"):
            deny_object_access("cohort", cohort_id, "Not allowed to view this cohort")

        cur.execute(
            f"""
            SELECT count(*)::int AS count FROM learners
            WHERE {learner_in_cohort_now_sql("learners", "%s")} AND deleted_at IS NULL
            """,
            (cohort_id, cohort_id),
        )
        count = cur.fetchone()["count"]

    return {**cohort, "learnerCount": count}


@router.patch("/cohorts/{cohort_id}")
def update_cohort(cohort_id: int, payload: CohortUpdate, request: Request, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        require_cohort_access(cur, cohort_id, session)
        cur.execute("SELECT * FROM cohorts WHERE id = %s AND deleted_at IS NULL", (cohort_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Cohort not found")

        updates = payload.model_dump(exclude_unset=True)

        # A Tutor may rename their own cohort -- every other field (schedule,
        # tutor assignment, active flag, etc.) remains Administrator-only,
        # enforced here rather than trusting the frontend to only ever send
        # the name field.
        if session.get("role") != "admin":
            disallowed = sorted(set(updates) - {"name"})
            if disallowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"Only an Administrator can change: {', '.join(disallowed)}",
                )

        _validate_cohort_schedule(
            updates.get("sessionStartTime", existing["session_start_time"]),
            updates.get("sessionEndTime", existing["session_end_time"]),
            updates.get("startDate", existing["start_date"]),
            updates.get("endDate", existing["end_date"]),
        )
        if "tutorId" in updates:
            _ensure_tutor_active(cur, updates["tutorId"])

        if "membershipType" in updates and updates["membershipType"] != existing["membership_type"]:
            cur.execute(
                "SELECT count(*)::int AS count FROM learners WHERE cohort_id = %s AND deleted_at IS NULL",
                (cohort_id,),
            )
            has_home_learners = cur.fetchone()["count"] > 0
            cur.execute(
                "SELECT count(*)::int AS count FROM learner_cohort_enrollments WHERE cohort_id = %s AND status = 'active'",
                (cohort_id,),
            )
            has_secondary_enrollments = cur.fetchone()["count"] > 0
            if updates["membershipType"] == "secondary" and has_home_learners:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot change to a Functional Skills cohort while it still has home learners.",
                )
            if updates["membershipType"] == "primary" and has_secondary_enrollments:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot change back to a standard cohort while it still has active Functional Skills enrollments.",
                )

        if "membershipType" in updates or "subject" in updates:
            final_membership_type = updates.get("membershipType", existing["membership_type"])
            final_subject = updates["subject"] if "subject" in updates else existing["subject"]
            _check_subject_matches_membership_type(final_membership_type, final_subject)

        column_map = {
            "name": "name",
            "programme": "programme",
            "level": "level",
            "tutorId": "tutor_id",
            "deliveryDay": "delivery_day",
            "sessionStartTime": "session_start_time",
            "sessionEndTime": "session_end_time",
            "startDate": "start_date",
            "endDate": "end_date",
            "active": "active",
            "externalSystemId": "external_system_id",
            "membershipType": "membership_type",
            "subject": "subject",
        }
        set_clauses = [f"{column_map[k]} = %s" for k in updates]
        params = list(updates.values())
        if set_clauses:
            cur.execute(
                f"UPDATE cohorts SET {', '.join(set_clauses)}, updated_at = now() WHERE id = %s RETURNING id",
                [*params, cohort_id],
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Cohort not found")

        cur.execute(f"{COHORT_SELECT} WHERE c.id = %s", (cohort_id,))
        full = cur.fetchone()

    write_audit_log(
        request, action="update", entity_type="cohort", entity_id=cohort_id, previous_value=existing, new_value=full
    )
    return full


@router.post("/cohorts/{cohort_id}/activate")
def activate_cohort(cohort_id: int, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM cohorts WHERE id = %s AND deleted_at IS NULL", (cohort_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Cohort not found")
        cur.execute("UPDATE cohorts SET active = true, updated_at = now() WHERE id = %s", (cohort_id,))
        cur.execute(f"{COHORT_SELECT} WHERE c.id = %s", (cohort_id,))
        full = cur.fetchone()

    write_audit_log(
        request, action="activate", entity_type="cohort", entity_id=cohort_id, previous_value=existing, new_value=full
    )
    return full


@router.post("/cohorts/{cohort_id}/deactivate")
def deactivate_cohort(cohort_id: int, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM cohorts WHERE id = %s AND deleted_at IS NULL", (cohort_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Cohort not found")
        cur.execute("UPDATE cohorts SET active = false, updated_at = now() WHERE id = %s", (cohort_id,))
        cur.execute(f"{COHORT_SELECT} WHERE c.id = %s", (cohort_id,))
        full = cur.fetchone()

    write_audit_log(
        request, action="deactivate", entity_type="cohort", entity_id=cohort_id, previous_value=existing, new_value=full
    )
    return full


class CohortDeleteInput(BaseModel):
    reason: str = Field(min_length=1)


@router.post("/cohorts/{cohort_id}/delete", status_code=204)
def delete_cohort(cohort_id: int, payload: CohortDeleteInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM cohorts WHERE id = %s", (cohort_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Cohort not found")
        if existing["deleted_at"] is not None:
            raise HTTPException(status_code=400, detail="Cohort is already deleted")

        cur.execute(
            "SELECT count(*)::int AS count FROM learners WHERE cohort_id = %s AND status = 'active' AND deleted_at IS NULL",
            (cohort_id,),
        )
        active_learner_count = cur.fetchone()["count"]
        cur.execute(
            "SELECT count(*)::int AS count FROM attendance_sessions WHERE cohort_id = %s AND deleted_at IS NULL",
            (cohort_id,),
        )
        session_count = cur.fetchone()["count"]
        cur.execute(
            "SELECT count(*)::int AS count FROM learner_cohort_enrollments WHERE cohort_id = %s AND status = 'active'",
            (cohort_id,),
        )
        active_secondary_enrollment_count = cur.fetchone()["count"]
        if active_learner_count > 0 or session_count > 0 or active_secondary_enrollment_count > 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "cohort_not_empty",
                    "message": "This cohort still has active learners, attendance sessions, or active Functional "
                               "Skills enrollments. Reassign/withdraw its learners, end its enrollments, and "
                               "delete its sessions before deleting the cohort.",
                    "activeLearnerCount": active_learner_count,
                    "sessionCount": session_count,
                    "activeSecondaryEnrollmentCount": active_secondary_enrollment_count,
                },
            )

        with cur.connection.transaction():
            cur.execute(
                "UPDATE cohorts SET deleted_at = now(), deleted_by = %s, deletion_reason = %s, updated_at = now() WHERE id = %s",
                (session["userId"], payload.reason, cohort_id),
            )
            write_audit_log(
                request,
                action="delete_cohort",
                entity_type="cohort",
                entity_id=cohort_id,
                previous_value=existing,
                new_value={"deletedAt": "now", "reason": payload.reason},
                cur=cur,
            )
    return None


@router.get("/cohorts/{cohort_id}/learners")
def get_cohort_learners(cohort_id: int, session: dict = Depends(require_auth)):
    from ..learners_query import LEARNERS_WITH_NAMES_SELECT

    with get_cursor() as cur:
        require_cohort_access(cur, cohort_id, session)
        cur.execute(
            f"""{LEARNERS_WITH_NAMES_SELECT}
            WHERE {learner_in_cohort_now_sql("l", "%s")} AND l.deleted_at IS NULL
            """,
            (cohort_id, cohort_id),
        )
        return cur.fetchall()
