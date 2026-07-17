import * as React from "react";
import {
  useGetAttendanceSession,
  useSaveAttendanceRegister,
  useMarkAllPresent,
  useUpdateAttendanceSession,
  useCancelAttendanceSession,
  useRefreshSessionRegister,
  useGetCurrentUser,
  AttendanceStatus,
  RegisterEntryInput,
} from "@workspace/api-client-react";
import { useLocation, useParams } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { Loader2, Save, ArrowLeft, CheckCircle2, Clock, CalendarDays, Users, Check, Pencil, Ban, RefreshCw } from "lucide-react";
import { format, parseISO } from "date-fns";
import { RegisterStatusBadge } from "@/components/status-badges";
import { useDebounce } from "@/hooks/use-debounce";
import { useQueryClient } from "@tanstack/react-query";
import { getGetAttendanceSessionQueryKey } from "@workspace/api-client-react";

type DraftEntry = RegisterEntryInput & { _isDirty?: boolean; _originalHours: number; _requireOverrideReason: boolean };

export default function RegisterPage() {
  const params = useParams();
  const sessionId = Number(params.id);
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data: currentUser } = useGetCurrentUser();
  const isAdmin = currentUser?.role === "admin";

  const { data: register, isLoading } = useGetAttendanceSession(sessionId);
  const saveMutation = useSaveAttendanceRegister();
  const markAllMutation = useMarkAllPresent();
  const updateMutation = useUpdateAttendanceSession();
  const cancelMutation = useCancelAttendanceSession();
  const refreshMutation = useRefreshSessionRegister();

  // Local state for edits
  const [drafts, setDrafts] = React.useState<Record<number, DraftEntry>>({});
  const [saveStatus, setSaveStatus] = React.useState<"idle"|"saving"|"saved">("idle");

  // We use debounce to auto-save
  const debouncedDrafts = useDebounce(drafts, 1000);

  // Initialize drafts when data loads
  const initRef = React.useRef<string>("");
  React.useEffect(() => {
    if (register && initRef.current !== JSON.stringify(register.entries)) {
      initRef.current = JSON.stringify(register.entries);
      const newDrafts: Record<number, DraftEntry> = {};
      register.entries.forEach(e => {
        newDrafts[e.learnerId] = {
          learnerId: e.learnerId,
          status: e.status,
          hoursAttended: e.hoursAttended,
          minutesLate: e.minutesLate,
          notes: e.notes || "",
          overrideReason: e.overrideReason || "",
          _originalHours: register.session.plannedDurationHours,
          _requireOverrideReason: e.hoursAttended !== register.session.plannedDurationHours && e.status === "present",
          _isDirty: false
        };
      });
      setDrafts(newDrafts);
    }
  }, [register]);

  // Auto-save effect
  // NOTE: depend only on primitives/stable references (debouncedDrafts, sessionId,
  // saveMutate, queryClient). Including the whole `saveMutation` result object or
  // `register` here caused this effect to re-run on every render (their identity
  // changes on unrelated renders/cache patches), which re-fired mutate() in a tight
  // loop and crashed the app with "Maximum update depth exceeded".
  const lastSavedRef = React.useRef<string>("");
  const saveMutate = saveMutation.mutate;
  const isCancelled = register?.session.status === "cancelled";
  React.useEffect(() => {
    if (isCancelled) return;
    // Only save dirty entries
    const dirtyEntries = Object.values(debouncedDrafts).filter(d => d._isDirty);
    if (dirtyEntries.length === 0) return;

    // Validate: if require override, ensure it's not empty
    const invalid = dirtyEntries.find(d => d._requireOverrideReason && !d.overrideReason);
    if (invalid) return; // Don't auto-save if validation fails

    const payloadString = JSON.stringify(dirtyEntries);
    if (payloadString === lastSavedRef.current) return;
    // Mark as "in flight" immediately so this effect can't re-fire the same
    // payload again before the request resolves.
    lastSavedRef.current = payloadString;

    setSaveStatus("saving");

    const entriesToSave = dirtyEntries.map(({ _isDirty, _originalHours, _requireOverrideReason, ...rest }) => rest);

    saveMutate({ id: sessionId, data: { entries: entriesToSave } }, {
      onSuccess: () => {
        setSaveStatus("saved");
        setTimeout(() => setSaveStatus("idle"), 2000);

        // Mark as clean locally (copy entries instead of mutating them in place,
        // since the mutated objects are also referenced by debouncedDrafts)
        setDrafts(prev => {
          const next = { ...prev };
          dirtyEntries.forEach(d => {
            const existing = next[d.learnerId];
            if (existing) next[d.learnerId] = { ...existing, _isDirty: false };
          });
          return next;
        });

        // Patch query cache instead of full invalidate to prevent jumping
        queryClient.setQueryData(getGetAttendanceSessionQueryKey(sessionId), (old: any) => {
          if (!old) return old;
          const newEntries = old.entries.map((e: any) => {
            const savedDraft = entriesToSave.find(d => d.learnerId === e.learnerId);
            return savedDraft ? { ...e, ...savedDraft } : e;
          });
          return { ...old, entries: newEntries };
        });
      },
      onError: (err) => {
        // Allow retry on the next debounce tick instead of getting stuck.
        lastSavedRef.current = "";
        setSaveStatus("idle");
        toast({ title: "Could not save register", description: getErrorMessage(err), variant: "destructive" });
      }
    });
  }, [debouncedDrafts, sessionId, saveMutate, queryClient, isCancelled]);

  const updateDraft = (learnerId: number, field: keyof DraftEntry, value: any) => {
    setDrafts(prev => {
      const draft = prev[learnerId];
      if (!draft) return prev;

      const next = { ...draft, [field]: value, _isDirty: true };

      // Auto-adjust hours if status changes
      if (field === "status") {
        if (value === "present" || value === "late") {
          next.hoursAttended = next._originalHours;
        } else {
          next.hoursAttended = 0;
          next.minutesLate = 0;
        }
      }

      // Check if override reason is required
      if (next.status === "present" && next.hoursAttended !== next._originalHours) {
        next._requireOverrideReason = true;
      } else {
        next._requireOverrideReason = false;
        if (field !== "overrideReason") next.overrideReason = ""; // clear it if no longer needed
      }

      return { ...prev, [learnerId]: next };
    });
  };

  const handleMarkAllPresent = () => {
    setSaveStatus("saving");
    markAllMutation.mutate({ id: sessionId }, {
      onSuccess: () => {
        setSaveStatus("saved");
        setTimeout(() => setSaveStatus("idle"), 2000);
        queryClient.invalidateQueries({ queryKey: getGetAttendanceSessionQueryKey(sessionId) });
      }
    });
  };

  // ---------------------------------------------------------------------
  // Edit session
  // ---------------------------------------------------------------------
  const [editOpen, setEditOpen] = React.useState(false);
  const [editTitle, setEditTitle] = React.useState("");
  const [editNotes, setEditNotes] = React.useState("");
  const [editDate, setEditDate] = React.useState("");
  const [editStartTime, setEditStartTime] = React.useState("");
  const [editEndTime, setEditEndTime] = React.useState("");
  const [editNeedsConfirm, setEditNeedsConfirm] = React.useState(false);

  const openEditDialog = () => {
    if (!register) return;
    setEditTitle(register.session.title || "");
    setEditNotes(register.session.notes || "");
    setEditDate(register.session.sessionDate.slice(0, 10));
    setEditStartTime(register.session.plannedStartTime.substring(0, 5));
    setEditEndTime(register.session.plannedEndTime.substring(0, 5));
    setEditNeedsConfirm(false);
    setEditOpen(true);
  };

  const submitEdit = (confirmChange = false) => {
    updateMutation.mutate({
      id: sessionId,
      data: {
        title: editTitle.trim() || null,
        notes: editNotes.trim() || null,
        sessionDate: editDate,
        plannedStartTime: `${editStartTime}:00`,
        plannedEndTime: `${editEndTime}:00`,
        confirmChange,
      },
    }, {
      onSuccess: () => {
        toast({ title: "Session updated" });
        setEditOpen(false);
        queryClient.invalidateQueries({ queryKey: getGetAttendanceSessionQueryKey(sessionId) });
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        const reason = (err as { data?: { reason?: string } } | undefined)?.data?.reason;
        if (status === 409 && reason === "attendance_already_recorded") {
          setEditNeedsConfirm(true);
        } else {
          toast({ title: "Could not update session", description: getErrorMessage(err), variant: "destructive" });
        }
      },
    });
  };

  // ---------------------------------------------------------------------
  // Cancel session
  // ---------------------------------------------------------------------
  const [cancelOpen, setCancelOpen] = React.useState(false);
  const [cancelReason, setCancelReason] = React.useState("");
  const [cancelNeedsConfirm, setCancelNeedsConfirm] = React.useState(false);

  const submitCancel = (confirmWithAttendance = false) => {
    cancelMutation.mutate({
      id: sessionId,
      data: { reason: cancelReason.trim(), confirmWithAttendance },
    }, {
      onSuccess: () => {
        toast({ title: "Session cancelled" });
        setCancelOpen(false);
        setCancelReason("");
        setCancelNeedsConfirm(false);
        queryClient.invalidateQueries({ queryKey: getGetAttendanceSessionQueryKey(sessionId) });
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        const reason = (err as { data?: { reason?: string } } | undefined)?.data?.reason;
        if (status === 409 && reason === "attendance_already_recorded") {
          setCancelNeedsConfirm(true);
        } else {
          toast({ title: "Could not cancel session", description: getErrorMessage(err), variant: "destructive" });
        }
      },
    });
  };

  // ---------------------------------------------------------------------
  // Refresh expected learners
  // ---------------------------------------------------------------------
  const [refreshOpen, setRefreshOpen] = React.useState(false);
  const [refreshDiff, setRefreshDiff] = React.useState<{ toAdd: { learnerId: number; learnerName: string }[]; toRemove: { learnerId: number; learnerName: string }[]; blocked: { learnerId: number; learnerName: string }[] } | null>(null);

  const openRefreshDialog = () => {
    setRefreshDiff(null);
    setRefreshOpen(true);
    refreshMutation.mutate({ id: sessionId, data: { confirm: false } }, {
      onSuccess: (result) => {
        if ("toAdd" in result) setRefreshDiff(result);
      },
      onError: (err) => {
        setRefreshOpen(false);
        toast({ title: "Could not preview refresh", description: getErrorMessage(err), variant: "destructive" });
      },
    });
  };

  const confirmRefresh = () => {
    refreshMutation.mutate({ id: sessionId, data: { confirm: true } }, {
      onSuccess: () => {
        toast({ title: "Register refreshed" });
        setRefreshOpen(false);
        setRefreshDiff(null);
        queryClient.invalidateQueries({ queryKey: getGetAttendanceSessionQueryKey(sessionId) });
      },
      onError: (err) => {
        toast({ title: "Could not refresh register", description: getErrorMessage(err), variant: "destructive" });
      },
    });
  };

  if (isLoading || !register) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;
  }

  const { session, entries } = register;
  const todayIso = format(new Date(), "yyyy-MM-dd");
  const canRefresh = isAdmin && session.status !== "cancelled" && session.registerStatus !== "completed"
    && session.sessionDate.slice(0, 10) >= todayIso;

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full flex flex-col h-[calc(100vh-64px)]">
      <div className="shrink-0 page-transition-enter">
        <Breadcrumbs items={[
          { label: "Attendance", href: "/attendance" },
          { label: "Register" }
        ]} />

        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
          <div className="flex items-center gap-4">
            <Button variant="outline" size="icon" onClick={() => setLocation("/attendance")}>
              <ArrowLeft className="w-4 h-4" />
            </Button>
            <div>
              <div className="flex items-center gap-3">
                <h1 className="text-2xl font-bold tracking-tight text-foreground">
                  {session.cohortName}
                </h1>
                <RegisterStatusBadge status={session.registerStatus} />
              </div>
              <p className="text-muted-foreground mt-1 flex items-center gap-3 text-sm flex-wrap">
                <span className="flex items-center"><CalendarDays className="w-3.5 h-3.5 mr-1.5" />{format(parseISO(session.sessionDate), "EEEE, MMM d, yyyy")}</span>
                <span>•</span>
                <span className="flex items-center"><Clock className="w-3.5 h-3.5 mr-1.5" />{session.plannedStartTime.substring(0,5)} - {session.plannedEndTime.substring(0,5)} ({session.plannedDurationHours}h)</span>
                <span>•</span>
                <span className="flex items-center"><Users className="w-3.5 h-3.5 mr-1.5" />{session.tutorName}</span>
                <span>•</span>
                <span>{session.recordedCount} of {session.expectedCount} learner{session.expectedCount === 1 ? "" : "s"} recorded</span>
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm font-medium text-muted-foreground flex items-center mr-2">
              {saveStatus === "saving" && <><Loader2 className="w-3 h-3 mr-1.5 animate-spin" /> Saving...</>}
              {saveStatus === "saved" && <><Check className="w-3 h-3 mr-1.5 text-emerald-500" /> Saved</>}
              {saveStatus === "idle" && <span className="opacity-0">Saved</span>}
            </div>
            {!isCancelled && (
              <Button variant="secondary" onClick={handleMarkAllPresent} disabled={markAllMutation.isPending} className="shadow-sm border">
                <CheckCircle2 className="w-4 h-4 mr-2" /> Mark All Present
              </Button>
            )}
            {!isCancelled && (
              <Button variant="outline" onClick={openEditDialog} className="shadow-sm">
                <Pencil className="w-4 h-4 mr-2" /> Edit
              </Button>
            )}
            {canRefresh && (
              <Button variant="outline" onClick={openRefreshDialog} className="shadow-sm">
                <RefreshCw className="w-4 h-4 mr-2" /> Refresh Expected Learners
              </Button>
            )}
            {isAdmin && !isCancelled && (
              <Button variant="outline" onClick={() => setCancelOpen(true)} className="shadow-sm text-destructive hover:text-destructive">
                <Ban className="w-4 h-4 mr-2" /> Cancel Session
              </Button>
            )}
          </div>
        </div>

        {isCancelled && (
          <div className="bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-800 p-4 rounded-md mb-6">
            <h4 className="font-semibold text-rose-800 dark:text-rose-500 mb-1">This session has been cancelled</h4>
            {session.cancellationReason && (
              <p className="text-sm text-rose-700 dark:text-rose-400">Reason: {session.cancellationReason}</p>
            )}
            <p className="text-sm text-rose-700 dark:text-rose-400 mt-1">
              Attendance cannot be recorded for a cancelled session. Any previously recorded attendance is preserved below for reference.
            </p>
          </div>
        )}
      </div>

      <Card className="flex-1 shadow-sm overflow-hidden flex flex-col min-h-0 page-transition-enter stagger-1">
        <div className="overflow-auto flex-1 relative">
          <Table>
            <TableHeader className="bg-muted/30 sticky top-0 z-10 backdrop-blur-md shadow-sm">
              <TableRow>
                <TableHead className="w-[250px]">Learner</TableHead>
                <TableHead className="w-[180px]">Status</TableHead>
                <TableHead className="w-[120px]">Hours</TableHead>
                <TableHead className="w-[120px]">Mins Late</TableHead>
                <TableHead>Notes / Reason</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map(entry => {
                const draft = drafts[entry.learnerId];
                if (!draft) return null;

                return (
                  <TableRow key={entry.learnerId} className="hover:bg-muted/10">
                    <TableCell>
                      <div className="font-medium text-sm">{entry.learnerName}</div>
                      <div className="text-xs text-muted-foreground font-mono mt-0.5">{entry.learnerRef}</div>
                    </TableCell>
                    <TableCell>
                      <Select
                        value={draft.status}
                        onValueChange={(v) => updateDraft(entry.learnerId, "status", v as AttendanceStatus)}
                        disabled={isCancelled}
                      >
                        <SelectTrigger className="h-9">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="present">Present</SelectItem>
                          <SelectItem value="late">Late</SelectItem>
                          <SelectItem value="absent_authorised">Absent (Auth)</SelectItem>
                          <SelectItem value="absent_unauthorised">Absent (Unauth)</SelectItem>
                          <SelectItem value="not_expected">Not Expected</SelectItem>
                          <SelectItem value="withdrawn">Withdrawn</SelectItem>
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        step="0.5"
                        min="0"
                        className={`h-9 w-20 ${draft._requireOverrideReason ? 'border-amber-400' : ''}`}
                        value={draft.hoursAttended}
                        onChange={(e) => updateDraft(entry.learnerId, "hoursAttended", parseFloat(e.target.value) || 0)}
                        disabled={isCancelled || !["present", "late"].includes(draft.status)}
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        min="0"
                        className="h-9 w-20"
                        value={draft.minutesLate}
                        onChange={(e) => updateDraft(entry.learnerId, "minutesLate", parseInt(e.target.value) || 0)}
                        disabled={isCancelled || draft.status !== "late"}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-2">
                        {draft._requireOverrideReason && (
                          <Input
                            placeholder="Reason for altered hours (Required)"
                            className="h-9 border-amber-400 focus-visible:ring-amber-400"
                            value={draft.overrideReason}
                            onChange={(e) => updateDraft(entry.learnerId, "overrideReason", e.target.value)}
                            disabled={isCancelled}
                          />
                        )}
                        <Input
                          placeholder="General notes (Optional)"
                          className="h-9 bg-transparent"
                          value={draft.notes}
                          onChange={(e) => updateDraft(entry.learnerId, "notes", e.target.value)}
                          disabled={isCancelled}
                        />
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
        <div className="bg-muted/10 p-3 text-xs text-muted-foreground text-center border-t">
          {isCancelled ? "This session is cancelled -- the register is read-only." : "Changes are saved automatically when you modify a field."}
        </div>
      </Card>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Edit Session</DialogTitle>
            <DialogDescription>Update details for this session.</DialogDescription>
          </DialogHeader>
          {editNeedsConfirm ? (
            <div className="py-4 space-y-4">
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-4 rounded-md">
                <p className="text-sm text-amber-700 dark:text-amber-400">
                  This session already has recorded attendance. Confirm to change the date/time anyway -- existing attendance will not be affected.
                </p>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setEditNeedsConfirm(false)}>Go Back</Button>
                <Button variant="destructive" onClick={() => submitEdit(true)} disabled={updateMutation.isPending}>
                  {updateMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                  Confirm Change
                </Button>
              </DialogFooter>
            </div>
          ) : (
            <div className="grid gap-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="edit-session-date">Date</Label>
                <Input id="edit-session-date" type="date" value={editDate} onChange={e => setEditDate(e.target.value)} />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="edit-session-start-time">Start Time</Label>
                  <Input id="edit-session-start-time" type="time" value={editStartTime} onChange={e => setEditStartTime(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="edit-session-end-time">End Time</Label>
                  <Input id="edit-session-end-time" type="time" value={editEndTime} onChange={e => setEditEndTime(e.target.value)} />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit-session-title">Title / Topic</Label>
                <Input id="edit-session-title" value={editTitle} onChange={e => setEditTitle(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit-session-notes">Notes</Label>
                <Textarea id="edit-session-notes" value={editNotes} onChange={e => setEditNotes(e.target.value)} rows={3} />
              </div>
              <DialogFooter>
                <Button onClick={() => submitEdit(false)} disabled={updateMutation.isPending}>
                  {updateMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                  <Save className="w-4 h-4 mr-2" /> Save Changes
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={cancelOpen} onOpenChange={(o) => { setCancelOpen(o); if (!o) { setCancelReason(""); setCancelNeedsConfirm(false); } }}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Cancel Session</DialogTitle>
            <DialogDescription>The session record and any recorded attendance are preserved, never deleted.</DialogDescription>
          </DialogHeader>
          <div className="py-4 space-y-4">
            {cancelNeedsConfirm && (
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-4 rounded-md">
                <p className="text-sm text-amber-700 dark:text-amber-400">
                  This session already has recorded attendance. Confirm to cancel anyway -- the recorded attendance will be preserved, not deleted.
                </p>
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="cancel-session-reason">Reason</Label>
              <Textarea id="cancel-session-reason" value={cancelReason} onChange={e => setCancelReason(e.target.value)} rows={3} placeholder="Why is this session being cancelled?" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCancelOpen(false)}>Go Back</Button>
            <Button
              variant="destructive"
              onClick={() => submitCancel(cancelNeedsConfirm)}
              disabled={cancelMutation.isPending || !cancelReason.trim()}
            >
              {cancelMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              {cancelNeedsConfirm ? "Cancel Anyway" : "Cancel Session"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={refreshOpen} onOpenChange={(o) => { setRefreshOpen(o); if (!o) setRefreshDiff(null); }}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>Refresh Expected Learners</DialogTitle>
            <DialogDescription>Review changes to this session's expected register before applying them.</DialogDescription>
          </DialogHeader>
          {!refreshDiff ? (
            <div className="flex justify-center py-10"><Loader2 className="w-6 h-6 animate-spin text-primary" /></div>
          ) : (
            <div className="py-2 space-y-4 max-h-[50vh] overflow-auto">
              {refreshDiff.toAdd.length === 0 && refreshDiff.toRemove.length === 0 && (
                <p className="text-sm text-muted-foreground">No changes -- the expected register is already up to date.</p>
              )}
              {refreshDiff.toAdd.length > 0 && (
                <div>
                  <h4 className="text-sm font-semibold text-emerald-700 dark:text-emerald-500 mb-1">To add ({refreshDiff.toAdd.length})</h4>
                  <ul className="text-sm text-muted-foreground list-disc list-inside">
                    {refreshDiff.toAdd.map(l => <li key={l.learnerId}>{l.learnerName}</li>)}
                  </ul>
                </div>
              )}
              {refreshDiff.toRemove.length > 0 && (
                <div>
                  <h4 className="text-sm font-semibold text-rose-700 dark:text-rose-500 mb-1">To remove ({refreshDiff.toRemove.length})</h4>
                  <ul className="text-sm text-muted-foreground list-disc list-inside">
                    {refreshDiff.toRemove.map(l => <li key={l.learnerId}>{l.learnerName}</li>)}
                  </ul>
                </div>
              )}
              {refreshDiff.blocked.length > 0 && (
                <div>
                  <h4 className="text-sm font-semibold text-amber-700 dark:text-amber-500 mb-1">Not removed -- already has recorded attendance ({refreshDiff.blocked.length})</h4>
                  <ul className="text-sm text-muted-foreground list-disc list-inside">
                    {refreshDiff.blocked.map(l => <li key={l.learnerId}>{l.learnerName}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setRefreshOpen(false)}>Cancel</Button>
            <Button
              onClick={confirmRefresh}
              disabled={!refreshDiff || refreshMutation.isPending || (refreshDiff.toAdd.length === 0 && refreshDiff.toRemove.length === 0)}
            >
              {refreshMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Apply Changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
