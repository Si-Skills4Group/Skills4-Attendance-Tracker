import { Router, type IRouter } from "express";
import { and, eq, gte, lte, sql } from "drizzle-orm";
import {
  db,
  learnersTable,
  tutorsTable,
  cohortsTable,
  attendanceSessionsTable,
  attendanceRecordsTable,
  usersTable,
} from "@workspace/db";
import {
  GetAdminDashboardResponse,
  GetTutorDashboardResponse,
} from "@workspace/api-zod";
import { requireAuth, requireAdmin } from "../lib/auth";
import { computeAttendanceTotals } from "../lib/attendance-calc";
import { getRecordsForOrganisation, getRecordsForTutor } from "../lib/attendance-data";
import { learnersWithNames } from "../lib/learners-query";

const router: IRouter = Router();

const isoDaysAgo = (days: number): string => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
};

const getLowAttendanceLearners = async (
  scopeTutorId?: number,
): Promise<
  { learnerId: number; learnerName: string; learnerRef: string; totals: ReturnType<typeof computeAttendanceTotals> }[]
> => {
  const [settingsRow] = await db.select().from((await import("@workspace/db")).appSettingsTable);
  const threshold = settingsRow?.lowAttendanceThreshold ?? 85;

  const learners = scopeTutorId
    ? await learnersWithNames.where(eq(learnersTable.tutorId, scopeTutorId))
    : await learnersWithNames;

  const { getRecordsForLearner } = await import("../lib/attendance-data");
  const results = await Promise.all(
    learners.map(async (learner) => {
      const records = await getRecordsForLearner(learner.id);
      const totals = computeAttendanceTotals(records);
      return {
        learnerId: learner.id,
        learnerName: `${learner.firstName} ${learner.lastName}`,
        learnerRef: learner.learnerRef,
        totals,
      };
    }),
  );

  return results.filter(
    (r) => r.totals.sessionCount > 0 && r.totals.attendancePercentage < threshold,
  );
};

const getSessionsAwaitingCompletion = async (scopeTutorId?: number) => {
  // Only sessions today or in the past can be "awaiting completion" - future
  // sessions haven't happened yet, so an incomplete register isn't a problem.
  const filters = [lte(attendanceSessionsTable.sessionDate, isoDaysAgo(0))];
  if (scopeTutorId) filters.push(eq(cohortsTable.tutorId, scopeTutorId));

  const sessions = await db
    .select({
      id: attendanceSessionsTable.id,
      cohortId: attendanceSessionsTable.cohortId,
      cohortName: cohortsTable.name,
      sessionDate: attendanceSessionsTable.sessionDate,
      tutorName: sql<string>`concat(${tutorsTable.firstName}, ' ', ${tutorsTable.lastName})`,
    })
    .from(attendanceSessionsTable)
    .innerJoin(cohortsTable, eq(attendanceSessionsTable.cohortId, cohortsTable.id))
    .leftJoin(tutorsTable, eq(cohortsTable.tutorId, tutorsTable.id))
    .where(filters.length > 0 ? and(...filters) : undefined);

  const withRecordCounts = await Promise.all(
    sessions.map(async (s) => {
      const [{ recorded }] = await db
        .select({ recorded: sql<number>`count(*)::int` })
        .from(attendanceRecordsTable)
        .where(eq(attendanceRecordsTable.sessionId, s.id));
      const [{ expected }] = await db
        .select({ expected: sql<number>`count(*)::int` })
        .from(learnersTable)
        .where(eq(learnersTable.cohortId, s.cohortId));
      return { ...s, recorded, expected };
    }),
  );

  return withRecordCounts
    .filter((s) => s.recorded < s.expected)
    .map(({ recorded: _r, expected: _e, ...rest }) => rest);
};

