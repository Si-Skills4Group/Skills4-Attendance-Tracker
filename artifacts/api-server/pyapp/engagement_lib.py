"""Stage 4: learner engagement recency.

Combines evidence signals into one "latest recorded engagement" date per
learner -- each signal always stays independently visible; they are never
silently blended into a single unlabelled figure.

Sources that currently CONTRIBUTE to the combined calculation (latest
date, days-since, "sources" list):
  A. Last live attendance   -- present/late, session not cancelled/deleted.
  D. Last confirmed catch-up completion -- attendance_catchup.completion_date,
     only when the catch-up is currently EFFECTIVE (catchup_lib.py's own
     definition, reused inline here), using the date the learner actually
     caught up on -- never the original missed session date, never the
     date a tutor happened to type the confirmation in.

Sources shown for reference but EXCLUDED from the calculation, pending
verified upstream meaning (see the module-level "BUD SOURCE SEMANTICS"
note below):
  B. learner_progress.last_submission_date
  C. learner_progress.last_completed_activity

Hard rules:
- learner_progress.synced_at is a source-REFRESH timestamp only --
  bootstrap.py confirms it is bulk-touched across the WHOLE table on every
  Bud sync, regardless of whether a given row's data changed, and
  allocation_reconciliation_lib.py's fetch_source_info explicitly documents
  it as "never used to gate or filter" anything. Read here (as
  budSyncedAt) purely to show source freshness -- never as an engagement
  date.
- Bud plan resolution reuses the ALREADY-reliable, admin-confirmed
  bud_learner_link (unique 1:1 internal_learner_id <-> bud_learning_plan_id,
  bootstrap.py) -- never a name/email match, never a new automatic
  relinking rule, and never bud_progress.py's get_bud_progress_by_uln
  (which ignores bud_learner_link and tie-breaks by the bulk-touched
  synced_at -- explicitly documented there as provisional).
- A link alone does not prove the linked plan is the learner's RELEVANT
  CURRENT plan -- see _plan_currency() below, which checks the linked
  plan's own status_desc against any OTHER 'In Progress' plan sharing the
  same learner_reference, and flags a mismatch/ambiguity for review rather
  than guessing.
- Evidence is scoped to what the REQUESTING user is individually
  authorised to see, not merely whether they can see the learner exists.
  See "PERMISSIONS ON EVIDENCE" below.
- Every candidate date is excluded from the calculation -- and flagged as
  a data-quality issue -- if it is later than uk_today().

BUD SOURCE SEMANTICS -- unresolved, deliberately excluded from the
calculation until the data-feed owner confirms them:
  learner_progress has no DDL in this repository (owned by a separate,
  already-deployed sync service -- see bootstrap.py's Phase 11 comment and
  bud_progress.py's module docstring) and no BUD API/ETL field-mapping
  code exists anywhere in this codebase. The only verifiable fact is each
  column's SQL TYPE (last_submission_date: timestamptz; last_completed_
  activity: date) -- a date-shaped column is not proof of date-shaped
  MEANING. Concretely unverified from source alone:
    - Whether last_submission_date reflects a LEARNER-initiated submission
      or an assessor/system-driven update (the table also carries an
      entirely unused last_submission_by_learner timestamptz column,
      never read anywhere in this codebase -- its existence suggests
      last_submission_date may be a broader, non-learner-specific
      timestamp, but this is a naming inference, not verified evidence,
      and swapping to it on that basis alone would be exactly the kind of
      unverified assumption this correction exists to avoid).
    - What "last completed activity" means in Bud's own domain (an
      e-learning module, an off-the-job log entry, a portfolio
      submission, or something else) -- no in-repo documentation exists.
  Questions for the Bud/data-feed owner (see also the completion report):
    1. Does last_submission_date represent an action taken BY THE LEARNER,
       or can it be set by an assessor/tutor/system process on the
       learner's behalf?
    2. What does last_submission_by_learner represent, and how does it
       differ from last_submission_date? Should Stage 4 use it instead?
    3. What real-world learner action does last_completed_activity record
       -- and can it ever be set without corresponding learner activity?
  Until answered, both fields are shown for reference only, clearly
  labelled as unverified, and never contribute to latestEngagementDate,
  sources, or daysSinceEngagement.

PERMISSIONS ON EVIDENCE (not just the learner):
  require_learner_access (used by the detail endpoint, and implicitly by
  _enforce_tutor_scope's learner-reachability population gate) decides
  whether a learner is visible to the caller AT ALL -- it does not decide
  which of that learner's underlying events the caller may see. A tutor
  who can see a learner (as their home tutor, or via an active
  Functional Skills secondary enrollment into one of the tutor's own
  cohorts) must still only have attendance/catch-up EVIDENCE drawn from
  sessions they hold real session-level authority over -- the exact same
  predicate require_attendance_access already uses per-session
  (cohort's own tutor, or that one session's specific cover tutor),
  applied here as an aggregate filter via evidence_tutor_id. This is what
  stops an FS-only tutor's view from being inflated by the learner's
  unrelated home-cohort attendance, and is also what makes cover-session
  access contribute only that one covered session's own evidence, never
  the learner's whole history. No finer-grained permission for Bud
  activity specifically exists in this codebase, so Bud evidence is shown
  only to the learner's home tutor or an admin -- never to an FS-only
  tutor, however they reached the learner. evidence_tutor_id is None for
  an admin caller (full, unrestricted evidence, "organisation-wide"), and
  is always the caller's OWN tutorId for a tutor caller -- callers can
  never see evidence scoped to a DIFFERENT tutor no matter what tutorId/
  cohortId population filter they pass (population filtering and evidence
  scoping are deliberately separate: an admin's tutorId filter narrows
  WHO is listed, never re-scopes the admin's own -- full -- evidence
  access)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from .attendance_metrics import uk_today
from .secondary_enrollment_lib import learner_in_cohort_now_sql, learner_reachable_via_tutor_now_sql

# Only these two currently ever contribute to latestEngagementDate/sources/
# daysSinceEngagement -- see the module docstring's "BUD SOURCE SEMANTICS"
# note for why bud_submission/bud_completed_activity are excluded.
_CALCULATION_SOURCES = ("attendance", "catchup")

_BUD_CAVEAT_UNVERIFIED = (
    "Unverified Bud source field(s): submission/completed-activity dates are shown for reference only -- their "
    "upstream meaning has not been confirmed (learner-initiated vs assessor/system update, and what 'completed "
    "activity' records), so they are excluded from Latest Recorded Engagement."
)

_ENGAGEMENT_CTE = """
    WITH attendance_agg AS (
        SELECT ar.learner_id, MAX(s.session_date) AS raw_date
        FROM attendance_records ar
        JOIN attendance_sessions s ON ar.session_id = s.id
        JOIN cohorts c ON s.cohort_id = c.id
        WHERE ar.status IN ('present', 'late') AND s.status != 'cancelled' AND s.deleted_at IS NULL
          AND (%(evidenceTutorId)s::integer IS NULL OR c.tutor_id = %(evidenceTutorId)s OR s.cover_tutor_id = %(evidenceTutorId)s)
        GROUP BY ar.learner_id
    ),
    catchup_agg AS (
        SELECT cu.learner_id, MAX(cu.completion_date) AS raw_date
        FROM attendance_catchup cu
        JOIN attendance_sessions s ON s.id = cu.session_id
        JOIN cohorts c ON s.cohort_id = c.id
        JOIN attendance_records ar ON ar.session_id = cu.session_id AND ar.learner_id = cu.learner_id
        WHERE cu.status = 'recorded' AND s.status != 'cancelled' AND s.deleted_at IS NULL
          AND ar.status IN ('absent_authorised', 'absent_unauthorised')
          AND (%(evidenceTutorId)s::integer IS NULL OR c.tutor_id = %(evidenceTutorId)s OR s.cover_tutor_id = %(evidenceTutorId)s)
        GROUP BY cu.learner_id
    ),
    bud_resolved AS (
        SELECT bll.internal_learner_id AS learner_id,
               bll.bud_learning_plan_id AS linked_plan_id,
               lp.learning_plan_id AS resolved_plan_id,
               lp.last_submission_date, lp.last_completed_activity,
               lp.synced_at, lp.status_desc, cp.current_plan_ids
        FROM bud_learner_link bll
        LEFT JOIN LATERAL (
            SELECT * FROM public.learner_progress lp2
            WHERE lp2.learning_plan_id = bll.bud_learning_plan_id
            ORDER BY lp2.synced_at DESC NULLS LAST
            LIMIT 1
        ) lp ON true
        LEFT JOIN LATERAL (
            SELECT array_agg(DISTINCT lp3.learning_plan_id) AS current_plan_ids
            FROM public.learner_progress lp3
            WHERE lp.learner_reference IS NOT NULL
              AND lp3.learner_reference = lp.learner_reference
              AND lp3.status_desc = 'In Progress'
        ) cp ON true
    ),
    combined AS (
        SELECT
            l.id AS learner_id,
            l.tutor_id AS home_tutor_id,
            aa.raw_date AS attendance_raw,
            CASE WHEN aa.raw_date IS NOT NULL AND aa.raw_date <= %(today)s THEN aa.raw_date END AS attendance_valid,
            ca.raw_date AS catchup_raw,
            CASE WHEN ca.raw_date IS NOT NULL AND ca.raw_date <= %(today)s THEN ca.raw_date END AS catchup_valid,
            br.linked_plan_id AS bud_linked_plan_id,
            br.resolved_plan_id AS bud_resolved_plan_id,
            br.last_submission_date AS bud_submission_raw,
            br.last_completed_activity AS bud_activity_raw,
            br.synced_at AS bud_synced_at,
            br.status_desc AS bud_status_desc,
            br.current_plan_ids AS bud_current_plan_ids,
            (%(evidenceTutorId)s::integer IS NULL OR l.tutor_id = %(evidenceTutorId)s) AS bud_authorized
        FROM learners l
        LEFT JOIN attendance_agg aa ON aa.learner_id = l.id
        LEFT JOIN catchup_agg ca ON ca.learner_id = l.id
        LEFT JOIN bud_resolved br ON br.learner_id = l.id
        WHERE {where}
    ),
    final AS (
        SELECT *,
            GREATEST(attendance_valid, catchup_valid) AS latest_date
        FROM combined
    )
