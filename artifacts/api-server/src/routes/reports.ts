import { Router, type IRouter } from "express";
import { eq, sql } from "drizzle-orm";
import { db, learnersTable, cohortsTable, tutorsTable } from "@workspace/db";
import {
  GetLearnerReportParams,
  GetLearnerReportResponse,
  GetCohortReportParams,
  GetCohortReportResponse,
  GetTutorReportParams,
  GetTutorReportResponse,
  GetOrganisationReportResponse,
  GetProgrammeReportResponse,
  ExportReportQueryParams,
  ExportReportResponse,
} from "@workspace/api-zod";
import { requireAuth, requireAdmin } from "../lib/auth";
import { computeAttendanceTotals } from "../lib/attendance-calc";
import {
  getRecordsForLearner,
  getRecordsForCohort,
  getRecordsForTutor,
  getRecordsForOrganisation,
} from "../lib/attendance-data";
import { learnersWithNames } from "../lib/learners-query";
import { stringifyRowsToCsv } from "../lib/csv";
import { toDateOnly } from "../lib/dates";

const router: IRouter = Router();

router.get(
  "/reports/learner/:id",
  requireAuth,
  async (req, res): Promise<void> => {
    const params = GetLearnerReportParams.safeParse(req.params);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }

    const [learner] = await learnersWithNames.where(
      eq(learnersTable.id, params.data.id),
    );
    if (!learner) {
      res.status(404).json({ error: "Learner not found" });
      return;
    }
    if (req.session.role === "tutor" && learner.tutorId !== req.session.tutorId) {
      res.status(403).json({ error: "Not allowed to view this learner" });
      return;
    }

    const records = await getRecordsForLearner(learner.id);
    const totals = computeAttendanceTotals(records);

    res.json(GetLearnerReportResponse.parse({ learner, totals }));
  },
);

router.get(
  "/reports/cohort/:id",
  requireAuth,
  async (req, res): Promise<void> => {
    const params = GetCohortReportParams.safeParse(req.params);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }

    const [cohort] = await db
      .select({
        id: cohortsTable.id,
        name: cohortsTable.name,
        programme: cohortsTable.programme,
        level: cohortsTable.level,
        tutorId: cohortsTable.tutorId,
        tutorName: sql<string | null>`concat(${tutorsTable.firstName}, ' ', ${tutorsTable.lastName})`,
        deliveryDay: cohortsTable.deliveryDay,
        sessionStartTime: cohortsTable.sessionStartTime,
        sessionEndTime: cohortsTable.sessionEndTime,
        startDate: cohortsTable.startDate,
        endDate: cohortsTable.endDate,
        active: cohortsTable.active,
        externalSystemId: cohortsTable.externalSystemId,
        createdAt: cohortsTable.createdAt,
        updatedAt: cohortsTable.updatedAt,
      })
      .from(cohortsTable)
      .leftJoin(tutorsTable, eq(cohortsTable.tutorId, tutorsTable.id))
      .where(eq(cohortsTable.id, params.data.id));
    if (!cohort) {
      res.status(404).json({ error: "Cohort not found" });
      return;
    }
    if (req.session.role === "tutor" && cohort.tutorId !== req.session.tutorId) {
      res.status(403).json({ error: "Not allowed to view this cohort" });
      return;
    }

    const records = await getRecordsForCohort(cohort.id);
    const totals = computeAttendanceTotals(records);

    const learners = await learnersWithNames.where(
      eq(learnersTable.cohortId, cohort.id),
    );
    const learnerBreakdown = await Promise.all(
      learners.map(async (learner) => {
        const learnerRecords = await getRecordsForLearner(learner.id);
        return {
          learnerId: learner.id,
          learnerName: `${learner.firstName} ${learner.lastName}`,
          learnerRef: learner.learnerRef,
          totals: computeAttendanceTotals(learnerRecords),
        };
      }),
    );

    res.json(
      GetCohortReportResponse.parse({ cohort, totals, learnerBreakdown }),
    );
  },
);

router.get(
  "/reports/tutor/:id",
  requireAuth,
  async (req, res): Promise<void> => {
    const params = GetTutorReportParams.safeParse(req.params);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }
    if (req.session.role === "tutor" && req.session.tutorId !== params.data.id) {
      res.status(403).json({ error: "Not allowed to view this tutor's report" });
      return;
    }

    const [tutor] = await db
      .select()
      .from(tutorsTable)
      .where(eq(tutorsTable.id, params.data.id));
    if (!tutor) {
      res.status(404).json({ error: "Tutor not found" });
      return;
    }

    const records = await getRecordsForTutor(tutor.id);
    const totals = computeAttendanceTotals(records);

    const cohorts = await db
      .select()
      .from(cohortsTable)
      .where(eq(cohortsTable.tutorId, tutor.id));
    const cohortBreakdown = await Promise.all(
      cohorts.map(async (cohort) => {
        const cohortRecords = await getRecordsForCohort(cohort.id);
        return {
          cohortId: cohort.id,
          cohortName: cohort.name,
          totals: computeAttendanceTotals(cohortRecords),
        };
      }),
    );

    res.json(GetTutorReportResponse.parse({ tutor, totals, cohortBreakdown }));
  },
);

