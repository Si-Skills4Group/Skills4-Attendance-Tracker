import * as React from "react";
import { Link } from "wouter";
import { Card, CardContent } from "@/components/ui/card";
import { CalendarDays, Clock, Users } from "lucide-react";
import { format, parseISO } from "date-fns";
import type { AttendanceSession } from "@workspace/api-client-react";

/** Session cards with completion status, shared between the flat
 * all-cohorts attendance view and a single cohort's sessions page --
 * extracted so the completion-bar/accent-color logic lives in one place. */
export function SessionCardGrid({
  sessions,
  isLoading,
  emptyTitle = "No sessions found",
  emptyDescription,
  emptyAction,
  showCohortName = true,
}: {
  sessions: AttendanceSession[];
  isLoading: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  emptyAction?: React.ReactNode;
  showCohortName?: boolean;
}) {
  if (isLoading) {
    return (
      <div className="flex justify-center py-20">
        <div className="w-8 h-8 rounded-full border-4 border-primary border-t-transparent animate-spin"></div>
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <Card className="border-dashed bg-muted/10 page-transition-enter stagger-2">
        <CardContent className="flex flex-col items-center justify-center py-16 text-center">
          <CalendarDays className="w-12 h-12 text-muted-foreground/30 mb-4" />
          <h3 className="text-lg font-semibold text-foreground mb-1">{emptyTitle}</h3>
          {emptyDescription && (
            <p className="text-sm text-muted-foreground max-w-sm mb-6">{emptyDescription}</p>
          )}
          {emptyAction}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 page-transition-enter stagger-2">
      {sessions.map((session) => {
        const isCancelled = session.registerStatus === "cancelled";
        const isComplete = session.registerStatus === "completed";
        // Only flag incomplete sessions as "needs attention" if they're today or in the past -
        // a future session naturally has no records yet, so it shouldn't be highlighted amber.
        // NOTE: session.sessionDate arrives as a coerced Date object (response date fields go
        // through zod.coerce.date()), so compare Date-to-Date, not Date-to-string.
        const sessionDateOnly = new Date(session.sessionDate);
        sessionDateOnly.setHours(0, 0, 0, 0);
        const todayOnly = new Date();
        todayOnly.setHours(0, 0, 0, 0);
        const isFuture = sessionDateOnly.getTime() > todayOnly.getTime();
        const needsAttention = !isCancelled && !isComplete && !isFuture;
        const accentClass = isCancelled
          ? 'border-l-4 border-l-muted'
          : isComplete
            ? 'border-l-4 border-l-emerald-500'
            : needsAttention
              ? 'border-l-4 border-l-amber-500'
              : 'border-l-4 border-l-muted';
        const completionLabelClass = isCancelled
          ? "text-muted-foreground"
          : isComplete
            ? "text-emerald-600"
            : needsAttention
              ? "text-amber-600"
              : "text-muted-foreground";
        const completionBarClass = isComplete
          ? 'bg-emerald-500'
          : needsAttention
            ? 'bg-amber-500'
            : 'bg-muted-foreground/40';
        return (
          <Link key={session.id} href={`/attendance/${session.id}`}>
            <Card className={`h-full overflow-hidden transition-all hover:border-primary/50 hover:shadow-md cursor-pointer group ${accentClass} ${isCancelled ? 'opacity-70' : ''}`}>
              <div className="p-5 flex flex-col h-full">
                <div className="flex justify-between items-start mb-3">
                  <div>
                    {showCohortName && (
                      <h3 className="font-bold text-lg text-foreground group-hover:text-primary transition-colors leading-tight">{session.cohortName}</h3>
                    )}
                    {session.title && <p className="text-sm text-muted-foreground mt-0.5">{session.title}</p>}
                  </div>
                  <div className="text-right shrink-0 bg-muted/30 px-2 py-1 rounded-md text-xs font-medium">
                    {format(parseISO(session.sessionDate), "MMM d")}
                  </div>
                </div>

                <div className="space-y-3 mt-4 mb-6 text-sm text-muted-foreground">
                  <div className="flex items-center gap-2">
                    <Clock className="w-4 h-4 text-muted-foreground/70" />
                    <span>{session.plannedStartTime.substring(0, 5)} - {session.plannedEndTime.substring(0, 5)} ({session.plannedDurationHours}h)</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Users className="w-4 h-4 text-muted-foreground/70" />
                      <span>{session.effectiveTutorName}</span>
                      {session.coverTutorId != null && (
                        <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-400">
                          Cover
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="mt-auto pt-4 border-t border-muted/50">
                  <div className={`text-sm font-semibold ${completionLabelClass}`}>
                    {isCancelled ? "Session cancelled" : isComplete ? "Register complete" : "Register incomplete"}
                  </div>
                  {!isCancelled && (
                    <>
                      <p className="text-xs text-muted-foreground mt-1">
                        {session.recordedCount} of {session.expectedCount} learner{session.expectedCount === 1 ? "" : "s"} recorded
                      </p>
                      <div className="w-full bg-muted/30 h-1.5 rounded-full mt-2 overflow-hidden">
                        <div
                          className={`h-full ${completionBarClass}`}
                          style={{ width: `${session.expectedCount ? (session.recordedCount / session.expectedCount) * 100 : 0}%` }}
                        ></div>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}