"""


def _learner_population_clause(
    *, tutor_id: int | None, cohort_id: int | None, learner_id: int | None, programme: str | None, search: str | None,
) -> tuple[str, dict]:
    """Item 4: defaults to internally active, non-deleted learners within
    the caller's already-resolved scope -- never restricted to learners
    who already have attendance rows or a confirmed Bud link (a learner
    with zero evidence in every source must still appear). This decides
    WHO is listed -- a separate concern from evidence_tutor_id below,
    which decides which of a listed learner's own events are visible."""
    clauses = ["l.deleted_at IS NULL", "l.status = 'active'"]
    params: dict = {}
    if tutor_id is not None:
        clauses.append(learner_reachable_via_tutor_now_sql("l", "%(tutorId)s"))
        params["tutorId"] = tutor_id
    if cohort_id is not None:
        clauses.append(learner_in_cohort_now_sql("l", "%(cohortId)s"))
        params["cohortId"] = cohort_id
    if learner_id is not None:
        clauses.append("l.id = %(learnerId)s")
        params["learnerId"] = learner_id
    if programme:
        clauses.append("l.programme = %(programme)s")
        params["programme"] = programme
    if search:
        clauses.append("(l.first_name ILIKE %(search)s OR l.last_name ILIKE %(search)s OR l.learner_ref ILIKE %(search)s)")
        params["search"] = f"%{search}%"
    return " AND ".join(clauses), params


