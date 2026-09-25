import * as React from "react";
import {
  useRecordCatchup,
  useCorrectCatchup,
  CatchupMethod,
  CatchupState,
} from "@workspace/api-client-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { Loader2, CheckCircle2 } from "lucide-react";
import { format } from "date-fns";

const METHOD_LABELS: Record<CatchupMethod, string> = {
  recording_watched: "Watched the session recording",
  activity_completed: "Completed a catch-up activity",
};

interface CatchupDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: "record" | "correct";
  sessionId: number;
  learnerId: number;
  learnerName: string;
  sessionDate: string;
  /** Prefilled values for "correct" mode -- the details of the currently
   * active catch-up record being changed. */
  existing?: { completionDate: string; method: CatchupMethod; note: string } | null;
  onSuccess?: (result: CatchupState) => void;
}

/** Tutor confirmation that an absent learner subsequently watched the
 * recording or completed a catch-up activity -- this is a human
 * attestation, never automated tracking, so a note is always required. The
 * original attendance record is never touched by this dialog; it only ever
 * writes to the separate catch-up table. Handles both the initial "Record
 * catch-up" action and a later "Correct" of an already-recorded one (which
 * additionally requires a reason, retained in the audit history). */
export function CatchupDialog({
  open, onOpenChange, mode, sessionId, learnerId, learnerName, sessionDate, existing, onSuccess,
}: CatchupDialogProps) {
  const { toast } = useToast();
  const [completionDate, setCompletionDate] = React.useState("");
  const [method, setMethod] = React.useState<CatchupMethod | "">("");
  const [note, setNote] = React.useState("");
  const [reason, setReason] = React.useState("");

  React.useEffect(() => {
    if (open) {
      setCompletionDate(existing?.completionDate ?? format(new Date(), "yyyy-MM-dd"));
      setMethod(existing?.method ?? "");
      setNote(existing?.note ?? "");
      setReason("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const recordMutation = useRecordCatchup();
  const correctMutation = useCorrectCatchup();
  const isPending = recordMutation.isPending || correctMutation.isPending;

  const isValid = completionDate.trim() !== "" && method.length > 0 && note.trim() !== "" && (mode === "record" || reason.trim() !== "");

  const submit = () => {
    if (!isValid || method === "") return;
    const onError = (err: unknown) => {
      toast({ title: mode === "record" ? "Could not record catch-up" : "Could not correct catch-up", description: getErrorMessage(err), variant: "destructive" });
    };
    const onSuccessHandler = (result: CatchupState) => {
      toast({ title: mode === "record" ? "Catch-up recorded" : "Catch-up corrected" });
      onOpenChange(false);
      onSuccess?.(result);
    };

    if (mode === "record") {
      recordMutation.mutate(
        { sessionId, learnerId, data: { completionDate, method, note: note.trim() } },
        { onSuccess: onSuccessHandler, onError },
      );
    } else {
      correctMutation.mutate(
        { sessionId, learnerId, data: { completionDate, method, note: note.trim(), reason: reason.trim() } },
        { onSuccess: onSuccessHandler, onError },
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>{mode === "record" ? "Record Catch-up" : "Correct Catch-up"}</DialogTitle>
          <DialogDescription>
            {learnerName} -- session on {format(new Date(sessionDate), "d MMM yyyy")}. This confirms the learner's own
            completion; it does not change their recorded absence for that session.
          </DialogDescription>
        </DialogHeader>
        <div className="py-2 space-y-4">
          <div className="space-y-2">
            <Label htmlFor="catchup-completion-date">Completion date</Label>
            <Input
              id="catchup-completion-date"
              type="date"
              value={completionDate}
              min={sessionDate}
              max={format(new Date(), "yyyy-MM-dd")}
              onChange={(e) => setCompletionDate(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="catchup-method">Method</Label>
            <Select value={method} onValueChange={(v) => setMethod(v as CatchupMethod)}>
              <SelectTrigger id="catchup-method"><SelectValue placeholder="How did they catch up?" /></SelectTrigger>
              <SelectContent>
                {(Object.keys(METHOD_LABELS) as CatchupMethod[]).map((m) => (
                  <SelectItem key={m} value={m}>{METHOD_LABELS[m]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="catchup-note">Evidence / activity note</Label>
            <Textarea
              id="catchup-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="Describe what the learner did, e.g. which recording or activity"
            />
          </div>
          {mode === "correct" && (
            <div className="space-y-2">
              <Label htmlFor="catchup-correction-reason">Reason for correction</Label>
              <Textarea id="catchup-correction-reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={submit} disabled={!isValid || isPending}>
            {isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            <CheckCircle2 className="w-4 h-4 mr-2" /> {mode === "record" ? "Record Catch-up" : "Save Correction"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
