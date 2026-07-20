import * as React from "react";
import {
  useGetCurrentUser,
  useListTutors,
  getListTutorsQueryKey,
  useListCohorts,
  useGetAttendanceHoursReport,
  exportAttendanceHoursReport,
  AttendanceHoursGroupBy,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Combobox } from "@/components/ui/combobox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DashboardDateFilter, DateFilterValue } from "@/components/dashboard/dashboard-date-filter";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Download, CalendarClock } from "lucide-react";

const ALL = "__all__";

const GROUP_OPTIONS: { value: AttendanceHoursGroupBy; label: string; adminOnly?: boolean }[] = [
  { value: "cohort", label: "By cohort" },
  { value: "week", label: "By week" },
  { value: "month", label: "By month" },
  { value: "learner", label: "By learner (requires a tutor or cohort filter)" },
  { value: "tutor", label: "By tutor (admin only)", adminOnly: true },
  { value: "programme", label: "By programme (admin only)", adminOnly: true },
  { value: "employer", label: "By employer (admin only)", adminOnly: true },
];

export default function AttendanceHoursReportPage() {
  const { toast } = useToast();
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === "admin";
  const isTutor = user?.role === "tutor";
  const tutorsListParams = { active: true };
  const { data: tutors = [] } = useListTutors(tutorsListParams, { query: { enabled: isAdmin, queryKey: getListTutorsQueryKey(tutorsListParams) } });
  const { data: cohorts = [] } = useListCohorts({ active: true });

  const [groupBy, setGroupBy] = React.useState<AttendanceHoursGroupBy>("cohort");
  const [dateFilter, setDateFilter] = React.useState<DateFilterValue>({ period: "current_month" });
  const [tutorId, setTutorId] = React.useState<number | undefined>(undefined);
  const [cohortId, setCohortId] = React.useState<number | undefined>(undefined);
  const [isExporting, setIsExporting] = React.useState(false);

  const availableOptions = GROUP_OPTIONS.filter((o) => !o.adminOnly || isAdmin);

  const queryParams = {
    groupBy,
    period: dateFilter.period,
    dateFrom: dateFilter.dateFrom,
    dateTo: dateFilter.dateTo,
    tutorId: isTutor ? undefined : tutorId,
    cohortId,
  };

  const { data, isLoading, isError, error } = useGetAttendanceHoursReport(queryParams);

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const csv = await exportAttendanceHoursReport({ ...queryParams });
      downloadCsv(csv, `attendance-hours-${groupBy}-report.csv`);
    } catch (err: any) {
      toast({ title: "Export failed", description: err?.message, variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Attendance Hours" }]} />

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <CalendarClock className="w-7 h-7 text-primary" /> Attendance Hours
          </h1>
          <p className="text-muted-foreground mt-1">Expected vs attended time, grouped however is most useful.</p>
        </div>
        <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting}>
          {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export CSV
        </Button>
      </div>

      <Card className="shadow-sm mb-6 page-transition-enter stagger-1">
        <CardContent className="p-4 flex flex-col sm:flex-row flex-wrap gap-4 items-end">
          <div className="w-full sm:w-64 space-y-1.5">
            <label className="text-sm font-medium">Group by</label>
            <Select value={groupBy} onValueChange={(v) => setGroupBy(v as AttendanceHoursGroupBy)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {availableOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          {isAdmin && (
            <div className="w-full sm:w-56 space-y-1.5">
              <label className="text-sm font-medium">Tutor</label>
              <Combobox
                options={[{ value: ALL, label: "All tutors" }, ...tutors.map((t) => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` }))]}
                value={tutorId != null ? String(tutorId) : ALL}
                onValueChange={(v) => setTutorId(v === ALL ? undefined : Number(v))}
                placeholder="Tutor"
                searchPlaceholder="Search tutors..."
              />
            </div>
          )}
          <div className="w-full sm:w-56 space-y-1.5">
            <label className="text-sm font-medium">Cohort</label>
            <Combobox
              options={[{ value: ALL, label: "All cohorts" }, ...cohorts.map((c) => ({ value: String(c.id), label: c.name }))]}
              value={cohortId != null ? String(cohortId) : ALL}
              onValueChange={(v) => setCohortId(v === ALL ? undefined : Number(v))}
              placeholder="Cohort"
              searchPlaceholder="Search cohorts..."
            />
          </div>
          <DashboardDateFilter value={dateFilter} onChange={setDateFilter} />
        </CardContent>
      </Card>

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">{(error as any)?.message ?? "Could not load the attendance-hours report."}</CardContent></Card>
      ) : (
        <Card className="shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/30">
                <TableRow>
                  <TableHead className="capitalize">{groupBy}</TableHead>
                  <TableHead className="text-right">Attendance</TableHead>
                  <TableHead className="text-right">Hours Attended / Expected</TableHead>
                  <TableHead className="text-right">Late</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.items.length === 0 ? (
                  <TableRow><TableCell colSpan={4} className="text-center p-8 text-muted-foreground">No data for these filters.</TableCell></TableRow>
                ) : (
                  data?.items.map((row) => (
                    <TableRow key={row.key}>
                      <TableCell className="font-medium">{row.label}</TableCell>
                      <TableCell className="text-right font-mono font-bold text-primary">
                        {row.metrics.attendancePercentage != null ? `${row.metrics.attendancePercentage.toFixed(1)}%` : "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono">{(row.metrics.attendedMinutes / 60).toFixed(1)} / {(row.metrics.expectedMinutes / 60).toFixed(1)}</TableCell>
                      <TableCell className="text-right font-mono">{row.metrics.lateSessionCount}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </Card>
      )}
    </div>
  );
}
