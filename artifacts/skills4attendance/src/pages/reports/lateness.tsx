import * as React from "react";
import {
  useGetCurrentUser,
  useListTutors,
  getListTutorsQueryKey,
  useListCohorts,
  useGetLatenessReport,
  exportLatenessReport,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AttendanceMetricsCards } from "@/components/reports/attendance-metrics-cards";
import { ReportFilterBar, ReportFilters } from "@/components/reports/report-filter-bar";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Download, Clock, ChevronLeft, ChevronRight } from "lucide-react";
import { format } from "date-fns";

export default function LatenessReportPage() {
  const { toast } = useToast();
  const { data: user } = useGetCurrentUser();
  const isTutor = user?.role === "tutor";
  const tutorsListParams = { active: true };
  const { data: tutors = [] } = useListTutors(tutorsListParams, { query: { enabled: !isTutor, queryKey: getListTutorsQueryKey(tutorsListParams) } });
  const { data: cohorts = [] } = useListCohorts({ active: true });

  const [filters, setFilters] = React.useState<ReportFilters>({ period: "current_month" });
  const [page, setPage] = React.useState(1);
  const [isExporting, setIsExporting] = React.useState(false);
  const pageSize = 25;

  const queryParams = {
    period: filters.period,
    dateFrom: filters.dateFrom,
    dateTo: filters.dateTo,
    tutorId: filters.tutorId,
    cohortId: filters.cohortId,
    programme: filters.programme,
    level: filters.level,
    employer: filters.employer,
    page,
    pageSize,
  };

  const { data, isLoading, isError } = useGetLatenessReport(queryParams);

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const csv = await exportLatenessReport({ ...queryParams });
      downloadCsv(csv, "lateness-report.csv");
    } catch (err: any) {
      toast({ title: "Export failed", description: err?.message, variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Late Attendance" }]} />

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <Clock className="w-7 h-7 text-primary" /> Late Attendance
          </h1>
          <p className="text-muted-foreground mt-1">Late arrivals, ordered by how many minutes were missed.</p>
        </div>
        <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting}>
          {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export CSV
        </Button>
      </div>

      <ReportFilterBar
        value={filters}
        onChange={(next) => { setFilters(next); setPage(1); }}
        tutors={tutors}
        cohorts={cohorts}
        showTutor={!isTutor}
        showProgrammeLevelEmployer
      />

      {data?.metrics && <AttendanceMetricsCards metrics={data.metrics} />}

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the lateness report.</CardContent></Card>
      ) : (
        <Card className="shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/30">
                <TableRow>
                  <TableHead>Learner</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead>Cohort</TableHead>
                  <TableHead>Tutor</TableHead>
                  <TableHead className="text-right">Minutes Late</TableHead>
                  <TableHead className="text-right">Hours Attended</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.items.length === 0 ? (
                  <TableRow><TableCell colSpan={6} className="text-center p-8 text-muted-foreground">No late arrivals found for these filters.</TableCell></TableRow>
                ) : (
                  data?.items.map((row, i) => (
                    <TableRow key={`${row.sessionId}-${row.learnerId}-${i}`}>
                      <TableCell className="font-medium">{row.learnerName} <span className="text-muted-foreground text-xs">{row.learnerRef}</span></TableCell>
                      <TableCell>{format(new Date(row.sessionDate), "d MMM yyyy")}</TableCell>
                      <TableCell>{row.cohortName}</TableCell>
                      <TableCell>{row.tutorName}</TableCell>
                      <TableCell className="text-right font-mono font-bold text-amber-600">{row.minutesLate}</TableCell>
                      <TableCell className="text-right font-mono">{row.hoursAttended ?? "—"}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
          {data && data.total > 0 && (
            <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/10">
              <div className="text-sm text-muted-foreground">Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, data.total)} of {data.total}</div>
              <div className="flex items-center space-x-2">
                <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}><ChevronLeft className="w-4 h-4" /></Button>
                <div className="text-sm font-medium px-2">{page}</div>
                <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * pageSize >= data.total}><ChevronRight className="w-4 h-4" /></Button>
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