const getRecentlyEditedAttendance = async (scopeTutorId?: number) => {
  const filters = [];
  if (scopeTutorId) filters.push(eq(cohortsTable.tutorId, scopeTutorId));

  const rows = await db
    .select({
      id: attendanceRecordsTable.id,
      sessionId: attendanceRecordsTable.sessionId,
      cohortName: cohortsTable.name,
      learnerName: sql<string>`concat(${learnersTable.firstName}, ' ', ${learnersTable.lastName})`,
      status: attendanceRecordsTable.status,
      editedBy: sql<string>`concat(${usersTable.firstName}, ' ', ${usersTable.lastName})`,
      editedAt: attendanceRecordsTable.updatedAt,
    })
    .from(attendanceRecordsTable)
    .innerJoin(
      attendanceSessionsTable,
      eq(attendanceRecordsTable.sessionId, attendanceSessionsTable.id),
    )
    .innerJoin(cohortsTable, eq(attendanceSessionsTable.cohortId, cohortsTable.id))
    .innerJoin(learnersTable, eq(attendanceRecordsTable.learnerId, learnersTable.id))
    .leftJoin(usersTable, eq(attendanceRecordsTable.lastEditedBy, usersTable.id))
    .where(filters.length > 0 ? and(...filters) : undefined)
    .orderBy(sql`${attendanceRecordsTable.updatedAt} desc`)
    .limit(10);

  return rows.map((r) => ({ ...r, editedBy: r.editedBy ?? "Unknown" }));
};

router.get("/dashboard/admin", requireAdmin, async (_req, res): Promise<void> => {
  const [{ activeLearners }] = await db
    .select({ activeLearners: sql<number>`count(*)::int` })
    .from(learnersTable)
    .where(eq(learnersTable.status, "active"));
  const [{ activeTutors }] = await db
    .select({ activeTutors: sql<number>`count(*)::int` })
    .from(tutorsTable)
    .where(eq(tutorsTable.active, true));
  const [{ activeCohorts }] = await db
    .select({ activeCohorts: sql<number>`count(*)::int` })
    .from(cohortsTable)
    .where(eq(cohortsTable.active, true));

  const weekRecords = await getRecordsForOrganisation({ dateFrom: isoDaysAgo(7) });
  const monthRecords = await getRecordsForOrganisation({ dateFrom: isoDaysAgo(30) });

  res.json(
    GetAdminDashboardResponse.parse({
      activeLearners,
      activeTutors,
      activeCohorts,
      attendancePercentageWeek: computeAttendanceTotals(weekRecords).attendancePercentage,
      attendancePercentageMonth: computeAttendanceTotals(monthRecords).attendancePercentage,
      sessionsAwaitingCompletion: await getSessionsAwaitingCompletion(),
      recentlyEditedAttendance: await getRecentlyEditedAttendance(),
      lowAttendanceLearners: await getLowAttendanceLearners(),
    }),
  );
});

router.get("/dashboard/tutor", requireAuth, async (req, res): Promise<void> => {
  const tutorId = req.session.tutorId;
  if (!tutorId) {
    res.status(403).json({ error: "No tutor profile associated with this account" });
    return;
  }

  const cohorts = await db
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
    .where(eq(cohortsTable.tutorId, tutorId));

  const cohortSummaries = await Promise.all(
    cohorts.map(async (cohort) => {
      const [{ learnerCount }] = await db
        .select({ learnerCount: sql<number>`count(*)::int` })
        .from(learnersTable)
        .where(eq(learnersTable.cohortId, cohort.id));
      const { getRecordsForCohort } = await import("../lib/attendance-data");
      const records = await getRecordsForCohort(cohort.id);
      return {
        cohort,
        learnerCount,
        attendancePercentage: computeAttendanceTotals(records).attendancePercentage,
      };
    }),
  );

  const upcoming = await db
    .select({
      id: attendanceSessionsTable.id,
      cohortId: attendanceSessionsTable.cohortId,
      cohortName: cohortsTable.name,
      sessionDate: attendanceSessionsTable.sessionDate,
      tutorName: sql<string>`concat(${tutorsTable.firstName}, ' ', ${tutorsTable.lastName})`,
    })
    .from(attendanceSessionsTable)
    .innerJoin(cohortsTable, eq(attendanceSessionsTable.cohortId, cohortsTable.id))
    .leftJoin(tutorsTable, eq(cohortsTable.tutorId, tutorsTable.id))
    .where(and(eq(cohortsTable.tutorId, tutorId), gte(attendanceSessionsTable.sessionDate, isoDaysAgo(0))))
    .orderBy(attendanceSessionsTable.sessionDate)
    .limit(1);

  res.json(
    GetTutorDashboardResponse.parse({
      cohorts: cohortSummaries,
      nextSession: upcoming[0] ?? null,
      sessionsAwaitingCompletion: await getSessionsAwaitingCompletion(tutorId),
      lowAttendanceLearners: await getLowAttendanceLearners(tutorId),
    }),
  );
});

export default router;
