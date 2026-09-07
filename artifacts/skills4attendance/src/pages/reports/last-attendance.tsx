import * as React from "react";
import {
  useGetCurrentUser,
  useListTutors,
  getListTutorsQueryKey,
  useListCohorts,
  useGetLastAttendanceReport,
  exportLastAttendanceReport,
  LearnerStatus,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Combobox } from "@/components/ui/combobox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Download, UserX, ChevronLeft, ChevronRight } from "lucide-react";
import { format } from "date-fns";

const allValue = "__all__";

const STATUS_LABELS: Record<LearnerStatus, string> = {
  active: "Active",
  paused: "Paused",
  completed: "Completed",
  withdrawn: "Withdrawn",
};

export default function LastAttendanceReportPage() {
  const { toast } = useToast();
  const { data: user } = useGetCurrentUser();
  const isTutor = user?.role === "tutor";
  const tutorsListParams = { active: true };
  const { data: tutors = [] } = useListTutors(tutorsListParams, { query: { enabled: !isTutor, queryKey: getListTutorsQueryKey(tutorsListParams) } });
  const { data: cohorts = [] } = useListCohorts({ active: true });

  const [tutorId, setTutorId] = React.useState<number | undefined>(undefined);
  const [cohortId, setCohortId] = React.useState<number | undefined>(undefined);
  const [status, setStatus] = React.useState<LearnerStatus | undefined>(undefined);
  const [page, setPage] = React.useState(1);
  const [isExporting, setIsExporting] = React.useState(false);
  const pageSize = 25;

  const queryParams = { tutorId, cohortId, status, page, pageSize };

  const { data, isLoading, isError } = useGetLastAttendanceReport(queryParams);

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const csv = await exportLastAttendanceReport({ tutorId, cohortId, status });
      downloadCsv(csv, "last-attendance-report.csv");
    } catch (err: any) {
      toast({ title: "Export failed", description: err?.message, variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Last Attendance" }]} />

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <UserX className="w-7 h-7 text-primary" /> Last Attendance
          </h1>
          <p className="text-muted-foreground mt-1">Every learner's most recently attended session, oldest/never first.</p>
        </div>
        <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting}>
          {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export CSV
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-6">
        {!isTutor && (
          <Combobox
            className="w-48"
            options={[{ value: allValue, label: "All tutors" }, ...tutors.map((t) => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` }))]}
            value={tutorId != null ? String(tutorId) : allValue}
            onValueChange={(v) => { setTutorId(v === allValue ? undefined : Number(v)); setPage(1); }}
            placeholder="Tutor"
            searchPlaceholder="Search tutors..."
          />
        )}
        <Combobox
          className="w-48"
          options={[{ value: allValue, label: "All cohorts" }, ...cohorts.map((c) => ({ value: String(c.id), label: c.name }))]}
          value={cohortId != null ? String(cohortId) : allValue}
          onValueChange={(v) => { setCohortId(v === allValue ? undefined : Number(v)); setPage(1); }}
          placeholder="Cohort"
          searchPlaceholder="Search cohorts..."
        />
        <Select
          value={status ?? allValue}
          onValueChange={(v) => { setStatus(v === allValue ? undefined : (v as LearnerStatus)); setPage(1); }}
        >
          <SelectTrigger className="w-40 h-9"><SelectValue placeholder="Status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={allValue}>All statuses</SelectItem>
            {(Object.keys(STATUS_LABELS) as LearnerStatus[]).map((s) => (
              <SelectItem key={s} value={s}>{STATUS_LABELS[s]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the last-attendance report.</CardContent></Card>
      ) : (
        <Card className="shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/30">
                <TableRow>
                  <TableHead>Learner</TableHead>
                  <TableHead>Cohort</TableHead>
                  <TableHead>Tutor</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last Attended</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.items.length === 0 ? (
                  <TableRow><TableCell colSpan={5} className="text-center p-8 text-muted-foreground">No learners found for these filters.</TableCell></TableRow>
                ) : (
                  data?.items.map((row) => (
                    <TableRow key={row.learnerId}>
                      <TableCell className="font-medium">{row.learnerName} <span className="text-muted-foreground text-xs">{row.learnerRef}</span></TableCell>
                      <TableCell>{row.cohortName ?? "—"}</TableCell>
                      <TableCell>{row.tutorName}</TableCell>
                      <TableCell><Badge variant="outline">{STATUS_LABELS[row.status]}</Badge></TableCell>
                      <TableCell>
                        {row.lastAttendedDate ? (
                          format(new Date(row.lastAttendedDate), "d MMM yyyy")
                        ) : (
                          <span className="text-amber-600 dark:text-amber-400 font-medium">Never attended</span>
                        )}
                      </TableCell>
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
