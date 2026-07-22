import * as React from "react";
import {
  useGetBudSyncStatus,
  useEstablishBudSyncBaseline,
  useResetBudSyncBaseline,
  useCreateBudSyncPreview,
  useGetBudSyncJob,
  useListBudSyncJobItems,
  useUpdateBudSyncJobItem,
  useCommitBudSyncJob,
  useGetSettings,
  getGetBudSyncStatusQueryKey,
  getGetBudSyncJobQueryKey,
  getListBudSyncJobItemsQueryKey,
  type BudSyncItem,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { RefreshCw, Loader2, AlertTriangle, ShieldAlert, Info } from "lucide-react";
import { format, parseISO } from "date-fns";

const MATCH_STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "new", label: "New" },
  { value: "existing_update", label: "Existing update" },
  { value: "unchanged", label: "Unchanged" },
  { value: "conflict", label: "Conflict" },
  { value: "existing_before_trial", label: "Before trial" },
];

const ACTION_TYPE_LABELS: Record<string, string> = {
  create_learner: "Create learner",
  update_learner: "Update learner",
  create_cohort: "Create cohort",
  create_allocation: "Create allocation",
  transfer_tutor: "Transfer tutor",
  change_start_date: "Change start date",
  change_status: "Change status",
  none: "No action",
};

function missingFieldPaths(item: BudSyncItem): string[] {
  if (item.actionType !== "create_learner") return [];
  const missing: string[] = [];
  const learner = (item.proposedValues?.learner as Record<string, unknown>) ?? {};
  if (!learner.level) missing.push("learner.level");
  if (!learner.learnerRef) missing.push("learner.learnerRef");
  const cohort = (item.proposedValues?.cohort as Record<string, unknown>) ?? {};
  if (cohort.action === "create") {
    if (!cohort.deliveryDay) missing.push("cohort.deliveryDay");
    if (!cohort.sessionStartTime) missing.push("cohort.sessionStartTime");
    if (!cohort.sessionEndTime) missing.push("cohort.sessionEndTime");
  }
  return missing;
}

function actionTypeForLimit(item: BudSyncItem): "learnerCreations" | "learnerUpdates" | "cohortCreations" | "tutorTransfers" | null {
  if (item.actionType === "create_learner") return "learnerCreations";
  if (item.actionType === "transfer_tutor") return "tutorTransfers";
  if (item.actionType === "update_learner" || item.actionType === "change_start_date") return "learnerUpdates";
  return null;
}

