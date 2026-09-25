import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useGetCurrentUser,
  useListCohorts,
  useListCatchupFollowUp,
  getListCatchupFollowUpQueryKey,
  CatchupFollowUpItem,
  CatchupMethod,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Combobox } from "@/components/ui/combobox";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { AttendanceStatusBadge } from "@/components/status-badges";
import { CatchupDialog } from "@/components/catchup-dialog";
import { CatchupRevokeDialog } from "@/components/catchup-revoke-dialog";
import { CatchupHistoryPanel } from "@/components/catchup-history-panel";
import { useDebounce } from "@/hooks/use-debounce";
import { Loader2, ChevronLeft, ChevronRight, ClipboardCheck, History, Ban, Undo2 } from "lucide-react";
import { startOfWeek, endOfWeek, addWeeks, format } from "date-fns";

const ALL_COHORTS = "__all__";
const METHOD_LABELS: Record<CatchupMethod, string> = {
  recording_watched: "Recording watched",
  activity_completed: "Activity completed",
};
const PAGE_SIZE = 25;

type StatusTab = "outstanding" | "completed" | "all";

interface RecordTarget {
  sessionId: number;
  learnerId: number;
  learnerName: string;
  sessionDate: string;
}
interface CorrectTarget extends RecordTarget {
  existing: { completionDate: string; method: CatchupMethod; note: string };
}

/** Weekly absence-follow-up view (Stage 3 catch-up feature). One row per
 * absent learner/session pair for the selected week -- the week always
 * refers to the ORIGINAL session date, so a catch-up recorded later still
 * appears here against that original absence. Reuses the same
 * session-attendance-access rules as every other attendance screen: a
 * tutor sees only their own cohorts (or a session they're covering), an
 * admin sees everything. */
