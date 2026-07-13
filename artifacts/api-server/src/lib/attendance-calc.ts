import type { AttendanceRecord } from "@workspace/db";

export interface AttendanceTotals {
  scheduledHours: number;
  attendedHours: number;
  authorisedAbsenceHours: number;
  unauthorisedAbsenceHours: number;
  lateCount: number;
  sessionCount: number;
  attendancePercentage: number;
}

export interface AttendanceRecordWithSession {
  status: AttendanceRecord["status"];
  hoursAttended: number;
  plannedDurationHours: number;
}

// A record only counts toward the learner's schedule when they were expected
// to be there. "not_expected" (e.g. not yet allocated to the cohort) and
// "withdrawn" (recorded after the learner left the programme) are excluded.
const isScheduled = (status: AttendanceRecord["status"]): boolean =>
  status !== "not_expected" && status !== "withdrawn";

export const computeAttendanceTotals = (
  records: AttendanceRecordWithSession[],
): AttendanceTotals => {
  const totals: AttendanceTotals = {
    scheduledHours: 0,
    attendedHours: 0,
    authorisedAbsenceHours: 0,
    unauthorisedAbsenceHours: 0,
    lateCount: 0,
    sessionCount: 0,
    attendancePercentage: 0,
  };

  for (const record of records) {
    if (!isScheduled(record.status)) continue;

    totals.sessionCount += 1;
    totals.scheduledHours += record.plannedDurationHours;

    if (record.status === "present" || record.status === "late") {
      totals.attendedHours += record.hoursAttended;
    }
    if (record.status === "late") {
      totals.lateCount += 1;
    }
    if (record.status === "absent_authorised") {
      totals.authorisedAbsenceHours += record.plannedDurationHours;
    }
    if (record.status === "absent_unauthorised") {
      totals.unauthorisedAbsenceHours += record.plannedDurationHours;
    }
  }

  totals.attendancePercentage =
    totals.scheduledHours > 0
      ? Math.round((totals.attendedHours / totals.scheduledHours) * 1000) / 10
      : 0;

  return totals;
};
