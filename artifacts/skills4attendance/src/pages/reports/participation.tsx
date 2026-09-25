import * as React from "react";
import {
  useGetCurrentUser,
  useListTutors,
  getListTutorsQueryKey,
  useListCohorts,
  useGetParticipationReport,
  exportParticipationReport,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ReportFilterBar, ReportFilters } from "@/components/reports/report-filter-bar";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Download, ClipboardCheck } from "lucide-react";
import { format, parseISO } from "date-fns";

export default function ParticipationReportPage() {
  const { toast } = useToast();
  const { data: user } = useGetCurrentUser();
  const isTutor = user?.role === "tutor";
  const tutorsListParams = { active: true };
  const { data: tutors = [] } = useListTutors(tutorsListParams, { query: { enabled: !isTutor, queryKey: getListTutorsQueryKey(tutorsListParams) } });
  const { data: cohorts = [] } = useListCohorts({ active: true });

  const [filters, setFilters] = React.useState<ReportFilters>({ period: "current_month" });
  const [isExporting, setIsExporting] = React.useState(false);

  const queryParams = {
    period: filters.period,
    dateFrom: filters.dateFrom,
    dateTo: filters.dateTo,
    tutorId: filters.tutorId,
    cohortId: filters.cohortId,
  };

  const { data: metrics, isLoading, isError } = useGetParticipationReport(queryParams);

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const csv = await exportParticipationReport(queryParams);
      downloadCsv(csv, "session-participation-report.csv");
    } catch (err: any) {
      toast({ title: "Export failed", description: err?.message, variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Session Participation" }]} />

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <ClipboardCheck className="w-7 h-7 text-primary" /> Session Participation Including Catch-up
          </h1>
          <p className="text-muted-foreground mt-1">
            Distinct expected learner-sessions, separate from the minutes-based attendance percentage elsewhere in
            this app. Catch-up is tutor-confirmed, never automatic evidence of Functional Skills delivery or funding
            eligibility.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting}>
          {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export CSV
        </Button>
      </div>

      <ReportFilterBar
        value={filters}
        onChange={setFilters}
        tutors={tutors}
        cohorts={cohorts}
        showTutor={!isTutor}
      />

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError || !metrics ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the participation report.</CardContent></Card>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <Card className="bg-primary/5 border-primary/10 shadow-sm">
              <CardContent className="p-4">
                <p className="text-sm font-medium text-muted-foreground">Session Participation Including Catch-up</p>
                <div className="text-3xl font-bold font-mono text-primary mt-1">
                  {metrics.participationRate != null ? `${metrics.participationRate.toFixed(1)}%` : "—"}
                </div>
                <p className="text-xs text-muted-foreground mt-1">{metrics.totalParticipation} of {metrics.expectedLearnerSessions} expected learner-sessions</p>
              </CardContent>
            </Card>
            <Card className="shadow-sm">
              <CardContent className="p-4">
                <p className="text-sm font-medium text-muted-foreground">Live Attended</p>
                <div className="text-3xl font-bold font-mono text-foreground mt-1">{metrics.liveAttendedLearnerSessions}</div>
                <p className="text-xs text-muted-foreground mt-1">Present or late</p>
              </CardContent>
            </Card>
            <Card className="shadow-sm">
              <CardContent className="p-4">
                <p className="text-sm font-medium text-muted-foreground">Recorded Absences</p>
                <div className="text-3xl font-bold font-mono mt-1">{metrics.recordedAbsences}</div>
                <p className="text-xs text-muted-foreground mt-1">{metrics.caughtUp} of these caught up (subset)</p>
              </CardContent>
            </Card>
            <Card className="shadow-sm">
              <CardContent className="p-4">
                <p className="text-sm font-medium text-muted-foreground">Attendance Not Recorded</p>
                <div className="text-3xl font-bold font-mono text-foreground mt-1">{metrics.attendanceNotRecorded}</div>
                <p className="text-xs text-muted-foreground mt-1">Not an absence -- never catch-up eligible</p>
              </CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <Card className="shadow-sm">
              <CardContent className="p-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-muted-foreground">Caught Up</p>
                  <div className="text-2xl font-bold font-mono text-emerald-600 mt-1">{metrics.caughtUp}</div>
                </div>
                <div className="text-right">
                  <p className="text-sm font-medium text-muted-foreground">Still Without Catch-up</p>
                  <div className="text-2xl font-bold font-mono text-amber-600 mt-1">{metrics.absencesWithoutCatchup}</div>
                </div>
              </CardContent>
            </Card>
            <Card className="bg-muted/30 border-dashed shadow-none">
              <CardContent className="p-4 text-sm text-muted-foreground">
                Calculated {format(parseISO(metrics.calculatedAt), "d MMM yyyy HH:mm")}. Sessions are selected by this
                reporting period; catch-up shown is only what's confirmed as of right now -- a catch-up recorded
                later can raise an earlier period's participation on a future call. This figure is never presented
                as final for a period that has already closed.
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
