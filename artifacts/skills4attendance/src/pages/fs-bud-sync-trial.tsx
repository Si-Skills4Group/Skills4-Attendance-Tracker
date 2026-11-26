import * as React from "react";
import {
  useGetFsBudSyncStatus,
  useCreateFsBudSyncPreview,
  useGetFsBudSyncJob,
  useGetFsBudSyncJobSummary,
  useListFsBudSyncJobItems,
  useBulkApproveFsBudSyncJobItems,
  useCommitFsBudSyncJob,
  getGetFsBudSyncStatusQueryKey,
  getGetFsBudSyncJobQueryKey,
  getGetFsBudSyncJobSummaryQueryKey,
  getListFsBudSyncJobItemsQueryKey,
  type FsBudSyncItem,
  type FsBudSyncMatchStatus,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { ListChecks, Loader2, Info, ChevronLeft, ChevronRight } from "lucide-react";
import { format, parseISO } from "date-fns";

const PAGE_SIZE = 25;

const TABS: { value: FsBudSyncMatchStatus; label: string }[] = [
  { value: "new", label: "New" },
  { value: "changed", label: "Changed" },
  { value: "blocked_by_clear", label: "Blocked by Clear" },
  { value: "unmatched", label: "Unmatched" },
  { value: "unchanged", label: "Unchanged" },
];

const SUBJECT_LABELS: Record<string, string> = { math: "Maths", english: "English" };

function subjectBreakdown(item: FsBudSyncItem): string {
  const bySubject: Record<string, { level: number; status: string | null }[]> = {};
  for (const row of item.sourceSubjectRows) {
    (bySubject[row.subject] ??= []).push({ level: row.level, status: row.status });
  }
  return Object.entries(bySubject)
    .map(([subject, rows]) => {
      const levels = rows
        .sort((a, b) => a.level - b.level)
        .map((r) => `L${r.level}: ${r.status ?? "—"}`)
        .join(", ");
      return `${SUBJECT_LABELS[subject] ?? subject} (${levels})`;
    })
    .join(" · ");
}

function hasUnknownStatus(item: FsBudSyncItem): boolean {
  return item.sourceSubjectRows.some((r) => r.statusCategory === "unknown");
}

function overwriteWarning(item: FsBudSyncItem): string | null {
  return item.warnings.find((w) => w.toLowerCase().includes("overwrite")) ?? null;
}

function otherWarnings(item: FsBudSyncItem): string[] {
  return item.warnings.filter((w) => !w.toLowerCase().includes("overwrite"));
}

function requirementBadge(existing: boolean | null, proposed: boolean | null, label: string) {
  if (proposed === null) return null;
  const changed = existing !== proposed;
  return (
    <Badge key={label} variant={changed ? "secondary" : "outline"} className="mr-1">
      {label}: {existing === null ? "—" : String(existing)} {changed ? `→ ${String(proposed)}` : ""}
    </Badge>
  );
}

function learnerDisplayName(item: FsBudSyncItem): string {
  return item.learnerName ?? item.learnerRef ?? item.budLearningPlanId;
}

export default function FsBudSyncTrialPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [activeJobId, setActiveJobId] = React.useState<number | null>(null);
  const [activeTab, setActiveTab] = React.useState<FsBudSyncMatchStatus>("new");
  const [pageByTab, setPageByTab] = React.useState<Record<FsBudSyncMatchStatus, number>>({
    new: 1, changed: 1, blocked_by_clear: 1, unmatched: 1, unchanged: 1,
  });
  const [selectedItemIds, setSelectedItemIds] = React.useState<Set<number>>(new Set());
  const [commitDialogOpen, setCommitDialogOpen] = React.useState(false);
  const [approvalReason, setApprovalReason] = React.useState("");

  const { data: status, isLoading: statusLoading } = useGetFsBudSyncStatus();

  const previewMutation = useCreateFsBudSyncPreview();
  const bulkApproveMutation = useBulkApproveFsBudSyncJobItems();
  const commitMutation = useCommitFsBudSyncJob();

  const { data: job } = useGetFsBudSyncJob(activeJobId ?? 0, {
    query: { enabled: activeJobId !== null, queryKey: getGetFsBudSyncJobQueryKey(activeJobId ?? 0) },
  });
  const { data: summary } = useGetFsBudSyncJobSummary(activeJobId ?? 0, {
    query: { enabled: activeJobId !== null, queryKey: getGetFsBudSyncJobSummaryQueryKey(activeJobId ?? 0) },
  });

  const paramsFor = (tab: FsBudSyncMatchStatus) => ({ matchStatus: tab, page: pageByTab[tab], pageSize: PAGE_SIZE });
  const newParams = paramsFor("new");
  const changedParams = paramsFor("changed");
  const blockedParams = paramsFor("blocked_by_clear");
  const unmatchedParams = paramsFor("unmatched");
  const unchangedParams = paramsFor("unchanged");

  const { data: newItems, isLoading: newLoading } = useListFsBudSyncJobItems(activeJobId ?? 0, newParams, {
    query: { enabled: activeJobId !== null, queryKey: getListFsBudSyncJobItemsQueryKey(activeJobId ?? 0, newParams) },
  });
  const { data: changedItems, isLoading: changedLoading } = useListFsBudSyncJobItems(activeJobId ?? 0, changedParams, {
    query: { enabled: activeJobId !== null, queryKey: getListFsBudSyncJobItemsQueryKey(activeJobId ?? 0, changedParams) },
  });
  const { data: blockedItems, isLoading: blockedLoading } = useListFsBudSyncJobItems(activeJobId ?? 0, blockedParams, {
    query: { enabled: activeJobId !== null, queryKey: getListFsBudSyncJobItemsQueryKey(activeJobId ?? 0, blockedParams) },
  });
  const { data: unmatchedItems, isLoading: unmatchedLoading } = useListFsBudSyncJobItems(activeJobId ?? 0, unmatchedParams, {
    query: { enabled: activeJobId !== null, queryKey: getListFsBudSyncJobItemsQueryKey(activeJobId ?? 0, unmatchedParams) },
  });
  const { data: unchangedItems, isLoading: unchangedLoading } = useListFsBudSyncJobItems(activeJobId ?? 0, unchangedParams, {
    query: { enabled: activeJobId !== null, queryKey: getListFsBudSyncJobItemsQueryKey(activeJobId ?? 0, unchangedParams) },
  });

  const itemsByTab: Record<FsBudSyncMatchStatus, { items: FsBudSyncItem[]; total: number } | undefined> = {
    new: newItems, changed: changedItems, blocked_by_clear: blockedItems, unmatched: unmatchedItems, unchanged: unchangedItems,
  };
  const loadingByTab: Record<FsBudSyncMatchStatus, boolean> = {
    new: newLoading, changed: changedLoading, blocked_by_clear: blockedLoading, unmatched: unmatchedLoading, unchanged: unchangedLoading,
  };

  const invalidateJob = () => {
    if (activeJobId === null) return;
    queryClient.invalidateQueries({ queryKey: getGetFsBudSyncJobQueryKey(activeJobId) });
    queryClient.invalidateQueries({ queryKey: getGetFsBudSyncJobSummaryQueryKey(activeJobId) });
    queryClient.invalidateQueries({ queryKey: getListFsBudSyncJobItemsQueryKey(activeJobId, {}) });
  };

  const handlePreview = () => {
    previewMutation.mutate(undefined, {
      onSuccess: (newJob) => {
        setActiveJobId(newJob.id);
        setActiveTab("new");
        setPageByTab({ new: 1, changed: 1, blocked_by_clear: 1, unmatched: 1, unchanged: 1 });
        setSelectedItemIds(new Set());
        toast({
          title: "Bud Functional Skills data checked",
          description: `${newJob.newCount} new, ${newJob.changedCount} changed, ${newJob.blockedByClearCount} blocked by clear, ${newJob.unmatchedCount} unmatched.`,
        });
      },
      onError: (err) => toast({ title: "Could not check Bud Functional Skills data", description: getErrorMessage(err), variant: "destructive" }),
    });
  };

  const toggleSelected = (itemId: number) => {
    setSelectedItemIds((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId); else next.add(itemId);
      return next;
    });
  };

  const toggleAllSelected = (checked: boolean, ids: number[]) => {
    setSelectedItemIds((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => { if (checked) next.add(id); else next.delete(id); });
      return next;
    });
  };

  const submitBulkApprove = () => {
    if (selectedItemIds.size === 0) return;
    const jobId = job?.id ?? approvableItems.find((i) => selectedItemIds.has(i.id))?.syncJobId;
    if (jobId === undefined) return;
    bulkApproveMutation.mutate(
      { jobId, data: { itemIds: Array.from(selectedItemIds) } },
      {
        onSuccess: (result) => {
          invalidateJob();
          setSelectedItemIds(new Set());
          if (result.errors.length === 0) {
            toast({ title: "Approved", description: `${result.approvedCount} item(s) ready to commit.` });
          } else {
            toast({
              title: `Approved ${result.approvedCount}, ${result.errors.length} need attention`,
              description: result.errors.map((e) => `#${e.itemId}: ${e.message}`).join("; "),
              variant: "destructive",
            });
          }
        },
        onError: (err) => toast({ title: "Could not approve selected items", description: getErrorMessage(err), variant: "destructive" }),
      },
    );
  };

  // approvedItems is drawn only from the currently-loaded (visible) pages of
  // the New/Changed tabs -- an item approved on a page that is no longer
  // loaded when Commit is pressed will not be included. This mirrors the
  // same accepted limitation in aptem-sync-trial.tsx/bud-sync-trial.tsx.
  const approvableItems = [...(newItems?.items ?? []), ...(changedItems?.items ?? [])];
  const approvedItems = approvableItems.filter((i) => i.approved && !i.applied);

  const runCommit = () => {
    const jobId = job?.id ?? approvedItems[0]?.syncJobId;
    if (jobId === undefined) return;
    commitMutation.mutate(
      { jobId, data: { itemIds: approvedItems.map((i) => i.id), approvalReason: approvalReason.trim() } },
      {
        onSuccess: (result) => {
          toast({ title: "Commit complete", description: `${result.appliedCount} item(s) applied.` });
          setCommitDialogOpen(false);
          setApprovalReason("");
          invalidateJob();
          queryClient.invalidateQueries({ queryKey: getGetFsBudSyncStatusQueryKey() });
        },
        onError: (err) => toast({ title: "Commit failed", description: getErrorMessage(err), variant: "destructive" }),
      },
    );
  };

  const setTabPage = (tab: FsBudSyncMatchStatus, page: number) => {
    setPageByTab((prev) => ({ ...prev, [tab]: page }));
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "FS Bud Sync Trial" }]} />

      <div className="mb-6 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <ListChecks className="w-8 h-8 text-primary" /> Functional Skills Bud Synchronisation Trial
        </h1>
      </div>

      <Card className="mb-6 border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20">
        <CardContent className="p-4 flex gap-3 items-start">
          <Info className="w-5 h-5 text-amber-700 dark:text-amber-500 shrink-0 mt-0.5" />
          <p className="text-sm text-amber-800 dark:text-amber-400">
            This proposes Maths/English requirement changes sourced from Bud's own Functional Skills aim data, as a
            second source alongside the manual CSV upload. A learner whose requirement was explicitly cleared by an
            admin is always listed under "Blocked by Clear" and never overwritten automatically -- only a manual
            re-record can supersede that. The Fabric source data itself is refreshed by a developer running the
            staging script, not from this page.
          </p>
        </CardContent>
      </Card>

      <Card className="mb-6 shadow-sm">
        <CardHeader>
          <CardTitle className="text-lg">Source status</CardTitle>
          <CardDescription>
            {statusLoading ? "Loading…" : status?.sourceMaxSyncedAt ? `Last Fabric snapshot ${format(parseISO(status.sourceMaxSyncedAt), "MMM d, yyyy HH:mm")}` : "No Bud Functional Skills data staged yet"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-4">
          <div className="text-sm text-muted-foreground space-x-4">
            <span>{status?.sourcePlanCount ?? "—"} plan(s) in the mirror table</span>
            <span>{status?.matchedPlanCount ?? "—"} matched</span>
            <span>{status?.unmatchedPlanCount ?? "—"} unmatched</span>
            {status?.lastSuccessfulSyncAt && (
              <span>Last commit {format(parseISO(status.lastSuccessfulSyncAt), "MMM d, yyyy")}</span>
            )}
          </div>
          <Button onClick={handlePreview} disabled={previewMutation.isPending} className="hover-elevate shadow-sm">
            {previewMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            Generate Preview
          </Button>
        </CardContent>
      </Card>

      {job && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <Card className="shadow-sm">
              <CardContent className="pt-6">
                <div className="text-2xl font-bold">{summary?.newCount ?? "—"}</div>
                <div className="text-xs text-muted-foreground">New requirements</div>
              </CardContent>
            </Card>
            <Card className="shadow-sm">
              <CardContent className="pt-6">
                <div className="text-2xl font-bold">{summary?.changedCount ?? "—"}</div>
                <div className="text-xs text-muted-foreground">Changed requirements</div>
              </CardContent>
            </Card>
            <Card className="shadow-sm">
              <CardContent className="pt-6">
                <div className="text-2xl font-bold">{summary?.blockedByClearCount ?? "—"}</div>
                <div className="text-xs text-muted-foreground">Blocked by an admin's clear</div>
              </CardContent>
            </Card>
            <Card className="shadow-sm">
              <CardContent className="pt-6">
                <div className="text-2xl font-bold">{summary?.unmatchedCount ?? "—"}</div>
                <div className="text-xs text-muted-foreground">Unmatched Bud plans</div>
              </CardContent>
            </Card>
          </div>

          <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as FsBudSyncMatchStatus)} className="w-full">
            <TabsList className="grid w-full grid-cols-5 max-w-3xl mb-6">
              {TABS.map((t) => <TabsTrigger key={t.value} value={t.value}>{t.label}</TabsTrigger>)}
            </TabsList>

            {TABS.map((tab) => {
              const data = itemsByTab[tab.value];
              const loading = loadingByTab[tab.value];
              const approvable = tab.value === "new" || tab.value === "changed";
              const page = pageByTab[tab.value];
              const items = data?.items ?? [];

              return (
                <TabsContent key={tab.value} value={tab.value}>
                  <Card className="shadow-sm">
                    <CardContent className="pt-6">
                      {approvable && (
                        <div className="flex items-center justify-between gap-4 mb-4 px-1">
                          <p className="text-sm text-muted-foreground">
                            Select rows and approve as a batch, then commit at the bottom of the page.
                          </p>
                          <div className="flex items-center gap-3 shrink-0">
                            <span className="text-sm text-muted-foreground">{selectedItemIds.size} selected</span>
                            <Button
                              size="sm"
                              disabled={selectedItemIds.size === 0 || bulkApproveMutation.isPending}
                              onClick={submitBulkApprove}
                            >
                              {bulkApproveMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                              Approve Selected
                            </Button>
                          </div>
                        </div>
                      )}
                      <div className="overflow-x-auto">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              {approvable && (
                                <TableHead className="w-10">
                                  <Checkbox
                                    checked={items.length > 0 && items.every((i) => selectedItemIds.has(i.id))}
                                    onCheckedChange={(c) => toggleAllSelected(!!c, items.map((i) => i.id))}
                                    aria-label={`Select all ${tab.label} items on this page`}
                                  />
                                </TableHead>
                              )}
                              <TableHead>Learner</TableHead>
                              <TableHead>Bud Plan ID</TableHead>
                              <TableHead>Source subjects</TableHead>
                              <TableHead>Requirement</TableHead>
                              <TableHead>Warnings</TableHead>
                              {approvable && <TableHead>Status</TableHead>}
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {loading ? (
                              <TableRow><TableCell colSpan={approvable ? 7 : 5} className="h-24 text-center">Loading…</TableCell></TableRow>
                            ) : items.length === 0 ? (
                              <TableRow><TableCell colSpan={approvable ? 7 : 5} className="h-24 text-center text-muted-foreground">No items in this category.</TableCell></TableRow>
                            ) : (
                              items.map((item) => {
                                const unknown = hasUnknownStatus(item);
                                const overwrite = overwriteWarning(item);
                                const rest = otherWarnings(item);
                                return (
                                  <TableRow key={item.id}>
                                    {approvable && (
                                      <TableCell>
                                        <Checkbox
                                          checked={selectedItemIds.has(item.id)}
                                          onCheckedChange={() => toggleSelected(item.id)}
                                          aria-label={`Select item ${item.id}`}
                                        />
                                      </TableCell>
                                    )}
                                    <TableCell className="text-sm">
                                      <div className="font-medium">{learnerDisplayName(item)}</div>
                                      {item.learnerRef && <div className="text-xs text-muted-foreground font-mono">{item.learnerRef}</div>}
                                    </TableCell>
                                    <TableCell className="font-mono text-xs">{item.budLearningPlanId}</TableCell>
                                    <TableCell className="text-sm">
                                      {subjectBreakdown(item)}
                                      {unknown && (
                                        <Badge variant="outline" className="ml-2 border-dashed text-muted-foreground">
                                          Unclear source data
                                        </Badge>
                                      )}
                                    </TableCell>
                                    <TableCell>
                                      {requirementBadge(item.existingMaths, item.proposedMaths, "Maths")}
                                      {requirementBadge(item.existingEnglish, item.proposedEnglish, "English")}
                                    </TableCell>
                                    <TableCell className="text-xs space-y-1">
                                      {overwrite && (
                                        <Badge variant="destructive" className="block w-fit">
                                          {overwrite}
                                        </Badge>
                                      )}
                                      {rest.map((w, i) => (
                                        <div key={i} className="text-amber-700 dark:text-amber-500">⚠ {w}</div>
                                      ))}
                                    </TableCell>
                                    {approvable && (
                                      <TableCell>
                                        <Badge variant={item.applied ? "default" : item.approved ? "secondary" : "outline"}>
                                          {item.applied ? "Applied" : item.approved ? "Approved — pending commit" : "Awaiting review"}
                                        </Badge>
                                      </TableCell>
                                    )}
                                  </TableRow>
                                );
                              })
                            )}
                          </TableBody>
                        </Table>
                      </div>
                      {data && data.total > PAGE_SIZE && (
                        <div className="flex items-center justify-between border-t px-4 py-3 -mx-6 -mb-6 mt-2 bg-muted/10">
                          <div className="text-sm text-muted-foreground">
                            Showing {((page - 1) * PAGE_SIZE) + 1} to {Math.min(page * PAGE_SIZE, data.total)} of {data.total}
                          </div>
                          <div className="flex items-center space-x-2">
                            <Button variant="outline" size="sm" aria-label={`Previous page of ${tab.label}`} onClick={() => setTabPage(tab.value, Math.max(1, page - 1))} disabled={page === 1}>
                              <ChevronLeft className="w-4 h-4" />
                            </Button>
                            <div className="text-sm font-medium px-2">{page}</div>
                            <Button variant="outline" size="sm" aria-label={`Next page of ${tab.label}`} onClick={() => setTabPage(tab.value, page + 1)} disabled={page * PAGE_SIZE >= data.total}>
                              <ChevronRight className="w-4 h-4" />
                            </Button>
                          </div>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                </TabsContent>
              );
            })}
          </Tabs>

          <div className="flex justify-end mt-6">
            <Button
              variant="destructive"
              disabled={approvedItems.length === 0}
              onClick={() => setCommitDialogOpen(true)}
            >
              Commit {approvedItems.length} Approved Change{approvedItems.length === 1 ? "" : "s"}
            </Button>
          </div>
        </>
      )}

      <Dialog open={commitDialogOpen} onOpenChange={(o) => { setCommitDialogOpen(o); if (!o) setApprovalReason(""); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Commit</DialogTitle>
            <DialogDescription>
              {approvedItems.length} learner(s)' Maths/English requirement will be updated from Bud's Functional
              Skills data.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor="commit-approval-reason">Approval reason</Label>
            <Textarea id="commit-approval-reason" value={approvalReason} onChange={(e) => setApprovalReason(e.target.value)} rows={2} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCommitDialogOpen(false)}>Go Back</Button>
            <Button
              variant="destructive"
              disabled={commitMutation.isPending || !approvalReason.trim()}
              onClick={runCommit}
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
