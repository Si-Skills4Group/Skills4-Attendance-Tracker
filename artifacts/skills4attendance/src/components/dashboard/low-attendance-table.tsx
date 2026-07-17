import * as React from "react";
import { Link } from "wouter";
import { LearnerAttendanceSummaryRow } from "@workspace/api-client-react";
import { Badge } from "@/components/ui/badge";
import { format, parseISO } from "date-fns";

/** Shared by both the tutor and admin dashboards. Always shows the
 * threshold being applied, keeps Bud context in its own clearly-labelled
 * block (never merged into the attendance percentage), and renders an
 * explicit empty/insufficient-data state rather than just an empty list. */
export function LowAttendanceTable({
  rows,
  threshold,
  emptyMessage,
}: {
  rows: LearnerAttendanceSummaryRow[];
  threshold: number;
  emptyMessage: string;
}) {
  if (rows.length === 0) {
    return <div className="p-8 text-center text-muted-foreground">{emptyMessage}</div>;
  }

  return (
    <div className="divide-y">
      <div className="px-4 py-2 text-xs text-muted-foreground bg-muted/10">
        Learners below {threshold}% attendance (minimum data required to flag)
      </div>
      {rows.map((row) => {
        const m = row.metrics;
        const expectedHours = m.expectedMinutes / 60;
        const attendedHours = m.attendedMinutes / 60;
        return (
          <div key={row.learnerId} className="p-4 hover:bg-muted/30 transition-colors">
            <div className="flex items-center justify-between">
              <div>
                <Link href={`/learners/${row.learnerId}`} className="font-semibold text-sm hover:underline hover:text-primary transition-colors">
                  {row.learnerName}
                </Link>
                <p className="text-xs text-muted-foreground mt-1">
                  {row.learnerRef}{row.cohortName ? ` · ${row.cohortName}` : ""}
                </p>
              </div>
              <div className="text-right">
                <div className="text-sm font-mono font-bold text-destructive">
                  {m.attendancePercentage != null ? `${m.attendancePercentage.toFixed(1)}%` : "—"}
                </div>
                <div className="text-xs text-muted-foreground">
                  {attendedHours.toFixed(1)} / {expectedHours.toFixed(1)} hrs
                </div>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5 mt-2 text-[11px]">
              <Badge variant="outline" className="bg-blue-50 text-blue-700 border-blue-200">Auth absence: {m.authorisedAbsenceSessions}</Badge>
              <Badge variant="outline" className="bg-rose-50 text-rose-700 border-rose-200">Unauth absence: {m.unauthorisedAbsenceSessions}</Badge>
              <Badge variant="outline" className="bg-amber-50 text-amber-700 border-amber-200">Late: {m.lateSessionCount}</Badge>
              {m.attendanceDataCompleteness != null && (
                <Badge variant="outline" className="bg-slate-50 text-slate-700 border-slate-200">
                  Data completeness: {m.attendanceDataCompleteness.toFixed(0)}%
                </Badge>
              )}
              {m.insufficientData && (
                <Badge variant="outline" className="bg-muted text-muted-foreground">Insufficient data</Badge>
              )}
            </div>
            {row.bud && (
              <div className="mt-2 pt-2 border-t border-dashed flex flex-wrap gap-3 text-[11px] text-muted-foreground">
                <span className="font-medium text-foreground/70">Bud:</span>
                {row.bud.activityProgress != null && <span>Activity progress {row.bud.activityProgress.toFixed(0)}%</span>}
                {row.bud.activitiesOverdue != null && <span>{row.bud.activitiesOverdue} overdue</span>}
                {row.bud.syncedAt && <span>Synced {format(parseISO(row.bud.syncedAt), "d MMM yyyy, HH:mm")}</span>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
