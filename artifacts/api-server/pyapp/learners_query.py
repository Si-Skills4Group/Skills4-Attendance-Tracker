"""Faithful port of lib/learners-query.ts -- shared learner query joined with
tutor/cohort names."""

LEARNERS_WITH_NAMES_SELECT = """
    SELECT
        l.id, l.learner_ref AS "learnerRef", l.uln,
        l.first_name AS "firstName", l.last_name AS "lastName",
        l.email, l.employer, l.programme, l.level,
        l.start_date AS "startDate", l.planned_end_date AS "plannedEndDate",
        l.status, l.tutor_id AS "tutorId", l.cohort_id AS "cohortId",
        l.external_system_id AS "externalSystemId",
        l.created_at AS "createdAt", l.updated_at AS "updatedAt",
        CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
        c.name AS "cohortName"
    FROM learners l
    LEFT JOIN tutors t ON l.tutor_id = t.id
    LEFT JOIN cohorts c ON l.cohort_id = c.id
"""
