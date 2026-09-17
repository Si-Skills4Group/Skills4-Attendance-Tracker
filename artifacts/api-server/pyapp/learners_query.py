"""Faithful port of lib/learners-query.ts -- shared learner query joined with
tutor/cohort names."""
from .secondary_enrollment_lib import functional_skills_subjects_sql

LEARNERS_WITH_NAMES_SELECT = f"""
    SELECT
        l.id, l.learner_ref AS "learnerRef", l.uln,
        l.first_name AS "firstName", l.last_name AS "lastName",
        l.email, l.mobile, l.employer, l.programme, l.level,
        l.start_date AS "startDate", l.planned_end_date AS "plannedEndDate",
        l.actual_end_date AS "actualEndDate", l.withdrawal_date AS "withdrawalDate",
        l.status, l.tutor_id AS "tutorId", l.cohort_id AS "cohortId",
        l.external_system_id AS "externalSystemId",
        l.created_at AS "createdAt", l.updated_at AS "updatedAt",
        CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
        c.name AS "cohortName",
        {functional_skills_subjects_sql("l.id")} AS "functionalSkillsSubjects"
    FROM learners l
    LEFT JOIN tutors t ON l.tutor_id = t.id
    LEFT JOIN cohorts c ON l.cohort_id = c.id
"""
