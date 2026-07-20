import * as React from "react";
import {
  useListCohorts,
  useGetCohortReportV2,
  getGetCohortReportV2QueryKey,
  exportCohortReport,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DashboardDateFilter, DateFilterValue } from "@/components/dashboard/dashboard-date-filter";
import { AttendanceMetricsCards } from "@/components/reports/attendance-metrics-cards";
import { RegisterCompletionSummaryView } from "@/components/dashboard/register-completion-summary";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Download, Users2, ChevronLeft, ChevronRight } from "lucide-react";

export default function CohortReportPage() {
  const { toast } = useToast();
  const { data: cohorts = [] } = useListCohorts({});
  const [cohortId, setCohortId] = React.useState<number | null>(null);
  const [dateFilter, setDateFilter] = React.useState<DateFilterValue>({ period: "current_month" });
  const [page, setPage] = React.useState(1);
  const [isExporting, setIsExporting] = React.useState(false);
  const pageSize = 20;

  const cohortReportParams = { period: dateFilter.period, dateFrom: dateFilter.dateFrom, dateTo: dateFilter.dateTo, page, pageSize };
  const { data: report, isLoading, isError } = useGetCohortReportV2(
    cohortId as number,
    cohortReportParams,
    { query: { enabled: cohortId !== null, queryKey: getGetCohortReportV2QueryKey(cohortId as number, cohortReportParams) } },
  );

  const handleExport = async () => {
    if (cohortId === null) return;
    setIsExporting(true);
    try {
      const csv = await exportCohortReport(cohortId, { period: dateFilter.period, dateFrom: dateFilter.dateFrom, dateTo: dateFilter.dateTo });
      downloadCsv(csv, `cohort-${cohortId}-report.csv`);
    } catch {
      toast({ title: "Export failed", variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Cohort Attendance" }]} />

      <div className="mb-6 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <Users2 className="w-7 h-7 text-primary" /> Cohort Attendance Report
        </h1>
        <p className="text-muted-foreground mt-1">Attendance totals for a cohort, with a per-learner breakdown.</p>
      </div>

      <Card className="shadow-sm mb-6 page-transition-enter stagger-1">
        <CardContent className="p-4 flex flex-col sm:flex-row gap-4 items-end">
          <div className="w-full sm:w-72 space-y-1.5">
            <label className="text-sm font-medium">Cohort</label>
            <Combobox
              options={cohorts.map((c) => ({ value: String(c.id), label: c.name }))}
              value={cohortId != null ? String(cohortId) : ""}
              onValueChange={(v) => { setCohortId(Number(v)); setPage(1); }}
              placeholder="Select a cohort..."
              searchPlaceholder="Search cohorts..."
            />
          </div>
          <DashboardDateFilter value={dateFilter} onChange={setDateFilter} />
        </CardContent>
      </Card>

      {cohortId !== null && (
        <>
          {isLoading ? (
            <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
          ) : isError ? (
            <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load this cohort's report.</CardContent></Card>
          ) : report ? (
            <>
              <div className="flex justify-between items-center mb-4">
                <div>
                  <h2 className="text-xl font-bold">{report.cohort.name}</h2>
                  <p className="text-sm text-muted-foreground">{report.cohort.programme} • Level {report.cohort.level} • {report.activeLearnerCount} active learners</p>
                </div>
                <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting}>
                  {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export CSV
                </Button>
              </div>

              <AttendanceMetricsCards metrics={report.metrics} />

              <Card className="shadow-sm mb-6">
                <CardContent className="p-4">
                  <RegisterCompletionSummaryView completion={report.registerCompletion} />
                </CardContent>
              </Card>

              <Card className="shadow-sm overflow-hidden">
                <CardHeader><CardTitle className="text-base">Learner Breakdown</CardTitle></CardHeader>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader className="bg-muted/30">
                      <TableRow>
                        <TableHead>Learner</TableHead>
                        <TableHead>Reference</TableHead>
                        <TableHead className="text-right">Attendance</TableHead>
                        <TableHead className="text-right">Hours</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {report.learnerBreakdown.items.length === 0 ? (
                        <TableRow><TableCell colSpan={4} className="text-center p-8 text-muted-foreground">No learners had sessions in this cohort during this period.</TableCell></TableRow>
                      ) : (
                        report.learnerBreakdown.items.map((row) => (
                          <TableRow key={row.learnerId}>
                            <TableCell className="font-medium">{row.learnerName}</TableCell>
                            <TableCell className="text-muted-foreground">{row.learnerRef}</TableCell>
                            <TableCell className="text-right font-mono font-bold text-primary">
                              {row.metrics.attendancePercentage != null ? `${row.metrics.attendancePercentage.toFixed(1)}%` : "—"}
                            </TableCell>
                            <TableCell className="text-right font-mono">{(row.metrics.attendedMinutes / 60).toFixed(1)} / {(row.metrics.expectedMinutes / 60).toFixed(1)}</TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </div>
                {report.learnerBreakdown.total > 0 && (
                  <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/10">
                    <div className="text-sm text-muted-foreground">
                      Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, report.learnerBreakdown.total)} of {report.learnerBreakdown.total}
                    </div>
                    <div className="flex items-center space-x-2">
                      <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}><ChevronLeft className="w-4 h-4" /></Button>
                      <div className="text-sm font-medium px-2">{page}</div>
                      <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * pageSize >= report.learnerBreakdown.total}><ChevronRight className="w-4 h-4" /></Button>
                    </div>
                  </div>
                )}
              </Card>
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
