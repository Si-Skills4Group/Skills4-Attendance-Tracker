import * as React from "react";
import { Link } from "wouter";
import {
  useGetCurrentUser,
  useListTutors,
  getListTutorsQueryKey,
  useListCohorts,
  useGetRegisterCompletionReport,
  exportRegisterCompletionReport,
  RegisterStatusFilter,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { RegisterStatusBadge } from "@/components/status-badges";
import { RegisterCompletionSummaryView } from "@/components/dashboard/register-completion-summary";
import { ReportFilterBar, ReportFilters } from "@/components/reports/report-filter-bar";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Download, ClipboardCheck, ChevronLeft, ChevronRight } from "lucide-react";
import { format } from "date-fns";

const ALL = "__all__";

export default function RegisterCompletionReportPage() {
  const { toast } = useToast();
  const { data: user } = useGetCurrentUser();
  const isTutor = user?.role === "tutor";
  const tutorsListParams = { active: true };
  const { data: tutors = [] } = useListTutors(tutorsListParams, { query: { enabled: !isTutor, queryKey: getListTutorsQueryKey(tutorsListParams) } });
  const { data: cohorts = [] } = useListCohorts({ active: true });

  const [filters, setFilters] = React.useState<ReportFilters>({ period: "current_month" });
  const [registerStatus, setRegisterStatus] = React.useState<RegisterStatusFilter | typeof ALL>(ALL);
  const [overdueOnly, setOverdueOnly] = React.useState(false);
  const [page, setPage] = React.useState(1);
  const [isExporting, setIsExporting] = React.useState(false);
  const pageSize = 25;

  const queryParams = {
    period: filters.period,
    dateFrom: filters.dateFrom,
    dateTo: filters.dateTo,
    tutorId: filters.tutorId,
    cohortId: filters.cohortId,
    registerStatus: registerStatus !== ALL ? registerStatus : undefined,
    overdueOnly,
    page,
    pageSize,
  };

  const { data, isLoading, isError } = useGetRegisterCompletionReport(queryParams);

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const csv = await exportRegisterCompletionReport({ ...queryParams });
      downloadCsv(csv, "register-completion-report.csv");
    } catch (err: any) {
      toast({ title: "Export failed", description: err?.message, variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Register Completion" }]} />

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <ClipboardCheck className="w-7 h-7 text-primary" /> Register Completion
          </h1>
          <p className="text-muted-foreground mt-1">Which registers are outstanding, in progress, completed or locked.</p>
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
      />

      <div className="flex flex-wrap items-center gap-4 -mt-3 mb-6">
        <Select value={registerStatus} onValueChange={(v) => { setRegisterStatus(v as RegisterStatusFilter | typeof ALL); setPage(1); }}>
          <SelectTrigger className="w-48"><SelectValue placeholder="Register status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All statuses</SelectItem>
            <SelectItem value="not_started">Not started</SelectItem>
            <SelectItem value="in_progress">In progress</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="locked">Locked</SelectItem>
            <SelectItem value="cancelled">Cancelled</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex items-center gap-2">
          <Switch id="overdue-only" checked={overdueOnly} onCheckedChange={(v) => { setOverdueOnly(v); setPage(1); }} />
          <Label htmlFor="overdue-only" className="text-sm">Overdue only</Label>
        </div>
      </div>

      {data?.registerCompletion && (
        <Card className="shadow-sm mb-6">
          <CardContent className="p-4">
            <RegisterCompletionSummaryView completion={data.registerCompletion} />
          </CardContent>
        </Card>
      )}

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the register-completion report.</CardContent></Card>
      ) : (
        <Card className="shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/30">
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Cohort</TableHead>
                  <TableHead>Tutor</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Recorded / Expected</TableHead>
                  <TableHead className="text-right">Overdue</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.items.length === 0 ? (
                  <TableRow><TableCell colSpan={6} className="text-center p-8 text-muted-foreground">No sessions found for these filters.</TableCell></TableRow>
                ) : (
                  data?.items.map((row) => (
                    <TableRow key={row.sessionId}>
                      <TableCell>
                        <Link href={`/attendance/${row.sessionId}`}><span className="hover:underline cursor-pointer">{format(new Date(row.sessionDate), "d MMM yyyy")}</span></Link>
                      </TableCell>
                      <TableCell>{row.cohortName}</TableCell>
                      <TableCell>{row.tutorName}</TableCell>
                      <TableCell><RegisterStatusBadge status={row.registerStatus} /></TableCell>
                      <TableCell className="text-right font-mono">{row.recordedCount} / {row.expectedCount}</TableCell>
                      <TableCell className="text-right font-mono">{row.outstandingDays != null ? `${row.outstandingDays}d` : "—"}</TableCell>
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
