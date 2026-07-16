import * as React from "react";
import {
  useListUnallocatedLearners,
  useGetAllocationByTutor,
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
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { Loader2, ArrowRightLeft, UserPlus, Calendar, Clock3, X, Info } from "lucide-react";
import { format } from "date-fns";

export default function AllocationPage() {
  const { toast } = useToast();
  
  const { data: unallocated = [], isLoading: loadUnalloc, refetch: refetchUnallocated } = useListUnallocatedLearners();
  const { data: allocations = [], isLoading: loadAlloc, refetch: refetchAllocations } = useGetAllocationByTutor();
  const { data: tutors = [] } = useListTutors({ active: true });
  const { data: cohorts = [] } = useListCohorts({ active: true });
  const { data: scheduledTransfers = [], isLoading: loadScheduled, refetch: refetchScheduled } = useListScheduledAllocations();

  const allocateMutation = useAllocateLearners();
  const cancelScheduledMutation = useCancelScheduledAllocation();

  const [selectedLearnerIds, setSelectedLearnerIds] = React.useState<number[]>([]);
  
  // Form State
  const [targetTutorId, setTargetTutorId] = React.useState<string>("");
  const [targetCohortId, setTargetCohortId] = React.useState<string>("");
  const [effectiveDate, setEffectiveDate] = React.useState(format(new Date(), "yyyy-MM-dd"));
  const [transferReason, setTransferReason] = React.useState("");

  const handleToggleSelect = (id: number) => {
    setSelectedLearnerIds(prev => 
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    );
  };

  const handleSelectAllGroup = (ids: number[], checked: boolean) => {
    if (checked) {
      const newSelection = new Set([...selectedLearnerIds, ...ids]);
      setSelectedLearnerIds(Array.from(newSelection));
    } else {
      setSelectedLearnerIds(selectedLearnerIds.filter(id => !ids.includes(id)));
    }
  };

  const handleAllocate = () => {
    if (selectedLearnerIds.length === 0) {
      toast({ title: "No learners selected", variant: "destructive" });
      return;
    }
    
    allocateMutation.mutate({
      data: {
        learnerIds: selectedLearnerIds,
        tutorId: targetTutorId ? Number(targetTutorId) : undefined,
        cohortId: targetCohortId ? Number(targetCohortId) : undefined,
        effectiveDate,
        transferReason: transferReason || undefined
      }
    }, {
      onSuccess: (res) => {
        if (res.scheduled > 0) {
          toast({ title: "Transfer scheduled", description: `${res.scheduled} learner(s) will move on ${effectiveDate}.` });
        } else {
          toast({ title: "Allocation Complete", description: `Updated ${res.updated} learners.` });
        }
        setSelectedLearnerIds([]);
        setTransferReason("");
        refetchUnallocated();
        refetchAllocations();
        refetchScheduled();
      },
      onError: (err) => {
        const status = (err as { status?: number } | undefined)?.status;
        if (status === 409) {
          toast({ title: "Learner(s) already have a pending transfer", description: "Cancel the existing scheduled transfer first.", variant: "destructive" });
        } else {
          toast({ title: "Allocation Failed", description: getErrorMessage(err), variant: "destructive" });
        }
      }
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

  const isSaving = allocateMutation.isPending;
  const today = format(new Date(), "yyyy-MM-dd");
  const isFutureDated = effectiveDate > today;

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full flex flex-col h-[calc(100vh-64px)]">
      <div className="shrink-0">
        <Breadcrumbs items={[{ label: "Allocation" }]} />
        <div className="mb-6 page-transition-enter">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Learner Allocation</h1>
          <p className="text-muted-foreground mt-1">Assign learners to tutors and cohorts in bulk.</p>
        </div>
      </div>

      <div className="flex-1 grid grid-cols-1 xl:grid-cols-12 gap-6 min-h-0">

        {/* Left Col: Lists */}
        <div className="xl:col-span-8 flex flex-col gap-6 min-h-0 page-transition-enter stagger-1">
          {!loadScheduled && scheduledTransfers.length > 0 && (
            <Card className="shrink-0 shadow-sm border-amber-200 dark:border-amber-800">
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

          <Card className="flex flex-col flex-1 shadow-sm min-h-0">
            <CardHeader className="bg-muted/10 border-b py-3 shrink-0">
              <CardTitle className="text-base flex items-center justify-between">
                <span>Unallocated Learners</span>
                <span className="bg-primary/10 text-primary text-xs px-2 py-1 rounded-full">{unallocated.length}</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0 overflow-auto flex-1">
              {loadUnalloc ? (
                <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
              ) : unallocated.length === 0 ? (
                <div className="p-8 text-center text-sm text-muted-foreground">All learners are allocated!</div>
              ) : (
                <div className="divide-y">
                  <div className="p-3 bg-muted/5 flex items-center border-b sticky top-0 z-10 backdrop-blur-md">
                    <Checkbox 
                      checked={unallocated.length > 0 && selectedLearnerIds.filter(id => unallocated.map(l => l.id).includes(id)).length === unallocated.length}
                      onCheckedChange={(c) => handleSelectAllGroup(unallocated.map(l => l.id), !!c)}
                      className="mr-3"
                    />
                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Select All Unallocated</span>
                  </div>
                  {unallocated.map(l => (
                    <div key={l.id} className={`p-3 flex items-center hover:bg-muted/30 transition-colors ${selectedLearnerIds.includes(l.id) ? 'bg-primary/5' : ''}`}>
                      <Checkbox 
                        checked={selectedLearnerIds.includes(l.id)} 
                        onCheckedChange={() => handleToggleSelect(l.id)}
                        className="mr-3"
                      />
                      <div className="flex-1 min-w-0">
                        <p className="font-medium text-sm text-foreground truncate">{l.firstName} {l.lastName}</p>
                        <p className="text-xs text-muted-foreground truncate">{l.programme}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="flex flex-col flex-1 shadow-sm min-h-0">
            <CardHeader className="bg-muted/10 border-b py-3 shrink-0">
              <CardTitle className="text-base">Current Allocations</CardTitle>
            </CardHeader>
            <CardContent className="p-0 overflow-auto flex-1 bg-muted/5">
               {loadAlloc ? (
                <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
              ) : allocations.length === 0 ? (
                <div className="p-8 text-center text-sm text-muted-foreground bg-card">No allocations exist.</div>
              ) : (
                <div className="p-4 space-y-6">
                  {allocations.map(tutorGroup => (
                    <div key={tutorGroup.tutorId} className="space-y-3">
                      <h3 className="font-bold text-sm flex items-center gap-2 text-foreground">
                        <UserPlus className="w-4 h-4 text-primary" /> {tutorGroup.tutorName}
                      </h3>
                      <div className="pl-6 space-y-3 border-l-2 border-muted">
                        {tutorGroup.cohorts.map(cohortGroup => {
                          const cohortLearnerIds = cohortGroup.learners.map(l => l.id);
                          const allSelected = cohortLearnerIds.length > 0 && cohortLearnerIds.every(id => selectedLearnerIds.includes(id));
                          
                          return (
                            <div key={cohortGroup.cohortId} className="bg-card border rounded-md shadow-sm overflow-hidden">
                              <div className="bg-muted/30 px-3 py-2 border-b flex items-center justify-between">
                                <div className="flex items-center gap-2 font-medium text-sm">
                                  <Checkbox checked={allSelected} onCheckedChange={(c) => handleSelectAllGroup(cohortLearnerIds, !!c)} />
                                  {cohortGroup.cohortName}
                                </div>
                                <span className="text-xs text-muted-foreground font-mono">{cohortGroup.learners.length}</span>
                              </div>
                              <div className="divide-y">
                                {cohortGroup.learners.map(l => (
                                  <div key={l.id} className={`px-3 py-2 flex items-center text-sm hover:bg-muted/10 transition-colors ${selectedLearnerIds.includes(l.id) ? 'bg-primary/5' : ''}`}>
                                    <Checkbox checked={selectedLearnerIds.includes(l.id)} onCheckedChange={() => handleToggleSelect(l.id)} className="mr-3" />
                                    <span>{l.firstName} {l.lastName}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right Col: Action Form */}
        <div className="xl:col-span-4 shrink-0 page-transition-enter stagger-2">
          <Card className="sticky top-6 shadow-md border-primary/20">
            <CardHeader className="bg-primary/5 border-b border-primary/10">
              <CardTitle className="text-lg flex items-center gap-2 text-primary">
                <ArrowRightLeft className="w-5 h-5" /> Execute Transfer
              </CardTitle>
              <CardDescription>
                Selected: <strong className="text-foreground">{selectedLearnerIds.length}</strong> learners
              </CardDescription>
            </CardHeader>
            <CardContent className="pt-6 space-y-5">
              <div className="space-y-2">
                <Label>Target Tutor</Label>
                <Combobox
                  options={[
                    { value: "", label: "Leave unchanged" },
                    ...tutors.map(t => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` })),
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
                  options={[
                    { value: "", label: "Leave unchanged" },
                    ...cohorts.map(c => ({ value: String(c.id), label: c.name })),
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
                <Input placeholder="e.g. Cohort consolidation" value={transferReason} onChange={e => setTransferReason(e.target.value)} />
              </div>

              <div className="pt-4 border-t">
                <Button 
                  className="w-full hover-elevate shadow-sm" 
                  size="lg"
                  disabled={selectedLearnerIds.length === 0 || (!targetTutorId && !targetCohortId) || isSaving}
                  onClick={handleAllocate}
                >
                  {isSaving ? <Loader2 className="w-5 h-5 mr-2 animate-spin" /> : isFutureDated ? "Schedule Transfer" : "Apply Allocation"}
                </Button>
                {selectedLearnerIds.length === 0 && (
                  <p className="text-xs text-center text-muted-foreground mt-2">Select learners from the lists to enable.</p>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

      </div>
    </div>
  );
}