def _combine_candidates(attendance: date | None, attendance_raw: date | None, catchup: date | None, catchup_raw: date | None, today: date) -> dict:
    """The one place "what is the latest recorded engagement, from which
    source(s), and is anything flagged" is decided -- used identically by
    the list query's per-row annotation and the single-learner detail
    resolver, so the two views can never disagree. Only attendance and
    catch-up are considered -- see the module docstring for why Bud dates
    are excluded."""
    valid = {"attendance": attendance, "catchup": catchup}
    raw = {"attendance": attendance_raw, "catchup": catchup_raw}

    data_quality_issues = [
        f"{name} date ({raw[name].isoformat()}) is in the future and was excluded from the calculation"
        for name in _CALCULATION_SOURCES
        if raw[name] is not None and valid[name] is None
    ]

    present = [d for d in valid.values() if d is not None]
    latest = max(present) if present else None
    sources = [name for name in _CALCULATION_SOURCES if valid[name] is not None and valid[name] == latest] if latest else []
    days_since = (today - latest).days if latest else None

    return {
        "latestEngagementDate": latest,
        "sources": sources,
        "daysSinceEngagement": days_since,
        "dataQualityIssues": data_quality_issues,
    }


def _plan_currency(resolved_plan_id: str | None, current_plan_ids: list[str] | None) -> str | None:
    """Item 3: a link alone doesn't prove the linked plan is the RELEVANT
    CURRENT one. current_plan_ids is every plan sharing the linked plan's
    own learner_reference that is currently 'In Progress' (computed in
    SQL, see bud_resolved's cp LATERAL join)."""
    if resolved_plan_id is None:
        return None
    ids = current_plan_ids or []
    if len(ids) == 0:
        return "no_current_plan"
    if len(ids) == 1 and ids[0] == resolved_plan_id:
        return "current"
    if len(ids) == 1:
        return "linked_plan_not_current"
    return "multiple_current_plans_ambiguous"


