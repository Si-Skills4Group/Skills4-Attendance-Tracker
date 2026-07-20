import * as React from "react";
import { Link } from "wouter";
import {
  useGetCurrentUser,
  useListTutors,
  getListTutorsQueryKey,
  useListCohorts,
  getListCohortsQueryKey,
  useGetAllocationHistoryReport,
  getGetAllocationHistoryReportQueryKey,
  exportAllocationHistoryReport,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Download, History, ShieldAlert, ChevronLeft, ChevronRight, Info } from "lucide-react";
import { format } from "date-fns";

const ALL = "__all__";

export default function AllocationHistoryReportPage() {
  const { toast } = useToast();
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === "admin";
  const tutorsListParams = { active: true };
  const { data: tutors = [] } = useListTutors(tutorsListParams, { query: { enabled: isAdmin, queryKey: getListTutorsQueryKey(tutorsListParams) } });
  const cohortsListParams = { active: true };
  const { data: cohorts = [] } = useListCohorts(cohortsListParams, { query: { enabled: isAdmin, queryKey: getListCohortsQueryKey(cohortsListParams) } });

  const [tutorId, setTutorId] = React.useState<number | undefined>(undefined);
  const [cohortId, setCohortId] = React.useState<number | undefined>(undefined);
  const [dateFrom, setDateFrom] = React.useState("");
  const [dateTo, setDateTo] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [isExporting, setIsExporting] = React.useState(false);
  const pageSize = 25;

  const queryParams = {
    tutorId,
    cohortId,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
    page,
    pageSize,
  };

  const { data, isLoading, isError } = useGetAllocationHistoryReport(queryParams, { query: { enabled: isAdmin, queryKey: getGetAllocationHistoryReportQueryKey(queryParams) } });

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const csv = await exportAllocationHistoryReport({ ...queryParams });
      downloadCsv(csv, "allocation-history-report.csv");
    } catch (err: any) {
      toast({ title: "Export failed", description: err?.message, variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  if (user && !isAdmin) {
    return (
      <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
        <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Allocation History" }]} />
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground flex flex-col items-center gap-2">
          <ShieldAlert className="w-6 h-6" /> Administrator access required.
        </CardContent></Card>
      </div>
    );
  }

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Allocation History" }]} />

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <History className="w-7 h-7 text-primary" /> Allocation History
          </h1>
          <p className="text-muted-foreground mt-1">Learner transfers between tutors and cohorts over time.</p>
        </div>
        <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting}>
          {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export CSV
        </Button>
      </div>

      <Card className="shadow-sm mb-4 page-transition-enter stagger-1">
        <CardContent className="p-4 flex flex-col sm:flex-row flex-wrap gap-4 items-end">
          <div className="w-full sm:w-56 space-y-1.5">
            <label className="text-sm font-medium">Tutor</label>
            <Combobox
              options={[{ value: ALL, label: "All tutors" }, ...tutors.map((t) => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` }))]}
              value={tutorId != null ? String(tutorId) : ALL}
              onValueChange={(v) => { setTutorId(v === ALL ? undefined : Number(v)); setPage(1); }}
              placeholder="Tutor"
              searchPlaceholder="Search tutors..."
            />
          </div>
          <div className="w-full sm:w-56 space-y-1.5">
            <label className="text-sm font-medium">Cohort</label>
            <Combobox
              options={[{ value: ALL, label: "All cohorts" }, ...cohorts.map((c) => ({ value: String(c.id), label: c.name }))]}
              value={cohortId != null ? String(cohortId) : ALL}
              onValueChange={(v) => { setCohortId(v === ALL ? undefined : Number(v)); setPage(1); }}
              placeholder="Cohort"
              searchPlaceholder="Search cohorts..."
            />
          </div>
          <div className="flex items-center gap-1.5">
            <Input type="date" className="h-9 w-36" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1); }} />
            <span className="text-muted-foreground text-sm">to</span>
            <Input type="date" className="h-9 w-36" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1); }} />
          </div>
        </CardContent>
      </Card>

      {data?.notice && (
        <div className="flex items-start gap-2 text-sm text-muted-foreground bg-muted/30 border rounded-md p-3 mb-4">
          <Info className="w-4 h-4 shrink-0 mt-0.5" /> {data.notice}
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the allocation-history report.</CardContent></Card>
      ) : (
        <Card className="shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/30">
                <TableRow>
                  <TableHead>Learner</TableHead>
                  <TableHead>Previous Tutor</TableHead>
                  <TableHead>New Tutor</TableHead>
                  <TableHead>Previous Cohort</TableHead>
                  <TableHead>New Cohort</TableHead>
                  <TableHead>Effective</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Changed By</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.items.length === 0 ? (
                  <TableRow><TableCell colSpan={8} className="text-center p-8 text-muted-foreground">No transfers found for these filters.</TableCell></TableRow>
                ) : (
                  data?.items.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell className="font-medium">
                        <Link href={`/learners/${row.learnerId}`}><span className="hover:underline cursor-pointer">{row.learnerName}</span></Link>
                      </TableCell>
                      <TableCell>{row.previousTutorName ?? "—"}</TableCell>
                      <TableCell>{row.newTutorName ?? "—"}</TableCell>
                      <TableCell>{row.previousCohortName ?? "—"}</TableCell>
                      <TableCell>{row.newCohortName ?? "—"}</TableCell>
                      <TableCell>
                        {format(new Date(row.effectiveDate), "d MMM yyyy")}
                        {row.effectiveTo && <span className="text-xs text-muted-foreground block">to {format(new Date(row.effectiveTo), "d MMM yyyy")}</span>}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">{row.transferReason ?? "—"}</TableCell>
                      <TableCell className="text-sm">{row.changedByName}</TableCell>
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
