import * as React from "react";
import {
  useGetCurrentUser,
  useGetTutorIdentityDiagnostics,
  getGetTutorIdentityDiagnosticsQueryKey,
  usePreviewTutorMappingCorrection,
  useCommitTutorMappingCorrection,
  type TutorMappingCorrectionPreview,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { Loader2, UserRoundSearch, AlertTriangle } from "lucide-react";

export default function TutorIdentityPage() {
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === "admin";
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data, isLoading, isError } = useGetTutorIdentityDiagnostics({
    query: { enabled: isAdmin, queryKey: getGetTutorIdentityDiagnosticsQueryKey() },
  });

  const [dialogState, setDialogState] = React.useState<{ sourceTutorId: number; targetTutorId: number; budTutorId: string } | null>(null);
  const [reason, setReason] = React.useState("");
  const [preview, setPreview] = React.useState<TutorMappingCorrectionPreview | null>(null);
  const [identityConfirmedByAdmin, setIdentityConfirmedByAdmin] = React.useState(false);

  const previewMutation = usePreviewTutorMappingCorrection({
    mutation: {
      onSuccess: (result) => setPreview(result),
      onError: (err) => toast({ title: "Could not preview correction", description: getErrorMessage(err), variant: "destructive" }),
    },
  });
  const commitMutation = useCommitTutorMappingCorrection({
    mutation: {
      onSuccess: () => {
        toast({ title: "Mapping correction applied", description: "Generate a fresh Bud sync preview to see how this changes matching." });
        setDialogState(null);
        setPreview(null);
        setReason("");
        queryClient.invalidateQueries({ queryKey: getGetTutorIdentityDiagnosticsQueryKey() });
      },
      onError: (err) => toast({ title: "Correction failed", description: getErrorMessage(err), variant: "destructive" }),
    },
  });

  const openDialog = (sourceTutorId: number, targetTutorId: number, budTutorId: string) => {
    setDialogState({ sourceTutorId, targetTutorId, budTutorId });
    setPreview(null);
    setReason("");
    setIdentityConfirmedByAdmin(false);
    previewMutation.mutate({ data: { sourceTutorId, targetTutorId, budTutorId } });
  };

  const confirmCorrection = () => {
    if (!dialogState || !preview) return;
    commitMutation.mutate({
      data: {
        sourceTutorId: dialogState.sourceTutorId, targetTutorId: dialogState.targetTutorId, budTutorId: dialogState.budTutorId,
        expectedSourceTutorUpdatedAt: preview.preview.sourceTutorUpdatedAt,
        expectedTargetTutorUpdatedAt: preview.preview.targetTutorUpdatedAt,
        reason,
        identityConfirmedByAdmin,
      },
    });
  };

  // Every correction requires an explicit identity confirmation, regardless
  // of what supporting signals the preview found -- never bypassed by
  // evidence strength (server-side enforced too; see commit_tutor_mapping_correction).
  const canConfirm = !!preview && !!reason.trim() && identityConfirmedByAdmin;

  if (user && !isAdmin) {
    return (
      <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
        <Breadcrumbs items={[{ label: "Tutor Identity" }]} />
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Administrator access required.</CardContent></Card>
      </div>
    );
  }

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Tutor Identity" }]} />
      <div className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <UserRoundSearch className="w-7 h-7 text-primary" /> Tutor Identity
        </h1>
        <p className="text-muted-foreground mt-1">
          Bud tutor identifier mapping diagnostics. Identity (which internal tutor a Bud id belongs to) is kept separate
          from eligibility (whether that tutor can currently receive learners) -- an inactive tutor's mapping is never
          used to route an active allocation. Nothing here merges tutor records or moves learners automatically.
        </p>
      </div>

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError || !data ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load tutor identity diagnostics.</CardContent></Card>
      ) : (
        <div className="space-y-6">
          <Card className="shadow-sm">
            <CardHeader>
              <CardTitle className="text-base">Summary</CardTitle>
              <CardDescription>
                {data.totals.distinctAffectedLearnerReferences} distinct learner reference(s) across{" "}
                {data.totals.affectedBudLearningPlanRows} Bud learning-plan row(s) affected by an identity mapping issue below.
              </CardDescription>
            </CardHeader>
          </Card>

          <Card className="shadow-sm">
            <CardHeader><CardTitle className="text-base">Unmatched Bud tutor identifiers</CardTitle></CardHeader>
            <CardContent>
              {data.unmatchedBudTutorIds.length === 0 ? (
                <p className="text-sm text-muted-foreground">None -- every Bud tutor identifier resolves to an internal tutor.</p>
              ) : (
                <Table>
                  <TableHeader><TableRow><TableHead>Bud tutor id</TableHead><TableHead>Bud tutor name</TableHead><TableHead>References</TableHead><TableHead>Plan rows</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {data.unmatchedBudTutorIds.map((e) => (
                      <TableRow key={e.budTutorId}>
                        <TableCell className="text-xs">{e.budTutorId}</TableCell>
                        <TableCell>{e.budTutorName ?? "—"}</TableCell>
                        <TableCell>{e.affectedLearnerReferences}</TableCell>
                        <TableCell>{e.affectedBudLearningPlanRows}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <Card className="shadow-sm">
            <CardHeader><CardTitle className="text-base">Bud id held only by an inactive tutor</CardTitle></CardHeader>
            <CardContent>
              {data.budIdsHeldOnlyByInactiveTutor.length === 0 ? (
                <p className="text-sm text-muted-foreground">None.</p>
              ) : (
                <Table>
                  <TableHeader><TableRow><TableHead>Bud tutor id</TableHead><TableHead>Inactive tutor</TableHead><TableHead>References</TableHead><TableHead>Plan rows</TableHead><TableHead /></TableRow></TableHeader>
                  <TableBody>
                    {data.budIdsHeldOnlyByInactiveTutor.map((e) => {
                      const candidateActive = data.duplicateTutorCandidates
                        .flatMap((g) => g.tutors)
                        .find((t) => t.id !== e.inactiveTutor.id && t.active
                          && t.firstName.toLowerCase() === e.inactiveTutor.firstName.toLowerCase()
                          && t.lastName.toLowerCase() === e.inactiveTutor.lastName.toLowerCase());
                      return (
                        <TableRow key={e.budTutorId}>
                          <TableCell className="text-xs">{e.budTutorId}</TableCell>
                          <TableCell>{e.inactiveTutor.firstName} {e.inactiveTutor.lastName} <Badge variant="outline" className="ml-1">inactive</Badge></TableCell>
                          <TableCell>{e.affectedLearnerReferences}</TableCell>
                          <TableCell>{e.affectedBudLearningPlanRows}</TableCell>
                          <TableCell>
                            {candidateActive && (
                              <Button size="sm" variant="outline" onClick={() => openDialog(e.inactiveTutor.id, candidateActive.id, e.budTutorId)}>
                                Propose correction to {candidateActive.firstName} {candidateActive.lastName}
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <Card className="shadow-sm">
            <CardHeader><CardTitle className="text-base">Bud id attached to multiple tutors</CardTitle></CardHeader>
            <CardContent>
              {data.budIdAttachedToMultipleTutors.length === 0 ? (
                <p className="text-sm text-muted-foreground">None.</p>
              ) : (
                <Table>
                  <TableHeader><TableRow><TableHead>Bud tutor id</TableHead><TableHead>Tutors</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {data.budIdAttachedToMultipleTutors.map((e) => (
                      <TableRow key={e.budTutorId}>
                        <TableCell className="text-xs">{e.budTutorId}</TableCell>
                        <TableCell>{e.tutors.map((t) => `${t.firstName} ${t.lastName} (${t.active ? "active" : "inactive"})`).join(", ")}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <Card className="shadow-sm">
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2"><AlertTriangle className="w-4 h-4 text-amber-500" /> Potential duplicate tutor records</CardTitle>
              <CardDescription>Exact name matches only, for human review -- never auto-merged.</CardDescription>
            </CardHeader>
            <CardContent>
              {data.duplicateTutorCandidates.length === 0 ? (
                <p className="text-sm text-muted-foreground">None.</p>
              ) : (
                <Table>
                  <TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Records</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {data.duplicateTutorCandidates.map((g) => (
                      <TableRow key={g.normalizedName}>
                        <TableCell>{g.normalizedName}</TableCell>
                        <TableCell>
                          {g.tutors.map((t) => (
                            <div key={t.id} className="text-xs">
                              #{t.id} {t.email} -- {t.active ? "active" : "inactive"}{t.externalSystemId ? ` -- Bud: ${t.externalSystemId}` : ""}
                            </div>
                          ))}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      <Dialog open={!!dialogState} onOpenChange={(open) => { if (!open) { setDialogState(null); setPreview(null); } }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Propose tutor mapping correction</DialogTitle>
            <DialogDescription>
              Reassigns which tutor record holds this Bud tutor identifier. Does not merge records, transfer learners,
              or touch attendance history.
            </DialogDescription>
          </DialogHeader>
          {previewMutation.isPending || !preview ? (
            <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-primary" /></div>
          ) : (
            <div className="space-y-3 text-sm max-h-[60vh] overflow-y-auto">
              <div>
                <div className="font-medium">Name match</div>
                <p className="text-muted-foreground">
                  {preview.nameMatch ? "First and last name are identical." : "Names do not match."} A supporting
                  signal only -- two different people can share a name -- never proof.
                </p>
              </div>
              <div>
                <div className="font-medium">Supporting signals</div>
                {preview.supportingSignals.length === 0
                  ? <p className="text-muted-foreground">None found (no shared phone, employee reference, or prior transfer between these two records).</p>
                  : <ul className="list-disc pl-5">{preview.supportingSignals.map((e, i) => <li key={i}>{e}</li>)}</ul>}
                <p className="text-muted-foreground mt-1">
                  These are signals to weigh, not proof -- even a prior transfer of a learner between these two
                  tutor records does not by itself establish that the same person sits behind both.
                </p>
              </div>
              <div className="rounded border border-amber-300 bg-amber-50 dark:bg-amber-950/20 p-3 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                <div>
                  <p className="font-medium text-amber-800 dark:text-amber-400">Identity confirmation required</p>
                  <p className="text-muted-foreground">
                    Every correction requires an administrator to personally verify -- from outside this system if
                    necessary -- that these are the same person, regardless of the signals shown above.
                  </p>
                  <label className="flex items-center gap-2 mt-2">
                    <Checkbox checked={identityConfirmedByAdmin} onCheckedChange={(v) => setIdentityConfirmedByAdmin(v === true)} />
                    <span>I have independently confirmed these records are the same person.</span>
                  </label>
                </div>
              </div>
              {preview.conflictingIdentifierOwnership.length > 0 && (
                <div>
                  <div className="font-medium text-amber-600">Conflicting identifier ownership</div>
                  <ul className="list-disc pl-5">{preview.conflictingIdentifierOwnership.map((c, i) => <li key={i}>{c}</li>)}</ul>
                </div>
              )}
              <div>
                <div className="font-medium">Exact field changes</div>
                <ul className="list-disc pl-5">
                  {preview.fieldChanges.map((c, i) => (
                    <li key={i}>Tutor {c.tutorId}: {c.field} "{c.before ?? "—"}" → "{c.after ?? "—"}"</li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="font-medium">Downstream effects</div>
                <ul className="list-disc pl-5">{preview.downstreamEffects.map((d, i) => <li key={i}>{d}</li>)}</ul>
              </div>
              <div>
                <div className="font-medium">What remains unresolved</div>
                <ul className="list-disc pl-5">{preview.remainingUnresolved.map((u, i) => <li key={i}>{u}</li>)}</ul>
              </div>
              <div className="pt-2">
                <Label htmlFor="correction-reason">Reason (required)</Label>
                <Textarea id="correction-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Confirmed same person via employee_ref and email match" />
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setDialogState(null); setPreview(null); }}>Cancel</Button>
            <Button onClick={confirmCorrection} disabled={!canConfirm || commitMutation.isPending}>
              {commitMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
              Confirm correction
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
