import * as React from "react";
import {
  useListLearners,
  useListTutors,
  useListCohorts,
  useAllocateLearners,
  useListScheduledAllocations,
  useCancelScheduledAllocation,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { useDebounce } from "@/hooks/use-debounce";
import { getErrorMessage } from "@/lib/errors";
import { Loader2, ArrowRightLeft, Search, Calendar, Clock3, X, Info, ChevronLeft, ChevronRight, GraduationCap, User } from "lucide-react";
import { format } from "date-fns";

const allValue = "__all__";
const unallocatedValue = "__unallocated__";

export default function AllocationPage() {
  const { toast } = useToast();

  const [searchQuery, setSearchQuery] = React.useState("");
  const debouncedSearch = useDebounce(searchQuery, 300);
  const [tutorFilter, setTutorFilter] = React.useState(allValue);
  const [cohortFilter, setCohortFilter] = React.useState(allValue);
  const [page, setPage] = React.useState(1);
  const pageSize = 20;

  const [selectedIds, setSelectedIds] = React.useState<Set<number>>(new Set());

  const [targetTutorId, setTargetTutorId] = React.useState("");
  const [targetCohortId, setTargetCohortId] = React.useState("");
  const [effectiveDate, setEffectiveDate] = React.useState(format(new Date(), "yyyy-MM-dd"));
  const [transferReason, setTransferReason] = React.useState("");

  const { data: tutors = [] } = useListTutors({ active: true });
  const { data: cohorts = [] } = useListCohorts({ active: true });

  const { data: learnersData, isLoading, refetch: refetchLearners } = useListLearners({
    search: debouncedSearch || undefined,
    unallocated: tutorFilter === unallocatedValue ? true : undefined,
    tutorId: tutorFilter !== allValue && tutorFilter !== unallocatedValue ? Number(tutorFilter) : undefined,
    cohortId: cohortFilter !== allValue ? Number(cohortFilter) : undefined,
    page,
    pageSize,
  });

  const { data: scheduledTransfers = [], isLoading: loadScheduled, refetch: refetchScheduled } = useListScheduledAllocations();

  const allocateMutation = useAllocateLearners();
  const cancelScheduledMutation = useCancelScheduledAllocation();

  const resetPage = () => setPage(1);

  const items = learnersData?.items ?? [];
  const allOnPageSelected = items.length > 0 && items.every((l) => selectedIds.has(l.id));

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleSelectAllOnPage = (checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      items.forEach((l) => { checked ? next.add(l.id) : next.delete(l.id); });
      return next;
    });
  };

  const clearSelection = () => setSelectedIds(new Set());

  const isSaving = allocateMutation.isPending;
  const today = format(new Date(), "yyyy-MM-dd");
  const isFutureDated = effectiveDate > today;
  const hasSelection = selectedIds.size > 0;
  const hasTarget = !!targetTutorId || !!targetCohortId;

  const handleAllocate = () => {
    allocateMutation.mutate({
      data: {
        learnerIds: Array.from(selectedIds),
        tutorId: targetTutorId ? Number(targetTutorId) : undefined,
        cohortId: targetCohortId ? Number(targetCohortId) : undefined,
        effectiveDate,
        transferReason: transferReason || undefined,
      },
    }, {
      onSuccess: (res) => {
        if (res.scheduled > 0) {
          toast({ title: "Transfer scheduled", description: `${res.scheduled} learner(s) will move on ${effectiveDate}.` });
        } else {
          toast({ title: "Allocation Complete", description: `Updated ${res.updated} learners.` });
        }
        clearSelection();
        setTransferReason("");
        refetchLearners();
        refetchScheduled();
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        if (status === 409) {
          toast({ title: "Learner(s) already have a pending transfer", description: "Cancel the existing scheduled transfer first.", variant: "destructive" });
        } else {
          toast({ title: "Allocation Failed", description: getErrorMessage(err), variant: "destructive" });
        }
      },
    });
  };

  const handleCancelScheduled = (id: number) => {
    cancelScheduledMutation.mutate({ id }, {
      onSuccess: () => {
        toast({ title: "Scheduled transfer cancelled" });
        refetchScheduled();
      },
      onError: (err) => toast({ title: "Cancel failed", description: getErrorMessage(err), variant: "destructive" }),
    });
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Allocation" }]} />
      <div className="mb-6 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground">Learner Allocation</h1>
        <p className="text-muted-foreground mt-1">Search, filter, and assign learners to tutors and cohorts in bulk.</p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-8 flex flex-col gap-6 page-transition-enter stagger-1">
          {!loadScheduled && scheduledTransfers.length > 0 && (
            <Card className="shadow-sm border-amber-200 dark:border-amber-800">
              <CardHeader className="bg-amber-50 dark:bg-amber-900/20 border-b py-3">
                <CardTitle className="text-base flex items-center gap-2 text-amber-800 dark:text-amber-500">
                  <Clock3 className="w-4 h-4" /> Pending Transfers
                  <span className="bg-amber-200/60 dark:bg-amber-800/40 text-xs px-2 py-0.5 rounded-full ml-auto">{scheduledTransfers.length}</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0 divide-y max-h-48 overflow-auto">
                {scheduledTransfers.map((s) => (
                  <div key={s.id} className="px-4 py-2.5 flex items-center justify-between text-sm">
                    <div>
                      <p className="font-medium text-foreground">{s.learnerName}</p>
                      <p className="text-xs text-muted-foreground">
                        {s.newCohortName && `→ ${s.newCohortName}`}{s.newTutorName && ` (${s.newTutorName})`} on {format(new Date(s.effectiveDate), "d MMM yyyy")}
                      </p>
                    </div>
                    <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive" onClick={() => handleCancelScheduled(s.id)} title="Cancel scheduled transfer">
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          <Card className="shadow-sm">
            <CardContent className="p-4 space-y-3">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  placeholder="Search learners by name, ref, employer..."
                  value={searchQuery}
                  onChange={(e) => { setSearchQuery(e.target.value); resetPage(); }}
                  className="pl-9 h-10 bg-background"
                />
              </div>
              <div className="flex flex-wrap gap-3">
                <Combobox
                  className="w-48 h-10 bg-background"
                  aria-label="Filter by tutor"
                  options={[
                    { value: allValue, label: "All tutors" },
                    { value: unallocatedValue, label: "Unallocated" },
                    ...tutors.map((t) => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` })),
                  ]}
                  value={tutorFilter}
                  onValueChange={(v) => { setTutorFilter(v); resetPage(); }}
                  placeholder="Tutor"
                  searchPlaceholder="Search tutors..."
                />
                <Combobox
                  className="w-48 h-10 bg-background"
                  aria-label="Filter by cohort"
                  options={[
                    { value: allValue, label: "All cohorts" },
                    ...cohorts.map((c) => ({ value: String(c.id), label: c.name })),
                  ]}
                  value={cohortFilter}
                  onValueChange={(v) => { setCohortFilter(v); resetPage(); }}
                  placeholder="Cohort"
                  searchPlaceholder="Search cohorts..."
                />
              </div>
            </CardContent>
          </Card>

          <div className="bg-card rounded-lg border shadow-sm overflow-hidden">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader className="bg-muted/30">
                  <TableRow>
                    <TableHead className="w-10">
                      <Checkbox
                        checked={allOnPageSelected}
                        onCheckedChange={(c) => toggleSelectAllOnPage(!!c)}
                        aria-label="Select all learners on this page"
                        disabled={items.length === 0}
                      />
                    </TableHead>
                    <TableHead>Learner</TableHead>
                    <TableHead>Programme</TableHead>
                    <TableHead>Tutor / Cohort</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {isLoading ? (
                    <TableRow>
                      <TableCell colSpan={4} className="h-32 text-center">
                        <div className="flex justify-center"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
                      </TableCell>
                    </TableRow>
                  ) : items.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={4} className="h-32 text-center text-muted-foreground">
                        No learners found matching your criteria.
                      </TableCell>
                    </TableRow>
                  ) : (
                    items.map((learner) => (
                      <TableRow key={learner.id} className={`hover:bg-muted/20 transition-colors ${selectedIds.has(learner.id) ? "bg-primary/5" : ""}`}>
                        <TableCell>
                          <Checkbox
                            checked={selectedIds.has(learner.id)}
                            onCheckedChange={() => toggleSelect(learner.id)}
                            aria-label={`Select ${learner.firstName} ${learner.lastName}`}
                          />
                        </TableCell>
                        <TableCell>
                          <div className="font-medium text-sm text-foreground">{learner.firstName} {learner.lastName}</div>
                          <div className="text-xs text-muted-foreground font-mono mt-0.5">{learner.learnerRef}</div>
                        </TableCell>
                        <TableCell>
                          <div className="text-sm font-medium">{learner.programme}</div>
                          <div className="text-xs text-muted-foreground">Level {learner.level}</div>
                        </TableCell>
                        <TableCell>
                          <div className="text-sm flex items-center gap-1.5">
                            <User className="w-3.5 h-3.5 text-muted-foreground" />
                            <span className="truncate max-w-[140px]">{learner.tutorName || <span className="text-muted-foreground italic text-xs">Unallocated</span>}</span>
                          </div>
                          <div className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1.5">
                            <GraduationCap className="w-3.5 h-3.5" />
                            <span className="truncate max-w-[140px]">{learner.cohortName || <span className="italic">Unallocated</span>}</span>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>

            {learnersData && learnersData.total > 0 && (
              <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/10">
                <div className="text-sm text-muted-foreground">
                  Showing <span className="font-medium text-foreground">{((page - 1) * pageSize) + 1}</span> to <span className="font-medium text-foreground">{Math.min(page * pageSize, learnersData.total)}</span> of <span className="font-medium text-foreground">{learnersData.total}</span> learners
                </div>
                <div className="flex items-center space-x-2">
                  <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>
                    <ChevronLeft className="w-4 h-4" />
                  </Button>
                  <div className="text-sm font-medium px-2">{page}</div>
                  <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * pageSize >= learnersData.total}>
                    <ChevronRight className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="xl:col-span-4 page-transition-enter stagger-2">
          <Card className="sticky top-6 shadow-md border-primary/20">
            <CardHeader className="bg-primary/5 border-b border-primary/10">
              <CardTitle className="text-lg flex items-center gap-2 text-primary">
                <ArrowRightLeft className="w-5 h-5" /> Execute Transfer
              </CardTitle>
              <CardDescription className="flex items-center justify-between">
                <span>Selected: <strong className="text-foreground">{selectedIds.size}</strong> learner{selectedIds.size === 1 ? "" : "s"}</span>
                {hasSelection && (
                  <Button variant="ghost" size="sm" className="h-auto px-2 py-1 text-xs" onClick={clearSelection}>Clear</Button>
                )}
              </CardDescription>
            </CardHeader>
            <CardContent className="pt-6 space-y-5">
              <div className="space-y-2">
                <Label>Target Tutor</Label>
                <Combobox
                  aria-label="Target Tutor"
                  options={[
                    { value: "", label: "Leave unchanged" },
                    ...tutors.map((t) => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` })),
                  ]}
                  value={targetTutorId}
                  onValueChange={setTargetTutorId}
                  placeholder="Leave unchanged"
                  searchPlaceholder="Search tutors..."
                />
              </div>

              <div className="space-y-2">
                <Label>Target Cohort</Label>
                <Combobox
                  aria-label="Target Cohort"
                  options={[
                    { value: "", label: "Leave unchanged" },
                    ...cohorts.map((c) => ({ value: String(c.id), label: c.name })),
                  ]}
                  value={targetCohortId}
                  onValueChange={setTargetCohortId}
                  placeholder="Leave unchanged"
                  searchPlaceholder="Search cohorts..."
                />
              </div>

              <div className="space-y-2">
                <Label>Effective Date</Label>
                <div className="relative">
                  <Calendar className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input type="date" className="pl-9" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} />
                </div>
                {isFutureDated && (
                  <p className="text-xs text-amber-700 dark:text-amber-500 flex items-start gap-1.5 pt-1">
                    <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                    This will be scheduled and applied automatically on {effectiveDate}, not immediately. You can cancel it before then from Pending Transfers.
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label>Transfer Reason (Optional)</Label>
                <Input placeholder="e.g. Cohort consolidation" value={transferReason} onChange={(e) => setTransferReason(e.target.value)} />
              </div>

              <div className="pt-4 border-t">
                <Button
                  className="w-full hover-elevate shadow-sm"
                  size="lg"
                  disabled={!hasSelection || !hasTarget || isSaving}
                  onClick={handleAllocate}
                >
                  {isSaving ? <Loader2 className="w-5 h-5 mr-2 animate-spin" /> : isFutureDated ? "Schedule Transfer" : "Apply Allocation"}
                </Button>
                {!hasSelection ? (
                  <p className="text-xs text-center text-muted-foreground mt-2">Select at least one learner.</p>
                ) : !hasTarget ? (
                  <p className="text-xs text-center text-muted-foreground mt-2">Choose a new tutor or cohort.</p>
                ) : null}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
