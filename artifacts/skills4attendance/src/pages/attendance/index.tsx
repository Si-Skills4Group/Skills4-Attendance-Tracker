import * as React from "react";
import {
  useListCohortSummary,
  useListTutors,
  useGetCurrentUser,
  getListCohortSummaryQueryKey,
  getListTutorsQueryKey,
} from "@workspace/api-client-react";
import { Link, useSearchParams, useSearch } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Search, CalendarDays, Clock, User, ArrowRight, Users, ClipboardList, AlertCircle } from "lucide-react";

const allValue = "__all__";

export default function AttendancePage() {
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === "admin";

  // Filter state lives in the URL, not local useState, so a cohort's
  // "Back to all cohorts" link can carry it forward and restore this exact
  // filtered view instead of resetting to defaults.
  const [searchParams, setSearchParams] = useSearchParams();
  const search = useSearch();
  const searchQuery = searchParams.get("q") ?? "";
  const showActiveOnly = searchParams.get("active") !== "all";
  const tutorFilter = searchParams.get("tutor") ?? allValue;

  const setParam = (key: string, value: string, isDefault: boolean) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (isDefault) next.delete(key);
      else next.set(key, value);
      return next;
    });
  };

  const { data: tutors = [] } = useListTutors({ active: true }, {
    query: { enabled: isAdmin, queryKey: getListTutorsQueryKey({ active: true }) },
  });

  const summaryParams = {
    active: showActiveOnly ? true : undefined,
    tutorId: isAdmin && tutorFilter !== allValue ? Number(tutorFilter) : undefined,
  };
  const { data: cohorts = [], isLoading, isError, refetch } = useListCohortSummary(summaryParams, {
    query: { queryKey: getListCohortSummaryQueryKey(summaryParams) },
  });

  const filteredCohorts = React.useMemo(() => {
    if (!searchQuery) return cohorts;
    const lowerQuery = searchQuery.toLowerCase();
    return cohorts.filter((c) =>
      c.name.toLowerCase().includes(lowerQuery) ||
      c.programme.toLowerCase().includes(lowerQuery) ||
      (c.tutorName && c.tutorName.toLowerCase().includes(lowerQuery))
    );
  }, [cohorts, searchQuery]);

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Attendance" }]} />

      <div className="mb-8 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground">Attendance</h1>
        <p className="text-muted-foreground mt-1">
          {isAdmin ? "Select a cohort to view its sessions and registers." : "Select one of your cohorts to view its sessions and registers."}
        </p>
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
          {isAdmin && (
            <Select value={tutorFilter} onValueChange={(v) => setParam("tutor", v, v === allValue)}>
              <SelectTrigger className="w-44"><SelectValue placeholder="Tutor" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={allValue}>All tutors</SelectItem>
                {tutors.map((t) => (
                  <SelectItem key={t.id} value={String(t.id)}>{t.firstName} {t.lastName}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
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
      ) : isError ? (
        <Card className="border-dashed border-destructive/40 bg-destructive/5 page-transition-enter stagger-2">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <AlertCircle className="w-10 h-10 text-destructive/60 mb-3" />
            <h3 className="text-lg font-semibold text-foreground mb-1">Couldn't load cohorts</h3>
            <p className="text-sm text-muted-foreground max-w-sm mb-4">Something went wrong fetching your cohorts. Please try again.</p>
            <button onClick={() => refetch()} className="text-sm font-medium text-primary hover:underline">Retry</button>
          </CardContent>
        </Card>
      ) : filteredCohorts.length === 0 ? (
        <Card className="border-dashed bg-muted/10 page-transition-enter stagger-2">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <CalendarDays className="w-12 h-12 text-muted-foreground/30 mb-4" />
            <h3 className="text-lg font-semibold text-foreground mb-1">No cohorts found</h3>
            <p className="text-sm text-muted-foreground max-w-sm">
              {searchQuery ? "No cohorts match your search criteria." : isAdmin ? "No delivery cohorts exist yet." : "You have no assigned cohorts yet."}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 page-transition-enter stagger-2">
          {filteredCohorts.map((cohort) => (
            <Link
              key={cohort.id}
              href={`/attendance/cohorts/${cohort.id}${search ? `?from=${encodeURIComponent(search)}` : ""}`}
            >
              <Card className={`h-full overflow-hidden transition-all hover:border-primary/50 hover:shadow-md cursor-pointer group ${!cohort.active ? "opacity-70 bg-muted/20" : ""}`}>
                <div className="p-5 flex flex-col h-full">
                  <div className="flex justify-between items-start mb-3">
                    <h3 className="font-bold text-lg text-foreground group-hover:text-primary transition-colors leading-tight">{cohort.name}</h3>
                    {!cohort.active && (
                      <span className="text-[10px] font-bold uppercase tracking-wider bg-muted text-muted-foreground px-2 py-1 rounded">Inactive</span>
                    )}
                  </div>

                  <div className="text-sm font-medium mb-4">{cohort.programme} <span className="text-muted-foreground font-normal">Level {cohort.level}</span></div>

                  <div className="space-y-2 mt-auto text-sm text-muted-foreground">
                    <div className="flex items-center gap-2">
                      <User className="w-4 h-4 text-muted-foreground/70" />
                      <span className="truncate">{cohort.tutorName || <span className="italic text-xs">No Tutor Assigned</span>}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <CalendarDays className="w-4 h-4 text-muted-foreground/70" />
                      <span className="capitalize">{cohort.deliveryDay}s</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Clock className="w-4 h-4 text-muted-foreground/70" />
                      <span>{cohort.sessionStartTime.substring(0, 5)} - {cohort.sessionEndTime.substring(0, 5)}</span>
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
                      <p className={`text-lg font-bold leading-none ${cohort.outstandingRegisterCount > 0 ? "text-amber-600" : "text-foreground"}`}>{cohort.outstandingRegisterCount}</p>
                      <p className="text-[11px] text-muted-foreground mt-1 flex items-center justify-center gap-1"><ClipboardList className="w-3 h-3" /> Outstanding</p>
                    </div>
                  </div>
                </div>
                <div className="bg-primary/5 border-t border-primary/10 px-5 py-3 text-xs font-medium text-primary flex items-center justify-between group-hover:bg-primary/10 transition-colors">
                  View Sessions & Registers
                  <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