def _bud_result(linked_plan_id, resolved_plan_id, bud_authorized, plan_currency, submission_raw, activity_raw, synced_at, status_desc) -> dict:
    """Item 2 + item 3 combined: decides both whether the caller is
    PERMITTED to see Bud evidence at all, and whether the linked plan is
    trustworthy enough to show even to someone who is. Either failing
    means the Bud dates -- and, when unauthorised, even the plan id
    itself -- are withheld (not just excluded from the calculation, which
    already never includes them): showing a stale or unauthorised plan's
    dates, even captioned "unverified", would still be misleading."""
    if not bud_authorized:
        return {
            "budStatus": "not_authorized", "budPlanCurrency": None,
            "budLinkedPlanId": None, "budResolvedPlanId": None,
            "lastBudSubmission": None, "lastBudCompletedActivity": None,
            "budSyncedAt": None, "budStatusDesc": None,
            "sourceLimitations": ["Bud activity is not shown because you do not have permission to view it for this learner."],
        }
    if linked_plan_id is None:
        return {
            "budStatus": "not_linked", "budPlanCurrency": None,
            "budLinkedPlanId": None, "budResolvedPlanId": None,
            "lastBudSubmission": None, "lastBudCompletedActivity": None,
            "budSyncedAt": None, "budStatusDesc": None,
            "sourceLimitations": ["No confirmed Bud learning-plan link exists for this learner -- Bud engagement is unavailable, not zero."],
        }
    if resolved_plan_id is None:
        return {
            "budStatus": "missing_source", "budPlanCurrency": None,
            "budLinkedPlanId": linked_plan_id, "budResolvedPlanId": None,
            "lastBudSubmission": None, "lastBudCompletedActivity": None,
            "budSyncedAt": None, "budStatusDesc": None,
            "sourceLimitations": ["The linked Bud plan is not present in the current Bud data extract -- needs review."],
        }
    if plan_currency == "linked_plan_not_current":
        return {
            "budStatus": "needs_review", "budPlanCurrency": plan_currency,
            "budLinkedPlanId": linked_plan_id, "budResolvedPlanId": resolved_plan_id,
            "lastBudSubmission": None, "lastBudCompletedActivity": None,
            "budSyncedAt": synced_at, "budStatusDesc": status_desc,
            "sourceLimitations": ["The linked Bud plan does not appear to be the learner's current in-progress plan -- needs review before treating its dates as relevant."],
        }
    if plan_currency == "multiple_current_plans_ambiguous":
        return {
            "budStatus": "needs_review", "budPlanCurrency": plan_currency,
            "budLinkedPlanId": linked_plan_id, "budResolvedPlanId": resolved_plan_id,
            "lastBudSubmission": None, "lastBudCompletedActivity": None,
            "budSyncedAt": synced_at, "budStatusDesc": status_desc,
            "sourceLimitations": ["Multiple Bud plans appear current for this learner -- which is relevant could not be determined automatically."],
        }
    limitations = [_BUD_CAVEAT_UNVERIFIED] if (submission_raw is not None or activity_raw is not None) else []
    return {
        "budStatus": "resolved", "budPlanCurrency": plan_currency,
        "budLinkedPlanId": linked_plan_id, "budResolvedPlanId": resolved_plan_id,
        "lastBudSubmission": submission_raw.date() if submission_raw else None,
        "lastBudCompletedActivity": activity_raw,
        "budSyncedAt": synced_at, "budStatusDesc": status_desc,
        "sourceLimitations": limitations,
    }


