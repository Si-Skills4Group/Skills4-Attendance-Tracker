import * as React from "react";
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
import { SessionCardGrid } from "@/components/session-card-grid";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { format } from "date-fns";
import { ArrowLeft, Plus, Loader2, CalendarDays, Clock, User, Users, AlertCircle } from "lucide-react";

export default function CohortSessionsPage() {
  const params = useParams();
  const cohortId = Number(params.id);
  const { toast } = useToast();
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
  const sessionsParams = { cohortId, dateFrom: dateFrom || undefined, dateTo: dateTo || undefined };
  const { data: sessions = [], isLoading: isLoadingSessions, isError: isSessionsError } = useListAttendanceSessions(sessionsParams, {
    query: { queryKey: getListAttendanceSessionsQueryKey(sessionsParams), enabled: !!cohort },
  });

  const createMutation = useCreateAttendanceSession();
  const [createModalOpen, setCreateModalOpen] = React.useState(false);
  const [duplicateConfirmMode, setDuplicateConfirmMode] = React.useState(false);
  const [sessionDate, setSessionDate] = React.useState<string>(format(new Date(), "yyyy-MM-dd"));
  const [plannedStartTime, setPlannedStartTime] = React.useState<string>("");
  const [plannedEndTime, setPlannedEndTime] = React.useState<string>("");
  const [plannedDurationHours, setPlannedDurationHours] = React.useState<number>(0);
  const [title, setTitle] = React.useState<string>("");

  React.useEffect(() => {
    if (cohort) {
      setPlannedStartTime(cohort.sessionStartTime.substring(0, 5));
      setPlannedEndTime(cohort.sessionEndTime.substring(0, 5));
      const sH = parseInt(cohort.sessionStartTime.split(':')[0]);
      const eH = parseInt(cohort.sessionEndTime.split(':')[0]);
      setPlannedDurationHours(Math.max(1, eH - sH));
    }
  }, [cohort]);

  const handleCreate = (force = false) => {
    if (!sessionDate || !plannedStartTime || !plannedEndTime) return;

    createMutation.mutate({
      data: {
        cohortId,
        sessionDate,
        plannedStartTime: `${plannedStartTime}:00`,
        plannedEndTime: `${plannedEndTime}:00`,
        plannedDurationHours,
        title: title || undefined,
        force,
      }
    }, {
      onSuccess: () => {
        toast({ title: "Session created" });
        setCreateModalOpen(false);
        setDuplicateConfirmMode(false);
      },
      onError: (err: any) => {
        if (err.status === 409) {
          setDuplicateConfirmMode(true);
        } else {
          toast({ title: "Failed to create", description: err?.data?.error || err.message, variant: "destructive" });
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
            <div className="flex items-center gap-2">
              <CalendarDays className="w-4 h-4 text-muted-foreground/70" />
              <span className="capitalize">{cohort.deliveryDay}s</span>
            </div>
            <div className="flex items-center gap-2">
              <Clock className="w-4 h-4 text-muted-foreground/70" />
              <span>{cohort.sessionStartTime.substring(0, 5)} - {cohort.sessionEndTime.substring(0, 5)}</span>
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
        </div>

        <Dialog open={createModalOpen} onOpenChange={(o) => { setCreateModalOpen(o); setDuplicateConfirmMode(false); }}>
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

            {duplicateConfirmMode ? (
              <div className="py-6 space-y-4">
                <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-4 rounded-md">
                  <h4 className="font-semibold text-amber-800 dark:text-amber-500 flex items-center gap-2 mb-2">
                    Duplicate Session Detected
                  </h4>
                  <p className="text-sm text-amber-700 dark:text-amber-400">
                    A session already exists for this cohort on {sessionDate}. Are you sure you want to create another one?
                  </p>
                </div>
                <DialogFooter className="mt-6">
                  <Button variant="outline" onClick={() => setDuplicateConfirmMode(false)}>Go Back</Button>
                  <Button variant="destructive" onClick={() => handleCreate(true)} disabled={createMutation.isPending}>
                    {createMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                    Create Anyway
                  </Button>
                </DialogFooter>
              </div>
            ) : (
              <div className="grid gap-4 py-4">
                <div className="space-y-2">
                  <Label>Date</Label>
                  <Input type="date" value={sessionDate} onChange={e => setSessionDate(e.target.value)} />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Start Time</Label>
                    <Input type="time" value={plannedStartTime} onChange={e => setPlannedStartTime(e.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label>End Time</Label>
                    <Input type="time" value={plannedEndTime} onChange={e => setPlannedEndTime(e.target.value)} />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>Duration (Hours)</Label>
                  <Input type="number" step="0.5" value={plannedDurationHours} onChange={e => setPlannedDurationHours(parseFloat(e.target.value))} />
                </div>
                <div className="space-y-2">
                  <Label>Title / Topic (Optional)</Label>
                  <Input placeholder="e.g. Module 1 Intro" value={title} onChange={e => setTitle(e.target.value)} />
                </div>
                <DialogFooter className="mt-4">
                  <Button onClick={() => handleCreate(false)} disabled={createMutation.isPending}>
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
