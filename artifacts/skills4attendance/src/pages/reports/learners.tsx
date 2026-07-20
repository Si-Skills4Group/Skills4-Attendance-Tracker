import * as React from "react";
import { Link } from "wouter";
import {
  useListLearners,
  getListLearnersQueryKey,
  useGetLearnerReportV2,
  getGetLearnerReportV2QueryKey,
  exportLearnerReport,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DashboardDateFilter, DateFilterValue } from "@/components/dashboard/dashboard-date-filter";
import { AttendanceMetricsCards } from "@/components/reports/attendance-metrics-cards";
import { RegisterCompletionSummaryView } from "@/components/dashboard/register-completion-summary";
import { AttendanceStatusBadge } from "@/components/status-badges";
import { useDebounce } from "@/hooks/use-debounce";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Search, Loader2, Download, GraduationCap, ChevronLeft, ChevronRight } from "lucide-react";
import { format } from "date-fns";

export default function LearnerReportPage() {
  const { toast } = useToast();
  const [search, setSearch] = React.useState("");
  const debouncedSearch = useDebounce(search, 300);
  const [selectedLearnerId, setSelectedLearnerId] = React.useState<number | null>(null);
  const [dateFilter, setDateFilter] = React.useState<DateFilterValue>({ period: "current_month" });
  const [page, setPage] = React.useState(1);
  const [isExporting, setIsExporting] = React.useState(false);
  const pageSize = 20;

  // Searchable, paginated learner lookup -- never loads the full learner
  // list into the browser, only the (small) page matching the current
  // search text.
  const learnerSearchParams = { search: debouncedSearch || undefined, page: 1, pageSize: 10 };
  const { data: searchResults, isFetching: searchLoading } = useListLearners(
    learnerSearchParams,
    { query: { enabled: debouncedSearch.length > 0 && selectedLearnerId === null, queryKey: getListLearnersQueryKey(learnerSearchParams) } },
  );

  const learnerReportParams = { period: dateFilter.period, dateFrom: dateFilter.dateFrom, dateTo: dateFilter.dateTo, page, pageSize: 25 };
  const { data: report, isLoading, isError } = useGetLearnerReportV2(
    selectedLearnerId as number,
    learnerReportParams,
    { query: { enabled: selectedLearnerId !== null, queryKey: getGetLearnerReportV2QueryKey(selectedLearnerId as number, learnerReportParams) } },
  );

  const handleExport = async () => {
    if (selectedLearnerId === null) return;
    setIsExporting(true);
    try {
      const csv = await exportLearnerReport(selectedLearnerId, { period: dateFilter.period, dateFrom: dateFilter.dateFrom, dateTo: dateFilter.dateTo });
      downloadCsv(csv, `learner-${selectedLearnerId}-report.csv`);
    } catch {
      toast({ title: "Export failed", variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Learner Attendance" }]} />

      <div className="mb-6 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <GraduationCap className="w-7 h-7 text-primary" /> Learner Attendance Report
        </h1>
        <p className="text-muted-foreground mt-1">Search for a learner to see their attendance, register history and Bud progress context.</p>
      </div>

      <Card className="shadow-sm mb-6 page-transition-enter stagger-1">
        <CardContent className="p-4">
          <div className="relative max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              placeholder="Search learners by name or reference..."
              className="pl-9"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setSelectedLearnerId(null); }}
            />
          </div>
          {search && selectedLearnerId === null && (
            <div className="mt-2 border rounded-md divide-y max-w-md overflow-hidden">
              {searchLoading ? (
                <div className="p-3 text-sm text-muted-foreground flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Searching...</div>
              ) : searchResults?.items.length ? (
                searchResults.items.map((l) => (
                  <button
                    key={l.id}
                    type="button"
                    className="w-full text-left p-2.5 hover:bg-muted/50 text-sm"
                    onClick={() => { setSelectedLearnerId(l.id); setSearch(`${l.firstName} ${l.lastName}`); setPage(1); }}
                  >
                    <span className="font-medium">{l.firstName} {l.lastName}</span>
                    <span className="text-muted-foreground ml-2">{l.learnerRef}{l.cohortName ? ` — ${l.cohortName}` : ""}</span>
                  </button>
                ))
              ) : (
                <div className="p-3 text-sm text-muted-foreground">No learners found.</div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {selectedLearnerId !== null && (
        <>
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4 page-transition-enter stagger-2">
            <DashboardDateFilter value={dateFilter} onChange={setDateFilter} />
            <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting}>
              {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export CSV
            </Button>
          </div>

          {isLoading ? (
            <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
          ) : isError ? (
            <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load this learner's report.</CardContent></Card>
          ) : report ? (
            <>
              <div className="mb-4">
                <h2 className="text-xl font-bold">{report.learner.firstName} {report.learner.lastName}</h2>
                <p className="text-sm text-muted-foreground">{report.learner.learnerRef} • {report.learner.programme} • Level {report.learner.level}{report.learner.cohortName ? ` • ${report.learner.cohortName}` : ""}</p>
              </div>

              <AttendanceMetricsCards metrics={report.metrics} />

              <Card className="shadow-sm mb-6">
                <CardContent className="p-4">
                  <RegisterCompletionSummaryView completion={report.registerCompletion} />
                </CardContent>
              </Card>

              {report.bud && (
                <Card className="shadow-sm mb-6 bg-violet-50/50 dark:bg-violet-950/10 border-violet-200 dark:border-violet-900">
                  <CardHeader className="pb-2"><CardTitle className="text-sm">Bud LMS Progress</CardTitle></CardHeader>
                  <CardContent className="p-4 pt-0 grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
                    <div><p className="text-muted-foreground">Activity progress</p><p className="font-mono font-medium">{report.bud.activityProgress != null ? `${report.bud.activityProgress}%` : "—"}</p></div>
                    <div><p className="text-muted-foreground">Activities overdue</p><p className="font-mono font-medium">{report.bud.activitiesOverdue ?? "—"}</p></div>
                    <div><p className="text-muted-foreground">Status</p><p className="font-medium">{report.bud.statusDesc ?? "—"}</p></div>
                    {report.bud.syncedAt && <div className="col-span-full text-xs text-muted-foreground">Synced {format(new Date(report.bud.syncedAt), "d MMM yyyy HH:mm")}</div>}
                  </CardContent>
                </Card>
              )}

              <Card className="shadow-sm overflow-hidden">
                <CardHeader><CardTitle className="text-base">Session History</CardTitle></CardHeader>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader className="bg-muted/30">
                      <TableRow>
                        <TableHead>Date</TableHead>
                        <TableHead>Cohort</TableHead>
                        <TableHead>Tutor</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead className="text-right">Hours</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {report.sessionHistory.items.length === 0 ? (
                        <TableRow><TableCell colSpan={5} className="text-center p-8 text-muted-foreground">No sessions in this period.</TableCell></TableRow>
                      ) : (
                        report.sessionHistory.items.map((row) => (
                          <TableRow key={row.sessionId}>
                            <TableCell>{format(new Date(row.sessionDate), "d MMM yyyy")}</TableCell>
                            <TableCell>{row.cohortName}</TableCell>
                            <TableCell>{row.tutorName}</TableCell>
                            <TableCell>{row.status ? <AttendanceStatusBadge status={row.status} /> : <span className="text-xs text-muted-foreground italic">No register entry</span>}</TableCell>
                            <TableCell className="text-right font-mono">{row.hoursAttended ?? "—"}</TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </div>
                {report.sessionHistory.total > 0 && (
                  <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/10">
                    <div className="text-sm text-muted-foreground">
                      Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, report.sessionHistory.total)} of {report.sessionHistory.total}
                    </div>
                    <div className="flex items-center space-x-2">
                      <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}><ChevronLeft className="w-4 h-4" /></Button>
                      <div className="text-sm font-medium px-2">{page}</div>
                      <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * pageSize >= report.sessionHistory.total}><ChevronRight className="w-4 h-4" /></Button>
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
