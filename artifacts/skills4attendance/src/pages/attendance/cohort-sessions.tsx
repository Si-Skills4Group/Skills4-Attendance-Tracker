import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useGetCohort,
  useListAttendanceSessions,
  useCreateAttendanceSession,
  useGetCurrentUser,
  getGetCohortQueryKey,
  getListAttendanceSessionsQueryKey,
} from "@workspace/api-client-react";
import { useParams, useSearch, Link } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SessionCardGrid } from "@/components/session-card-grid";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { format } from "date-fns";
import { ArrowLeft, Plus, Loader2, User, Users, AlertCircle } from "lucide-react";

const CONFLICT_MESSAGES: Record<string, string> = {
  duplicate_session: "A session already exists for this cohort on this date and start time.",
  outside_cohort_date_range: "This date falls outside the cohort's start/end dates.",
};

// Rounds to the nearest whole hour rather than truncating, so e.g. 09:00-13:40
// (4h40m) comes out as 5, not 4 -- closer to what the tutor actually planned.
function calculateDurationHours(start: string, end: string): number {
  if (!start || !end) return 0;
  const [startHours, startMinutes] = start.split(":").map(Number);
  const [endHours, endMinutes] = end.split(":").map(Number);
  const totalMinutes = (endHours * 60 + endMinutes) - (startHours * 60 + startMinutes);
  return Math.max(0, Math.round(totalMinutes / 60));
}