def _annotate_row(row: dict, today: date) -> dict:
    # Only the transient "*Raw" columns (needed solely to detect an
    # excluded future date) are dropped -- lastLiveAttendance/
    # lastCatchupCompletion stay in the row exactly as the SQL already
    # computed them, since item 5 shows those separately alongside the
    # combined figure.
    combined = _combine_candidates(row["lastLiveAttendance"], row.pop("attendanceRaw"), row["lastCatchupCompletion"], row.pop("catchupRaw"), today)
    bud_status_desc = row.pop("budStatusDescRaw")
    submission_raw = row.pop("budSubmissionRaw")
    activity_raw = row.pop("budActivityRaw")
    synced_at = row.pop("budSyncedAtRaw")
    plan_currency = _plan_currency(row["budResolvedPlanId"], row.pop("budCurrentPlanIds"))
    bud = _bud_result(
        row["budLinkedPlanId"], row["budResolvedPlanId"], row.pop("budAuthorized"), plan_currency,
        submission_raw, activity_raw, synced_at, bud_status_desc,
    )
    row.update(combined)
    row.update(bud)
    row["hasIncompleteSourceCoverage"] = bud["budStatus"] != "resolved"
    row["sourcesText"] = "; ".join(combined["sources"])
    row["dataQualityIssuesText"] = "; ".join(combined["dataQualityIssues"])
    row["sourceLimitationsText"] = "; ".join(bud["sourceLimitations"])
    return row


def _evidence_params(evidence_tutor_id: int | None) -> dict:
    return {"evidenceTutorId": evidence_tutor_id}