export default function BudSyncTrialPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [resetDialogOpen, setResetDialogOpen] = React.useState(false);
  const [resetReason, setResetReason] = React.useState("");

  const [activeJobId, setActiveJobId] = React.useState<number | null>(null);
  const [matchStatusFilter, setMatchStatusFilter] = React.useState<string>("all");
  const [reviewItem, setReviewItem] = React.useState<BudSyncItem | null>(null);
  const [reviewFields, setReviewFields] = React.useState<Record<string, string>>({});
  const [commitDialogOpen, setCommitDialogOpen] = React.useState(false);
  const [approvalReason, setApprovalReason] = React.useState("");
  const [limitOverrideReason, setLimitOverrideReason] = React.useState("");
  const [overLimitInfo, setOverLimitInfo] = React.useState<Record<string, number> | null>(null);

  const { data: status, isLoading: statusLoading } = useGetBudSyncStatus();
  const { data: settings } = useGetSettings();

  const establishMutation = useEstablishBudSyncBaseline();
  const resetMutation = useResetBudSyncBaseline();
  const previewMutation = useCreateBudSyncPreview();
  const updateItemMutation = useUpdateBudSyncJobItem();
  const commitMutation = useCommitBudSyncJob();

  const { data: job } = useGetBudSyncJob(activeJobId ?? 0, {
    query: { enabled: activeJobId !== null, queryKey: getGetBudSyncJobQueryKey(activeJobId ?? 0) },
  });

  const { data: itemsData, isLoading: itemsLoading } = useListBudSyncJobItems(
    activeJobId ?? 0,
    { matchStatus: matchStatusFilter !== "all" ? (matchStatusFilter as any) : undefined, pageSize: 200 },
    {
      query: {
        enabled: activeJobId !== null,
        queryKey: getListBudSyncJobItemsQueryKey(activeJobId ?? 0, { matchStatus: matchStatusFilter !== "all" ? (matchStatusFilter as any) : undefined, pageSize: 200 }),
      },
    },
  );

  const invalidateJob = () => {
    if (activeJobId === null) return;
    queryClient.invalidateQueries({ queryKey: getGetBudSyncJobQueryKey(activeJobId) });
    queryClient.invalidateQueries({ queryKey: getListBudSyncJobItemsQueryKey(activeJobId, {}) });
  };

  const handleEstablishBaseline = () => {
    establishMutation.mutate(
      { data: {} },
      {
        onSuccess: () => {
          toast({ title: "Trial baseline established" });
          queryClient.invalidateQueries({ queryKey: getGetBudSyncStatusQueryKey() });
        },
        onError: (err) => toast({ title: "Could not establish baseline", description: getErrorMessage(err), variant: "destructive" }),
      },
    );
  };

  const handleResetBaseline = () => {
    resetMutation.mutate(
      { data: { reason: resetReason.trim() } },
      {
        onSuccess: () => {
          toast({ title: "Baseline reset" });
          setResetDialogOpen(false);
          setResetReason("");
          setActiveJobId(null);
          queryClient.invalidateQueries({ queryKey: getGetBudSyncStatusQueryKey() });
        },
        onError: (err) => toast({ title: "Could not reset baseline", description: getErrorMessage(err), variant: "destructive" }),
      },
    );
  };

  const handlePreview = () => {
    previewMutation.mutate(undefined, {
      onSuccess: (newJob) => {
        setActiveJobId(newJob.id);
        setMatchStatusFilter("all");
        toast({ title: "Preview generated", description: `${newJob.newLearnersDetected} new, ${newJob.learnerUpdatesDetected} updated, ${newJob.conflictCount} conflicts.` });
      },
      onError: (err) => toast({ title: "Preview failed", description: getErrorMessage(err), variant: "destructive" }),
    });
  };

  const toggleApproval = (item: BudSyncItem) => {
    if (item.matchStatus === "conflict") return;
    if (!item.approved && missingFieldPaths(item).length > 0) {
      openReview(item);
      return;
    }
    updateItemMutation.mutate(
      { jobId: item.syncJobId, itemId: item.id, data: { approved: !item.approved } },
      {
        onSuccess: invalidateJob,
        onError: (err) => toast({ title: "Could not update item", description: getErrorMessage(err), variant: "destructive" }),
      },
    );
  };

  const openReview = (item: BudSyncItem) => {
    setReviewItem(item);
    setReviewFields({});
  };

  const submitReview = () => {
    if (!reviewItem) return;
    updateItemMutation.mutate(
      { jobId: reviewItem.syncJobId, itemId: reviewItem.id, data: { fieldUpdates: reviewFields, approved: true } },
      {
        onSuccess: () => {
          invalidateJob();
          setReviewItem(null);
          toast({ title: "Item approved" });
        },
        onError: (err) => toast({ title: "Could not approve item", description: getErrorMessage(err), variant: "destructive" }),
      },
    );
  };

  const approvedItems = (itemsData?.items ?? []).filter((i) => i.approved);
  const commitSummary = approvedItems.reduce(
    (acc, item) => {
      const key = actionTypeForLimit(item);
      if (key) acc[key] += 1;
      if (item.actionType === "create_learner" && (item.proposedValues?.cohort as any)?.action === "create") {
        acc.cohortCreations += 1;
      }
      return acc;
    },
    { learnerCreations: 0, learnerUpdates: 0, cohortCreations: 0, tutorTransfers: 0 },
  );

  const runCommit = (withOverrideReason?: string) => {
    if (!activeJobId) return;
    commitMutation.mutate(
      {
        jobId: activeJobId,
        data: {
          itemIds: approvedItems.map((i) => i.id),
          approvalReason: approvalReason.trim(),
          limitOverrideReason: withOverrideReason,
        },
      },
      {
        onSuccess: (result) => {
          toast({ title: "Commit complete", description: `${result.appliedCount} item(s) applied.` });
          setCommitDialogOpen(false);
          setApprovalReason("");
          setOverLimitInfo(null);
          setLimitOverrideReason("");
          invalidateJob();
          queryClient.invalidateQueries({ queryKey: getGetBudSyncStatusQueryKey() });
        },
        onError: (err) => {
          const status = (err as { status?: number } | undefined)?.status;
          const detail = (err as { data?: any } | undefined)?.data;
          if (status === 409 && detail?.reason === "trial_limit_exceeded") {
            setOverLimitInfo(detail.overLimit);
            return;
          }
          toast({ title: "Commit failed", description: getErrorMessage(err), variant: "destructive" });
        },
      },
    );
  };

  const hasActiveBaseline = !!status?.activeBaseline;

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Bud Sync Trial" }]} />

      <div className="mb-6 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <RefreshCw className="w-8 h-8 text-primary" /> Bud Synchronisation Trial
        </h1>
      </div>

      <Card className="mb-6 border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20">
        <CardContent className="p-4 flex gap-3 items-start">
          <Info className="w-5 h-5 text-amber-700 dark:text-amber-500 shrink-0 mt-0.5" />
          <p className="text-sm text-amber-800 dark:text-amber-400">
            Only learners newly appearing after the trial baseline, and post-baseline changes to existing
            Attendance learners, are eligible. Existing unmatched Bud learners are excluded.
          </p>
        </CardContent>
      </Card>

      <Card className="mb-6 shadow-sm">
        <CardHeader>
          <CardTitle className="text-lg">Bud source status</CardTitle>
          <CardDescription>
            {statusLoading ? "Loading…" : status?.sourceMaxSyncedAt ? `Last synced ${format(parseISO(status.sourceMaxSyncedAt), "MMM d, yyyy HH:mm")}` : "No Bud data synced yet"}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <div className="text-2xl font-bold">{status?.sourceRowCount ?? "—"}</div>
            <div className="text-xs text-muted-foreground">Bud rows visible</div>
          </div>
          <div>
            <div className="text-2xl font-bold">{status?.matchedLearnerCount ?? "—"}</div>
            <div className="text-xs text-muted-foreground">Matched learners</div>
          </div>
          <div>
            <div className="text-2xl font-bold">{status?.unmatchedLearnerCount ?? "—"}</div>
            <div className="text-xs text-muted-foreground">Unmatched Bud rows</div>
          </div>
          <div>
            {hasActiveBaseline ? (
              <>
                <div className="text-sm font-medium">
                  Baseline #{status?.activeBaseline?.id} — {status?.activeBaseline && format(parseISO(status.activeBaseline.establishedAt), "MMM d, yyyy")}
                </div>
                <Button variant="outline" size="sm" className="mt-1" onClick={() => setResetDialogOpen(true)}>
                  Reset Baseline
                </Button>
              </>
            ) : (
              <Button onClick={handleEstablishBaseline} disabled={establishMutation.isPending} size="sm">
                {establishMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                Establish Trial Baseline
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center justify-between mb-4">
        <div className="text-sm text-muted-foreground">
          Trial limits: {settings?.budSyncMaxLearnerCreations ?? "—"} creations · {settings?.budSyncMaxLearnerUpdates ?? "—"} updates ·{" "}
          {settings?.budSyncMaxCohortCreations ?? "—"} cohorts · {settings?.budSyncMaxTutorTransfers ?? "—"} transfers per commit
        </div>
        <Button onClick={handlePreview} disabled={!hasActiveBaseline || previewMutation.isPending} className="hover-elevate shadow-sm">
          {previewMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
          Run Preview
        </Button>
      </div>

      {job && (
        <Card className="mb-6 shadow-sm">
          <CardHeader>
            <CardTitle className="text-lg">Preview #{job.id}</CardTitle>
            <CardDescription>
              {job.totalSourceRowsExamined} Bud rows examined · {job.newLearnersDetected} new · {job.learnerUpdatesDetected} updated ·{" "}
              {job.conflictCount} conflicts · {job.skippedCount} before trial
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-4 mb-4">
              <Label className="text-xs text-muted-foreground" htmlFor="bud-sync-match-status">Change type</Label>
              <Select value={matchStatusFilter} onValueChange={setMatchStatusFilter}>
                <SelectTrigger id="bud-sync-match-status" aria-label="Change type" className="w-[220px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MATCH_STATUS_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>

            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10"></TableHead>
                    <TableHead>ID</TableHead>
                    <TableHead>First Name</TableHead>
                    <TableHead>Last Name</TableHead>
                    <TableHead>Change type</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Warnings</TableHead>
                    <TableHead className="text-right">Review</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {itemsLoading ? (
                    <TableRow><TableCell colSpan={8} className="h-24 text-center">Loading…</TableCell></TableRow>
                  ) : (itemsData?.items.length ?? 0) === 0 ? (
                    <TableRow><TableCell colSpan={8} className="h-24 text-center text-muted-foreground">No items match this filter.</TableCell></TableRow>
                  ) : (
                    itemsData?.items.map((item) => (
                      <TableRow key={item.id}>
                        <TableCell>
                          <Checkbox
                            checked={item.approved}
                            disabled={item.matchStatus === "conflict"}
                            onCheckedChange={() => toggleApproval(item)}
                            aria-label={`Approve item ${item.id}`}
                          />
                        </TableCell>
                        <TableCell className="font-mono text-xs">{item.sourceLearnerReference ?? "—"}</TableCell>
                        <TableCell className="text-sm">{item.sourceFirstName ?? "—"}</TableCell>
                        <TableCell className="text-sm">{item.sourceLastName ?? "—"}</TableCell>
                        <TableCell className="text-sm">{ACTION_TYPE_LABELS[item.actionType] ?? item.actionType}</TableCell>
                        <TableCell>
                          <Badge variant={item.matchStatus === "conflict" ? "destructive" : item.matchStatus === "new" ? "default" : "secondary"}>
                            {item.matchStatus.replace(/_/g, " ")}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          {item.warnings.length > 0 && (
                            <span className="inline-flex items-center gap-1 text-xs text-amber-700 dark:text-amber-500">
                              <AlertTriangle className="w-3.5 h-3.5" /> {item.warnings.length}
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button variant="ghost" size="sm" onClick={() => openReview(item)}>View</Button>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>

            <div className="flex justify-end mt-6">
              <Button
                variant="destructive"
                disabled={approvedItems.length === 0}
                onClick={() => setCommitDialogOpen(true)}
              >
                Commit {approvedItems.length} Approved Change{approvedItems.length === 1 ? "" : "s"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Reset baseline dialog */}
      <Dialog open={resetDialogOpen} onOpenChange={(o) => { setResetDialogOpen(o); if (!o) setResetReason(""); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset Trial Baseline</DialogTitle>
            <DialogDescription>
              This does not delete the previous baseline, but every future preview will interpret "new since
              baseline" relative to the new one. A reason is required and this action is audited.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2 space-y-2">
            <Label htmlFor="baseline-reset-reason">Reason</Label>
            <Textarea id="baseline-reset-reason" value={resetReason} onChange={(e) => setResetReason(e.target.value)} rows={3} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setResetDialogOpen(false)}>Go Back</Button>
            <Button variant="destructive" onClick={handleResetBaseline} disabled={resetMutation.isPending || !resetReason.trim()}>
              {resetMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Reset Baseline
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Item review/edit dialog */}
      <Dialog open={!!reviewItem} onOpenChange={(o) => { if (!o) setReviewItem(null); }}>
        <DialogContent className="sm:max-w-[550px]">
          <DialogHeader>
            <DialogTitle>
              Review {reviewItem ? `${reviewItem.sourceFirstName ?? ""} ${reviewItem.sourceLastName ?? ""}`.trim() || reviewItem.sourceLearnerReference || "item" : ""}
            </DialogTitle>
            <DialogDescription>{reviewItem && (ACTION_TYPE_LABELS[reviewItem.actionType] ?? reviewItem.actionType)}</DialogDescription>
          </DialogHeader>
          {reviewItem && (
            <div className="space-y-4 py-2 max-h-[60vh] overflow-auto">
              {reviewItem.matchStatus === "conflict" ? (
                <div className="flex items-start gap-2 text-sm text-rose-700 dark:text-rose-500">
                  <ShieldAlert className="w-4 h-4 mt-0.5 shrink-0" />
                  <span>{reviewItem.reason?.replace(/_/g, " ")} — this item cannot be approved and requires manual investigation.</span>
                </div>
              ) : (
                <>
                  <pre className="text-xs bg-muted/40 rounded-md p-3 overflow-auto">{JSON.stringify(reviewItem.proposedValues, null, 2)}</pre>
                  {missingFieldPaths(reviewItem).map((path) => (
                    <div key={path} className="space-y-1">
                      <Label htmlFor={`field-${path}`}>{path}</Label>
                      <Input
                        id={`field-${path}`}
                        value={reviewFields[path] ?? ""}
                        onChange={(e) => setReviewFields((f) => ({ ...f, [path]: e.target.value }))}
                      />
                    </div>
                  ))}
                  {reviewItem.warnings.length > 0 && (
                    <div className="text-xs text-amber-700 dark:text-amber-500 space-y-1">
                      {reviewItem.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setReviewItem(null)}>Close</Button>
            {reviewItem && reviewItem.matchStatus !== "conflict" && (
              <Button onClick={submitReview} disabled={updateItemMutation.isPending}>
                {updateItemMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                Approve
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Commit confirmation dialog */}
      <Dialog open={commitDialogOpen} onOpenChange={(o) => { setCommitDialogOpen(o); if (!o) { setApprovalReason(""); setOverLimitInfo(null); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Commit</DialogTitle>
            <DialogDescription>You are about to:</DialogDescription>
          </DialogHeader>
          <div className="text-sm space-y-1 py-2">
            <div>Create {commitSummary.learnerCreations} learner(s)</div>
            <div>Create {commitSummary.cohortCreations} cohort(s)</div>
            <div>Update {commitSummary.learnerUpdates} learner record(s)</div>
            <div>Transfer {commitSummary.tutorTransfers} learner(s)</div>
            <div className="font-semibold mt-2">Historical attendance changes: 0</div>
          </div>
          {overLimitInfo && (
            <div className="space-y-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-3 rounded-md">
              <p className="text-sm text-amber-800 dark:text-amber-400">
                This exceeds the configured trial limit(s): {Object.entries(overLimitInfo).map(([k, v]) => `${k} (${v})`).join(", ")}.
                Provide a reason to override.
              </p>
              <Textarea value={limitOverrideReason} onChange={(e) => setLimitOverrideReason(e.target.value)} rows={2} placeholder="Reason for exceeding the trial limit" />
            </div>
          )}
          <div className="space-y-2 py-2">
            <Label htmlFor="commit-approval-reason">Approval reason</Label>
            <Textarea id="commit-approval-reason" value={approvalReason} onChange={(e) => setApprovalReason(e.target.value)} rows={2} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCommitDialogOpen(false)}>Go Back</Button>
            <Button
              variant="destructive"
              disabled={commitMutation.isPending || !approvalReason.trim() || (!!overLimitInfo && !limitOverrideReason.trim())}
              onClick={() => runCommit(overLimitInfo ? limitOverrideReason.trim() : undefined)}
            >
              {commitMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Confirm Commit
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
