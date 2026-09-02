import * as React from "react";
import {
  useGetBudSyncStatus,
  useEstablishBudSyncBaseline,
  useResetBudSyncBaseline,
  useCreateBudSyncPreview,
  useGetBudSyncJob,
  useGetBudSyncJobSummary,
  useListBudSyncJobItems,
  useUpdateBudSyncJobItem,
  useBulkApproveBudSyncJobItems,
  useCommitBudSyncJob,
  useLinkBudSyncJobItemToExistingLearner,
  useListLearners,
  useGetSettings,
  getGetBudSyncStatusQueryKey,
  getGetBudSyncJobQueryKey,
  getGetBudSyncJobSummaryQueryKey,
  getListBudSyncJobItemsQueryKey,
  getListLearnersQueryKey,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { RefreshCw, Loader2, AlertTriangle, ShieldAlert, Info, ArrowRight, ChevronLeft, ChevronRight } from "lucide-react";
import { format, parseISO } from "date-fns";

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

type StatusChangeValues = {
  previousAcceptedStatusDesc: string | null;
  currentStatusDesc: string;
  currentLearnerStatus: string;
  kind: "automatic" | "needs_date" | "informational" | "unrecognised";
  targetStatus: string | null;
  dateField: string | null;
  effectiveDate: string | null;
};

function statusChangeOf(item: BudSyncItem): StatusChangeValues | null {
  return (item.proposedValues?.statusChange as StatusChangeValues | undefined) ?? null;
}

function statusChangeOutcomeLabel(item: BudSyncItem): { label: string; variant: "default" | "secondary" | "destructive" | "outline" } {
  if (item.applied) return { label: item.outcome === "stale_source_rejected" || item.outcome === "stale_internal_rejected" ? "Stale — rejected" : "Applied", variant: item.outcome?.includes("rejected") ? "destructive" : "default" };
  const change = statusChangeOf(item);
  if (change?.kind === "needs_date") return { label: "Awaiting information", variant: "secondary" };
  if (change?.kind === "informational") return { label: "Informational", variant: "outline" };
  if (item.approved) return { label: "Approved — pending commit", variant: "secondary" };
  return { label: "Awaiting review", variant: "outline" };
}

function missingFieldPaths(item: BudSyncItem): string[] {
  if (item.actionType === "change_status") {
    const change = statusChangeOf(item);
    if (change?.kind === "needs_date" && !change.effectiveDate) return [`statusChange.${change.dateField ?? "effectiveDate"}`];
    return [];
  }
  if (item.actionType !== "create_learner") return [];
  const missing: string[] = [];
  const learner = (item.proposedValues?.learner as Record<string, unknown>) ?? {};
  if (!learner.level) missing.push("learner.level");
  if (!learner.learnerRef) missing.push("learner.learnerRef");
  return missing;
}

function actionTypeForLimit(item: BudSyncItem): "learnerCreations" | "learnerUpdates" | "cohortCreations" | "tutorTransfers" | null {
  if (item.actionType === "create_learner") return "learnerCreations";
  if (item.actionType === "transfer_tutor") return "tutorTransfers";
  if (item.actionType === "update_learner" || item.actionType === "change_start_date" || item.actionType === "change_status") return "learnerUpdates";
  return null;
}

function itemDisplayName(item: BudSyncItem | null): string {
  if (!item) return "";
  return `${item.sourceFirstName ?? ""} ${item.sourceLastName ?? ""}`.trim() || item.sourceLearnerReference || "item";
}

export default function BudSyncTrialPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [resetDialogOpen, setResetDialogOpen] = React.useState(false);
  const [resetReason, setResetReason] = React.useState("");

  const [activeJobId, setActiveJobId] = React.useState<number | null>(null);
  const [activeTab, setActiveTab] = React.useState("status-changes");
  const [reviewItem, setReviewItem] = React.useState<BudSyncItem | null>(null);
  const [reviewFields, setReviewFields] = React.useState<Record<string, string>>({});
  const [commitDialogOpen, setCommitDialogOpen] = React.useState(false);
  const [approvalReason, setApprovalReason] = React.useState("");
  const [limitOverrideReason, setLimitOverrideReason] = React.useState("");
  const [overLimitInfo, setOverLimitInfo] = React.useState<Record<string, number> | null>(null);
  const [linkItem, setLinkItem] = React.useState<BudSyncItem | null>(null);
  const [linkSearch, setLinkSearch] = React.useState("");

  // New Learners bulk-ingest: per-row learnerRef/level drafts (Bud has no
  // equivalent for either, so they always need typing) and which rows are
  // selected for the bulk "Approve & Create Selected" action -- separate
  // from item.approved, since selection here is just "include this row in
  // the next bulk call," not the item's actual approved state.
  const [newLearnerDrafts, setNewLearnerDrafts] = React.useState<Record<number, { learnerRef: string; level: string }>>({});
  const [selectedNewLearnerIds, setSelectedNewLearnerIds] = React.useState<Set<number>>(new Set());

  const { data: status, isLoading: statusLoading } = useGetBudSyncStatus();
  const { data: settings } = useGetSettings();

  const establishMutation = useEstablishBudSyncBaseline();
  const resetMutation = useResetBudSyncBaseline();
  const previewMutation = useCreateBudSyncPreview();
  const updateItemMutation = useUpdateBudSyncJobItem();
  const bulkApproveMutation = useBulkApproveBudSyncJobItems();
  const commitMutation = useCommitBudSyncJob();
  const linkExistingMutation = useLinkBudSyncJobItemToExistingLearner();

  const { data: job } = useGetBudSyncJob(activeJobId ?? 0, {
    query: { enabled: activeJobId !== null, queryKey: getGetBudSyncJobQueryKey(activeJobId ?? 0) },
  });

  const { data: summary } = useGetBudSyncJobSummary(activeJobId ?? 0, {
    query: { enabled: activeJobId !== null, queryKey: getGetBudSyncJobSummaryQueryKey(activeJobId ?? 0) },
  });

  const statusChangeParams = { matchStatus: "status_change" as const, pageSize: 200 };
  // Unlike Status Changes/Conflicts, New Learners can now realistically run
  // into the thousands (the historical-backfill gate that used to keep this
  // tab small was retired), so it needs real pagination rather than a
  // single fixed-size fetch.
  const [newLearnersPage, setNewLearnersPage] = React.useState(1);
  const newLearnersPageSize = 25;
  const newLearnerParams = { matchStatus: "new" as const, page: newLearnersPage, pageSize: newLearnersPageSize };
  const conflictParams = { matchStatus: "conflict" as const, pageSize: 200 };

  const { data: statusChangeItems, isLoading: statusChangesLoading } = useListBudSyncJobItems(activeJobId ?? 0, statusChangeParams, {
    query: { enabled: activeJobId !== null, queryKey: getListBudSyncJobItemsQueryKey(activeJobId ?? 0, statusChangeParams) },
  });
  const { data: newLearnerItems, isLoading: newLearnersLoading } = useListBudSyncJobItems(activeJobId ?? 0, newLearnerParams, {
    query: { enabled: activeJobId !== null, queryKey: getListBudSyncJobItemsQueryKey(activeJobId ?? 0, newLearnerParams) },
  });
  const { data: conflictItems, isLoading: conflictsLoading } = useListBudSyncJobItems(activeJobId ?? 0, conflictParams, {
    query: { enabled: activeJobId !== null, queryKey: getListBudSyncJobItemsQueryKey(activeJobId ?? 0, conflictParams) },
  });

  const linkSearchParams = { search: linkSearch, pageSize: 10 };
  const { data: linkCandidates } = useListLearners(
    linkSearchParams,
    { query: { enabled: !!linkItem && linkSearch.trim().length > 1, queryKey: getListLearnersQueryKey(linkSearchParams) } },
  );

  const invalidateJob = () => {
    if (activeJobId === null) return;
    queryClient.invalidateQueries({ queryKey: getGetBudSyncJobQueryKey(activeJobId) });
    queryClient.invalidateQueries({ queryKey: getGetBudSyncJobSummaryQueryKey(activeJobId) });
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
        setActiveTab("status-changes");
        setNewLearnersPage(1);
        setNewLearnerDrafts({});
        setSelectedNewLearnerIds(new Set());
        toast({
          title: "Bud checked for changes",
          description: `${newJob.statusChangesDetected} status change(s), ${newJob.newLearnersDetected} new learner(s), ${newJob.conflictCount} conflict(s).`,
        });
      },
      onError: (err) => toast({ title: "Could not check Bud for changes", description: getErrorMessage(err), variant: "destructive" }),
    });
  };

  const computeNewLearnerDefaultDraft = (item: BudSyncItem) => {
    const learner = (item.proposedValues?.learner as Record<string, unknown>) ?? {};
    const programme = (learner.programme as string) ?? "";
    // Level defaults to 3 for every programme except a "Services" one (e.g.
    // Business Administration Services), which is level 2 -- per Skills 4's
    // own programme naming, not something Bud itself reports.
    const defaultLevel = programme.toLowerCase().includes("services") ? "2" : "3";
    return {
      learnerRef: (learner.learnerRef as string) || item.sourceLearnerReference || "",
      level: (learner.level as string) || defaultLevel,
    };
  };

  // Each page fetch is only 25 rows, so newLearnerDrafts (keyed by item id,
  // never reset by pagination) is seeded with every visited page's defaults
  // as soon as it loads -- not just lazily computed for display. Without
  // this, selecting rows across two pages and then approving in one go
  // would only find drafts for whichever page happened to be on screen at
  // submit time, silently dropping the other page's selections.
  React.useEffect(() => {
    const items = newLearnerItems?.items;
    if (!items || items.length === 0) return;
    setNewLearnerDrafts((prev) => {
      const additions = items.filter((item) => !prev[item.id]);
      if (additions.length === 0) return prev;
      const next = { ...prev };
      additions.forEach((item) => { next[item.id] = computeNewLearnerDefaultDraft(item); });
      return next;
    });
  }, [newLearnerItems]);

  const newLearnerDraftFor = (item: BudSyncItem) => newLearnerDrafts[item.id] ?? computeNewLearnerDefaultDraft(item);

  const updateNewLearnerDraft = (
    itemId: number, field: "learnerRef" | "level", value: string, seed: { learnerRef: string; level: string },
  ) => {
    setNewLearnerDrafts((prev) => ({
      ...prev,
      [itemId]: { learnerRef: prev[itemId]?.learnerRef ?? seed.learnerRef, level: prev[itemId]?.level ?? seed.level, [field]: value },
    }));
  };

  const toggleNewLearnerSelected = (itemId: number) => {
    setSelectedNewLearnerIds((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId); else next.add(itemId);
      return next;
    });
  };

  const toggleAllNewLearnersSelected = (checked: boolean, ids: number[]) => {
    setSelectedNewLearnerIds((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => { if (checked) next.add(id); else next.delete(id); });
      return next;
    });
  };

  const submitBulkApproveNewLearners = () => {
    if (selectedNewLearnerIds.size === 0) return;
    // Derived from the selected items' own syncJobId (same convention as
    // toggleApproval/submitReview elsewhere in this file), not activeJobId
    // -- they're the same value in the real app, but the item is what's
    // actually in scope here.
    const jobId = newLearnerItems?.items.find((i) => selectedNewLearnerIds.has(i.id))?.syncJobId;
    if (jobId === undefined) return;
    // Iterates selectedNewLearnerIds directly, NOT newLearnerItems.items --
    // the latter is only the currently-loaded page (25 rows), so filtering
    // against it would silently drop selections made on any other page.
    // newLearnerDrafts is seeded for every visited page (see the effect
    // above), so a row that was never manually edited still resolves to
    // the same default shown on screen, not blank strings.
    const items = Array.from(selectedNewLearnerIds).map((itemId) => {
      const draft = newLearnerDrafts[itemId] ?? { learnerRef: "", level: "" };
      return { itemId, learnerRef: draft.learnerRef, level: draft.level };
    });
    bulkApproveMutation.mutate(
      { jobId, data: { items } },
      {
        onSuccess: (result) => {
          invalidateJob();
          setSelectedNewLearnerIds(new Set());
          if (result.errors.length === 0) {
            toast({ title: "Approved", description: `${result.approvedCount} learner(s) ready to commit.` });
          } else {
            toast({
              title: `Approved ${result.approvedCount}, ${result.errors.length} need attention`,
              description: result.errors.map((e) => `#${e.itemId}: ${e.message}`).join("; "),
              variant: "destructive",
            });
          }
        },
        onError: (err) => toast({ title: "Could not approve selected learners", description: getErrorMessage(err), variant: "destructive" }),
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

  const submitLink = (learnerId: number) => {
    if (!linkItem) return;
    linkExistingMutation.mutate(
      { jobId: linkItem.syncJobId, itemId: linkItem.id, data: { learnerId } },
      {
        onSuccess: () => {
          invalidateJob();
          setLinkItem(null);
          setLinkSearch("");
          toast({ title: "Linked to existing learner" });
        },
        onError: (err) => toast({ title: "Could not link learner", description: getErrorMessage(err), variant: "destructive" }),
      },
    );
  };

  const allVisibleItems = [...(statusChangeItems?.items ?? []), ...(newLearnerItems?.items ?? [])];
  const approvedItems = allVisibleItems.filter((i) => i.approved && !i.applied);
  const commitSummary = approvedItems.reduce(
    (acc, item) => {
      const key = actionTypeForLimit(item);
      if (key) acc[key] += 1;
      if (item.actionType === "create_learner" && (item.proposedValues?.cohort as any)?.action === "create") {
        acc.cohortCreations += 1;
      }
      if (item.actionType === "transfer_tutor") acc.tutorTransfers += 1;
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
            Bud synchronisation trial: learners whose Bud status has changed are shown in Status Changes at any
            status. Any Bud learner not yet in Attendance — regardless of when their Bud record first appeared — is
            shown in New Learners for your review, as long as they're actively enrolled (Bud status "In Progress").
            Bud learners with a different status (e.g. Withdrawn, Completed, Pending) who were never matched are not
            proposed for creation — see Sync History.
          </p>
        </CardContent>
      </Card>

      <Card className="mb-6 shadow-sm">
        <CardHeader>
          <CardTitle className="text-lg">Baseline &amp; source status</CardTitle>
          <CardDescription>
            {statusLoading ? "Loading…" : status?.sourceMaxSyncedAt ? `Last Bud source sync ${format(parseISO(status.sourceMaxSyncedAt), "MMM d, yyyy HH:mm")}` : "No Bud data synced yet"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-4">
          <div>
            {hasActiveBaseline ? (
              <div className="text-sm font-medium">
                Baseline #{status?.activeBaseline?.id} established {status?.activeBaseline && format(parseISO(status.activeBaseline.establishedAt), "MMM d, yyyy")}
              </div>
            ) : (
              <div className="text-sm text-muted-foreground">No trial baseline established yet</div>
            )}
            <div className="text-xs text-muted-foreground mt-1">
              Trial limits: {settings?.budSyncMaxLearnerCreations ?? "—"} creations · {settings?.budSyncMaxLearnerUpdates ?? "—"} updates ·{" "}
              {settings?.budSyncMaxCohortCreations ?? "—"} cohorts · {settings?.budSyncMaxTutorTransfers ?? "—"} transfers per commit
            </div>
          </div>
          <div className="flex gap-2">
            {hasActiveBaseline && (
              <Button variant="outline" size="sm" onClick={() => setResetDialogOpen(true)}>
                Reset Baseline
              </Button>
            )}
            {!hasActiveBaseline && (
              <Button onClick={handleEstablishBaseline} disabled={establishMutation.isPending} size="sm">
                {establishMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                Establish Trial Baseline
              </Button>
            )}
            <Button onClick={handlePreview} disabled={!hasActiveBaseline || previewMutation.isPending} className="hover-elevate shadow-sm">
              {previewMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Check Bud for Changes
            </Button>
          </div>
        </CardContent>
      </Card>

      {job && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <Card className="shadow-sm">
              <CardContent className="pt-6">
                <div className="text-2xl font-bold">{summary?.statusChangesCount ?? "—"}</div>
                <div className="text-xs text-muted-foreground">Status changes requiring processing</div>
              </CardContent>
            </Card>
            <Card className="shadow-sm">
              <CardContent className="pt-6">
                <div className="text-2xl font-bold">{summary?.newLearnersCount ?? "—"}</div>
                <div className="text-xs text-muted-foreground">New learners requiring review</div>
              </CardContent>
            </Card>
            <Card className="shadow-sm">
              <CardContent className="pt-6">
                <div className="text-2xl font-bold">{summary?.conflictsCount ?? "—"}</div>
                <div className="text-xs text-muted-foreground">Conflicts requiring investigation</div>
              </CardContent>
            </Card>
            <Card className="shadow-sm">
              <CardContent className="pt-6">
                <div className="text-2xl font-bold">
                  {summary?.lastSuccessfulSyncAt ? format(parseISO(summary.lastSuccessfulSyncAt), "MMM d") : "—"}
                </div>
                <div className="text-xs text-muted-foreground">Last successful sync</div>
              </CardContent>
            </Card>
          </div>

          <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
            <TabsList className="grid w-full grid-cols-4 max-w-2xl mb-6">
              <TabsTrigger value="status-changes">Status Changes</TabsTrigger>
              <TabsTrigger value="new-learners">New Learners</TabsTrigger>
              <TabsTrigger value="conflicts">Conflicts</TabsTrigger>
              <TabsTrigger value="sync-history">Sync History</TabsTrigger>
            </TabsList>

            <TabsContent value="status-changes">
              <Card className="shadow-sm">
                <CardContent className="pt-6">
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>ID</TableHead>
                          <TableHead>First name</TableHead>
                          <TableHead>Last name</TableHead>
                          <TableHead>Current Attendance status</TableHead>
                          <TableHead>New Bud status</TableHead>
                          <TableHead>Detected at</TableHead>
                          <TableHead>Processing outcome</TableHead>
                          <TableHead className="text-right">Review</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {statusChangesLoading ? (
                          <TableRow><TableCell colSpan={8} className="h-24 text-center">Loading…</TableCell></TableRow>
                        ) : (statusChangeItems?.items.length ?? 0) === 0 ? (
                          <TableRow><TableCell colSpan={8} className="h-24 text-center text-muted-foreground">No status changes detected.</TableCell></TableRow>
                        ) : (
                          statusChangeItems?.items.map((item) => {
                            const change = statusChangeOf(item);
                            const outcome = statusChangeOutcomeLabel(item);
                            return (
                              <TableRow key={item.id}>
                                <TableCell className="font-mono text-xs">{item.sourceLearnerReference ?? "—"}</TableCell>
                                <TableCell className="text-sm">{item.sourceFirstName ?? "—"}</TableCell>
                                <TableCell className="text-sm">{item.sourceLastName ?? "—"}</TableCell>
                                <TableCell className="text-sm">{change?.currentLearnerStatus ?? "—"}</TableCell>
                                <TableCell className="text-sm">
                                  <span className="inline-flex items-center gap-1.5">
                                    <span className="text-muted-foreground">{change?.previousAcceptedStatusDesc ?? "(none)"}</span>
                                    <ArrowRight className="w-3.5 h-3.5 text-muted-foreground" />
                                    <span className="font-medium">{change?.currentStatusDesc}</span>
                                  </span>
                                </TableCell>
                                <TableCell className="text-sm text-muted-foreground">
                                  {item.createdAt ? format(parseISO(item.createdAt), "MMM d, yyyy HH:mm") : "—"}
                                </TableCell>
                                <TableCell>
                                  <Badge variant={outcome.variant}>{outcome.label}</Badge>
                                </TableCell>
                                <TableCell className="text-right">
                                  <Button variant="ghost" size="sm" onClick={() => openReview(item)}>Review</Button>
                                </TableCell>
                              </TableRow>
                            );
                          })
                        )}
                      </TableBody>
                    </Table>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="new-learners">
              <Card className="shadow-sm">
                <CardContent className="pt-6">
                  <div className="flex items-center justify-between gap-4 mb-4 px-1">
                    <p className="text-sm text-muted-foreground">
                      Fill in Ref and Level for the learners you want to create, select them, then approve as a batch.
                      Cohort assignment is handled afterward from the Allocation screen.
                    </p>
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-sm text-muted-foreground">{selectedNewLearnerIds.size} selected</span>
                      <Button
                        size="sm"
                        disabled={selectedNewLearnerIds.size === 0 || bulkApproveMutation.isPending}
                        onClick={submitBulkApproveNewLearners}
                      >
                        {bulkApproveMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                        Approve &amp; Create Selected
                      </Button>
                    </div>
                  </div>
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-10">
                            <Checkbox
                              checked={(newLearnerItems?.items.length ?? 0) > 0 && (newLearnerItems?.items ?? []).every((i) => selectedNewLearnerIds.has(i.id))}
                              onCheckedChange={(c) => toggleAllNewLearnersSelected(!!c, (newLearnerItems?.items ?? []).map((i) => i.id))}
                              aria-label="Select all new learners on this page"
                            />
                          </TableHead>
                          <TableHead>ID</TableHead>
                          <TableHead>First name</TableHead>
                          <TableHead>Last name</TableHead>
                          <TableHead>Bud status</TableHead>
                          <TableHead>Programme</TableHead>
                          <TableHead>Start date</TableHead>
                          <TableHead>Ref</TableHead>
                          <TableHead>Level</TableHead>
                          <TableHead className="text-right">Action</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {newLearnersLoading ? (
                          <TableRow><TableCell colSpan={10} className="h-24 text-center">Loading…</TableCell></TableRow>
                        ) : (newLearnerItems?.items.length ?? 0) === 0 ? (
                          <TableRow><TableCell colSpan={10} className="h-24 text-center text-muted-foreground">No eligible new learners detected.</TableCell></TableRow>
                        ) : (
                          newLearnerItems?.items.map((item) => {
                            const draft = newLearnerDraftFor(item);
                            const learner = (item.proposedValues?.learner as Record<string, unknown>) ?? {};
                            return (
                              <TableRow key={item.id}>
                                <TableCell>
                                  <Checkbox
                                    checked={selectedNewLearnerIds.has(item.id)}
                                    onCheckedChange={() => toggleNewLearnerSelected(item.id)}
                                    aria-label={`Select item ${item.id}`}
                                  />
                                </TableCell>
                                <TableCell className="font-mono text-xs">{item.sourceLearnerReference ?? "—"}</TableCell>
                                <TableCell className="text-sm">{item.sourceFirstName ?? "—"}</TableCell>
                                <TableCell className="text-sm">{item.sourceLastName ?? "—"}</TableCell>
                                <TableCell className="text-sm">{(item.proposedValues?.budStatus as string) ?? "—"}</TableCell>
                                <TableCell className="text-sm">{(learner.programme as string) ?? "—"}</TableCell>
                                <TableCell className="text-sm">{(learner.startDate as string) ?? "—"}</TableCell>
                                <TableCell>
                                  <Input
                                    value={draft.learnerRef}
                                    onChange={(e) => updateNewLearnerDraft(item.id, "learnerRef", e.target.value, draft)}
                                    placeholder="Required"
                                    className="h-8 w-32"
                                    aria-label={`Learner reference for item ${item.id}`}
                                  />
                                </TableCell>
                                <TableCell>
                                  <Input
                                    value={draft.level}
                                    onChange={(e) => updateNewLearnerDraft(item.id, "level", e.target.value, draft)}
                                    placeholder="Required"
                                    className="h-8 w-20"
                                    aria-label={`Level for item ${item.id}`}
                                  />
                                </TableCell>
                                <TableCell className="text-right space-x-1">
                                  <Button variant="ghost" size="sm" onClick={() => setLinkItem(item)}>Already represented</Button>
                                </TableCell>
                              </TableRow>
                            );
                          })
                        )}
                      </TableBody>
                    </Table>
                  </div>
                  {newLearnerItems && newLearnerItems.total > 0 && (
                    <div className="flex items-center justify-between border-t px-4 py-3 -mx-6 -mb-6 mt-2 bg-muted/10">
                      <div className="text-sm text-muted-foreground" data-testid="new-learners-pager-summary">
                        Showing{" "}
                        <span className="font-medium text-foreground">{((newLearnersPage - 1) * newLearnersPageSize) + 1}</span> to{" "}
                        <span className="font-medium text-foreground">{Math.min(newLearnersPage * newLearnersPageSize, newLearnerItems.total)}</span> of{" "}
                        <span className="font-medium text-foreground">{newLearnerItems.total}</span> new learners
                      </div>
                      <div className="flex items-center space-x-2">
                        <Button
                          variant="outline"
                          size="sm"
                          aria-label="Previous page of new learners"
                          onClick={() => setNewLearnersPage((p) => Math.max(1, p - 1))}
                          disabled={newLearnersPage === 1}
                        >
                          <ChevronLeft className="w-4 h-4" />
                        </Button>
                        <div className="text-sm font-medium px-2">{newLearnersPage}</div>
                        <Button
                          variant="outline"
                          size="sm"
                          aria-label="Next page of new learners"
                          onClick={() => setNewLearnersPage((p) => p + 1)}
                          disabled={newLearnersPage * newLearnersPageSize >= newLearnerItems.total}
                        >
                          <ChevronRight className="w-4 h-4" />
                        </Button>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="conflicts">
              <Card className="shadow-sm">
                <CardContent className="pt-6">
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>ID</TableHead>
                          <TableHead>First name</TableHead>
                          <TableHead>Last name</TableHead>
                          <TableHead>Reason</TableHead>
                          <TableHead>Warnings</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {conflictsLoading ? (
                          <TableRow><TableCell colSpan={5} className="h-24 text-center">Loading…</TableCell></TableRow>
                        ) : (conflictItems?.items.length ?? 0) === 0 ? (
                          <TableRow><TableCell colSpan={5} className="h-24 text-center text-muted-foreground">No conflicts.</TableCell></TableRow>
                        ) : (
                          conflictItems?.items.map((item) => (
                            <TableRow key={item.id}>
                              <TableCell className="font-mono text-xs">{item.sourceLearnerReference ?? "—"}</TableCell>
                              <TableCell className="text-sm">{item.sourceFirstName ?? "—"}</TableCell>
                              <TableCell className="text-sm">{item.sourceLastName ?? "—"}</TableCell>
                              <TableCell className="text-sm flex items-center gap-1.5">
                                <ShieldAlert className="w-3.5 h-3.5 text-rose-600 dark:text-rose-500 shrink-0" />
                                {item.reason?.replace(/_/g, " ")}
                              </TableCell>
                              <TableCell className="text-xs text-muted-foreground">{item.warnings.join("; ") || "—"}</TableCell>
                            </TableRow>
                          ))
                        )}
                      </TableBody>
                    </Table>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="sync-history">
              <Card className="shadow-sm">
                <CardHeader>
                  <CardTitle className="text-lg">Preview #{job.id}</CardTitle>
                  <CardDescription>Technical details for this sync run.</CardDescription>
                </CardHeader>
                <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  <div><div className="text-lg font-semibold">{job.totalSourceRowsExamined}</div><div className="text-xs text-muted-foreground">Bud rows examined</div></div>
                  <div><div className="text-lg font-semibold">{job.skippedCount}</div><div className="text-xs text-muted-foreground">Existing before trial / non-actionable</div></div>
                  <div><div className="text-lg font-semibold">{status?.matchedLearnerCount ?? "—"}</div><div className="text-xs text-muted-foreground">Matched learners (source-wide)</div></div>
                  <div><div className="text-lg font-semibold">{status?.unmatchedLearnerCount ?? "—"}</div><div className="text-xs text-muted-foreground">Unmatched Bud rows (source-wide)</div></div>
                  <div><div className="text-lg font-semibold">{job.appliedCount}</div><div className="text-xs text-muted-foreground">Applied (last commit)</div></div>
                  <div><div className="text-lg font-semibold">{job.errorCount}</div><div className="text-xs text-muted-foreground">Errors</div></div>
                  <div className="col-span-2"><div className="text-lg font-semibold font-mono truncate">{job.correlationId ?? "—"}</div><div className="text-xs text-muted-foreground">Correlation ID</div></div>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>

          {activeTab !== "sync-history" && (
            <div className="flex justify-end mt-6">
              <Button
                variant="destructive"
                disabled={approvedItems.length === 0}
                onClick={() => setCommitDialogOpen(true)}
              >
                Commit {approvedItems.length} Approved Change{approvedItems.length === 1 ? "" : "s"}
              </Button>
            </div>
          )}
        </>
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
            <DialogTitle>Review {itemDisplayName(reviewItem)}</DialogTitle>
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
                  {reviewItem.actionType === "change_status" && statusChangeOf(reviewItem) && (
                    <div className="text-sm space-y-1 bg-muted/40 rounded-md p-3">
                      <div>Previous accepted Bud status: <span className="font-medium">{statusChangeOf(reviewItem)!.previousAcceptedStatusDesc ?? "(none)"}</span></div>
                      <div>Current Attendance status: <span className="font-medium">{statusChangeOf(reviewItem)!.currentLearnerStatus}</span></div>
                      <div>New Bud status: <span className="font-medium">{statusChangeOf(reviewItem)!.currentStatusDesc}</span></div>
                      <div>Proposed Attendance action: <span className="font-medium">{statusChangeOf(reviewItem)!.targetStatus ? `Set status to ${statusChangeOf(reviewItem)!.targetStatus}` : "None (informational only)"}</span></div>
                    </div>
                  )}
                  <pre className="text-xs bg-muted/40 rounded-md p-3 overflow-auto">{JSON.stringify(reviewItem.proposedValues, null, 2)}</pre>
                  {missingFieldPaths(reviewItem).map((path) => (
                    <div key={path} className="space-y-1">
                      <Label htmlFor={`field-${path}`}>{path}</Label>
                      <Input
                        id={`field-${path}`}
                        type={path.startsWith("statusChange.") ? "date" : "text"}
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

      {/* Mark as already represented dialog */}
      <Dialog open={!!linkItem} onOpenChange={(o) => { if (!o) { setLinkItem(null); setLinkSearch(""); } }}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>Mark {itemDisplayName(linkItem)} as already represented</DialogTitle>
            <DialogDescription>Search for the existing Attendance learner this Bud row actually is.</DialogDescription>
          </DialogHeader>
          <div className="py-2 space-y-2">
            <Label htmlFor="link-existing-search">Search learners</Label>
            <Input id="link-existing-search" value={linkSearch} onChange={(e) => setLinkSearch(e.target.value)} placeholder="Name or reference" />
            <div className="max-h-60 overflow-auto divide-y">
              {(linkCandidates?.items ?? []).map((l) => (
                <button
                  key={l.id}
                  type="button"
                  className="w-full text-left py-2 px-1 hover:bg-muted/50 text-sm flex items-center justify-between"
                  onClick={() => submitLink(l.id)}
                  disabled={linkExistingMutation.isPending}
                >
                  <span>{l.firstName} {l.lastName}</span>
                  <span className="text-xs text-muted-foreground font-mono">{l.learnerRef}</span>
                </button>
              ))}
              {linkSearch.trim().length > 1 && (linkCandidates?.items.length ?? 0) === 0 && (
                <p className="text-xs text-muted-foreground py-2">No matching learners.</p>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setLinkItem(null); setLinkSearch(""); }}>Cancel</Button>
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