def fetch_engagement_rows(
    cur, *, tutor_id: int | None, cohort_id: int | None, learner_id: int | None,
    programme: str | None, search: str | None,
    no_engagement_only: bool, min_days_since: int | None,
    evidence_tutor_id: int | None,
    page: int, page_size: int,
) -> tuple[list[dict], int]:
    """One row per learner, worst-first (oldest engagement, then never-
    engaged learners, surface first), stable via a final l.id tie-breaker.
    evidence_tutor_id is None for an admin caller (full evidence) and
    always the caller's OWN tutorId for a tutor caller (never a different
    tutor's id, regardless of the tutor_id/cohort_id POPULATION filter
    above) -- see the module docstring's "PERMISSIONS ON EVIDENCE" note."""
    today = uk_today()
    where, params = _learner_population_clause(
        tutor_id=tutor_id, cohort_id=cohort_id, learner_id=learner_id, programme=programme, search=search,
    )
    params.update(_evidence_params(evidence_tutor_id))
    params["today"] = today
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size

    final_filters = []
    if no_engagement_only:
        final_filters.append("f.latest_date IS NULL")
    if min_days_since is not None:
        final_filters.append("f.latest_date IS NOT NULL AND (%(today)s - f.latest_date) >= %(minDays)s")
        params["minDays"] = min_days_since
    final_where = (" AND " + " AND ".join(final_filters)) if final_filters else ""

    cte = _ENGAGEMENT_CTE.format(where=where)

    cur.execute(f"{cte} SELECT count(*)::int AS n FROM final f WHERE true{final_where}", params)
    total = cur.fetchone()["n"]

    cur.execute(
        f"""
        {cte}
        SELECT
            l.id, l.learner_ref AS "learnerRef", concat(l.first_name, ' ', l.last_name) AS "learnerName",
            l.programme,
            CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
            f.attendance_raw AS "attendanceRaw", f.attendance_valid AS "lastLiveAttendance",
            f.catchup_raw AS "catchupRaw", f.catchup_valid AS "lastCatchupCompletion",
            f.bud_linked_plan_id AS "budLinkedPlanId", f.bud_resolved_plan_id AS "budResolvedPlanId",
            f.bud_submission_raw AS "budSubmissionRaw", f.bud_activity_raw AS "budActivityRaw",
            f.bud_synced_at AS "budSyncedAtRaw", f.bud_status_desc AS "budStatusDescRaw",
            f.bud_current_plan_ids AS "budCurrentPlanIds", f.bud_authorized AS "budAuthorized"
        FROM final f
        JOIN learners l ON l.id = f.learner_id
        LEFT JOIN tutors t ON t.id = l.tutor_id
        WHERE true{final_where}
        ORDER BY f.latest_date ASC NULLS FIRST, l.last_name, l.first_name, l.id
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    rows = cur.fetchall()
    return [_annotate_row(row, today) for row in rows], total


def fetch_engagement_summary(cur, *, tutor_id: int | None, cohort_id: int | None, evidence_tutor_id: int | None) -> dict:
    """Compact counts for a tutor dashboard link/admin overview -- the
    SAME population/evidence machinery as fetch_engagement_rows (no
    duplicated definitions), so a count shown here always reconciles with
    the full report under the same scope and the same evidence
    restrictions."""
    today = uk_today()
    where, params = _learner_population_clause(
        tutor_id=tutor_id, cohort_id=cohort_id, learner_id=None, programme=None, search=None,
    )
    params.update(_evidence_params(evidence_tutor_id))
    params["today"] = today
    cte = _ENGAGEMENT_CTE.format(where=where)
    cur.execute(
        f"""
        {cte}
        SELECT
            count(*)::int AS "totalLearners",
            count(*) FILTER (WHERE latest_date IS NULL)::int AS "noRecordedEngagementCount"
        FROM final
        """,
        params,
    )
    row = cur.fetchone()
    row["calculatedAt"] = datetime.now(timezone.utc)
    return row


def resolve_learner_engagement_detail(cur, learner_id: int, *, evidence_tutor_id: int | None) -> dict:
    """Single-learner traceability view (item 6) -- the exact same
    combination rule as fetch_engagement_rows (via the shared
    _combine_candidates/_bud_result), but with full supporting-evidence
    references. Access to the LEARNER is gated by the caller (require_
    learner_access) before this runs; evidence_tutor_id additionally
    restricts which of the learner's own events are returned, exactly
    like the list/summary/export (None for admin, the caller's own
    tutorId for a tutor -- never a different tutor's)."""
    today = uk_today()
    evidence_clause = "AND (%(evidenceTutorId)s::integer IS NULL OR c.tutor_id = %(evidenceTutorId)s OR s.cover_tutor_id = %(evidenceTutorId)s)"

    cur.execute(
        f"""
        SELECT ar.session_id AS "sessionId", s.session_date AS "sessionDate", s.title AS "sessionTitle", ar.status
        FROM attendance_records ar
        JOIN attendance_sessions s ON ar.session_id = s.id
        JOIN cohorts c ON s.cohort_id = c.id
        WHERE ar.learner_id = %(learnerId)s AND ar.status IN ('present', 'late')
          AND s.status != 'cancelled' AND s.deleted_at IS NULL
          {evidence_clause}
        ORDER BY s.session_date DESC
        LIMIT 1
        """,
        {"learnerId": learner_id, "evidenceTutorId": evidence_tutor_id},
    )
    attendance_evidence = cur.fetchone()

    cur.execute(
        f"""
        SELECT cu.id AS "catchupId", cu.session_id AS "sessionId", s.session_date AS "sessionDate",
               cu.completion_date AS "completionDate", cu.method, cu.note,
               cu.recorded_at AS "confirmedAt", cu.recorded_by AS "confirmedByUserId"
        FROM attendance_catchup cu
        JOIN attendance_sessions s ON s.id = cu.session_id
        JOIN cohorts c ON s.cohort_id = c.id
        JOIN attendance_records ar ON ar.session_id = cu.session_id AND ar.learner_id = cu.learner_id
        WHERE cu.learner_id = %(learnerId)s AND cu.status = 'recorded'
          AND s.status != 'cancelled' AND s.deleted_at IS NULL
          AND ar.status IN ('absent_authorised', 'absent_unauthorised')
          {evidence_clause}
        ORDER BY cu.completion_date DESC
        LIMIT 1
        """,
        {"learnerId": learner_id, "evidenceTutorId": evidence_tutor_id},
    )
    catchup_evidence = cur.fetchone()

    cur.execute('SELECT tutor_id AS "tutorId" FROM learners WHERE id = %(learnerId)s', {"learnerId": learner_id})
    home_tutor_id = cur.fetchone()["tutorId"]
    bud_authorized = evidence_tutor_id is None or home_tutor_id == evidence_tutor_id

    cur.execute(
        """
        SELECT bll.bud_learning_plan_id AS "linkedPlanId", lp.learning_plan_id AS "resolvedPlanId",
               lp.last_submission_date AS "lastSubmissionDate", lp.last_completed_activity AS "lastCompletedActivity",
               lp.synced_at AS "syncedAt", lp.status_desc AS "statusDesc", lp.learning_plan_url AS "learningPlanUrl",
               cp.current_plan_ids AS "currentPlanIds"
        FROM bud_learner_link bll
        LEFT JOIN LATERAL (
            SELECT * FROM public.learner_progress lp2
            WHERE lp2.learning_plan_id = bll.bud_learning_plan_id
            ORDER BY lp2.synced_at DESC NULLS LAST
            LIMIT 1
        ) lp ON true
        LEFT JOIN LATERAL (
            SELECT array_agg(DISTINCT lp3.learning_plan_id) AS current_plan_ids
            FROM public.learner_progress lp3
            WHERE lp.learner_reference IS NOT NULL
              AND lp3.learner_reference = lp.learner_reference
              AND lp3.status_desc = 'In Progress'
        ) cp ON true
        WHERE bll.internal_learner_id = %(learnerId)s
        """,
        {"learnerId": learner_id},
    )
    bud_row = cur.fetchone()

    attendance_date = attendance_evidence["sessionDate"] if attendance_evidence else None
    catchup_date = catchup_evidence["completionDate"] if catchup_evidence else None

    def _valid(d: date | None) -> date | None:
        return d if d is not None and d <= today else None

    combined = _combine_candidates(_valid(attendance_date), attendance_date, _valid(catchup_date), catchup_date, today)

    linked_plan_id = bud_row["linkedPlanId"] if bud_row else None
    resolved_plan_id = bud_row["resolvedPlanId"] if bud_row else None
    plan_currency = _plan_currency(resolved_plan_id, bud_row["currentPlanIds"] if bud_row else None)
    bud = _bud_result(
        linked_plan_id, resolved_plan_id, bud_authorized, plan_currency,
        bud_row["lastSubmissionDate"] if bud_row else None,
        bud_row["lastCompletedActivity"] if bud_row else None,
        bud_row["syncedAt"] if bud_row else None,
        bud_row["statusDesc"] if bud_row else None,
    )
    bud_evidence = None
    if bud["budStatus"] == "resolved" and bud_row is not None:
        bud_evidence = {
            "linkedPlanId": linked_plan_id, "resolvedPlanId": resolved_plan_id,
            "lastSubmissionDate": bud_row["lastSubmissionDate"], "lastCompletedActivity": bud_row["lastCompletedActivity"],
            "syncedAt": bud_row["syncedAt"], "statusDesc": bud_row["statusDesc"], "learningPlanUrl": bud_row["learningPlanUrl"],
        }

    return {
        **combined,
        **bud,
        "hasIncompleteSourceCoverage": bud["budStatus"] != "resolved",
        "attendanceEvidence": attendance_evidence,
        "catchupEvidence": catchup_evidence,
        "budEvidence": bud_evidence,
    }
