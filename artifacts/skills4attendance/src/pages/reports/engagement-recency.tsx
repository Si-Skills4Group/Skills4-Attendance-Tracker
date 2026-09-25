import * as React from "react";
import { Link } from "wouter";
import {
  useGetCurrentUser,
  useListTutors,
  getListTutorsQueryKey,
  useListEngagementRecency,
  exportEngagementRecency,
  EngagementRecencyItem,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Combobox } from "@/components/ui/combobox";
import { useDebounce } from "@/hooks/use-debounce";
import { downloadCsv } from "@/lib/csv-download";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Download, History } from "lucide-react";
import { format } from "date-fns";

const ALL_TUTORS = "__all__";
const PAGE_SIZE = 25;

const SOURCE_LABELS: Record<string, string> = {
  attendance: "Live attendance",
  bud_submission: "Bud submission",
  bud_completed_activity: "Bud activity",
  catchup: "Catch-up",
};

const BUD_STATUS_LABELS: Record<string, string> = {
  not_authorized: "Not available to you",
  not_linked: "No Bud link",
  missing_source: "Needs review",
  needs_review: "Needs review",
  resolved: "Resolved (unverified dates)",
};

export default function EngagementRecencyReportPage() {
  const { toast } = useToast();
  const { data: user } = useGetCurrentUser();
  const isTutor = user?.role === "tutor";
  const tutorsListParams = { active: true };
  const { data: tutors = [] } = useListTutors(tutorsListParams, { query: { enabled: !isTutor, queryKey: getListTutorsQueryKey(tutorsListParams) } });

  const [tutorId, setTutorId] = React.useState<number | undefined>(undefined);
  const [programme, setProgramme] = React.useState("");
  const debouncedProgramme = useDebounce(programme, 300);
  const [search, setSearch] = React.useState("");
  const debouncedSearch = useDebounce(search, 300);
  const [noEngagementOnly, setNoEngagementOnly] = React.useState(false);
  const [minDaysSinceInput, setMinDaysSinceInput] = React.useState("");
  const debouncedMinDays = useDebounce(minDaysSinceInput, 300);
  const [page, setPage] = React.useState(1);
  const [isExporting, setIsExporting] = React.useState(false);

  React.useEffect(() => {
    setPage(1);
  }, [tutorId, debouncedProgramme, debouncedSearch, noEngagementOnly, debouncedMinDays]);

  const minDaysSince = debouncedMinDays.trim() !== "" && !Number.isNaN(Number(debouncedMinDays)) ? Number(debouncedMinDays) : undefined;

  const queryParams = {
    tutorId,
    programme: debouncedProgramme || undefined,
    search: debouncedSearch || undefined,
    noEngagementOnly,
    minDaysSince,
    page,
    pageSize: PAGE_SIZE,
  };
  const { data, isLoading, isError } = useListEngagementRecency(queryParams);

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const csv = await exportEngagementRecency({ tutorId, programme: debouncedProgramme || undefined, search: debouncedSearch || undefined, noEngagementOnly, minDaysSince });
      downloadCsv(csv, "engagement-recency-report.csv");
    } catch (err: any) {
      toast({ title: "Export failed", description: err?.message, variant: "destructive" });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports", href: "/reports" }, { label: "Engagement Recency" }]} />

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-4 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <History className="w-7 h-7 text-primary" /> Learner Engagement Recency
          </h1>
          <p className="text-muted-foreground mt-1">
            Latest Recorded Engagement combines live attendance and confirmed catch-up only. Bud submission/completed-
            activity dates are shown for reference -- their upstream meaning is unverified, so they never count towards
            this figure.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting}>
          {isExporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />} Export CSV
        </Button>
      </div>

      {data?.scopeLabel && <p className="text-xs text-muted-foreground mb-4">{data.scopeLabel}</p>}

      <Card className="shadow-sm mb-4 page-transition-enter stagger-1">
        <CardContent className="p-4 flex flex-wrap items-end gap-3">
          {!isTutor && (
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Tutor</Label>
              <Combobox
                className="w-48"
                options={[{ value: ALL_TUTORS, label: "All tutors" }, ...tutors.map((t) => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` }))]}
                value={tutorId != null ? String(tutorId) : ALL_TUTORS}
                onValueChange={(v) => setTutorId(v === ALL_TUTORS ? undefined : Number(v))}
                placeholder="Tutor"
                searchPlaceholder="Search tutors..."
              />
            </div>
          )}
          <div className="space-y-1">
            <Label htmlFor="engagement-programme" className="text-xs text-muted-foreground">Programme</Label>
            <Input id="engagement-programme" className="w-40 h-9" placeholder="Programme" value={programme} onChange={(e) => setProgramme(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="engagement-search" className="text-xs text-muted-foreground">Search learner</Label>
            <Input id="engagement-search" className="w-48 h-9" placeholder="Name or reference..." value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="engagement-min-days" className="text-xs text-muted-foreground">At least N days since engagement</Label>
            <Input
              id="engagement-min-days"
              type="number"
              min={0}
              className="w-40 h-9"
              placeholder="e.g. 30"
              value={minDaysSinceInput}
              onChange={(e) => setMinDaysSinceInput(e.target.value)}
              disabled={noEngagementOnly}
            />
          </div>
          <div className="flex items-center gap-2 pb-1.5">
            <Switch id="no-engagement-only" checked={noEngagementOnly} onCheckedChange={setNoEngagementOnly} />
            <Label htmlFor="no-engagement-only" className="text-sm">No recorded engagement only</Label>
          </div>
        </CardContent>
      </Card>

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the engagement recency report.</CardContent></Card>
      ) : (
        <Card className="shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/30">
                <TableRow>
                  <TableHead>Learner</TableHead>
                  <TableHead>Tutor</TableHead>
                  <TableHead>Programme</TableHead>
                  <TableHead>Latest Recorded Engagement</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead className="text-right">Days Since</TableHead>
                  <TableHead>Bud</TableHead>
                  <TableHead className="text-right">Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.items.length === 0 ? (
                  <TableRow><TableCell colSpan={8} className="text-center p-8 text-muted-foreground">No learners match these filters.</TableCell></TableRow>
                ) : (
                  data?.items.map((row) => <EngagementRow key={row.id} row={row} />)
                )}
              </TableBody>
            </Table>
          </div>
          {data && data.total > 0 && (
            <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/10">
              <div className="text-sm text-muted-foreground">Showing {(page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, data.total)} of {data.total}</div>
              <div className="flex items-center space-x-2">
                <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>Previous</Button>
                <div className="text-sm font-medium px-2">{page}</div>
                <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * PAGE_SIZE >= data.total}>Next</Button>
              </div>
            </div>
          )}
          {data?.calculatedAt && (
            <div className="border-t px-4 py-2 text-xs text-muted-foreground bg-muted/5">
              Calculated {format(new Date(data.calculatedAt), "d MMM yyyy HH:mm")} -- a current view of available evidence, not a reconstruction of what was known on a historical date.
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

function EngagementRow({ row }: { row: EngagementRecencyItem }) {
  return (
    <TableRow>
      <TableCell className="font-medium">{row.learnerName} <span className="text-muted-foreground text-xs">{row.learnerRef}</span></TableCell>
      <TableCell>{row.tutorName ?? "—"}</TableCell>
      <TableCell>{row.programme}</TableCell>
      <TableCell>
        {row.latestEngagementDate ? (
          <span className="font-medium">{format(new Date(row.latestEngagementDate), "d MMM yyyy")}</span>
        ) : (
          <Badge variant="outline" className="bg-slate-100 text-slate-800 border-slate-200 bg-opacity-50 font-medium px-2 py-0">No recorded engagement</Badge>
        )}
        {row.dataQualityIssues.length > 0 && (
          <p className="text-xs text-rose-600 mt-0.5">Data quality issue flagged</p>
        )}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">{row.sources.map((s) => SOURCE_LABELS[s]).join(", ") || "—"}</TableCell>
      <TableCell className="text-right font-mono">{row.daysSinceEngagement ?? "—"}</TableCell>
      <TableCell>
        <Badge
          variant="outline"
          title={row.sourceLimitations.join(" ")}
          className={`bg-opacity-50 font-medium px-2 py-0 ${
            row.budStatus === "resolved"
              ? "bg-emerald-100 text-emerald-800 border-emerald-200"
              : row.budStatus === "not_authorized"
              ? "bg-slate-100 text-slate-800 border-slate-200"
              : "bg-amber-100 text-amber-800 border-amber-200"
          }`}
        >
          {BUD_STATUS_LABELS[row.budStatus]}
        </Badge>
      </TableCell>
      <TableCell className="text-right">
        <Link href={`/reports/learners?learnerId=${row.id}`}>
          <Button variant="ghost" size="sm">View</Button>
        </Link>
      </TableCell>
    </TableRow>
  );
}
