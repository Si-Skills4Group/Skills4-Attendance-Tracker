import { eq, and, gte, lte, type SQL } from "drizzle-orm";
import {
  db,
  attendanceRecordsTable,
  attendanceSessionsTable,
  cohortsTable,
} from "@workspace/db";
import type { AttendanceRecordWithSession } from "./attendance-calc";

export interface DateRange {
  dateFrom?: string;
  dateTo?: string;
}

const sessionDateFilters = (range?: DateRange): SQL[] => {
  const filters: SQL[] = [];
  if (range?.dateFrom) {
    filters.push(gte(attendanceSessionsTable.sessionDate, range.dateFrom));
  }
  if (range?.dateTo) {
    filters.push(lte(attendanceSessionsTable.sessionDate, range.dateTo));
  }
  return filters;
};

export const getRecordsForLearner = async (
  learnerId: number,
  range?: DateRange,
): Promise<AttendanceRecordWithSession[]> => {
  const rows = await db
    .select({
      status: attendanceRecordsTable.status,
      hoursAttended: attendanceRecordsTable.hoursAttended,
      plannedDurationHours: attendanceSessionsTable.plannedDurationHours,
    })
    .from(attendanceRecordsTable)
    .innerJoin(
      attendanceSessionsTable,
      eq(attendanceRecordsTable.sessionId, attendanceSessionsTable.id),
    )
    .where(
      and(
        eq(attendanceRecordsTable.learnerId, learnerId),
        ...sessionDateFilters(range),
      ),
    );
  return rows;
};

export const getRecordsForCohort = async (
  cohortId: number,
  range?: DateRange,
): Promise<AttendanceRecordWithSession[]> => {
  const rows = await db
    .select({
      status: attendanceRecordsTable.status,
      hoursAttended: attendanceRecordsTable.hoursAttended,
      plannedDurationHours: attendanceSessionsTable.plannedDurationHours,
    })
    .from(attendanceRecordsTable)
    .innerJoin(
      attendanceSessionsTable,
      eq(attendanceRecordsTable.sessionId, attendanceSessionsTable.id),
    )
    .where(
      and(
        eq(attendanceSessionsTable.cohortId, cohortId),
        ...sessionDateFilters(range),
      ),
    );
  return rows;
};

// Approximation: attributes attendance to a tutor via each session's cohort's
// *current* tutor assignment, not a historical snapshot at the time of the
// session. Acceptable for reporting purposes; allocation changes are tracked
// separately in learner_allocation_history for audit purposes.
export const getRecordsForTutor = async (
  tutorId: number,
  range?: DateRange,
): Promise<AttendanceRecordWithSession[]> => {
  const rows = await db
    .select({
      status: attendanceRecordsTable.status,
      hoursAttended: attendanceRecordsTable.hoursAttended,
      plannedDurationHours: attendanceSessionsTable.plannedDurationHours,
    })
    .from(attendanceRecordsTable)
    .innerJoin(
      attendanceSessionsTable,
      eq(attendanceRecordsTable.sessionId, attendanceSessionsTable.id),
    )
    .innerJoin(
      cohortsTable,
      eq(attendanceSessionsTable.cohortId, cohortsTable.id),
    )
    .where(
      and(eq(cohortsTable.tutorId, tutorId), ...sessionDateFilters(range)),
    );
  return rows;
};

export const getRecordsForOrganisation = async (
  range?: DateRange,
  programme?: string,
): Promise<
  (AttendanceRecordWithSession & { cohortId: number; programme: string })[]
> => {
  const rows = await db
    .select({
      status: attendanceRecordsTable.status,
      hoursAttended: attendanceRecordsTable.hoursAttended,
      plannedDurationHours: attendanceSessionsTable.plannedDurationHours,
      cohortId: attendanceSessionsTable.cohortId,
      programme: cohortsTable.programme,
    })
    .from(attendanceRecordsTable)
    .innerJoin(
      attendanceSessionsTable,
      eq(attendanceRecordsTable.sessionId, attendanceSessionsTable.id),
    )
    .innerJoin(
      cohortsTable,
      eq(attendanceSessionsTable.cohortId, cohortsTable.id),
    )
    .where(
      and(
        ...sessionDateFilters(range),
        ...(programme ? [eq(cohortsTable.programme, programme)] : []),
      ),
    );
  return rows;
};
