import * as React from "react";
import {
  useGetAttendanceSession,
  useSaveAttendanceRegister,
  useCompleteRegister,
  useLockAttendanceRegister,
  useUnlockAttendanceRegister,
  useUpdateAttendanceSession,
  useCancelAttendanceSession,
  useDeleteAttendanceSession,
  useRefreshSessionRegister,
  useAssignCoverTutor,
  useRemoveCoverTutor,
  useListTutors,
  useGetCurrentUser,
  AttendanceStatus,
  RegisterEntryInput,
  AttendanceRegister,
  CoverReason,
} from "@workspace/api-client-react";
import { useLocation, useParams, useSearch } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Combobox } from "@/components/ui/combobox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import {
  Loader2, Save, ArrowLeft, CheckCircle2, Clock, CalendarDays, Users, Pencil, Ban, RefreshCw,
  ChevronDown, Lock, Unlock, ShieldCheck, AlertTriangle, Trash2, UserCog, UserX,
} from "lucide-react";
import { format, parseISO } from "date-fns";
import { RegisterStatusBadge } from "@/components/status-badges";
import { RegisterHistoryPanel } from "@/components/register-history-panel";
import { useQueryClient } from "@tanstack/react-query";
import { getGetAttendanceSessionQueryKey } from "@workspace/api-client-react";

type DraftEntry = {
  learnerId: number;
  status: AttendanceStatus | null;
  hoursAttended: number;
  minutesLate: number;
  notes: string;
  overrideReason: string;
  _originalHours: number;
  _requireOverrideReason: boolean;
};

const ZERO_HOURS_STATUSES: AttendanceStatus[] = ["absent_authorised", "absent_unauthorised", "not_expected", "withdrawn", "bil"];

const STATUS_LABELS: Record<AttendanceStatus, string> = {
  present: "Present",
  late: "Late",
  absent_authorised: "Absent (Authorised)",
  absent_unauthorised: "Absent (Unauthorised)",
  not_expected: "Not Expected",
  withdrawn: "Withdrawn",
  bil: "BIL",
};

const COVER_REASON_LABELS: Record<CoverReason, string> = {
  tutor_sickness: "Tutor sickness",
  annual_leave: "Annual leave",
  emergency_cover: "Emergency cover",
  tutor_unavailable: "Tutor unavailable",
  operational_reassignment: "Operational reassignment",
  other: "Other",
};

function buildDraft(entry: {
  learnerId: number; status: AttendanceStatus | null; hoursAttended: number; minutesLate: number;
  notes: string | null; overrideReason: string | null;
}, originalHours: number): DraftEntry {
  return {
    learnerId: entry.learnerId,
    status: entry.status,
    hoursAttended: entry.hoursAttended,
    minutesLate: entry.minutesLate,
    notes: entry.notes || "",
    overrideReason: entry.overrideReason || "",
    _originalHours: originalHours,
    _requireOverrideReason: entry.status === "present" && entry.hoursAttended > originalHours,
  };
}

function draftsEqual(a: DraftEntry, b: DraftEntry): boolean {
  return a.status === b.status && a.hoursAttended === b.hoursAttended
    && a.minutesLate === b.minutesLate && a.notes === b.notes;
}

