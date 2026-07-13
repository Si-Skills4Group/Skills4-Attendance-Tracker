import * as React from "react";
import { 
  useListAttendanceSessions, 
  useCreateAttendanceSession,
  useListCohorts,
  useGetCurrentUser
} from "@workspace/api-client-react";
import { Link, useLocation } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { format, parseISO } from "date-fns";
import { CalendarDays, Plus, Clock, Users, ArrowRight, Loader2, Search } from "lucide-react";

export default function AttendancePage() {
  const { data: user } = useGetCurrentUser();
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  
  const [dateFrom, setDateFrom] = React.useState<string>("");
  const [dateTo, setDateTo] = React.useState<string>("");
  const [createModalOpen, setCreateModalOpen] = React.useState(false);
  const [duplicateConfirmMode, setDuplicateConfirmMode] = React.useState(false);
  
  const { data: sessions = [], isLoading, refetch } = useListAttendanceSessions({
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined
  });

  const { data: activeCohorts = [] } = useListCohorts({ active: true });

  const createMutation = useCreateAttendanceSession();

  // Create Form State
  const [cohortId, setCohortId] = React.useState<string>("");
  const [sessionDate, setSessionDate] = React.useState<string>(format(new Date(), "yyyy-MM-dd"));
  const [plannedStartTime, setPlannedStartTime] = React.useState<string>("");
  const [plannedEndTime, setPlannedEndTime] = React.useState<string>("");
  const [plannedDurationHours, setPlannedDurationHours] = React.useState<number>(0);
  const [title, setTitle] = React.useState<string>("");

  // Pre-fill defaults when cohort changes
  React.useEffect(() => {
    if (cohortId) {
      const c = activeCohorts.find(x => String(x.id) === cohortId);
      if (c) {
        setPlannedStartTime(c.sessionStartTime.substring(0, 5));
        setPlannedEndTime(c.sessionEndTime.substring(0, 5));
        // Rough hour calc
        const sH = parseInt(c.sessionStartTime.split(':')[0]);
        const eH = parseInt(c.sessionEndTime.split(':')[0]);
        const dur = Math.max(1, eH - sH);
        setPlannedDurationHours(dur);
      }
    }
  }, [cohortId, activeCohorts]);

  const handleCreate = (force = false) => {
    if (!cohortId || !sessionDate || !plannedStartTime || !plannedEndTime) return;
    
    createMutation.mutate({
      data: {
        cohortId: Number(cohortId),
        sessionDate,
        plannedStartTime: `${plannedStartTime}:00`,
        plannedEndTime: `${plannedEndTime}:00`,
        plannedDurationHours,
        title: title || undefined,
        force
      }
    }, {
      onSuccess: (res) => {
        toast({ title: "Session created" });
        setCreateModalOpen(false);
        setDuplicateConfirmMode(false);
        setLocation(`/attendance/${res.id}`);
      },
      onError: (err: any) => {
        if (err.status === 409) {
          setDuplicateConfirmMode(true);
        } else {
          toast({ title: "Failed to create", description: err.error, variant: "destructive" });
        }
      }
    });
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Attendance" }]} />
      
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Attendance Registers</h1>
          <p className="text-muted-foreground mt-1">Manage class sessions and record attendance.</p>
        </div>
        
        <Dialog open={createModalOpen} onOpenChange={(o) => { setCreateModalOpen(o); setDuplicateConfirmMode(false); }}>
          <DialogTrigger asChild>
            <Button className="hover-elevate shadow-sm">
              <Plus className="w-4 h-4 mr-2" /> New Session
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-[425px]">
            <DialogHeader>
              <DialogTitle>Create Attendance Session</DialogTitle>
              <DialogDescription>
                Generate a new register for a cohort.
              </DialogDescription>
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
                  <Label>Cohort</Label>
                  <Select value={cohortId} onValueChange={setCohortId}>
                    <SelectTrigger><SelectValue placeholder="Select cohort..." /></SelectTrigger>
                    <SelectContent>
                      {activeCohorts.map(c => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
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
                  <Button onClick={() => handleCreate(false)} disabled={createMutation.isPending || !cohortId}>
                    {createMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                    Create Register
                  </Button>
                </DialogFooter>
              </div>
            )}
          </DialogContent>
        </Dialog>
      </div>

      <div className="flex items-center gap-4 mb-6 page-transition-enter stagger-1 bg-card p-2 rounded-lg border shadow-sm w-fit">
        <div className="flex items-center px-2 text-sm font-medium text-muted-foreground">Filter:</div>
        <Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="h-8 w-auto text-sm border-transparent bg-muted/20" />
        <span className="text-muted-foreground">-</span>
        <Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="h-8 w-auto text-sm border-transparent bg-muted/20" />
      </div>

      {isLoading ? (
        <div className="flex justify-center py-20">
          <div className="w-8 h-8 rounded-full border-4 border-primary border-t-transparent animate-spin"></div>
        </div>
      ) : sessions.length === 0 ? (
        <Card className="border-dashed bg-muted/10 page-transition-enter stagger-2">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <CalendarDays className="w-12 h-12 text-muted-foreground/30 mb-4" />
            <h3 className="text-lg font-semibold text-foreground mb-1">No sessions found</h3>
            <p className="text-sm text-muted-foreground max-w-sm mb-6">
              Create a new session to take attendance for your learners.
            </p>
            <Button onClick={() => setCreateModalOpen(true)}>Create Session</Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 page-transition-enter stagger-2">
          {sessions.map((session) => {
            const isComplete = session.recordedCount === session.expectedCount && session.expectedCount > 0;
            return (
              <Link key={session.id} href={`/attendance/${session.id}`}>
                <Card className={`h-full overflow-hidden transition-all hover:border-primary/50 hover:shadow-md cursor-pointer group ${isComplete ? 'border-l-4 border-l-emerald-500' : 'border-l-4 border-l-amber-500'}`}>
                  <div className="p-5 flex flex-col h-full">
                    <div className="flex justify-between items-start mb-3">
                      <div>
                        <h3 className="font-bold text-lg text-foreground group-hover:text-primary transition-colors leading-tight">{session.cohortName}</h3>
                        {session.title && <p className="text-sm text-muted-foreground mt-0.5">{session.title}</p>}
                      </div>
                      <div className="text-right shrink-0 bg-muted/30 px-2 py-1 rounded-md text-xs font-medium">
                        {format(parseISO(session.sessionDate), "MMM d")}
                      </div>
                    </div>
                    
                    <div className="space-y-3 mt-4 mb-6 text-sm text-muted-foreground">
                      <div className="flex items-center gap-2">
                        <Clock className="w-4 h-4 text-muted-foreground/70" />
                        <span>{session.plannedStartTime.substring(0,5)} - {session.plannedEndTime.substring(0,5)} ({session.plannedDurationHours}h)</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Users className="w-4 h-4 text-muted-foreground/70" />
                          <span>{session.tutorName}</span>
                        </div>
                      </div>
                    </div>
                    
                    <div className="mt-auto pt-4 border-t border-muted/50">
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium text-foreground">Completion</span>
                        <span className={isComplete ? "text-emerald-600 font-bold font-mono" : "text-amber-600 font-bold font-mono"}>
                          {session.recordedCount} / {session.expectedCount}
                        </span>
                      </div>
                      <div className="w-full bg-muted/30 h-1.5 rounded-full mt-2 overflow-hidden">
                        <div 
                          className={`h-full ${isComplete ? 'bg-emerald-500' : 'bg-amber-500'}`} 
                          style={{ width: `${session.expectedCount ? (session.recordedCount/session.expectedCount)*100 : 0}%` }}
                        ></div>
                      </div>
                    </div>
                  </div>
                </Card>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  );
}
