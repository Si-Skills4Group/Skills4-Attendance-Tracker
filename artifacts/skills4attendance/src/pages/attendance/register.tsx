import * as React from "react";
import { 
  useGetAttendanceSession,
  useSaveAttendanceRegister,
  useMarkAllPresent,
  AttendanceStatus,
  RegisterEntryInput
} from "@workspace/api-client-react";
import { useLocation, useParams } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Save, ArrowLeft, CheckCircle2, Clock, CalendarDays, Users, Check } from "lucide-react";
import { format, parseISO } from "date-fns";
import { AttendanceStatusBadge } from "@/components/status-badges";
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
  
  const { data: register, isLoading } = useGetAttendanceSession(sessionId);
  const saveMutation = useSaveAttendanceRegister();
  const markAllMutation = useMarkAllPresent();
  
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
  const lastSavedRef = React.useRef<string>("");
  React.useEffect(() => {
    if (!register) return;
    
    // Only save dirty entries
    const dirtyEntries = Object.values(debouncedDrafts).filter(d => d._isDirty);
    if (dirtyEntries.length === 0) return;

    // Validate: if require override, ensure it's not empty
    const invalid = dirtyEntries.find(d => d._requireOverrideReason && !d.overrideReason);
    if (invalid) return; // Don't auto-save if validation fails

    const payloadString = JSON.stringify(dirtyEntries);
    if (payloadString === lastSavedRef.current) return;
    
    setSaveStatus("saving");
    
    const entriesToSave = dirtyEntries.map(({ _isDirty, _originalHours, _requireOverrideReason, ...rest }) => rest);
    
    saveMutation.mutate({ id: sessionId, data: { entries: entriesToSave } }, {
      onSuccess: () => {
        setSaveStatus("saved");
        lastSavedRef.current = payloadString;
        setTimeout(() => setSaveStatus("idle"), 2000);
        
        // Mark as clean locally
        setDrafts(prev => {
          const next = { ...prev };
          dirtyEntries.forEach(d => {
            if (next[d.learnerId]) next[d.learnerId]._isDirty = false;
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
      }
    });
  }, [debouncedDrafts, sessionId, saveMutation, register, queryClient]);

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

  if (isLoading || !register) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;
  }

  const { session, entries } = register;
  const isComplete = session.recordedCount === session.expectedCount && session.expectedCount > 0;

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
                {isComplete && <span className="bg-emerald-100 text-emerald-800 text-xs px-2 py-0.5 rounded flex items-center font-bold"><CheckCircle2 className="w-3 h-3 mr-1" /> Complete</span>}
              </div>
              <p className="text-muted-foreground mt-1 flex items-center gap-3 text-sm">
                <span className="flex items-center"><CalendarDays className="w-3.5 h-3.5 mr-1.5" />{format(parseISO(session.sessionDate), "EEEE, MMM d, yyyy")}</span>
                <span>•</span>
                <span className="flex items-center"><Clock className="w-3.5 h-3.5 mr-1.5" />{session.plannedStartTime.substring(0,5)} - {session.plannedEndTime.substring(0,5)} ({session.plannedDurationHours}h)</span>
                <span>•</span>
                <span className="flex items-center"><Users className="w-3.5 h-3.5 mr-1.5" />{session.tutorName}</span>
              </p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="text-sm font-medium text-muted-foreground flex items-center">
              {saveStatus === "saving" && <><Loader2 className="w-3 h-3 mr-1.5 animate-spin" /> Saving...</>}
              {saveStatus === "saved" && <><Check className="w-3 h-3 mr-1.5 text-emerald-500" /> Saved</>}
              {saveStatus === "idle" && <span className="opacity-0">Saved</span>}
            </div>
            <Button variant="secondary" onClick={handleMarkAllPresent} disabled={markAllMutation.isPending} className="shadow-sm border">
              <CheckCircle2 className="w-4 h-4 mr-2" /> Mark All Present
            </Button>
          </div>
        </div>
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
                        disabled={!["present", "late"].includes(draft.status)}
                      />
                    </TableCell>
                    <TableCell>
                      <Input 
                        type="number" 
                        min="0"
                        className="h-9 w-20"
                        value={draft.minutesLate}
                        onChange={(e) => updateDraft(entry.learnerId, "minutesLate", parseInt(e.target.value) || 0)}
                        disabled={draft.status !== "late"}
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
                          />
                        )}
                        <Input 
                          placeholder="General notes (Optional)" 
                          className="h-9 bg-transparent"
                          value={draft.notes}
                          onChange={(e) => updateDraft(entry.learnerId, "notes", e.target.value)}
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
          Changes are saved automatically when you modify a field.
        </div>
      </Card>
    </div>
  );
}
