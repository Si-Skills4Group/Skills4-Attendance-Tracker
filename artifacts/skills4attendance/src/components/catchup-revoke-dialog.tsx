import * as React from "react";
import { useRevokeCatchup, CatchupState } from "@workspace/api-client-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { Loader2, Ban } from "lucide-react";

interface CatchupRevokeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sessionId: number;
  learnerId: number;
  learnerName: string;
  onSuccess?: (result: CatchupState) => void;
}

/** Revokes an already-recorded catch-up -- the row and its full history are
 * retained (never hard-deleted), a reason is required, and the record can
 * be re-recorded later if needed. */
export function CatchupRevokeDialog({ open, onOpenChange, sessionId, learnerId, learnerName, onSuccess }: CatchupRevokeDialogProps) {
  const { toast } = useToast();
  const [reason, setReason] = React.useState("");
  const mutation = useRevokeCatchup();

  React.useEffect(() => {
    if (open) setReason("");
  }, [open]);

  const submit = () => {
    if (!reason.trim()) return;
    mutation.mutate(
      { sessionId, learnerId, data: { reason: reason.trim() } },
      {
        onSuccess: (result) => {
          toast({ title: "Catch-up revoked" });
          onOpenChange(false);
          onSuccess?.(result);
        },
        onError: (err) => toast({ title: "Could not revoke catch-up", description: getErrorMessage(err), variant: "destructive" }),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Revoke Catch-up</DialogTitle>
          <DialogDescription>
            {learnerName}'s catch-up will no longer count toward participation. This is audited and the record is
            kept, never deleted -- it can be re-recorded later if needed.
          </DialogDescription>
        </DialogHeader>
        <div className="py-2 space-y-2">
          <Label htmlFor="catchup-revoke-reason">Reason</Label>
          <Textarea id="catchup-revoke-reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="destructive" onClick={submit} disabled={!reason.trim() || mutation.isPending}>
            {mutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            <Ban className="w-4 h-4 mr-2" /> Revoke Catch-up
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