export default function CatchupFollowUpPage() {
  const queryClient = useQueryClient();
  const { data: user } = useGetCurrentUser();
  const isTutor = user?.role === "tutor";
  const { data: cohorts = [] } = useListCohorts({ active: true });

  const [weekAnchor, setWeekAnchor] = React.useState(() => new Date());
  const weekStart = startOfWeek(weekAnchor, { weekStartsOn: 1 });
  const weekEnd = endOfWeek(weekAnchor, { weekStartsOn: 1 });
  const weekStartIso = format(weekStart, "yyyy-MM-dd");
  const weekEndIso = format(weekEnd, "yyyy-MM-dd");
  const currentWeekStart = startOfWeek(new Date(), { weekStartsOn: 1 });
  const canGoForward = weekStart.getTime() < currentWeekStart.getTime();

  const [statusTab, setStatusTab] = React.useState<StatusTab>("outstanding");
  const [cohortId, setCohortId] = React.useState<number | undefined>(undefined);
  const [search, setSearch] = React.useState("");
  const debouncedSearch = useDebounce(search, 300);
  const [page, setPage] = React.useState(1);

  // Any filter change re-queries the full authorised dataset from page 1 --
  // never re-filters a previously-fetched page client-side, so a match
  // beyond the current page is always reachable.
  React.useEffect(() => {
    setPage(1);
  }, [weekStartIso, weekEndIso, statusTab, cohortId, debouncedSearch]);

  const queryParams = {
    weekStart: weekStartIso,
    weekEnd: weekEndIso,
    cohortId,
    status: statusTab === "all" ? undefined : statusTab,
    search: debouncedSearch.trim() || undefined,
    page,
    pageSize: PAGE_SIZE,
  };
  const { data, isLoading, isError } = useListCatchupFollowUp(queryParams);
  const items = data?.items ?? [];

  const invalidate = () => queryClient.invalidateQueries({ queryKey: getListCatchupFollowUpQueryKey(queryParams) });

  const [recordTarget, setRecordTarget] = React.useState<RecordTarget | null>(null);
  const [correctTarget, setCorrectTarget] = React.useState<CorrectTarget | null>(null);
  const [revokeTarget, setRevokeTarget] = React.useState<RecordTarget | null>(null);
  const [historyTarget, setHistoryTarget] = React.useState<RecordTarget | null>(null);

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Absence Follow-up" }]} />

      <div className="mb-6 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <ClipboardCheck className="w-7 h-7 text-primary" /> Absence Follow-up
        </h1>
        <p className="text-muted-foreground mt-1">
          Confirm when an absent learner has watched the recording or completed a catch-up activity. The original
          absence is always preserved -- this only records what happened afterwards.
        </p>
      </div>

      <Card className="shadow-sm mb-4 page-transition-enter stagger-1">
        <CardContent className="p-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" aria-label="Previous week" onClick={() => setWeekAnchor((d) => addWeeks(d, -1))}>
              <ChevronLeft className="w-4 h-4" />
            </Button>
            <div className="text-sm font-medium px-2 min-w-[180px] text-center">
              {format(weekStart, "d MMM")} -- {format(weekEnd, "d MMM yyyy")}
            </div>
            <Button variant="outline" size="sm" aria-label="Next week" onClick={() => setWeekAnchor((d) => addWeeks(d, 1))} disabled={!canGoForward}>
              <ChevronRight className="w-4 h-4" />
            </Button>
            {weekStart.getTime() !== currentWeekStart.getTime() && (
              <Button variant="ghost" size="sm" onClick={() => setWeekAnchor(new Date())}>This week</Button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!isTutor && (
              <Combobox
                className="w-48"
                options={[{ value: ALL_COHORTS, label: "All cohorts" }, ...cohorts.map((c) => ({ value: String(c.id), label: c.name }))]}
                value={cohortId != null ? String(cohortId) : ALL_COHORTS}
                onValueChange={(v) => setCohortId(v === ALL_COHORTS ? undefined : Number(v))}
                placeholder="Cohort"
                searchPlaceholder="Search cohorts..."
              />
            )}
            <Input className="w-48 h-9" placeholder="Search learner..." value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
        </CardContent>
      </Card>

      <Tabs value={statusTab} onValueChange={(v) => setStatusTab(v as StatusTab)} className="mb-4 page-transition-enter stagger-1">
        <TabsList>
          <TabsTrigger value="outstanding">Outstanding</TabsTrigger>
          <TabsTrigger value="completed">Completed</TabsTrigger>
          <TabsTrigger value="all">All</TabsTrigger>
        </TabsList>
      </Tabs>

      {isLoading ? (
        <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : isError ? (
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the follow-up list.</CardContent></Card>
      ) : (
        <Card className="shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/30">
                <TableRow>
                  <TableHead>Learner</TableHead>
                  <TableHead>Cohort</TableHead>
                  <TableHead>Session</TableHead>
                  <TableHead>Original Status</TableHead>
                  <TableHead>Catch-up</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.length === 0 ? (
                  <TableRow><TableCell colSpan={6} className="text-center p-8 text-muted-foreground">No absences match these filters for this week.</TableCell></TableRow>
                ) : (
                  items.map((row) => <FollowUpRow
                    key={`${row.sessionId}-${row.learnerId}`}
                    row={row}
                    onRecord={() => setRecordTarget({ sessionId: row.sessionId, learnerId: row.learnerId, learnerName: row.learnerName, sessionDate: row.sessionDate })}
                    onCorrect={() => row.completionDate && row.method && setCorrectTarget({
                      sessionId: row.sessionId, learnerId: row.learnerId, learnerName: row.learnerName, sessionDate: row.sessionDate,
                      existing: { completionDate: row.completionDate, method: row.method, note: row.note ?? "" },
                    })}
                    onRevoke={() => setRevokeTarget({ sessionId: row.sessionId, learnerId: row.learnerId, learnerName: row.learnerName, sessionDate: row.sessionDate })}
                    onHistory={() => setHistoryTarget({ sessionId: row.sessionId, learnerId: row.learnerId, learnerName: row.learnerName, sessionDate: row.sessionDate })}
                  />)
                )}
              </TableBody>
            </Table>
          </div>
          {data && data.total > 0 && (
            <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/10">
              <div className="text-sm text-muted-foreground">Showing {(page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, data.total)} of {data.total}</div>
              <div className="flex items-center space-x-2">
                <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}><ChevronLeft className="w-4 h-4" /></Button>
                <div className="text-sm font-medium px-2">{page}</div>
                <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * PAGE_SIZE >= data.total}><ChevronRight className="w-4 h-4" /></Button>
              </div>
            </div>
          )}
        </Card>
      )}

      {recordTarget && (
        <CatchupDialog
          open={recordTarget !== null}
          onOpenChange={(o) => !o && setRecordTarget(null)}
          mode="record"
          sessionId={recordTarget.sessionId}
          learnerId={recordTarget.learnerId}
          learnerName={recordTarget.learnerName}
          sessionDate={recordTarget.sessionDate}
          onSuccess={invalidate}
        />
      )}
      {correctTarget && (
        <CatchupDialog
          open={correctTarget !== null}
          onOpenChange={(o) => !o && setCorrectTarget(null)}
          mode="correct"
          sessionId={correctTarget.sessionId}
          learnerId={correctTarget.learnerId}
          learnerName={correctTarget.learnerName}
          sessionDate={correctTarget.sessionDate}
          existing={correctTarget.existing}
          onSuccess={invalidate}
        />
      )}
      {revokeTarget && (
        <CatchupRevokeDialog
          open={revokeTarget !== null}
          onOpenChange={(o) => !o && setRevokeTarget(null)}
          sessionId={revokeTarget.sessionId}
          learnerId={revokeTarget.learnerId}
          learnerName={revokeTarget.learnerName}
          onSuccess={invalidate}
        />
      )}
      <Dialog open={historyTarget !== null} onOpenChange={(o) => !o && setHistoryTarget(null)}>
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle>Catch-up History</DialogTitle>
            <DialogDescription>{historyTarget?.learnerName}</DialogDescription>
          </DialogHeader>
          {historyTarget && <CatchupHistoryPanel sessionId={historyTarget.sessionId} learnerId={historyTarget.learnerId} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function FollowUpRow({
  row, onRecord, onCorrect, onRevoke, onHistory,
}: {
  row: CatchupFollowUpItem;
  onRecord: () => void;
  onCorrect: () => void;
  onRevoke: () => void;
  onHistory: () => void;
}) {
  const hasHistory = row.catchupId != null;

  return (
    <TableRow>
      <TableCell className="font-medium">{row.learnerName}</TableCell>
      <TableCell>{row.cohortName}</TableCell>
      <TableCell>{format(new Date(row.sessionDate), "d MMM yyyy")}{row.sessionTitle ? <span className="text-muted-foreground text-xs block">{row.sessionTitle}</span> : null}</TableCell>
      <TableCell><AttendanceStatusBadge status={row.originalStatus} /></TableCell>
      <TableCell>
        {row.effective ? (
          <div>
            <Badge variant="outline" className="bg-emerald-100 text-emerald-800 border-emerald-200 bg-opacity-50 font-medium px-2 py-0">Completed</Badge>
            {row.completionDate && (
              <p className="text-xs text-muted-foreground mt-1">
                {format(new Date(row.completionDate), "d MMM yyyy")}
                {row.method && ` -- ${METHOD_LABELS[row.method]}`}
              </p>
            )}
          </div>
        ) : row.catchupStatus === "revoked" ? (
          <Badge variant="outline" className="bg-rose-100 text-rose-800 border-rose-200 bg-opacity-50 font-medium px-2 py-0">Revoked</Badge>
        ) : (
          <Badge variant="outline" className="bg-amber-100 text-amber-800 border-amber-200 bg-opacity-50 font-medium px-2 py-0">Outstanding</Badge>
        )}
      </TableCell>
      <TableCell className="text-right space-x-1 whitespace-nowrap">
        {row.effective ? (
          <>
            <Button variant="outline" size="sm" onClick={onCorrect}>Correct</Button>
            <Button variant="outline" size="sm" aria-label="Revoke catch-up" onClick={onRevoke}><Ban className="w-3.5 h-3.5" /></Button>
          </>
        ) : (
          <Button variant="outline" size="sm" onClick={onRecord}>
            {row.catchupStatus === "revoked" ? <Undo2 className="w-3.5 h-3.5 mr-1.5" /> : null}
            Record catch-up
          </Button>
        )}
        {hasHistory && (
          <Button variant="ghost" size="sm" onClick={onHistory} aria-label="View catch-up history"><History className="w-3.5 h-3.5" /></Button>
        )}
      </TableCell>
    </TableRow>
  );
}