export default function RegisterPage() {
  const params = useParams();
  const sessionId = Number(params.id);
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const queryClient = useQueryClient();

  // The sessions page hands its own (filtered) URL forward as `from` when
  // linking into a register, so "back" restores that exact view instead of
  // resetting to the top-level cohort list -- same convention as the
  // cohort-list -> cohort-sessions hop. Falls back to /attendance when
  // absent (dashboard links, the register-completion report, direct URLs).
  const fromParam = new URLSearchParams(useSearch()).get("from");
  const backHref = fromParam ? decodeURIComponent(fromParam) : "/attendance";

  const { data: currentUser } = useGetCurrentUser();
  const isAdmin = currentUser?.role === "admin";

  const { data: register, isLoading } = useGetAttendanceSession(sessionId);
  const saveMutation = useSaveAttendanceRegister();
  const completeMutation = useCompleteRegister();
  const lockMutation = useLockAttendanceRegister();
  const unlockMutation = useUnlockAttendanceRegister();
  const updateMutation = useUpdateAttendanceSession();
  const cancelMutation = useCancelAttendanceSession();
  const deleteMutation = useDeleteAttendanceSession();
  const refreshMutation = useRefreshSessionRegister();
  const assignCoverMutation = useAssignCoverTutor();
  const removeCoverMutation = useRemoveCoverTutor();

  // ---------------------------------------------------------------------
  // Local draft state -- purely local until an explicit Save Draft /
  // Complete Register action. No autosave.
  // ---------------------------------------------------------------------
  const [drafts, setDrafts] = React.useState<Record<number, DraftEntry>>({});
  const [baseline, setBaseline] = React.useState<Record<number, DraftEntry>>({});
  const [selected, setSelected] = React.useState<Set<number>>(new Set());
  const [rowErrors, setRowErrors] = React.useState<Record<number, string[]>>({});
  const [saveState, setSaveState] = React.useState<"idle" | "saving" | "saved">("idle");

  const savedIdleTimeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  React.useEffect(() => () => {
    if (savedIdleTimeoutRef.current) clearTimeout(savedIdleTimeoutRef.current);
  }, []);

  const initRef = React.useRef<string>("");
  React.useEffect(() => {
    if (register && initRef.current !== JSON.stringify(register.entries)) {
      initRef.current = JSON.stringify(register.entries);
      const next: Record<number, DraftEntry> = {};
      register.entries.forEach(e => { next[e.learnerId] = buildDraft(e, register.session.plannedDurationHours); });
      setDrafts(next);
      setBaseline(next);
      setRowErrors({});
    }
  }, [register]);

  const isRowDirty = React.useCallback((learnerId: number) => {
    const d = drafts[learnerId];
    const b = baseline[learnerId];
    if (!d || !b) return false;
    return !draftsEqual(d, b);
  }, [drafts, baseline]);

  const dirtyLearnerIds = Object.keys(drafts).map(Number).filter(isRowDirty);
  const hasUnsavedChanges = dirtyLearnerIds.length > 0;

  React.useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasUnsavedChanges]);

  const guardNavigate = (to: string) => {
    if (hasUnsavedChanges && !window.confirm("You have unsaved attendance changes. Leave without saving?")) {
      return;
    }
    setLocation(to);
  };

  const applySavedResult = (result: AttendanceRegister) => {
    const next: Record<number, DraftEntry> = {};
    result.entries.forEach(e => { next[e.learnerId] = buildDraft(e, result.session.plannedDurationHours); });
    setDrafts(next);
    setBaseline(next);
    setRowErrors({});
    queryClient.setQueryData(getGetAttendanceSessionQueryKey(sessionId), result);
  };

  const updateDraft = (learnerId: number, field: keyof DraftEntry, value: any) => {
    setRowErrors(prev => { if (!prev[learnerId]) return prev; const next = { ...prev }; delete next[learnerId]; return next; });
    setDrafts(prev => {
      const draft = prev[learnerId];
      if (!draft) return prev;
      const next = { ...draft, [field]: value };

      if (field === "status") {
        if (value === "present" || value === "late") {
          next.hoursAttended = next._originalHours;
        } else {
          next.hoursAttended = 0;
          next.minutesLate = 0;
        }
      }

      next._requireOverrideReason = next.status === "present" && next.hoursAttended > next._originalHours;
      if (!next._requireOverrideReason && field !== "overrideReason") next.overrideReason = "";

      return { ...prev, [learnerId]: next };
    });
  };

  // ---------------------------------------------------------------------
  // Row selection + bulk actions (local-draft-only; confirm before
  // overwriting unsaved edits, per design decision 5)
  // ---------------------------------------------------------------------
  const entries = register?.entries ?? [];
  const allSelected = entries.length > 0 && entries.every(e => selected.has(e.learnerId));

  const toggleAll = (checked: boolean) => {
    setSelected(checked ? new Set(entries.map(e => e.learnerId)) : new Set());
  };
  const toggleRow = (learnerId: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(learnerId)) next.delete(learnerId); else next.add(learnerId);
      return next;
    });
  };

  const [bulkConfirm, setBulkConfirm] = React.useState<{ message: string; run: () => void } | null>(null);

  const applyToRows = (targetIds: number[], label: string, apply: (d: DraftEntry) => Partial<DraftEntry>) => {
    if (targetIds.length === 0) return;
    const run = () => {
      setRowErrors({});
      setDrafts(prev => {
        const next = { ...prev };
        targetIds.forEach(id => {
          if (next[id]) {
            const merged = { ...next[id], ...apply(next[id]) };
            merged._requireOverrideReason = merged.status === "present" && merged.hoursAttended > merged._originalHours;
            next[id] = merged;
          }
        });
        return next;
      });
    };
    const overwriting = targetIds.filter(isRowDirty).length;
    if (overwriting > 0) {
      setBulkConfirm({
        message: `${label} will overwrite unsaved changes on ${overwriting} row${overwriting === 1 ? "" : "s"}. Continue?`,
        run,
      });
    } else {
      run();
    }
  };

  const handleMarkAllPresent = () => {
    applyToRows(entries.map(e => e.learnerId), "Mark all present", (d) => ({
      status: "present", hoursAttended: d._originalHours, minutesLate: 0, overrideReason: "",
    }));
  };

  const handleSetSelectedStatus = (status: AttendanceStatus) => {
    applyToRows(Array.from(selected), `Set ${STATUS_LABELS[status]}`, (d) => {
      if (status === "present") return { status, hoursAttended: d._originalHours, minutesLate: 0, overrideReason: "" };
      if (ZERO_HOURS_STATUSES.includes(status)) return { status, hoursAttended: 0, minutesLate: 0, overrideReason: "" };
      return { status };
    });
  };

  const handleClearSelected = () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    setRowErrors(prev => { const next = { ...prev }; ids.forEach(id => delete next[id]); return next; });
    setDrafts(prev => {
      const next = { ...prev };
      ids.forEach(id => { if (baseline[id]) next[id] = baseline[id]; });
      return next;
    });
  };

  // ---------------------------------------------------------------------
  // Save / Complete / conflict / historical change-reason
  // ---------------------------------------------------------------------
  const [reasonDialog, setReasonDialog] = React.useState<{ then?: (result: AttendanceRegister) => void } | null>(null);
  const [reasonInput, setReasonInput] = React.useState("");
  const [conflictVersion, setConflictVersion] = React.useState<number | null>(null);

  // Only rows the Tutor actually touched are ever submitted -- an untouched
  // row stays at its unrecorded (null) status and must never be resent as a
  // deliberate attendance decision. A row with a still-null status is by
  // definition never "dirty" (its baseline is also null), so filtering to
  // dirty rows already excludes every unrecorded row; the explicit
  // status !== null check is a second, independent guard against ever
  // submitting a null status (e.g. a note typed before a status is chosen).
  const buildEntries = (): RegisterEntryInput[] => {
    const result: RegisterEntryInput[] = [];
    for (const d of Object.values(drafts)) {
      if (d.status === null || !isRowDirty(d.learnerId)) continue;
      result.push({
        learnerId: d.learnerId,
        status: d.status,
        hoursAttended: d.hoursAttended,
        minutesLate: d.minutesLate,
        notes: d.notes.trim() ? d.notes.trim() : undefined,
        overrideReason: d.overrideReason.trim() ? d.overrideReason.trim() : undefined,
      });
    }
    return result;
  };

  const performSave = (reason: string | undefined, then?: (result: AttendanceRegister) => void) => {
    if (!register) return;
    const entriesToSave = buildEntries();
    if (entriesToSave.length === 0) {
      // Nothing changed -- skip the round trip rather than bumping the
      // register version and writing a no-op audit entry. Complete Register
      // still proceeds using the current (unbumped) version, so an
      // incomplete register is correctly rejected by the backend.
      then?.(register);
      return;
    }
    setSaveState("saving");
    saveMutation.mutate({
      id: sessionId,
      data: { registerVersion: register.session.registerVersion, entries: entriesToSave, changeReason: reason },
    }, {
      onSuccess: (result) => {
        setSaveState("saved");
        if (savedIdleTimeoutRef.current) clearTimeout(savedIdleTimeoutRef.current);
        savedIdleTimeoutRef.current = setTimeout(() => setSaveState("idle"), 2000);
        applySavedResult(result);
        setReasonDialog(null);
        setReasonInput("");
        then?.(result);
      },
      onError: (err) => {
        setSaveState("idle");
        const status = (err as { status?: number } | undefined)?.status;
        const detail = (err as { data?: any } | undefined)?.data;

        if (status === 409 && detail?.reason === "stale_register_version") {
          setConflictVersion(detail.currentVersion ?? null);
          return;
        }
        if (status === 422 && Array.isArray(detail?.errors)) {
          const needsReason = detail.errors.some((e: any) => e.field === "changeReason");
          if (needsReason) {
            setReasonDialog({ then });
            return;
          }
          const nextRowErrors: Record<number, string[]> = {};
          const general: string[] = [];
          detail.errors.forEach((e: any) => {
            if (e.learnerId != null) {
              nextRowErrors[e.learnerId] = [...(nextRowErrors[e.learnerId] || []), e.message];
            } else {
              general.push(e.message);
            }
          });
          setRowErrors(nextRowErrors);
          toast({
            title: "Some rows could not be saved",
            description: general.join("; ") || "Check the highlighted rows and try again.",
            variant: "destructive",
          });
          return;
        }
        toast({ title: "Could not save register", description: getErrorMessage(err), variant: "destructive" });
      },
    });
  };

  const performComplete = (registerVersion: number) => {
    completeMutation.mutate({ id: sessionId, data: { registerVersion } }, {
      onSuccess: (result) => {
        toast({ title: "Register completed" });
        queryClient.setQueryData(getGetAttendanceSessionQueryKey(sessionId), result);
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        const detail = (err as { data?: any } | undefined)?.data;
        if (status === 409 && detail?.reason === "stale_register_version") {
          setConflictVersion(detail.currentVersion ?? null);
          return;
        }
        if (status === 422 && Array.isArray(detail?.errors)) {
          const messages = detail.errors.map((e: any) =>
            e.learnerId != null ? `Learner ${e.learnerId}: ${e.message}` : e.message);
          toast({ title: "Register is not complete", description: messages.join("; "), variant: "destructive" });
          return;
        }
        toast({ title: "Could not complete register", description: getErrorMessage(err), variant: "destructive" });
      },
    });
  };

  const handleSaveDraft = () => performSave(undefined);
  const handleCompleteRegister = () => performSave(undefined, (saved) => performComplete(saved.session.registerVersion));

  const submitReason = () => {
    const then = reasonDialog?.then;
    performSave(reasonInput.trim(), then);
  };

  const reloadRegister = () => {
    setConflictVersion(null);
    initRef.current = "";
    queryClient.invalidateQueries({ queryKey: getGetAttendanceSessionQueryKey(sessionId) });
  };

  // ---------------------------------------------------------------------
  // Lock / Unlock (admin-only)
  // ---------------------------------------------------------------------
  const [lockOpen, setLockOpen] = React.useState(false);
  const [lockReasonInput, setLockReasonInput] = React.useState("");
  const [unlockOpen, setUnlockOpen] = React.useState(false);
  const [unlockReasonInput, setUnlockReasonInput] = React.useState("");

  const submitLock = () => {
    if (!register) return;
    lockMutation.mutate({
      id: sessionId,
      data: { reason: lockReasonInput.trim(), registerVersion: register.session.registerVersion },
    }, {
      onSuccess: (result) => {
        toast({ title: "Register locked" });
        setLockOpen(false);
        setLockReasonInput("");
        queryClient.setQueryData(getGetAttendanceSessionQueryKey(sessionId), (old: any) =>
          old ? { ...old, session: result } : old);
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        const detail = (err as { data?: any } | undefined)?.data;
        if (status === 409 && detail?.reason === "stale_register_version") {
          setLockOpen(false);
          setConflictVersion(detail.currentVersion ?? null);
          return;
        }
        toast({ title: "Could not lock register", description: getErrorMessage(err), variant: "destructive" });
      },
    });
  };

  const submitUnlock = () => {
    if (!register) return;
    unlockMutation.mutate({
      id: sessionId,
      data: { reason: unlockReasonInput.trim(), registerVersion: register.session.registerVersion },
    }, {
      onSuccess: (result) => {
        toast({ title: "Register unlocked" });
        setUnlockOpen(false);
        setUnlockReasonInput("");
        queryClient.setQueryData(getGetAttendanceSessionQueryKey(sessionId), (old: any) =>
          old ? { ...old, session: result } : old);
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        const detail = (err as { data?: any } | undefined)?.data;
        if (status === 409 && detail?.reason === "stale_register_version") {
          setUnlockOpen(false);
          setConflictVersion(detail.currentVersion ?? null);
          return;
        }
        toast({ title: "Could not unlock register", description: getErrorMessage(err), variant: "destructive" });
      },
    });
  };

  // ---------------------------------------------------------------------
  // Edit session (unchanged from Phase 6)
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
  // Cancel session (unchanged from Phase 6)
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
  // Delete session (admin-only)
  // ---------------------------------------------------------------------
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const [deleteReason, setDeleteReason] = React.useState("");
  const [deleteNeedsConfirm, setDeleteNeedsConfirm] = React.useState(false);

  const submitDelete = (confirmWithAttendance = false) => {
    deleteMutation.mutate({
      id: sessionId,
      data: { reason: deleteReason.trim(), confirmWithAttendance },
    }, {
      onSuccess: () => {
        toast({ title: "Session deleted" });
        setDeleteOpen(false);
        setDeleteReason("");
        setDeleteNeedsConfirm(false);
        setLocation("/attendance");
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        const reason = (err as { data?: { reason?: string } } | undefined)?.data?.reason;
        if (status === 409 && reason === "attendance_already_recorded") {
          setDeleteNeedsConfirm(true);
        } else {
          toast({ title: "Could not delete session", description: getErrorMessage(err), variant: "destructive" });
        }
      },
    });
  };

  // ---------------------------------------------------------------------
  // Refresh expected learners (unchanged from Phase 6)
  // ---------------------------------------------------------------------
  const [refreshOpen, setRefreshOpen] = React.useState(false);
  const [refreshDiff, setRefreshDiff] = React.useState<{ toAdd: { learnerId: number; learnerName: string }[]; toRemove: { learnerId: number; learnerName: string }[]; blocked: { learnerId: number; learnerName: string }[] } | null>(null);
  const [refreshReasonInput, setRefreshReasonInput] = React.useState("");

  const openRefreshDialog = () => {
    setRefreshDiff(null);
    setRefreshReasonInput("");
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
    refreshMutation.mutate({ id: sessionId, data: { confirm: true, reason: isHistorical ? refreshReasonInput.trim() : undefined } }, {
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

  // ---------------------------------------------------------------------
  // Cover tutor (admin-only): reassigns just this session's register to a
  // different Tutor -- cohort ownership, learner allocations, and
  // historical attendance authorship are never touched. One dialog handles
  // both first assignment and changing an existing cover tutor, mirroring
  // the backend's own PUT .../cover upsert endpoint.
  // ---------------------------------------------------------------------
  const { data: tutors = [] } = useListTutors({ active: true });
  const [coverOpen, setCoverOpen] = React.useState(false);
  const [coverTutorId, setCoverTutorId] = React.useState("");
  const [coverReason, setCoverReason] = React.useState<CoverReason | "">("");
  const [coverNotes, setCoverNotes] = React.useState("");
  const [removeCoverOpen, setRemoveCoverOpen] = React.useState(false);
  const [removeCoverReason, setRemoveCoverReason] = React.useState("");
  const [removeCoverNeedsConfirm, setRemoveCoverNeedsConfirm] = React.useState(false);

  const openCoverDialog = () => {
    setCoverTutorId("");
    setCoverReason("");
    setCoverNotes("");
    setCoverOpen(true);
  };

  const submitCover = () => {
    if (!register || !coverTutorId || !coverReason) return;
    assignCoverMutation.mutate({
      id: sessionId,
      data: {
        coverTutorId: Number(coverTutorId),
        reason: coverReason,
        notes: coverNotes.trim() ? coverNotes.trim() : undefined,
        registerVersion: register.session.registerVersion,
      },
    }, {
      onSuccess: (result) => {
        toast({ title: register.session.coverTutorId != null ? "Cover tutor changed" : "Cover tutor assigned" });
        setCoverOpen(false);
        queryClient.setQueryData(getGetAttendanceSessionQueryKey(sessionId), (old: any) =>
          old ? { ...old, session: result } : old);
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        const detail = (err as { data?: any } | undefined)?.data;
        if (status === 409 && detail?.reason === "stale_register_version") {
          setCoverOpen(false);
          setConflictVersion(detail.currentVersion ?? null);
          return;
        }
        toast({ title: "Could not assign cover tutor", description: getErrorMessage(err), variant: "destructive" });
      },
    });
  };

  const submitRemoveCover = (confirmWithAttendance = false) => {
    if (!register) return;
    removeCoverMutation.mutate({
      id: sessionId,
      data: { reason: removeCoverReason.trim(), confirmWithAttendance, registerVersion: register.session.registerVersion },
    }, {
      onSuccess: (result) => {
        toast({ title: "Cover tutor removed" });
        setRemoveCoverOpen(false);
        setRemoveCoverReason("");
        setRemoveCoverNeedsConfirm(false);
        queryClient.setQueryData(getGetAttendanceSessionQueryKey(sessionId), (old: any) =>
          old ? { ...old, session: result } : old);
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        const detail = (err as { data?: any } | undefined)?.data;
        if (status === 409 && detail?.reason === "stale_register_version") {
          setRemoveCoverOpen(false);
          setConflictVersion(detail.currentVersion ?? null);
          return;
        }
        if (status === 409 && detail?.reason === "attendance_already_recorded") {
          setRemoveCoverNeedsConfirm(true);
          return;
        }
        toast({ title: "Could not remove cover tutor", description: getErrorMessage(err), variant: "destructive" });
      },
    });
  };

  if (isLoading || !register) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;
  }

  const { session } = register;
  const todayIso = format(new Date(), "yyyy-MM-dd");
  const isCancelled = session.status === "cancelled";
  const isLocked = session.registerLockedAt != null;
  const isCompleted = session.registerStatus === "completed";
  const isHistorical = session.registerStatus === "completed" || session.registerStatus === "locked"
    || session.sessionDate.slice(0, 10) < todayIso;
  const hasCover = session.coverTutorId != null;
  // The original Tutor's access degrades to read-only (not blocked
  // entirely) while an active cover Tutor is assigned -- only the cover
  // Tutor or an Administrator may edit. Folded into isReadOnly rather than
  // threaded through every button separately.
  const isOriginalTutorWhileCoverActive = !isAdmin && hasCover
    && currentUser?.tutorId != null
    && currentUser.tutorId === session.tutorId
    && currentUser.tutorId !== session.coverTutorId;
  const isReadOnly = isCancelled || isLocked || isOriginalTutorWhileCoverActive;
  const canRefresh = !isCancelled && !isCompleted && !isLocked && !isOriginalTutorWhileCoverActive;
  const canLock = isAdmin && isCompleted && !isLocked;
  const canUnlock = isAdmin && isLocked;
  const canManageCover = isAdmin && !isCancelled && !isLocked;
  const coverTutorOptions = tutors
    .filter(t => t.id !== session.tutorId)
    .map(t => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` }));
  const isSaving = saveMutation.isPending || completeMutation.isPending;

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full flex flex-col h-[calc(100vh-64px)]">
      <div className="shrink-0 page-transition-enter">
        <Breadcrumbs items={[
          { label: "Attendance", href: backHref },
          { label: "Register" }
        ]} />

        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
          <div className="flex items-center gap-4">
            <Button variant="outline" size="icon" aria-label="Back to Attendance" onClick={() => guardNavigate(backHref)}>
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
                <span className="flex items-center">
                  <Users className="w-3.5 h-3.5 mr-1.5" />
                  {session.effectiveTutorName}
                  {hasCover && (
                    <span className="ml-1.5 text-xs font-medium px-1.5 py-0.5 rounded bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-400">
                      Cover
                    </span>
                  )}
                </span>
                <span>•</span>
                <span>{session.recordedCount} of {session.expectedCount} learner{session.expectedCount === 1 ? "" : "s"} recorded</span>
                {session.completedAt && (
                  <>
                    <span>•</span>
                    <span>Completed {format(parseISO(session.completedAt), "MMM d, yyyy HH:mm")}</span>
                  </>
                )}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm font-medium text-muted-foreground flex items-center mr-1">
              {isSaving && <><Loader2 className="w-3 h-3 mr-1.5 animate-spin" /> Saving...</>}
              {!isSaving && saveState === "saved" && <><CheckCircle2 className="w-3 h-3 mr-1.5 text-emerald-500" /> Saved</>}
              {!isSaving && saveState === "idle" && hasUnsavedChanges && (
                <span className="text-amber-600 dark:text-amber-500 flex items-center"><AlertTriangle className="w-3 h-3 mr-1.5" /> Unsaved changes</span>
              )}
            </div>
            {!isReadOnly && (
              <Button variant="secondary" onClick={handleMarkAllPresent} className="shadow-sm border">
                <CheckCircle2 className="w-4 h-4 mr-2" /> Mark All Present
              </Button>
            )}
            {!isReadOnly && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" disabled={selected.size === 0} className="shadow-sm">
                    Bulk Actions ({selected.size}) <ChevronDown className="w-4 h-4 ml-2" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => handleSetSelectedStatus("present")}>Set Present</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleSetSelectedStatus("absent_authorised")}>Set Absent (Authorised)</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleSetSelectedStatus("absent_unauthorised")}>Set Absent (Unauthorised)</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleSetSelectedStatus("not_expected")}>Set Not Expected</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={handleClearSelected}>Clear Selected (revert to last save)</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
            {!isReadOnly && (
              <>
                <Button variant="outline" onClick={handleSaveDraft} disabled={isSaving} className="shadow-sm">
                  {saveMutation.isPending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                  Save Draft
                </Button>
                <Button onClick={handleCompleteRegister} disabled={isSaving} className="shadow-sm">
                  {completeMutation.isPending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <ShieldCheck className="w-4 h-4 mr-2" />}
                  Complete Register
                </Button>
              </>
            )}
            {!isReadOnly && (
              <Button variant="outline" onClick={openEditDialog} className="shadow-sm">
                <Pencil className="w-4 h-4 mr-2" /> Edit
              </Button>
            )}
            {canRefresh && (
              <Button variant="outline" onClick={openRefreshDialog} className="shadow-sm">
                <RefreshCw className="w-4 h-4 mr-2" /> Refresh Expected Learners
              </Button>
            )}
            {canLock && (
              <Button variant="outline" onClick={() => setLockOpen(true)} className="shadow-sm">
                <Lock className="w-4 h-4 mr-2" /> Lock Register
              </Button>
            )}
            {canUnlock && (
              <Button variant="outline" onClick={() => setUnlockOpen(true)} className="shadow-sm">
                <Unlock className="w-4 h-4 mr-2" /> Unlock Register
              </Button>
            )}
            {!isCancelled && !isLocked && (
              <Button variant="outline" onClick={() => setCancelOpen(true)} className="shadow-sm text-destructive hover:text-destructive">
                <Ban className="w-4 h-4 mr-2" /> Cancel Session
              </Button>
            )}
            {isAdmin && !isLocked && (
              <Button variant="outline" onClick={() => setDeleteOpen(true)} className="shadow-sm text-destructive hover:text-destructive">
                <Trash2 className="w-4 h-4 mr-2" /> Delete Session
              </Button>
            )}
            {canManageCover && !hasCover && (
              <Button variant="outline" onClick={openCoverDialog} className="shadow-sm">
                <UserCog className="w-4 h-4 mr-2" /> Assign Cover Tutor
              </Button>
            )}
            {canManageCover && hasCover && (
              <>
                <Button variant="outline" onClick={openCoverDialog} className="shadow-sm">
                  <UserCog className="w-4 h-4 mr-2" /> Change Cover Tutor
                </Button>
                <Button variant="outline" onClick={() => setRemoveCoverOpen(true)} className="shadow-sm">
                  <UserX className="w-4 h-4 mr-2" /> Remove Cover Tutor
                </Button>
              </>
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

        {isLocked && (
          <div className="bg-violet-50 dark:bg-violet-900/20 border border-violet-200 dark:border-violet-800 p-4 rounded-md mb-6">
            <h4 className="font-semibold text-violet-800 dark:text-violet-400 mb-1 flex items-center"><Lock className="w-4 h-4 mr-2" /> This register is locked</h4>
            {session.lockReason && <p className="text-sm text-violet-700 dark:text-violet-400">Reason: {session.lockReason}</p>}
            <p className="text-sm text-violet-700 dark:text-violet-400 mt-1">
              {isAdmin ? "An Administrator must unlock the register before it can be edited." : "Attendance cannot be edited while this register is locked. Contact an Administrator to unlock it."}
            </p>
          </div>
        )}

        {hasCover && (
          <div className="bg-sky-50 dark:bg-sky-900/20 border border-sky-200 dark:border-sky-800 p-4 rounded-md mb-6">
            <h4 className="font-semibold text-sky-800 dark:text-sky-400 mb-1 flex items-center"><UserCog className="w-4 h-4 mr-2" /> Cover session</h4>
            <p className="text-sm text-sky-700 dark:text-sky-400">
              Originally assigned to: {session.coverOriginalTutorName ?? session.tutorName}
            </p>
            <p className="text-sm text-sky-700 dark:text-sky-400">Delivered by: {session.coverTutorName}</p>
            {session.coverReason && (
              <p className="text-sm text-sky-700 dark:text-sky-400">
                Reason: {COVER_REASON_LABELS[session.coverReason as CoverReason] ?? session.coverReason}
                {session.coverNotes ? ` -- ${session.coverNotes}` : ""}
              </p>
            )}
          </div>
        )}

        {!isReadOnly && isHistorical && (
          <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-3 rounded-md mb-6 text-sm text-amber-700 dark:text-amber-400">
            This is a historical register. Changing recorded attendance (status or hours) will require a reason.
          </div>
        )}
      </div>

      <Card className="flex-1 shadow-sm overflow-hidden flex flex-col min-h-0 page-transition-enter stagger-1">
        <div className="overflow-auto flex-1 relative">
          <Table>
            <TableHeader className="bg-muted/30 sticky top-0 z-10 backdrop-blur-md shadow-sm">
              <TableRow>
                <TableHead className="w-[40px]">
                  {!isReadOnly && (
                    <Checkbox checked={allSelected} onCheckedChange={(c) => toggleAll(!!c)} aria-label="Select all learners" />
                  )}
                </TableHead>
                <TableHead className="w-[230px]">Learner</TableHead>
                <TableHead className="w-[180px]">Status</TableHead>
                <TableHead className="w-[110px]">Hours</TableHead>
                <TableHead className="w-[110px]">Mins Late</TableHead>
                <TableHead>Notes / Reason</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map(entry => {
                const draft = drafts[entry.learnerId];
                if (!draft) return null;
                const errors = rowErrors[entry.learnerId];

                return (
                  <TableRow key={entry.learnerId} className="hover:bg-muted/10 align-top">
                    <TableCell className="pt-3">
                      {!isReadOnly && (
                        <Checkbox
                          checked={selected.has(entry.learnerId)}
                          onCheckedChange={() => toggleRow(entry.learnerId)}
                          aria-label={`Select ${entry.learnerName}`}
                        />
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="font-medium text-sm">{entry.learnerName}</div>
                      <div className="text-xs text-muted-foreground font-mono mt-0.5">{entry.learnerRef}</div>
                      {isRowDirty(entry.learnerId) && (
                        <div className="text-xs text-amber-600 dark:text-amber-500 mt-0.5">Unsaved</div>
                      )}
                    </TableCell>
                    <TableCell>
                      <Select
                        value={draft.status ?? ""}
                        onValueChange={(v) => updateDraft(entry.learnerId, "status", v as AttendanceStatus)}
                        disabled={isReadOnly}
                      >
                        <SelectTrigger
                          className={`h-9 ${draft.status === null ? "text-muted-foreground" : ""}`}
                          aria-label={`Attendance status for ${entry.learnerName}`}
                        >
                          <SelectValue placeholder="Not recorded" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="present">Present</SelectItem>
                          <SelectItem value="late">Late</SelectItem>
                          <SelectItem value="absent_authorised">Absent (Auth)</SelectItem>
                          <SelectItem value="absent_unauthorised">Absent (Unauth)</SelectItem>
                          <SelectItem value="not_expected">Not Expected</SelectItem>
                          <SelectItem value="withdrawn">Withdrawn</SelectItem>
                          <SelectItem value="bil">BIL</SelectItem>
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        step="0.5"
                        min="0"
                        className={`h-9 w-20 ${draft._requireOverrideReason ? 'border-amber-400' : ''}`}
                        aria-label={`Hours attended for ${entry.learnerName}`}
                        value={draft.hoursAttended}
                        onChange={(e) => updateDraft(entry.learnerId, "hoursAttended", parseFloat(e.target.value) || 0)}
                        disabled={isReadOnly || draft.status !== "present" && draft.status !== "late"}
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        min="0"
                        className="h-9 w-20"
                        value={draft.minutesLate}
                        onChange={(e) => updateDraft(entry.learnerId, "minutesLate", parseInt(e.target.value) || 0)}
                        disabled={isReadOnly || draft.status !== "late"}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-2">
                        {draft._requireOverrideReason && isAdmin && (
                          <Input
                            placeholder="Reason for altered hours (Required)"
                            className="h-9 border-amber-400 focus-visible:ring-amber-400"
                            value={draft.overrideReason}
                            onChange={(e) => updateDraft(entry.learnerId, "overrideReason", e.target.value)}
                            disabled={isReadOnly}
                          />
                        )}
                        {draft._requireOverrideReason && !isAdmin && (
                          <p className="text-xs text-amber-600 dark:text-amber-500">
                            Exceeds planned hours -- an Administrator must approve this.
                          </p>
                        )}
                        <Input
                          placeholder="General notes (Optional)"
                          className="h-9 bg-transparent"
                          value={draft.notes}
                          onChange={(e) => updateDraft(entry.learnerId, "notes", e.target.value)}
                          disabled={isReadOnly}
                        />
                        {errors && errors.map((msg, i) => (
                          <p key={i} className="text-xs text-rose-600 dark:text-rose-500">{msg}</p>
                        ))}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
        <div className="bg-muted/10 p-3 text-xs text-muted-foreground text-center border-t">
          {isCancelled ? "This session is cancelled -- the register is read-only." :
            isLocked ? "This register is locked -- unlock it to make changes." :
            "Changes are kept locally until you click Save Draft or Complete Register."}
        </div>
      </Card>

      {isAdmin && (
        <div className="mt-6 shrink-0">
          <RegisterHistoryPanel sessionId={sessionId} />
        </div>
      )}

      {/* Historical change-reason prompt */}
      <Dialog open={!!reasonDialog} onOpenChange={(o) => { if (!o) { setReasonDialog(null); setReasonInput(""); } }}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Reason for historical change</DialogTitle>
            <DialogDescription>
              This register is historical or already completed. Explain why you're changing previously recorded attendance.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <Label htmlFor="change-reason">Reason</Label>
            <Textarea id="change-reason" value={reasonInput} onChange={e => setReasonInput(e.target.value)} rows={3} className="mt-2" />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setReasonDialog(null); setReasonInput(""); }}>Cancel</Button>
            <Button onClick={submitReason} disabled={!reasonInput.trim() || saveMutation.isPending}>
              {saveMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Concurrency conflict */}
      <AlertDialog open={conflictVersion !== null} onOpenChange={(o) => { if (!o) setConflictVersion(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>This register has changed</AlertDialogTitle>
            <AlertDialogDescription>
              Someone else saved changes to this register since you loaded it. Reload to see the latest version --
              your local unsaved changes will be discarded.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep Editing</AlertDialogCancel>
            <AlertDialogAction onClick={reloadRegister}>Reload Register</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Bulk-action overwrite confirm */}
      <AlertDialog open={!!bulkConfirm} onOpenChange={(o) => { if (!o) setBulkConfirm(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Overwrite unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>{bulkConfirm?.message}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => { bulkConfirm?.run(); setBulkConfirm(null); }}>Continue</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Lock register */}
      <Dialog open={lockOpen} onOpenChange={(o) => { setLockOpen(o); if (!o) setLockReasonInput(""); }}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Lock Register</DialogTitle>
            <DialogDescription>Locking prevents any further edits until an Administrator unlocks it.</DialogDescription>
          </DialogHeader>
          <div className="py-2 space-y-2">
            <Label htmlFor="lock-reason">Reason</Label>
            <Textarea id="lock-reason" value={lockReasonInput} onChange={e => setLockReasonInput(e.target.value)} rows={3} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLockOpen(false)}>Cancel</Button>
            <Button onClick={submitLock} disabled={!lockReasonInput.trim() || lockMutation.isPending}>
              {lockMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              <Lock className="w-4 h-4 mr-2" /> Lock Register
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unlock register */}
      <Dialog open={unlockOpen} onOpenChange={(o) => { setUnlockOpen(o); if (!o) setUnlockReasonInput(""); }}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Unlock Register</DialogTitle>
            <DialogDescription>This action is audited. Provide a reason for unlocking.</DialogDescription>
          </DialogHeader>
          <div className="py-2 space-y-2">
            <Label htmlFor="unlock-reason">Reason</Label>
            <Textarea id="unlock-reason" value={unlockReasonInput} onChange={e => setUnlockReasonInput(e.target.value)} rows={3} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setUnlockOpen(false)}>Cancel</Button>
            <Button onClick={submitUnlock} disabled={!unlockReasonInput.trim() || unlockMutation.isPending}>
              {unlockMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              <Unlock className="w-4 h-4 mr-2" /> Unlock Register
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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

      <Dialog open={deleteOpen} onOpenChange={(o) => { setDeleteOpen(o); if (!o) { setDeleteReason(""); setDeleteNeedsConfirm(false); } }}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Delete Session</DialogTitle>
            <DialogDescription>The session record and any recorded attendance are preserved, never deleted -- but the session will disappear from every listing and report.</DialogDescription>
          </DialogHeader>
          <div className="py-4 space-y-4">
            {deleteNeedsConfirm && (
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-4 rounded-md">
                <p className="text-sm text-amber-700 dark:text-amber-400">
                  This session already has recorded attendance. Confirm to delete anyway -- the recorded attendance will be preserved, not deleted, but the session will no longer appear in any report.
                </p>
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="delete-session-reason">Reason</Label>
              <Textarea id="delete-session-reason" value={deleteReason} onChange={e => setDeleteReason(e.target.value)} rows={3} placeholder="Why is this session being deleted?" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>Go Back</Button>
            <Button
              variant="destructive"
              onClick={() => submitDelete(deleteNeedsConfirm)}
              disabled={deleteMutation.isPending || !deleteReason.trim()}
            >
              {deleteMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              {deleteNeedsConfirm ? "Delete Anyway" : "Delete Session"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={refreshOpen} onOpenChange={(o) => { setRefreshOpen(o); if (!o) { setRefreshDiff(null); setRefreshReasonInput(""); } }}>
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
              {isHistorical && (
                <div className="border-t pt-4">
                  <p className="text-xs text-amber-700 dark:text-amber-500 mb-2">
                    This session has already happened -- refreshing it will be logged as a correction.
                  </p>
                  <Label htmlFor="refresh-reason">Reason</Label>
                  <Textarea
                    id="refresh-reason" value={refreshReasonInput} onChange={e => setRefreshReasonInput(e.target.value)}
                    rows={2} className="mt-2"
                  />
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setRefreshOpen(false)}>Cancel</Button>
            <Button
              onClick={confirmRefresh}
              disabled={
                !refreshDiff || refreshMutation.isPending
                || (refreshDiff.toAdd.length === 0 && refreshDiff.toRemove.length === 0)
                || (isHistorical && !refreshReasonInput.trim())
              }
            >
              {refreshMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Apply Changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Assign / change cover tutor */}
      <Dialog open={coverOpen} onOpenChange={(o) => { setCoverOpen(o); if (!o) { setCoverTutorId(""); setCoverReason(""); setCoverNotes(""); } }}>
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle>{hasCover ? "Change Cover Tutor" : "Assign Cover Tutor"}</DialogTitle>
            <DialogDescription>
              This assigns only this session to the selected Tutor. The cohort owner, learner allocations and historical attendance will not change.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2 space-y-4">
            <div className="text-sm text-muted-foreground space-y-1 bg-muted/30 p-3 rounded-md">
              <div><span className="font-medium text-foreground">Session:</span> {format(parseISO(session.sessionDate), "EEEE, MMM d, yyyy")}, {session.plannedStartTime.substring(0, 5)}-{session.plannedEndTime.substring(0, 5)}</div>
              <div><span className="font-medium text-foreground">Cohort:</span> {session.cohortName}</div>
              <div><span className="font-medium text-foreground">Original Tutor:</span> {session.tutorName}</div>
              <div><span className="font-medium text-foreground">Expected learners:</span> {session.expectedCount}</div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="cover-tutor">Replacement Tutor</Label>
              <Combobox
                id="cover-tutor"
                options={coverTutorOptions}
                value={coverTutorId}
                onValueChange={setCoverTutorId}
                placeholder="Select a tutor..."
                searchPlaceholder="Search tutors..."
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="cover-reason">Reason</Label>
              <Select value={coverReason} onValueChange={(v) => setCoverReason(v as CoverReason)}>
                <SelectTrigger id="cover-reason"><SelectValue placeholder="Select a reason..." /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(COVER_REASON_LABELS) as CoverReason[]).map(r => (
                    <SelectItem key={r} value={r}>{COVER_REASON_LABELS[r]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="cover-notes">Notes {coverReason === "other" ? "(Required)" : "(Optional)"}</Label>
              <Textarea
                id="cover-notes"
                value={coverNotes}
                onChange={e => setCoverNotes(e.target.value)}
                rows={3}
                className={coverReason === "other" && !coverNotes.trim() ? "border-amber-400" : ""}
              />
            </div>
            {isCompleted && (
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-3 rounded-md">
                <p className="text-sm text-amber-700 dark:text-amber-400">
                  This register is already completed. Reassigning the delivery Tutor now will be recorded as a correction.
                </p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCoverOpen(false)}>Cancel</Button>
            <Button
              onClick={submitCover}
              disabled={
                assignCoverMutation.isPending || !coverTutorId || !coverReason ||
                (coverReason === "other" && !coverNotes.trim())
              }
            >
              {assignCoverMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              <UserCog className="w-4 h-4 mr-2" /> {hasCover ? "Change Cover Tutor" : "Assign Cover Tutor"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Remove cover tutor */}
      <Dialog open={removeCoverOpen} onOpenChange={(o) => { setRemoveCoverOpen(o); if (!o) { setRemoveCoverReason(""); setRemoveCoverNeedsConfirm(false); } }}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Remove Cover Tutor</DialogTitle>
            <DialogDescription>The session returns to {session.tutorName}. Any attendance already recorded is preserved, never deleted.</DialogDescription>
          </DialogHeader>
          <div className="py-4 space-y-4">
            {removeCoverNeedsConfirm && (
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-4 rounded-md">
                <p className="text-sm text-amber-700 dark:text-amber-400">
                  Attendance has already been recorded while cover was active. Confirm to remove cover anyway -- the recorded attendance will be preserved, not deleted.
                </p>
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="remove-cover-reason">Reason</Label>
              <Textarea id="remove-cover-reason" value={removeCoverReason} onChange={e => setRemoveCoverReason(e.target.value)} rows={3} placeholder="Why is cover being removed?" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRemoveCoverOpen(false)}>Go Back</Button>
            <Button
              variant="destructive"
              onClick={() => submitRemoveCover(removeCoverNeedsConfirm)}
              disabled={removeCoverMutation.isPending || !removeCoverReason.trim()}
            >
              {removeCoverMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              {removeCoverNeedsConfirm ? "Remove Anyway" : "Remove Cover Tutor"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
