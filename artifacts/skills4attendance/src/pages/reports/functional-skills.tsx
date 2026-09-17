import * as React from "react";
import {
  useGetCurrentUser,
  useListTutors,
  getListTutorsQueryKey,
  useGetFunctionalSkillsReport,
  getGetFunctionalSkillsReportQueryKey,
  exportFunctionalSkillsReport,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AttendanceMetricsCards } from "@/components/reports/attendance-metrics-cards";
import { RegisterCompletionSummaryView } from "@/components/dashboard/register-completion-summary";
import { ReportFilterBar, ReportFilters } from "@/components/reports/report-filter-bar";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Download, BookOpenCheck, ShieldAlert } from "lucide-react";

type Breakdown = "subject" | "cohort" | "learner";

const SUBJECT_LABELS: Record<string, string> = { math: "Math", english: "English", both: "Both" };

function MetricsRow({ label, metrics }: { label: React.ReactNode; metrics: { attendancePercentage: number | null; attendedMinutes: number; expectedMinutes: number } }) {
  return (
    <TableRow>
      <TableCell className="font-medium">{label}</TableCell>
      <TableCell className="text-right font-mono font-bold text-primary">{metrics.attendancePercentage != null ? `${metrics.attendancePercentage.toFixed(1)}%` : "—"}</TableCell>
      <TableCell className="text-right font-mono">{(metrics.attendedMinutes / 60).toFixed(1)} / {(metrics.expectedMinutes / 60).toFixed(1)}</TableCell>
    </TableRow>
  );
}

