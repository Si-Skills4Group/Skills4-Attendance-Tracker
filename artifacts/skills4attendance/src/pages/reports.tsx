import * as React from "react";
import { 
  useGetOrganisationReport,
  useGetProgrammeReport,
  useGetTutorReport,
  useGetCurrentUser,
  useListCohorts,
  useListTutors,
  exportReport
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Download, Loader2, BarChart3, Building2, BookOpen } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

function TotalsCards({ totals, title }: { totals: any, title?: string }) {
  if (!totals) return null;
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6 page-transition-enter">
      {title && <h3 className="col-span-full font-bold text-lg">{title}</h3>}
      <Card className="bg-primary/5 border-primary/10 shadow-sm">
        <CardContent className="p-4">
          <p className="text-sm font-medium text-muted-foreground">Attendance Rate</p>
          <div className="text-3xl font-bold font-mono text-primary mt-1">{totals.attendancePercentage.toFixed(1)}%</div>
        </CardContent>
      </Card>
      <Card className="shadow-sm">
        <CardContent className="p-4">
          <p className="text-sm font-medium text-muted-foreground">Total Hours</p>
          <div className="text-3xl font-bold font-mono text-foreground mt-1">{totals.attendedHours} <span className="text-lg text-muted-foreground">/ {totals.scheduledHours}</span></div>
        </CardContent>
      </Card>
      <Card className="shadow-sm">
        <CardContent className="p-4">
          <p className="text-sm font-medium text-muted-foreground">Absences (Auth/Unauth)</p>
          <div className="text-3xl font-bold font-mono text-amber-600 mt-1">{totals.authorisedAbsenceHours} <span className="text-lg text-muted-foreground">/</span> <span className="text-rose-600">{totals.unauthorisedAbsenceHours}</span></div>
        </CardContent>
      </Card>
      <Card className="shadow-sm">
        <CardContent className="p-4">
          <p className="text-sm font-medium text-muted-foreground">Late Occurrences</p>
          <div className="text-3xl font-bold font-mono text-foreground mt-1">{totals.lateCount}</div>
        </CardContent>
      </Card>
    </div>
  );
}

