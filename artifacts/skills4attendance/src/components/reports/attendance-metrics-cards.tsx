import * as React from "react";
import { AttendanceMetrics } from "@workspace/api-client-react";
import { Card, CardContent } from "@/components/ui/card";

function minutesToHours(minutes: number): string {
  return (minutes / 60).toFixed(1);
}

/** The one summary-card layout shared by every Phase 9 report that carries
 * an AttendanceMetrics -- learner/cohort/tutor/organisation/absence/
 * lateness/attendance-hours reports all show exactly this shape, powered
 * by the same Phase 8 calculation engine, so the numbers always reconcile
 * with the dashboards. */
export function AttendanceMetricsCards({ metrics }: { metrics: AttendanceMetrics }) {
  if (metrics.insufficientData) {
    return (
      <Card className="bg-muted/30 border-dashed shadow-none mb-6">
        <CardContent className="p-4 text-sm text-muted-foreground">
          Insufficient recorded attendance data for this period to show a reliable percentage
          ({metrics.completedRegisterRowCount} completed register row{metrics.completedRegisterRowCount === 1 ? "" : "s"}).
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
      <Card className="bg-primary/5 border-primary/10 shadow-sm">
        <CardContent className="p-4">
          <p className="text-sm font-medium text-muted-foreground">Attendance Rate</p>
          <div className="text-3xl font-bold font-mono text-primary mt-1">
            {metrics.attendancePercentage != null ? `${metrics.attendancePercentage.toFixed(1)}%` : "—"}
          </div>
        </CardContent>
      </Card>
      <Card className="shadow-sm">
        <CardContent className="p-4">
          <p className="text-sm font-medium text-muted-foreground">Hours Attended</p>
          <div className="text-3xl font-bold font-mono text-foreground mt-1">
            {minutesToHours(metrics.attendedMinutes)} <span className="text-lg text-muted-foreground">/ {minutesToHours(metrics.expectedMinutes)}</span>
          </div>
        </CardContent>
      </Card>
      <Card className="shadow-sm">
        <CardContent className="p-4">
          <p className="text-sm font-medium text-muted-foreground">Absences (Auth/Unauth)</p>
          <div className="text-3xl font-bold font-mono mt-1">
            <span className="text-amber-600">{metrics.authorisedAbsenceSessions}</span>
            <span className="text-lg text-muted-foreground"> / </span>
            <span className="text-rose-600">{metrics.unauthorisedAbsenceSessions}</span>
          </div>
        </CardContent>
      </Card>
      <Card className="shadow-sm">
        <CardContent className="p-4">
          <p className="text-sm font-medium text-muted-foreground">Late Sessions</p>
          <div className="text-3xl font-bold font-mono text-foreground mt-1">{metrics.lateSessionCount}</div>
          {metrics.averageMinutesLate != null && (
            <p className="text-xs text-muted-foreground mt-1">avg {metrics.averageMinutesLate.toFixed(0)} min late</p>
          )}
        </CardContent>
      </Card>
      {metrics.missingRecordCount > 0 && (
        <Card className="col-span-2 md:col-span-4 bg-amber-50 dark:bg-amber-950/20 border-amber-200 dark:border-amber-900 shadow-none">
          <CardContent className="p-3 text-sm text-amber-800 dark:text-amber-400">
            {metrics.missingRecordCount} session{metrics.missingRecordCount === 1 ? "" : "s"} with no register entry recorded -- shown as missing data, not counted as absence.
          </CardContent>
        </Card>
      )}
    </div>
  );
}
