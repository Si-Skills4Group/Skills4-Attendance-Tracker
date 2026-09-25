import * as React from "react";
import {
  useGetCurrentUser,
  useListTutors,
  getListTutorsQueryKey,
  useGetAllocationReconciliation,
  getGetAllocationReconciliationQueryKey,
  useGetAllocationReconciliationPopulationSummary,
  getGetAllocationReconciliationPopulationSummaryQueryKey,
  useGetAllocationReconciliationMultiplePlanBreakdown,
  getGetAllocationReconciliationMultiplePlanBreakdownQueryKey,
  GetAllocationReconciliationView,
  GetAllocationReconciliationTutorSource,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Combobox } from "@/components/ui/combobox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Loader2, ClipboardList, ChevronLeft, ChevronRight, Info, ChevronDown } from "lucide-react";
import { format } from "date-fns";

function PopulationSummaryPanel() {
  const [open, setOpen] = React.useState(false);
  const { data, isLoading } = useGetAllocationReconciliationPopulationSummary({
    query: { enabled: open, queryKey: getGetAllocationReconciliationPopulationSummaryQueryKey() },
  });
  const { data: planBreakdown } = useGetAllocationReconciliationMultiplePlanBreakdown({
    query: { enabled: open, queryKey: getGetAllocationReconciliationMultiplePlanBreakdownQueryKey() },
  });

  return (
    <Card className="shadow-sm mb-6">
      <CardHeader className="pb-2 cursor-pointer" onClick={() => setOpen((o) => !o)}>
        <CardTitle className="text-base flex items-center justify-between">
          <span>Stage 1 population summary (corrected)</span>
          <ChevronDown className={`w-4 h-4 transition-transform ${open ? "rotate-180" : ""}`} />
        </CardTitle>
      </CardHeader>
      {open && (
        <CardContent className="text-sm space-y-4">
          {isLoading || !data ? (
            <div className="flex justify-center p-4"><Loader2 className="w-5 h-5 animate-spin text-primary" /></div>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                Calculated {format(new Date(data.calculatedAt), "d MMM yyyy HH:mm:ss")}. Bud source:{" "}
                {data.sourceInfo.sourceRowCount} learning-plan rows, last synced{" "}
                {data.sourceInfo.sourceMaxSyncedAt ? format(new Date(data.sourceInfo.sourceMaxSyncedAt), "d MMM yyyy HH:mm") : "unknown"}.
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="rounded border p-3">
                  <div className="text-xs text-muted-foreground">Confirmed Assigned</div>
                  <div className="text-xl font-semibold">{data.confirmedAssigned}</div>
                </div>
                <div className="rounded border p-3">
                  <div className="text-xs text-muted-foreground">Confirmed Unassigned</div>
                  <div className="text-xl font-semibold">{data.confirmedUnassigned}</div>
                </div>
                <div className="rounded border p-3">
                  <div className="text-xs text-muted-foreground">Needs Review (records / people / plans)</div>
                  <div className="text-xl font-semibold">
                    {data.needsReview.recordCount} / {data.needsReview.distinctPeopleCount} / {data.needsReview.budLearningPlanRowCount}
                  </div>
                </div>
                <div className="rounded border p-3">
                  <div className="text-xs text-muted-foreground">Broader active, no home cohort (any Bud status)</div>
                  <div className="text-xl font-semibold">{data.broaderActiveWithoutActiveHomeCohort}</div>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Of those {data.broaderActiveWithoutActiveHomeCohort}, {data.budCorroboratedActiveWithoutActiveHomeCohort}{" "}
                are referenced by Bud at all (any status, any resolution cleanliness) -- a weaker test than Confirmed
                Unassigned's requirement that Bud specifically confirms "In Progress" via a clean match.
              </p>
              <div>
                <div className="font-medium mb-1">Why the broader population isn't Confirmed Unassigned (mutually exclusive)</div>
                <Table>
                  <TableHeader><TableRow><TableHead>Reason</TableHead><TableHead className="text-right">Count</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {data.exclusionBreakdown.map((entry) => (
                      <TableRow key={entry.reason}>
                        <TableCell className="text-xs">{entry.reason === "confirmed_unassigned" ? "Counted in Confirmed Unassigned" : entry.reason}</TableCell>
                        <TableCell className="text-right">{entry.count}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {planBreakdown && (planBreakdown.linkPointsToCurrentPlan + planBreakdown.linkPointsToHistoricalPlan + planBreakdown.noExistingLink + planBreakdown.conflictingOrUnresolved > 0) && (
                <div>
                  <div className="font-medium mb-1">One current plan + historical plans: does an existing link point at the right one? (read-only)</div>
                  <p className="text-xs text-muted-foreground mb-2">
                    Never auto-resolved -- this informs a future matching-policy change only.
                  </p>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <div className="rounded border p-2"><div className="text-xs text-muted-foreground">Link → current plan</div><div className="text-lg font-semibold">{planBreakdown.linkPointsToCurrentPlan}</div></div>
                    <div className="rounded border p-2"><div className="text-xs text-muted-foreground">Link → historical plan</div><div className="text-lg font-semibold">{planBreakdown.linkPointsToHistoricalPlan}</div></div>
                    <div className="rounded border p-2"><div className="text-xs text-muted-foreground">No existing link</div><div className="text-lg font-semibold">{planBreakdown.noExistingLink}</div></div>
                    <div className="rounded border p-2"><div className="text-xs text-muted-foreground">Conflicting/unresolved</div><div className="text-lg font-semibold">{planBreakdown.conflictingOrUnresolved}</div></div>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      )}
    </Card>
  );
}

const ALL = "__all__";
const UNKNOWN = "Unknown";

const REVIEW_REASON_LABELS: Record<string, string> = {
  no_home_cohort: "No home cohort set",
  cohort_not_found: "Referenced cohort does not exist",
  cohort_inactive: "Home cohort inactive",
  cohort_deleted: "Home cohort deleted",
  cohort_membership_type_secondary: "Home cohort is a Functional Skills (secondary) cohort",
  cohort_membership_type_invalid: "Home cohort has an unrecognised membership type",
  no_reliable_bud_match: "No reliable Bud match",
  bud_status_not_in_progress_but_internally_active: "Bud status is not In Progress",
  bud_in_progress_internal_inactive: "Bud says In Progress, but internal status is not active",
  bud_unresolved_no_internal_learner: "Bud record does not resolve to an internal learner",
  bud_unresolved_tutor_unmatched: "Bud tutor could not be matched to an internal tutor",
  bud_unresolved_missing_required_field: "Bud record is missing a field required to create the learner",
  ambiguous_multiple_plans: "Learner has multiple Bud learning plans (ambiguous)",
  matched_learner_deleted: "Matched learner has since been deleted",
  learner_already_linked_elsewhere: "Learner is already linked to a different Bud record",
  unsupported_status_transition: "Bud status change has no agreed internal mapping",
};

function reasonLabel(reason: string | null | undefined): string {
  if (!reason) return "—";
  return REVIEW_REASON_LABELS[reason] ?? reason;
}

function ClassificationBadge({ classification }: { classification: string }) {
  if (classification === "assigned") return <Badge className="bg-emerald-600 hover:bg-emerald-600">Assigned</Badge>;
  if (classification === "unassigned") return <Badge variant="outline" className="border-amber-400 text-amber-700 dark:text-amber-400">Unassigned</Badge>;
  return <Badge variant="destructive">Needs Review</Badge>;
}

export default function AllocationReconciliationPage() {
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === "admin";

  const tutorsListParams = { active: true };
  const { data: tutors = [] } = useListTutors(tutorsListParams, { query: { enabled: isAdmin, queryKey: getListTutorsQueryKey(tutorsListParams) } });

  const [view, setView] = React.useState<GetAllocationReconciliationView>("assigned");
  const [tutorSource, setTutorSource] = React.useState<GetAllocationReconciliationTutorSource | "">("");
  const [tutorValue, setTutorValue] = React.useState("");
  const [programmeSource, setProgrammeSource] = React.useState<GetAllocationReconciliationTutorSource | "">("");
  const [programmeValue, setProgrammeValue] = React.useState("");
  const [searchInput, setSearchInput] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [page, setPage] = React.useState(1);
  const pageSize = 25;

  React.useEffect(() => {
    const timeout = setTimeout(() => { setSearch(searchInput); setPage(1); }, 300);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  const queryParams = {
    view,
    tutorSource: tutorSource || undefined,
    tutorValue: tutorValue || undefined,
    programmeSource: programmeSource || undefined,
    programmeValue: programmeValue || undefined,
    search: search || undefined,
    page,
    pageSize,
  };

  const { data, isLoading, isError } = useGetAllocationReconciliation(
    queryParams,
    { query: { enabled: isAdmin, queryKey: getGetAllocationReconciliationQueryKey(queryParams) } },
  );

  const resetPage = () => setPage(1);

  if (user && !isAdmin) {
    return (
      <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
        <Breadcrumbs items={[{ label: "Allocation Reconciliation" }]} />
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Administrator access required.</CardContent></Card>
      </div>
    );
  }

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Allocation Reconciliation" }]} />

      <div className="mb-6 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <ClipboardList className="w-7 h-7 text-primary" /> Allocation Reconciliation
        </h1>
        <p className="text-muted-foreground mt-1">
          Bud "In Progress" learners reconciled against active cohort allocations. Visibility only -- this screen never changes a learner, tutor, cohort or attendance record.
        </p>
      </div>

      {data && (
        <Card className="shadow-sm mb-6 bg-muted/20">
          <CardContent className="p-4 text-sm text-muted-foreground flex flex-col gap-1">
            <div className="flex items-start gap-2">
              <Info className="w-4 h-4 mt-0.5 shrink-0" />
              <div>
                <p>
                  Bud source data: {data.sourceInfo.sourceRowCount} learning-plan rows, last synced{" "}
                  {data.sourceInfo.sourceMaxSyncedAt ? format(new Date(data.sourceInfo.sourceMaxSyncedAt), "d MMM yyyy HH:mm") : "unknown"}.
                  {" "}Most recent app-side sync job:{" "}
                  {data.sourceInfo.latestAppSyncJob
                    ? `#${data.sourceInfo.latestAppSyncJob.id} (${data.sourceInfo.latestAppSyncJob.status}, started ${format(new Date(data.sourceInfo.latestAppSyncJob.startedAt), "d MMM yyyy HH:mm")})`
                    : "none"}
                  . This report calculated at {format(new Date(data.calculatedAt), "d MMM yyyy HH:mm:ss")}.
                </p>
                <p className="mt-1">{data.sourceInfo.sourceCompletenessNote}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <PopulationSummaryPanel />

      <div className="flex flex-col sm:flex-row flex-wrap items-start sm:items-center gap-3 mb-6 page-transition-enter stagger-1">
        <Input
          className="w-56 h-9" placeholder="Search learner name or reference..."
          value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
        />
        <div className="flex items-center gap-1.5">
          <Combobox
            className="w-32"
            options={[{ value: "", label: "Tutor: any" }, { value: "internal", label: "Tutor (Internal)" }, { value: "bud", label: "Tutor (Bud)" }]}
            value={tutorSource} onValueChange={(v) => { setTutorSource(v as any); setTutorValue(""); resetPage(); }}
            placeholder="Tutor source"
          />
          {tutorSource === "internal" && (
            <Combobox
              className="w-48"
              options={[{ value: ALL, label: "All tutors" }, ...tutors.map((t) => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` }))]}
              value={tutorValue || ALL} onValueChange={(v) => { setTutorValue(v === ALL ? "" : v); resetPage(); }}
              placeholder="Tutor" searchPlaceholder="Search tutors..."
            />
          )}
          {tutorSource === "bud" && (
            <Combobox
              className="w-48"
              options={[{ value: ALL, label: "All Bud tutors" }, ...(data?.availableFilters.budTutorNames ?? []).map((n) => ({ value: n, label: n }))]}
              value={tutorValue || ALL} onValueChange={(v) => { setTutorValue(v === ALL ? "" : v); resetPage(); }}
              placeholder="Bud tutor" searchPlaceholder="Search Bud tutor names..."
            />
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <Combobox
            className="w-40"
            options={[{ value: "", label: "Programme: any" }, { value: "internal", label: "Programme (Internal)" }, { value: "bud", label: "Programme (Bud)" }]}
            value={programmeSource} onValueChange={(v) => { setProgrammeSource(v as any); setProgrammeValue(""); resetPage(); }}
            placeholder="Programme source"
          />
          {programmeSource && (
            <Combobox
              className="w-48"
              options={[{ value: ALL, label: "All programmes" }, ...(data?.availableFilters.budProgrammes ?? []).map((n) => ({ value: n, label: n }))]}
              value={programmeValue || ALL} onValueChange={(v) => { setProgrammeValue(v === ALL ? "" : v); resetPage(); }}
              placeholder="Programme" searchPlaceholder="Search programmes..."
            />
          )}
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the allocation reconciliation.</CardContent></Card>
      ) : data ? (
        <Card className="shadow-sm overflow-hidden">
          <CardHeader className="pb-0">
            <Tabs value={view} onValueChange={(v) => { setView(v as GetAllocationReconciliationView); resetPage(); }}>
              <TabsList className="grid grid-cols-3 max-w-xl">
                <TabsTrigger value="assigned">Assigned ({data.counts.confirmedAssigned})</TabsTrigger>
                <TabsTrigger value="unassigned">Unassigned ({data.counts.confirmedUnassigned})</TabsTrigger>
                <TabsTrigger value="needs_review">Needs Review ({data.counts.needsReview.recordCount})</TabsTrigger>
              </TabsList>
            </Tabs>
          </CardHeader>
          <CardContent className="pt-4">
            {/* Assigned/Unassigned are counts of distinct people. Needs
                Review is NOT a person count -- a single person with more
                than one ambiguous Bud plan produces more than one review
                row, so recordCount, distinctPeopleCount and
                budLearningPlanRowCount are shown separately rather than
                collapsed into one misleading number. */}
            <p className="text-xs text-muted-foreground mb-3">
              Assigned/Unassigned counts above are distinct learners. Needs Review is {data.counts.needsReview.recordCount} review record{data.counts.needsReview.recordCount === 1 ? "" : "s"}, covering {data.counts.needsReview.distinctPeopleCount} distinct identifiable learner{data.counts.needsReview.distinctPeopleCount === 1 ? "" : "s"} and {data.counts.needsReview.budLearningPlanRowCount} Bud learning-plan row{data.counts.needsReview.budLearningPlanRowCount === 1 ? "" : "s"} -- the same person can appear on more than one row (e.g. each of their ambiguous plans). None of these are the same as the {data.sourceInfo.sourceRowCount} raw Bud learning-plan rows shown in the source summary above.
            </p>
            {data.ambiguousPlanBreakdown.distinctLearnerReferences > 0 && (
              <Card className="mb-3 bg-muted/10">
                <CardContent className="p-3 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">Ambiguous-plan breakdown ({data.ambiguousPlanBreakdown.distinctLearnerReferences} people, {data.ambiguousPlanBreakdown.affectedBudLearningPlans} Bud plans):</span>{" "}
                  {data.ambiguousPlanBreakdown.withMultipleInProgressPlans} with more than one concurrent In Progress plan,{" "}
                  {data.ambiguousPlanBreakdown.withOneInProgressPlusHistorical} with one In Progress plan plus historical plans,{" "}
                  {data.ambiguousPlanBreakdown.withOnlyHistoricalPlans} with only historical plans (no plan currently In Progress). These are never auto-resolved; each remains visible for manual review.
                </CardContent>
              </Card>
            )}
            <div className="overflow-x-auto">
              <Table>
                <TableHeader className="bg-muted/30">
                  <TableRow>
                    <TableHead>Learner</TableHead>
                    <TableHead>Reference</TableHead>
                    <TableHead>Bud Plan ID</TableHead>
                    <TableHead>Internal Status</TableHead>
                    <TableHead>Bud Status</TableHead>
                    <TableHead>Internal Tutor</TableHead>
                    <TableHead>Bud Tutor</TableHead>
                    <TableHead>Programme</TableHead>
                    <TableHead>Home Cohort</TableHead>
                    <TableHead>Classification</TableHead>
                    <TableHead>Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.length === 0 ? (
                    <TableRow><TableCell colSpan={11} className="text-center p-8 text-muted-foreground">No records for these filters.</TableCell></TableRow>
                  ) : (
                    data.items.map((row, i) => (
                      <TableRow key={`${row.internalLearnerId ?? "none"}-${row.budLearningPlanId ?? "none"}-${i}`}>
                        <TableCell className="font-medium">{row.learnerName ?? "—"}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{row.learnerRef ?? "—"}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{row.budLearningPlanId ?? "—"}</TableCell>
                        <TableCell>{row.internalStatus ?? "—"}</TableCell>
                        <TableCell>{row.budStatus ?? "—"}</TableCell>
                        <TableCell>{row.internalTutorName ?? "—"}</TableCell>
                        <TableCell>{row.budTutorName ?? "—"}</TableCell>
                        <TableCell>{row.programme ?? "—"}</TableCell>
                        <TableCell>
                          {row.homeCohortName ?? "—"}
                          {row.homeCohortName && !row.homeCohortActive && <span className="text-xs text-amber-600 ml-1">(inactive)</span>}
                          {row.homeCohortDeleted && <span className="text-xs text-rose-600 ml-1">(deleted)</span>}
                        </TableCell>
                        <TableCell><ClassificationBadge classification={row.classification} /></TableCell>
                        <TableCell className="text-xs">
                          {reasonLabel(row.reviewReason)}
                          {row.issueFlags.length > 1 && (
                            <span className="block text-muted-foreground">+{row.issueFlags.length - 1} more flag{row.issueFlags.length - 1 === 1 ? "" : "s"}</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
            {data.total > 0 && (
              <div className="flex items-center justify-between border-t px-1 py-3 mt-2">
                <div className="text-sm text-muted-foreground">Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, data.total)} of {data.total}</div>
                <div className="flex items-center space-x-2">
                  <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}><ChevronLeft className="w-4 h-4" /></Button>
                  <div className="text-sm font-medium px-2">{page}</div>
                  <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * pageSize >= data.total}><ChevronRight className="w-4 h-4" /></Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