export default function CohortSessionsPage() {
  const params = useParams();
  const cohortId = Number(params.id);
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const { data: currentUser } = useGetCurrentUser();

  // Preserve the attendance cohort-list's filters across this round trip --
  // an explicit link, not just browser history, so "back" is correct even
  // if this page was opened directly (bookmark, refresh, deep link).
  const search = useSearch();
  const fromQuery = new URLSearchParams(search).get("from");
  const backHref = fromQuery ? `/attendance?${fromQuery}` : "/attendance";
  const backLabel = currentUser?.role === "tutor" ? "Back to my cohorts" : "Back to all cohorts";

  const { data: cohort, isLoading: isLoadingCohort, isError: isCohortError } = useGetCohort(cohortId, {
    query: { queryKey: getGetCohortQueryKey(cohortId) },
  });

  const [dateFrom, setDateFrom] = React.useState("");
  const [dateTo, setDateTo] = React.useState("");
  const [statusFilter, setStatusFilter] = React.useState("all");
  const [registerStatusFilter, setRegisterStatusFilter] = React.useState("all");
  const sessionsParams = {
    cohortId,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
    status: statusFilter !== "all" ? (statusFilter as "scheduled" | "cancelled") : undefined,
    registerStatus: registerStatusFilter !== "all"
      ? (registerStatusFilter as "not_started" | "in_progress" | "completed" | "cancelled")
      : undefined,
  };
  const { data: sessions = [], isLoading: isLoadingSessions, isError: isSessionsError } = useListAttendanceSessions(sessionsParams, {
    query: { queryKey: getListAttendanceSessionsQueryKey(sessionsParams), enabled: !!cohort },
  });

  const createMutation = useCreateAttendanceSession();
  const [createModalOpen, setCreateModalOpen] = React.useState(false);
  const [conflictReasons, setConflictReasons] = React.useState<string[] | null>(null);
  const [overrideReason, setOverrideReason] = React.useState("");
  const [sessionDate, setSessionDate] = React.useState<string>(format(new Date(), "yyyy-MM-dd"));
  const [plannedStartTime, setPlannedStartTime] = React.useState<string>("");
  const [plannedEndTime, setPlannedEndTime] = React.useState<string>("");
  const [plannedDurationHours, setPlannedDurationHours] = React.useState<number>(0);
  const [title, setTitle] = React.useState<string>("");

  const isAdmin = currentUser?.role === "admin";
  const isDateOutsideCohortRange = !!sessionDate && !!cohort && (
    sessionDate < cohort.startDate || (!!cohort.endDate && sessionDate > cohort.endDate)
  );

  React.useEffect(() => {
    if (cohort) {
      const start = cohort.sessionStartTime.substring(0, 5);
      const end = cohort.sessionEndTime.substring(0, 5);
      setPlannedStartTime(start);
      setPlannedEndTime(end);
      setPlannedDurationHours(calculateDurationHours(start, end));
    }
  }, [cohort]);

  const handleStartTimeChange = (value: string) => {
    setPlannedStartTime(value);
    setPlannedDurationHours(calculateDurationHours(value, plannedEndTime));
  };

  const handleEndTimeChange = (value: string) => {
    setPlannedEndTime(value);
    setPlannedDurationHours(calculateDurationHours(plannedStartTime, value));
  };

  const handleCreate = (force = false) => {
    if (!sessionDate || !plannedStartTime || !plannedEndTime || !title.trim()) return;

    createMutation.mutate({
      data: {
        cohortId,
        sessionDate,
        plannedStartTime: `${plannedStartTime}:00`,
        plannedEndTime: `${plannedEndTime}:00`,
        plannedDurationHours,
        title: title.trim(),
        force,
        overrideReason: force ? overrideReason.trim() : undefined,
      }
    }, {
      onSuccess: () => {
        toast({ title: "Session created" });
        setCreateModalOpen(false);
        setConflictReasons(null);
        setOverrideReason("");
        queryClient.invalidateQueries({ queryKey: getListAttendanceSessionsQueryKey() });
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        const reasons = (err as { data?: { reasons?: string[] } } | undefined)?.data?.reasons;
        if (status === 409 && reasons) {
          setConflictReasons(reasons);
        } else {
          toast({ title: "Failed to create", description: getErrorMessage(err), variant: "destructive" });
        }
      }
    });
  };

  if (isLoadingCohort) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;
  }

  if (isCohortError || !cohort) {
    return (
      <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
        <Button variant="ghost" size="sm" asChild className="mb-4 -ml-2 text-muted-foreground hover:text-foreground">
          <Link href={backHref}><ArrowLeft className="w-4 h-4 mr-2" /> {backLabel}</Link>
        </Button>
        <Card className="border-dashed border-destructive/40 bg-destructive/5">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <AlertCircle className="w-10 h-10 text-destructive/60 mb-3" />
            <h3 className="text-lg font-semibold text-foreground mb-1">Cohort not found</h3>
            <p className="text-sm text-muted-foreground max-w-sm">This cohort doesn't exist, or you don't have access to it.</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Attendance", href: "/attendance" }, { label: cohort.name }]} />

      <Button variant="ghost" size="sm" asChild className="mb-4 -ml-2 text-muted-foreground hover:text-foreground">
        <Link href={backHref}><ArrowLeft className="w-4 h-4 mr-2" /> {backLabel}</Link>
      </Button>

      <Card className="mb-6 shadow-sm page-transition-enter">
        <CardContent className="p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-bold tracking-tight text-foreground">{cohort.name}</h1>
              {!cohort.active && (
                <span className="text-[10px] font-bold uppercase tracking-wider bg-muted text-muted-foreground px-2 py-1 rounded">Inactive</span>
              )}
            </div>
            <p className="text-muted-foreground mt-1">{cohort.programme} &middot; Level {cohort.level}</p>
          </div>
          <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
            <div className="flex items-center gap-2">
              <User className="w-4 h-4 text-muted-foreground/70" />
              <span>{cohort.tutorName || "No tutor assigned"}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6 page-transition-enter stagger-1">
        <div className="flex items-center gap-4 bg-card p-2 rounded-lg border shadow-sm w-fit">
          <div className="flex items-center px-2 text-sm font-medium text-muted-foreground">Filter:</div>
          <Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="h-8 w-auto text-sm border-transparent bg-muted/20" />
          <span className="text-muted-foreground">-</span>
          <Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="h-8 w-auto text-sm border-transparent bg-muted/20" />
          {(dateFrom || dateTo) && (
            <button
              onClick={() => { setDateFrom(""); setDateTo(""); }}
              className="text-xs text-muted-foreground hover:text-foreground underline ml-1"
            >
              Clear
            </button>
          )}
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="h-8 w-auto text-sm border-transparent bg-muted/20"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All sessions</SelectItem>
              <SelectItem value="scheduled">Scheduled</SelectItem>
              <SelectItem value="cancelled">Cancelled</SelectItem>
            </SelectContent>
          </Select>
          <Select value={registerStatusFilter} onValueChange={setRegisterStatusFilter}>
            <SelectTrigger className="h-8 w-auto text-sm border-transparent bg-muted/20"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any register status</SelectItem>
              <SelectItem value="not_started">Not started</SelectItem>
              <SelectItem value="in_progress">In progress</SelectItem>
              <SelectItem value="completed">Register complete</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <Dialog open={createModalOpen} onOpenChange={(o) => { setCreateModalOpen(o); setConflictReasons(null); setOverrideReason(""); }}>
          <DialogTrigger asChild>
            <Button className="hover-elevate shadow-sm" size="sm">
              <Plus className="w-4 h-4 mr-2" /> New Session
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-[425px]">
            <DialogHeader>
              <DialogTitle>Create Attendance Session</DialogTitle>
              <DialogDescription>Generate a new register for {cohort.name}.</DialogDescription>
            </DialogHeader>

            {conflictReasons ? (
              <div className="py-6 space-y-4">
                <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-4 rounded-md">
                  <h4 className="font-semibold text-amber-800 dark:text-amber-500 flex items-center gap-2 mb-2">
                    Session Conflict Detected
                  </h4>
                  <ul className="text-sm text-amber-700 dark:text-amber-400 list-disc list-inside space-y-1">
                    {conflictReasons.map((reason) => (
                      <li key={reason}>{CONFLICT_MESSAGES[reason] || reason}</li>
                    ))}
                  </ul>
                </div>
                {isAdmin ? (
                  <div className="space-y-2">
                    <Label htmlFor="session-override-reason">Reason for creating anyway</Label>
                    <Textarea
                      id="session-override-reason"
                      value={overrideReason}
                      onChange={e => setOverrideReason(e.target.value)}
                      placeholder="Explain why this session should be created despite the conflict"
                      rows={3}
                    />
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Only an Administrator can confirm and create a session despite this conflict.
                  </p>
                )}
                <DialogFooter className="mt-6">
                  <Button variant="outline" onClick={() => { setConflictReasons(null); setOverrideReason(""); }}>Go Back</Button>
                  {isAdmin && (
                    <Button
                      variant="destructive"
                      onClick={() => handleCreate(true)}
                      disabled={createMutation.isPending || !overrideReason.trim()}
                    >
                      {createMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                      Create Anyway
                    </Button>
                  )}
                </DialogFooter>
              </div>
            ) : (
              <div className="grid gap-4 py-4">
                <div className="space-y-2">
                  <Label htmlFor="new-session-date">Date</Label>
                  <Input id="new-session-date" type="date" value={sessionDate} onChange={e => setSessionDate(e.target.value)} />
                  {isDateOutsideCohortRange && (
                    <p className="text-xs text-amber-600 dark:text-amber-500 flex items-center gap-1">
                      <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                      This date falls outside the cohort's start/end dates.
                    </p>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="new-session-start-time">Start Time</Label>
                    <Input id="new-session-start-time" type="time" value={plannedStartTime} onChange={e => handleStartTimeChange(e.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="new-session-end-time">End Time</Label>
                    <Input id="new-session-end-time" type="time" value={plannedEndTime} onChange={e => handleEndTimeChange(e.target.value)} />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="new-session-duration">Duration (Hours)</Label>
                  <Input id="new-session-duration" type="number" step="0.5" value={plannedDurationHours} onChange={e => setPlannedDurationHours(parseFloat(e.target.value))} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="new-session-title">Title / Topic</Label>
                  <Input id="new-session-title" placeholder="e.g. Module 1 Intro" value={title} onChange={e => setTitle(e.target.value)} required />
                </div>
                <DialogFooter className="mt-4">
                  <Button onClick={() => handleCreate(false)} disabled={createMutation.isPending || !title.trim()}>
                    {createMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                    Create Register
                  </Button>
                </DialogFooter>
              </div>
            )}
          </DialogContent>
        </Dialog>
      </div>

      {isSessionsError ? (
        <Card className="border-dashed border-destructive/40 bg-destructive/5">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <AlertCircle className="w-10 h-10 text-destructive/60 mb-3" />
            <h3 className="text-lg font-semibold text-foreground mb-1">Couldn't load sessions</h3>
            <p className="text-sm text-muted-foreground max-w-sm">Something went wrong fetching sessions for this cohort. Please try again.</p>
          </CardContent>
        </Card>
      ) : (
        <SessionCardGrid
          sessions={sessions}
          isLoading={isLoadingSessions}
          emptyTitle="No sessions yet"
          emptyDescription="Create the first session for this cohort to start taking attendance."
          emptyAction={<Button onClick={() => setCreateModalOpen(true)}>Create Session</Button>}
          showCohortName={false}
        />
      )}
    </div>
  );
}
