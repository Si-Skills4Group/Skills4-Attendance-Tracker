import * as React from "react";
import {
  useListCohorts,
  useListCohortSummary,
  useListTutors,
  useGetCurrentUser,
  useActivateCohort,
  useDeactivateCohort,
  getListCohortSummaryQueryKey,
} from "@workspace/api-client-react";
import { Link, useSearchParams } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Combobox } from "@/components/ui/combobox";
import { Search, Plus, BookOpen, CalendarDays, User, ArrowRight, Users, ClipboardList } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";

const allValue = "__all__";

export default function CohortsPage() {
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === 'admin';
  const { toast } = useToast();

  // Filter state lives in the URL, not local useState, so the browser's
  // native back navigation from /cohorts/:id/sessions returns to this exact
  // filtered/scrolled view instead of resetting to defaults.
  const [searchParams, setSearchParams] = useSearchParams();
  const searchQuery = searchParams.get("q") ?? "";
  const showActiveOnly = searchParams.get("active") !== "all";
  const tutorFilter = searchParams.get("tutor") ?? allValue;
  const programmeFilter = searchParams.get("programme") ?? allValue;
  const levelFilter = searchParams.get("level") ?? allValue;

  const setParam = (key: string, value: string, isDefault: boolean) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (isDefault) next.delete(key);
      else next.set(key, value);
      return next;
    });
  };

  const { data: allCohorts = [] } = useListCohorts({});
  const { data: tutors = [] } = useListTutors({ active: true });

  const summaryParams = {
    active: showActiveOnly ? true : undefined,
    tutorId: tutorFilter !== allValue ? Number(tutorFilter) : undefined,
    programme: programmeFilter !== allValue ? programmeFilter : undefined,
    level: levelFilter !== allValue ? levelFilter : undefined,
  };
  const { data: cohorts = [], isLoading, refetch } = useListCohortSummary(summaryParams, {
    query: { queryKey: getListCohortSummaryQueryKey(summaryParams) },
  });

  const activateCohort = useActivateCohort();
  const deactivateCohort = useDeactivateCohort();

  const programmes = React.useMemo(
    () => Array.from(new Set(allCohorts.map((c) => c.programme))).sort(),
    [allCohorts],
  );
  const levels = React.useMemo(
    () => Array.from(new Set(allCohorts.map((c) => c.level))).sort(),
    [allCohorts],
  );

  const filteredCohorts = React.useMemo(() => {
    if (!searchQuery) return cohorts;
    const lowerQuery = searchQuery.toLowerCase();
    return cohorts.filter(c =>
      c.name.toLowerCase().includes(lowerQuery) ||
      c.programme.toLowerCase().includes(lowerQuery) ||
      (c.tutorName && c.tutorName.toLowerCase().includes(lowerQuery))
    );
  }, [cohorts, searchQuery]);

  const handleToggleActive = (cohortId: number, name: string, newActive: boolean) => {
    const mutation = newActive ? activateCohort : deactivateCohort;
    mutation.mutate({ id: cohortId }, {
      onSuccess: () => {
        toast({ title: newActive ? "Cohort activated" : "Cohort deactivated", description: name });
        refetch();
      },
      onError: (err) => toast({ title: "Update failed", description: getErrorMessage(err), variant: "destructive" }),
    });
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Cohorts" }]} />

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Cohorts</h1>
          <p className="text-muted-foreground mt-1">Manage delivery groups and schedules.</p>
        </div>
        {isAdmin && (
          <Link href="/cohorts/new">
            <Button className="hover-elevate shadow-sm">
              <Plus className="w-4 h-4 mr-2" /> Create Cohort
            </Button>
          </Link>
        )}
      </div>

      <div className="flex flex-col gap-4 mb-6 page-transition-enter stagger-1">
        <div className="relative w-full sm:w-96">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search cohorts..."
            value={searchQuery}
            onChange={(e) => setParam("q", e.target.value, e.target.value === "")}
            className="pl-9 h-10 bg-card"
          />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Combobox
            className="w-44"
            options={[
              { value: allValue, label: "All tutors" },
              ...tutors.map((t) => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` })),
            ]}
            value={tutorFilter}
            onValueChange={(v) => setParam("tutor", v, v === allValue)}
            placeholder="Tutor"
            searchPlaceholder="Search tutors..."
          />
          <Select value={programmeFilter} onValueChange={(v) => setParam("programme", v, v === allValue)}>
            <SelectTrigger className="w-44"><SelectValue placeholder="Programme" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={allValue}>All programmes</SelectItem>
              {programmes.map((p) => (
                <SelectItem key={p} value={p}>{p}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={levelFilter} onValueChange={(v) => setParam("level", v, v === allValue)}>
            <SelectTrigger className="w-36"><SelectValue placeholder="Level" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={allValue}>All levels</SelectItem>
              {levels.map((l) => (
                <SelectItem key={l} value={l}>{l}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="flex items-center space-x-2 shrink-0 ml-auto">
            <Switch
              id="active-only"
              checked={showActiveOnly}
              onCheckedChange={(checked) => setParam("active", checked ? "" : "all", checked)}
            />
            <Label htmlFor="active-only" className="cursor-pointer">Active Only</Label>
          </div>
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-20">
          <div className="w-8 h-8 rounded-full border-4 border-primary border-t-transparent animate-spin"></div>
        </div>
      ) : filteredCohorts.length === 0 ? (
        <Card className="border-dashed bg-muted/10 page-transition-enter stagger-2">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <BookOpen className="w-12 h-12 text-muted-foreground/30 mb-4" />
            <h3 className="text-lg font-semibold text-foreground mb-1">No cohorts found</h3>
            <p className="text-sm text-muted-foreground max-w-sm">
              {searchQuery ? "No cohorts match your search criteria." : "No delivery cohorts exist yet."}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 page-transition-enter stagger-2">
          {filteredCohorts.map((cohort) => (
            <Card key={cohort.id} className={`h-full overflow-hidden transition-all hover:border-primary/50 hover:shadow-md group ${!cohort.active ? 'opacity-70 bg-muted/20' : ''}`}>
              <div className="p-5 flex flex-col h-full">
                <div className="flex justify-between items-start mb-3">
                  <Link href={`/cohorts/${cohort.id}`}>
                    <h3 className="font-bold text-lg text-foreground group-hover:text-primary transition-colors leading-tight cursor-pointer">{cohort.name}</h3>
                  </Link>
                  {isAdmin ? (
                    <Switch
                      checked={cohort.active}
                      onCheckedChange={(v) => handleToggleActive(cohort.id, cohort.name, v)}
                      aria-label="Toggle active status"
                    />
                  ) : (
                    !cohort.active && <span className="text-[10px] font-bold uppercase tracking-wider bg-muted text-muted-foreground px-2 py-1 rounded">Inactive</span>
                  )}
                </div>

                <div className="text-sm font-medium mb-4">{cohort.programme} <span className="text-muted-foreground font-normal">Level {cohort.level}</span></div>

                <div className="space-y-2 mt-auto text-sm text-muted-foreground">
                  <div className="flex items-center gap-2">
                    <User className="w-4 h-4 text-muted-foreground/70" />
                    <span className="truncate">{cohort.tutorName || <span className="italic text-xs">No Tutor Assigned</span>}</span>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-2 mt-4 pt-4 border-t border-muted/50 text-center">
                  <div>
                    <p className="text-lg font-bold text-foreground leading-none">{cohort.activeLearnerCount}</p>
                    <p className="text-[11px] text-muted-foreground mt-1 flex items-center justify-center gap-1"><Users className="w-3 h-3" /> Learners</p>
                  </div>
                  <div>
                    <p className="text-lg font-bold text-foreground leading-none">{cohort.upcomingSessionCount}</p>
                    <p className="text-[11px] text-muted-foreground mt-1 flex items-center justify-center gap-1"><CalendarDays className="w-3 h-3" /> Upcoming</p>
                  </div>
                  <div>
                    <p className={`text-lg font-bold leading-none ${cohort.outstandingRegisterCount > 0 ? 'text-amber-600' : 'text-foreground'}`}>{cohort.outstandingRegisterCount}</p>
                    <p className="text-[11px] text-muted-foreground mt-1 flex items-center justify-center gap-1"><ClipboardList className="w-3 h-3" /> Outstanding</p>
                  </div>
                </div>
              </div>
              <Link href={`/cohorts/${cohort.id}`}>
                <div className="bg-primary/5 border-t border-primary/10 px-5 py-3 text-xs font-medium text-primary flex items-center justify-between group-hover:bg-primary/10 transition-colors cursor-pointer">
                  Manage Cohort
                  <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                </div>
              </Link>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
