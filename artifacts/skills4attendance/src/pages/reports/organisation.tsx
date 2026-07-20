import * as React from "react";
import {
  useGetOrganisationReportV2,
  getGetOrganisationReportV2QueryKey,
  exportOrganisationReport,
  useGetCurrentUser,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DashboardDateFilter, DateFilterValue } from "@/components/dashboard/dashboard-date-filter";
import { AttendanceMetricsCards } from "@/components/reports/attendance-metrics-cards";
import { RegisterCompletionSummaryView } from "@/components/dashboard/register-completion-summary";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Download, Building2, ShieldAlert } from "lucide-react";

type Breakdown = "tutor" | "cohort" | "programme" | "level" | "employer";

function MetricsRow({ label, metrics }: { label: string; metrics: { attendancePercentage: number | null; attendedMinutes: number; expectedMinutes: number } }) {
  return (
    <TableRow>
      <TableCell className="font-medium">{label}</TableCell>
      <TableCell className="text-right font-mono font-bold text-primary">{metrics.attendancePercentage != null ? `${metrics.attendancePercentage.toFixed(1)}%` : "—"}</TableCell>
      <TableCell className="text-right font-mono">{(metrics.attendedMinutes / 60).toFixed(1)} / {(metrics.expectedMinutes / 60).toFixed(1)}</TableCell>
    </TableRow>
  );
}

export default function OrganisationReportPage() {
  const { toast } = useToast();
  const { data: user } = useGetCurrentUser();
  const [dateFilter, setDateFilter] = React.useState<DateFilterValue>({ period: "current_month" });
  const [breakdown, setBreakdown] = React.useState<Breakdown>("tutor");
  const [isExporting, setIsExporting] = React.useState(false);

  const isAdmin = user?.role === "admin";

  const orgReportParams = { period: dateFilter.period, dateFrom: dateFilter.dateFrom, dateTo: dateFilter.dateTo };
  const { data: report, isLoading, isError } = useGetOrganisationReportV2(
    orgReportParams,
    { query: { enabled: isAdmin, queryKey: getGetOrganisationReportV2QueryKey(orgReportParams) } },
  );

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const csv = await exportOrganisationReport({ period: dateFilter.period, dateFrom: dateFilter.dateFrom, dateTo: dateFilter.dateTo, breakdown });
      downloadCsv(csv, `organisation-${breakdown}-report.csv`);
    } catch {
      toast({ title: "Export failed", variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  if (user && !isAdmin) {
    return (
      <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
        <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Organisation Overview" }]} />
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground flex flex-col items-center gap-2">
          <ShieldAlert className="w-6 h-6" /> Administrator access required.
        </CardContent></Card>
      </div>
    );
  }

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Organisation Overview" }]} />

      <div className="mb-6 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <Building2 className="w-7 h-7 text-primary" /> Organisation Overview
        </h1>
        <p className="text-muted-foreground mt-1">Attendance across the whole organisation, broken down by tutor, cohort, programme, level and employer.</p>
      </div>

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6 page-transition-enter stagger-1">
        <DashboardDateFilter value={dateFilter} onChange={setDateFilter} />
        <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting}>
          {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export {breakdown} breakdown
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the organisation report.</CardContent></Card>
      ) : report ? (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-2">
            <Card className="shadow-sm"><CardContent className="p-4"><p className="text-sm text-muted-foreground">Active Learners</p><p className="text-2xl font-bold font-mono">{report.activeLearners}</p></CardContent></Card>
            <Card className="shadow-sm"><CardContent className="p-4"><p className="text-sm text-muted-foreground">Active Tutors</p><p className="text-2xl font-bold font-mono">{report.activeTutors}</p></CardContent></Card>
            <Card className="shadow-sm"><CardContent className="p-4"><p className="text-sm text-muted-foreground">Active Cohorts</p><p className="text-2xl font-bold font-mono">{report.activeCohorts}</p></CardContent></Card>
            <Card className="shadow-sm"><CardContent className="p-4"><p className="text-sm text-muted-foreground">Sessions in Period</p><p className="text-2xl font-bold font-mono">{report.sessionsInPeriod}</p></CardContent></Card>
          </div>

          <AttendanceMetricsCards metrics={report.metrics} />

          <Card className="shadow-sm mb-6">
            <CardContent className="p-4">
              <RegisterCompletionSummaryView completion={report.registerCompletion} />
            </CardContent>
          </Card>

          <Card className="shadow-sm overflow-hidden">
            <CardHeader><CardTitle className="text-base">Breakdown</CardTitle></CardHeader>
            <CardContent className="pt-0">
              <Tabs value={breakdown} onValueChange={(v) => setBreakdown(v as Breakdown)}>
                <TabsList className="grid grid-cols-5 mb-4 max-w-xl">
                  <TabsTrigger value="tutor">Tutor</TabsTrigger>
                  <TabsTrigger value="cohort">Cohort</TabsTrigger>
                  <TabsTrigger value="programme">Programme</TabsTrigger>
                  <TabsTrigger value="level">Level</TabsTrigger>
                  <TabsTrigger value="employer">Employer</TabsTrigger>
                </TabsList>
                {(["tutor", "cohort", "programme", "level", "employer"] as Breakdown[]).map((dim) => (
                  <TabsContent key={dim} value={dim} className="overflow-x-auto">
                    <Table>
                      <TableHeader className="bg-muted/30">
                        <TableRow>
                          <TableHead className="capitalize">{dim}</TableHead>
                          <TableHead className="text-right">Attendance</TableHead>
                          <TableHead className="text-right">Hours</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {dim === "tutor" && report.tutorBreakdown.map((r) => <MetricsRow key={r.tutorId} label={r.tutorName} metrics={r.metrics} />)}
                        {dim === "cohort" && report.cohortBreakdown.map((r) => <MetricsRow key={r.cohort.id} label={r.cohort.name} metrics={r.metrics} />)}
                        {dim === "programme" && report.programmeBreakdown.map((r) => <MetricsRow key={r.programme} label={r.programme} metrics={r.metrics} />)}
                        {dim === "level" && report.levelBreakdown.map((r) => <MetricsRow key={r.level} label={r.level} metrics={r.metrics} />)}
                        {dim === "employer" && report.employerBreakdown.map((r) => <MetricsRow key={r.employer} label={r.employer} metrics={r.metrics} />)}
                      </TableBody>
                    </Table>
                  </TabsContent>
                ))}
              </Tabs>
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}
