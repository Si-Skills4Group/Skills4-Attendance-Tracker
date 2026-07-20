import * as React from "react";
import { Link } from "wouter";
import {
  useListTutors,
  getListTutorsQueryKey,
  useGetCurrentUser,
  useGetTutorReportV2,
  getGetTutorReportV2QueryKey,
  exportTutorReport,
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
import { Loader2, Download, UserCog, AlertTriangle } from "lucide-react";

export default function TutorReportPage() {
  const { toast } = useToast();
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === "admin";
  const tutorsListParams = { active: true };
  const { data: tutors = [] } = useListTutors(tutorsListParams, { query: { enabled: isAdmin, queryKey: getListTutorsQueryKey(tutorsListParams) } });

  const [selectedTutorId, setSelectedTutorId] = React.useState<number | null>(null);
  const [dateFilter, setDateFilter] = React.useState<DateFilterValue>({ period: "current_month" });
  const [isExporting, setIsExporting] = React.useState(false);

  const tutorId = isAdmin ? selectedTutorId : (user?.tutorId ?? null);

  const tutorReportParams = { period: dateFilter.period, dateFrom: dateFilter.dateFrom, dateTo: dateFilter.dateTo };
  const { data: report, isLoading, isError } = useGetTutorReportV2(
    tutorId as number,
    tutorReportParams,
    { query: { enabled: tutorId !== null, queryKey: getGetTutorReportV2QueryKey(tutorId as number, tutorReportParams) } },
  );

  const handleExport = async () => {
    if (tutorId === null) return;
    setIsExporting(true);
    try {
      const csv = await exportTutorReport(tutorId, { period: dateFilter.period, dateFrom: dateFilter.dateFrom, dateTo: dateFilter.dateTo });
      downloadCsv(csv, `tutor-${tutorId}-report.csv`);
    } catch {
      toast({ title: "Export failed", variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Tutor Attendance" }]} />

      <div className="mb-6 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <UserCog className="w-7 h-7 text-primary" /> Tutor Attendance Report
        </h1>
        <p className="text-muted-foreground mt-1">{isAdmin ? "A tutor's attendance totals across their assigned cohorts." : "Your attendance totals across your assigned cohorts."}</p>
      </div>

      <Card className="shadow-sm mb-6 page-transition-enter stagger-1">
        <CardContent className="p-4 flex flex-col sm:flex-row gap-4 items-end">
          {isAdmin && (
            <div className="w-full sm:w-72 space-y-1.5">
              <label className="text-sm font-medium">Tutor</label>
              <Combobox
                options={tutors.map((t) => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` }))}
                value={selectedTutorId != null ? String(selectedTutorId) : ""}
                onValueChange={(v) => setSelectedTutorId(Number(v))}
                placeholder="Select a tutor..."
                searchPlaceholder="Search tutors..."
              />
            </div>
          )}
          <DashboardDateFilter value={dateFilter} onChange={setDateFilter} />
        </CardContent>
      </Card>

      {tutorId === null && !isAdmin && (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground flex flex-col items-center gap-2">
          <AlertTriangle className="w-6 h-6" /> Your account is not linked to a tutor profile.
        </CardContent></Card>
      )}

      {tutorId !== null && (
        <>
          {isLoading ? (
            <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
          ) : isError ? (
            <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load this tutor's report.</CardContent></Card>
          ) : report ? (
            <>
              <div className="flex justify-between items-center mb-4">
                <div>
                  <h2 className="text-xl font-bold">{report.tutor.firstName} {report.tutor.lastName}</h2>
                  <p className="text-sm text-muted-foreground">{report.activeCohorts} active cohorts • {report.activeLearners} active learners</p>
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

              <Card className="shadow-sm overflow-hidden mb-6">
                <CardHeader><CardTitle className="text-base">Cohort Breakdown</CardTitle></CardHeader>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader className="bg-muted/30">
                      <TableRow>
                        <TableHead>Cohort</TableHead>
                        <TableHead className="text-right">Attendance</TableHead>
                        <TableHead className="text-right">Hours</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {report.cohortBreakdown.length === 0 ? (
                        <TableRow><TableCell colSpan={3} className="text-center p-8 text-muted-foreground">No cohorts in this period.</TableCell></TableRow>
                      ) : (
                        report.cohortBreakdown.map((row) => (
                          <TableRow key={row.cohort.id}>
                            <TableCell className="font-medium">
                              <Link href={`/attendance/cohorts/${row.cohort.id}`}><span className="hover:underline cursor-pointer">{row.cohort.name}</span></Link>
                            </TableCell>
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
              </Card>

              {report.lowAttendanceLearners.length > 0 && (
                <Card className="shadow-sm overflow-hidden">
                  <CardHeader><CardTitle className="text-base">Learners Below Threshold</CardTitle></CardHeader>
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader className="bg-muted/30">
                        <TableRow>
                          <TableHead>Learner</TableHead>
                          <TableHead>Cohort</TableHead>
                          <TableHead className="text-right">Attendance</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {report.lowAttendanceLearners.map((row) => (
                          <TableRow key={row.learnerId}>
                            <TableCell className="font-medium">
                              <Link href={`/learners/${row.learnerId}`}><span className="hover:underline cursor-pointer">{row.learnerName}</span></Link>
                            </TableCell>
                            <TableCell>{row.cohortName ?? "—"}</TableCell>
                            <TableCell className="text-right font-mono font-bold text-rose-600">
                              {row.metrics.attendancePercentage != null ? `${row.metrics.attendancePercentage.toFixed(1)}%` : "—"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </Card>
              )}
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