router.get(
  "/reports/organisation",
  requireAdmin,
  async (req, res): Promise<void> => {
    const { dateFrom, dateTo, programme } = req.query as Record<
      string,
      string | undefined
    >;

    const records = await getRecordsForOrganisation(
      { dateFrom, dateTo },
      programme,
    );
    const totals = computeAttendanceTotals(records);

    const programmes = [...new Set(records.map((r) => r.programme))];
    const programmeBreakdown = programmes.map((p) => ({
      programme: p,
      totals: computeAttendanceTotals(records.filter((r) => r.programme === p)),
    }));

    const cohortIds = [...new Set(records.map((r) => r.cohortId))];
    const cohorts = cohortIds.length
      ? await db.select().from(cohortsTable)
      : [];
    const cohortBreakdown = cohortIds.map((cohortId) => ({
      cohortId,
      cohortName:
        cohorts.find((c) => c.id === cohortId)?.name ?? "Unknown cohort",
      totals: computeAttendanceTotals(
        records.filter((r) => r.cohortId === cohortId),
      ),
    }));

    res.json(
      GetOrganisationReportResponse.parse({
        totals,
        programmeBreakdown,
        cohortBreakdown,
      }),
    );
  },
);

router.get(
  "/reports/programme",
  requireAdmin,
  async (req, res): Promise<void> => {
    const { dateFrom, dateTo } = req.query as Record<string, string | undefined>;
    const records = await getRecordsForOrganisation({ dateFrom, dateTo });
    const programmes = [...new Set(records.map((r) => r.programme))];
    const rows = programmes.map((p) => ({
      programme: p,
      totals: computeAttendanceTotals(records.filter((r) => r.programme === p)),
    }));
    res.json(GetProgrammeReportResponse.parse(rows));
  },
);

router.get(
  "/reports/export",
  requireAuth,
  async (req, res): Promise<void> => {
    const parsed = ExportReportQueryParams.safeParse(req.query);
    if (!parsed.success) {
      res.status(400).json({ error: parsed.error.message });
      return;
    }

    const { reportType, entityId, programme } = parsed.data;

    // Tutors may only export their own tutor report or a cohort/learner that
    // belongs to them. Organisation-wide and programme exports remain admin-only.
    if (req.session.role === "tutor") {
      if (reportType === "tutor") {
        if (entityId !== req.session.tutorId) {
          res.status(403).json({ error: "Not allowed to export this tutor's report" });
          return;
        }
      } else if (reportType === "cohort" && entityId) {
        const [cohort] = await db
          .select({ tutorId: cohortsTable.tutorId })
          .from(cohortsTable)
          .where(eq(cohortsTable.id, entityId));
        if (!cohort || cohort.tutorId !== req.session.tutorId) {
          res.status(403).json({ error: "Not allowed to export this cohort's report" });
          return;
        }
      } else if (reportType === "learner" && entityId) {
        const [learner] = await db
          .select({ tutorId: learnersTable.tutorId })
          .from(learnersTable)
          .where(eq(learnersTable.id, entityId));
        if (!learner || learner.tutorId !== req.session.tutorId) {
          res.status(403).json({ error: "Not allowed to export this learner's report" });
          return;
        }
      } else {
        res.status(403).json({ error: "Administrator access required" });
        return;
      }
    }

    const dateFromStr = parsed.data.dateFrom ? toDateOnly(parsed.data.dateFrom) : undefined;
    const dateToStr = parsed.data.dateTo ? toDateOnly(parsed.data.dateTo) : undefined;
    let rows: Record<string, unknown>[] = [];
    const filename = `${reportType}-report.csv`;

    if (reportType === "learner" && entityId) {
      const records = await getRecordsForLearner(entityId, {
        dateFrom: dateFromStr,
        dateTo: dateToStr,
      });
      rows = [{ ...computeAttendanceTotals(records) }];
    } else if (reportType === "cohort" && entityId) {
      const records = await getRecordsForCohort(entityId, {
        dateFrom: dateFromStr,
        dateTo: dateToStr,
      });
      rows = [{ ...computeAttendanceTotals(records) }];
    } else if (reportType === "tutor" && entityId) {
      const records = await getRecordsForTutor(entityId, {
        dateFrom: dateFromStr,
        dateTo: dateToStr,
      });
      rows = [{ ...computeAttendanceTotals(records) }];
    } else if (reportType === "programme") {
      const records = await getRecordsForOrganisation({
        dateFrom: dateFromStr,
        dateTo: dateToStr,
      });
      const programmes = [...new Set(records.map((r) => r.programme))];
      rows = programmes.map((p) => ({
        programme: p,
        ...computeAttendanceTotals(records.filter((r) => r.programme === p)),
      }));
    } else {
      const records = await getRecordsForOrganisation(
        { dateFrom: dateFromStr, dateTo: dateToStr },
        programme,
      );
      rows = [{ ...computeAttendanceTotals(records) }];
    }

    const csv = stringifyRowsToCsv(rows, Object.keys(rows[0] ?? {}));
    res.json(ExportReportResponse.parse({ csv, filename }));
  },
);

export default router;