export default function ReportsPage() {
  const { toast } = useToast();
  const [dateFrom, setDateFrom] = React.useState<string>("");
  const [dateTo, setDateTo] = React.useState<string>("");
  const [isExporting, setIsExporting] = React.useState(false);

  const { data: currentUser } = useGetCurrentUser();
  const isTutor = currentUser?.role === "tutor";
  const tutorId = currentUser?.tutorId ?? undefined;

  // Org-wide report: admin only (the backend rejects this for tutors).
  const { data: orgReport, isLoading: loadOrgReport } = useGetOrganisationReport(
    { dateFrom, dateTo },
    { query: { enabled: !!currentUser && !isTutor } as any },
  );

  // Scoped report for tutors: their own cohorts only.
  const { data: tutorReport, isLoading: loadTutorReport } = useGetTutorReport(
    tutorId as number,
    { query: { enabled: !!tutorId } as any },
  );

  const loadOrg = isTutor ? loadTutorReport : loadOrgReport;
  const summaryTotals = isTutor ? tutorReport?.totals : orgReport?.totals;
  const cohortBreakdown = isTutor ? tutorReport?.cohortBreakdown : orgReport?.cohortBreakdown;

  // Programme Report: admin only (organisation-wide comparison, not relevant per-tutor).
  const { data: progReport, isLoading: loadProg } = useGetProgrammeReport(
    { dateFrom, dateTo },
    { query: { enabled: !isTutor } as any },
  );

  const handleExport = async (reportType: "organisation"|"programme"|"tutor", entityId?: number) => {
    setIsExporting(true);
    try {
      // Only send dateFrom/dateTo when actually set - the backend's date
      // query params expect a real date value, not an empty string, and
      // reject "" with a 400.
      const data = await exportReport({
        reportType,
        entityId,
        ...(dateFrom ? { dateFrom } : {}),
        ...(dateTo ? { dateTo } : {}),
      } as any);
      const blob = new Blob([data.csv], { type: 'text/csv' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = data.filename || `report-${reportType}.csv`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      a.remove();
    } catch (err) {
      toast({ title: "Export failed", variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports" }]} />
      
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Reporting & Analytics</h1>
          <p className="text-muted-foreground mt-1">
            {isTutor ? "Exportable attendance data for your cohorts." : "Exportable attendance data across the organisation."}
          </p>
        </div>
      </div>

      <Card className="mb-6 shadow-sm page-transition-enter stagger-1">
        <CardContent className="p-4 flex flex-col sm:flex-row gap-4 items-end">
          <div className="space-y-2 flex-1">
            <label className="text-sm font-medium">Date Range (From)</label>
            <Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
          </div>
          <div className="space-y-2 flex-1">
            <label className="text-sm font-medium">Date Range (To)</label>
            <Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} />
          </div>
          <Button variant="ghost" onClick={() => { setDateFrom(""); setDateTo(""); }}>Clear Filters</Button>
        </CardContent>
      </Card>

      <Tabs defaultValue="organisation" className="page-transition-enter stagger-2">
        <TabsList className={`grid w-full max-w-md mb-6 ${isTutor ? "grid-cols-1" : "grid-cols-2"}`}>
          <TabsTrigger value="organisation">{isTutor ? "My Cohorts Summary" : "Organisation Summary"}</TabsTrigger>
          {!isTutor && <TabsTrigger value="programmes">By Programme</TabsTrigger>}
        </TabsList>

        <TabsContent value="organisation" className="space-y-6">
          {loadOrg ? <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div> : (
            <>
              <div className="flex justify-between items-center">
                <h2 className="text-xl font-bold flex items-center gap-2">
                  <Building2 className="w-5 h-5" /> {isTutor ? "My Totals" : "Organisation Totals"}
                </h2>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => isTutor ? handleExport("tutor", tutorId) : handleExport("organisation")}
                  disabled={isExporting || (isTutor && !tutorId)}
                >
                  {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export CSV
                </Button>
              </div>
              
              <TotalsCards totals={summaryTotals} />
              
              <Card className="shadow-sm">
                <CardHeader>
                  <CardTitle className="text-base">Breakdown by Cohort</CardTitle>
                </CardHeader>
                <CardContent className="p-0 overflow-x-auto">
                  <Table>
                    <TableHeader className="bg-muted/30">
                      <TableRow>
                        <TableHead>Cohort</TableHead>
                        <TableHead className="text-right">Sessions</TableHead>
                        <TableHead className="text-right">Attended / Sched</TableHead>
                        <TableHead className="text-right">Auth Abs</TableHead>
                        <TableHead className="text-right">Unauth Abs</TableHead>
                        <TableHead className="text-right font-bold">Rate</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {cohortBreakdown?.map(c => (
                        <TableRow key={c.cohortId}>
                          <TableCell className="font-medium">{c.cohortName}</TableCell>
                          <TableCell className="text-right">{c.totals.sessionCount}</TableCell>
                          <TableCell className="text-right">{c.totals.attendedHours} / {c.totals.scheduledHours}</TableCell>
                          <TableCell className="text-right">{c.totals.authorisedAbsenceHours}</TableCell>
                          <TableCell className="text-right">{c.totals.unauthorisedAbsenceHours}</TableCell>
                          <TableCell className="text-right font-bold font-mono">{c.totals.attendancePercentage.toFixed(1)}%</TableCell>
                        </TableRow>
                      ))}
                      {cohortBreakdown?.length === 0 && (
                        <TableRow><TableCell colSpan={6} className="text-center p-8 text-muted-foreground">No data available for this range.</TableCell></TableRow>
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </>
          )}
        </TabsContent>

        {!isTutor && <TabsContent value="programmes" className="space-y-6">
          {loadProg ? <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div> : (
            <>
              <div className="flex justify-between items-center">
                <h2 className="text-xl font-bold flex items-center gap-2"><BookOpen className="w-5 h-5" /> Programme Comparison</h2>
                <Button variant="outline" size="sm" onClick={() => handleExport("programme")} disabled={isExporting}>
                  {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export CSV
                </Button>
              </div>
              
              <Card className="shadow-sm">
                <CardContent className="p-0 overflow-x-auto">
                  <Table>
                    <TableHeader className="bg-muted/30">
                      <TableRow>
                        <TableHead>Programme</TableHead>
                        <TableHead className="text-right">Sessions</TableHead>
                        <TableHead className="text-right">Attended / Sched</TableHead>
                        <TableHead className="text-right font-bold">Rate</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {progReport?.map((row, i) => (
                        <TableRow key={i}>
                          <TableCell className="font-medium">{row.programme}</TableCell>
                          <TableCell className="text-right">{row.totals.sessionCount}</TableCell>
                          <TableCell className="text-right">{row.totals.attendedHours} / {row.totals.scheduledHours}</TableCell>
                          <TableCell className="text-right font-bold font-mono text-primary">{row.totals.attendancePercentage.toFixed(1)}%</TableCell>
                        </TableRow>
                      ))}
                      {progReport?.length === 0 && (
                        <TableRow><TableCell colSpan={4} className="text-center p-8 text-muted-foreground">No data available for this range.</TableCell></TableRow>
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </>
          )}
        </TabsContent>}
      </Tabs>
    </div>
  );
}