export default function FunctionalSkillsReportPage() {
  const { toast } = useToast();
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === "admin";

  const tutorsListParams = { active: true };
  const { data: tutors = [] } = useListTutors(tutorsListParams, { query: { enabled: isAdmin, queryKey: getListTutorsQueryKey(tutorsListParams) } });

  const [filters, setFilters] = React.useState<ReportFilters>({ period: "current_month" });
  const [breakdown, setBreakdown] = React.useState<Breakdown>("cohort");
  const [isExporting, setIsExporting] = React.useState(false);

  const reportParams = {
    period: filters.period, dateFrom: filters.dateFrom, dateTo: filters.dateTo,
    subject: filters.subject, tutorId: filters.tutorId,
  };
  const { data: report, isLoading, isError } = useGetFunctionalSkillsReport(
    reportParams,
    { query: { enabled: isAdmin, queryKey: getGetFunctionalSkillsReportQueryKey(reportParams) } },
  );

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const csv = await exportFunctionalSkillsReport({ ...reportParams, breakdown });
      downloadCsv(csv, `functional-skills-${breakdown}-report.csv`);
    } catch (err: any) {
      toast({ title: "Export failed", description: err?.message, variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  if (user && !isAdmin) {
    return (
      <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
        <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Functional Skills" }]} />
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground flex flex-col items-center gap-2">
          <ShieldAlert className="w-6 h-6" /> Administrator access required.
        </CardContent></Card>
      </div>
    );
  }

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Functional Skills" }]} />

      <div className="mb-6 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <BookOpenCheck className="w-7 h-7 text-primary" /> Functional Skills
        </h1>
        <p className="text-muted-foreground mt-1">Math and English Functional Skills attendance, by cohort and subject, with at-risk learners flagged.</p>
      </div>

      <ReportFilterBar
        value={filters}
        onChange={setFilters}
        tutors={tutors}
        showTutor
        showCohort={false}
        showSubject
      />

      <div className="flex justify-end mb-4 page-transition-enter stagger-1">
        <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting}>
          {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export {breakdown} breakdown
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the Functional Skills report.</CardContent></Card>
      ) : report ? (
        <>
          <div className="grid grid-cols-2 md:grid-cols-2 gap-4 mb-2">
            <Card className="shadow-sm"><CardContent className="p-4"><p className="text-sm text-muted-foreground">Active FS Cohorts</p><p className="text-2xl font-bold font-mono">{report.activeFsCohorts}</p></CardContent></Card>
            <Card className="shadow-sm"><CardContent className="p-4"><p className="text-sm text-muted-foreground">Active FS Learners</p><p className="text-2xl font-bold font-mono">{report.activeFsLearners}</p></CardContent></Card>
          </div>

          <AttendanceMetricsCards metrics={report.metrics} />

          <Card className="shadow-sm mb-6">
            <CardContent className="p-4">
              <RegisterCompletionSummaryView completion={report.registerCompletion} />
            </CardContent>
          </Card>

          <Card className="shadow-sm overflow-hidden">
            <CardContent className="pt-6">
              <Tabs value={breakdown} onValueChange={(v) => setBreakdown(v as Breakdown)}>
                <TabsList className="grid grid-cols-3 mb-4 max-w-md">
                  <TabsTrigger value="subject">Subject Breakdown</TabsTrigger>
                  <TabsTrigger value="cohort">Cohorts</TabsTrigger>
                  <TabsTrigger value="learner">At-Risk Learners</TabsTrigger>
                </TabsList>

                <TabsContent value="subject" className="overflow-x-auto">
                  <Table>
                    <TableHeader className="bg-muted/30">
                      <TableRow>
                        <TableHead>Subject</TableHead>
                        <TableHead className="text-right">Attendance</TableHead>
                        <TableHead className="text-right">Hours</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {report.subjectBreakdown.map((r) => (
                        <MetricsRow key={r.subject} label={SUBJECT_LABELS[r.subject] ?? r.subject} metrics={r.metrics} />
                      ))}
                    </TableBody>
                  </Table>
                  {report.subjectBreakdown.length === 0 && (
                    <p className="text-sm text-muted-foreground text-center py-6">No Functional Skills cohorts for these filters.</p>
                  )}
                </TabsContent>

                <TabsContent value="cohort" className="overflow-x-auto">
                  <Table>
                    <TableHeader className="bg-muted/30">
                      <TableRow>
                        <TableHead>Cohort</TableHead>
                        <TableHead>Tutor</TableHead>
                        <TableHead>Subject</TableHead>
                        <TableHead className="text-right">Active Learners</TableHead>
                        <TableHead className="text-right">Attendance</TableHead>
                        <TableHead className="text-right">Register Completion</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {report.cohortBreakdown.map((r) => (
                        <TableRow key={r.cohort.id}>
                          <TableCell className="font-medium">{r.cohort.name}</TableCell>
                          <TableCell>{r.cohort.tutorName ?? "Unassigned"}</TableCell>
                          <TableCell><Badge variant="outline">{r.cohort.subject ? (SUBJECT_LABELS[r.cohort.subject] ?? r.cohort.subject) : "—"}</Badge></TableCell>
                          <TableCell className="text-right font-mono">{r.activeLearnerCount}</TableCell>
                          <TableCell className="text-right font-mono font-bold text-primary">{r.metrics.attendancePercentage != null ? `${r.metrics.attendancePercentage.toFixed(1)}%` : "—"}</TableCell>
                          <TableCell className="text-right font-mono">{r.registerCompletion.completionPercentage != null ? `${r.registerCompletion.completionPercentage.toFixed(0)}%` : "—"}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {report.cohortBreakdown.length === 0 && (
                    <p className="text-sm text-muted-foreground text-center py-6">No Functional Skills cohorts for these filters.</p>
                  )}
                </TabsContent>

                <TabsContent value="learner" className="overflow-x-auto">
                  <p className="text-sm text-muted-foreground mb-3">
                    Learners below the organisation's low-attendance threshold ({report.lowAttendanceThreshold}%) in their Functional Skills cohort specifically.
                  </p>
                  <Table>
                    <TableHeader className="bg-muted/30">
                      <TableRow>
                        <TableHead>Learner</TableHead>
                        <TableHead>FS Cohort</TableHead>
                        <TableHead>Subject</TableHead>
                        <TableHead>Tutor</TableHead>
                        <TableHead className="text-right">Attendance</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {report.atRiskLearners.map((r) => (
                        <TableRow key={r.learnerId}>
                          <TableCell className="font-medium">{r.learnerName} <span className="text-muted-foreground text-xs">{r.learnerRef}</span></TableCell>
                          <TableCell>{r.cohortName}</TableCell>
                          <TableCell><Badge variant="outline">{SUBJECT_LABELS[r.subject] ?? r.subject}</Badge></TableCell>
                          <TableCell>{r.tutorName}</TableCell>
                          <TableCell className="text-right font-mono font-bold text-rose-600">{r.metrics.attendancePercentage != null ? `${r.metrics.attendancePercentage.toFixed(1)}%` : "—"}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {report.atRiskLearners.length === 0 && (
                    <p className="text-sm text-muted-foreground text-center py-6">No Functional Skills learners below the threshold for these filters.</p>
                  )}
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}
