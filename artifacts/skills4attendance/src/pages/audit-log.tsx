import * as React from "react";
import { useListAuditLog, useListUsers, AuditLogEntry } from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { History, ChevronLeft, ChevronRight, Eye } from "lucide-react";
import { format, parseISO } from "date-fns";

function parseJson(value: string | null): Record<string, unknown> | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === "object" && parsed !== null ? parsed : null;
  } catch {
    return null;
  }
}

// Field-level diff between previousValue/newValue JSON blobs -- only keys
// that differ (or are only present on one side) are shown, so an admin can
// see exactly what changed without eyeballing two whole JSON blobs.
function buildFieldDiff(previous: Record<string, unknown> | null, next: Record<string, unknown> | null) {
  const keys = new Set([...Object.keys(previous ?? {}), ...Object.keys(next ?? {})]);
  const rows: { field: string; before: unknown; after: unknown }[] = [];
  keys.forEach((key) => {
    const before = previous?.[key];
    const after = next?.[key];
    if (JSON.stringify(before) !== JSON.stringify(after)) {
      rows.push({ field: key, before, after });
    }
  });
  return rows;
}

function formatCellValue(value: unknown): string {
  if (value === undefined) return "—";
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const ENTITY_TYPE_OPTIONS = [
  { value: "all", label: "All Entities" },
  { value: "tutor", label: "Tutor" },
  { value: "learner", label: "Learner" },
  { value: "cohort", label: "Cohort" },
  { value: "attendance_session", label: "Session" },
  { value: "user", label: "User" },
  { value: "security", label: "Security" },
  { value: "report_export", label: "Report Export" },
  { value: "learner_import_job", label: "Learner Import" },
  { value: "tutor_import_job", label: "Tutor Import" },
  { value: "settings", label: "Settings" },
];

const ACTION_OPTIONS = [
  { value: "all", label: "All Actions" },
  { value: "create", label: "Create" },
  { value: "update", label: "Update" },
  { value: "delete_learner", label: "Delete Learner" },
  { value: "delete_cohort", label: "Delete Cohort" },
  { value: "delete_session", label: "Delete Session" },
  { value: "cancel", label: "Cancel" },
  { value: "login", label: "Login" },
  { value: "login_failed", label: "Login Failed" },
  { value: "rate_limited", label: "Rate Limited" },
  { value: "authorization_denied", label: "Authorization Denied" },
  { value: "export_report", label: "Export Report" },
];

export default function AuditLogPage() {
  const [page, setPage] = React.useState(1);
  const pageSize = 20;

  const [entityType, setEntityType] = React.useState<string>("all");
  const [actionFilter, setActionFilter] = React.useState<string>("all");
  const [userIdFilter, setUserIdFilter] = React.useState<string>("all");
  const [entityIdFilter, setEntityIdFilter] = React.useState<string>("");
  const [dateFrom, setDateFrom] = React.useState<string>("");
  const [dateTo, setDateTo] = React.useState<string>("");
  const [detailEntry, setDetailEntry] = React.useState<AuditLogEntry | null>(null);

  const { data: users = [] } = useListUsers({});

  const { data, isLoading } = useListAuditLog({
    page,
    pageSize,
    entityType: entityType !== "all" ? entityType : undefined,
    action: actionFilter !== "all" ? actionFilter : undefined,
    userId: userIdFilter !== "all" ? Number(userIdFilter) : undefined,
    entityId: entityIdFilter.trim() ? Number(entityIdFilter.trim()) : undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
  });

  const resetToFirstPage = () => setPage(1);

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Audit Log" }]} />

      <div className="mb-8 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <History className="w-8 h-8 text-primary" /> Audit Log
        </h1>
        <p className="text-muted-foreground mt-1">Review system changes and administrative actions.</p>
      </div>

      <Card className="mb-6 shadow-sm page-transition-enter stagger-1">
        <CardContent className="p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-4">
          <div>
            <Label className="text-xs text-muted-foreground" htmlFor="audit-entity-type">Entity Type</Label>
            <Select value={entityType} onValueChange={(val: string) => { setEntityType(val); resetToFirstPage(); }}>
              <SelectTrigger id="audit-entity-type" aria-label="Entity Type"><SelectValue placeholder="Entity Type" /></SelectTrigger>
              <SelectContent>
                {ENTITY_TYPE_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground" htmlFor="audit-action">Action</Label>
            <Select value={actionFilter} onValueChange={(val: string) => { setActionFilter(val); resetToFirstPage(); }}>
              <SelectTrigger id="audit-action" aria-label="Action"><SelectValue placeholder="Action" /></SelectTrigger>
              <SelectContent>
                {ACTION_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground" htmlFor="audit-user">User</Label>
            <Select value={userIdFilter} onValueChange={(val: string) => { setUserIdFilter(val); resetToFirstPage(); }}>
              <SelectTrigger id="audit-user" aria-label="User"><SelectValue placeholder="User" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Users</SelectItem>
                {users.map((u) => (
                  <SelectItem key={u.id} value={String(u.id)}>{u.firstName} {u.lastName}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground" htmlFor="audit-entity-id">Entity ID</Label>
            <Input
              id="audit-entity-id"
              type="number"
              placeholder="e.g. 42"
              value={entityIdFilter}
              onChange={(e) => { setEntityIdFilter(e.target.value); resetToFirstPage(); }}
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground" htmlFor="audit-date-from">From</Label>
            <Input
              id="audit-date-from"
              type="date"
              value={dateFrom}
              onChange={(e) => { setDateFrom(e.target.value); resetToFirstPage(); }}
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground" htmlFor="audit-date-to">To</Label>
            <Input
              id="audit-date-to"
              type="date"
              value={dateTo}
              onChange={(e) => { setDateTo(e.target.value); resetToFirstPage(); }}
            />
          </div>
        </CardContent>
      </Card>

      <Card className="shadow-sm overflow-hidden page-transition-enter stagger-2">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader className="bg-muted/30">
              <TableRow>
                <TableHead className="w-[180px]">Timestamp</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Entity</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead className="text-right">Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow><TableCell colSpan={6} className="h-32 text-center">Loading...</TableCell></TableRow>
              ) : data?.items.length === 0 ? (
                <TableRow><TableCell colSpan={6} className="h-32 text-center text-muted-foreground">No logs found matching filters.</TableCell></TableRow>
              ) : (
                data?.items.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                      {format(parseISO(log.timestamp), "MMM d, yyyy HH:mm:ss")}
                    </TableCell>
                    <TableCell className="font-medium text-sm">{log.userName || "System"}</TableCell>
                    <TableCell>
                      <span className={`text-xs px-2 py-1 rounded font-mono ${
                        log.action.startsWith('create') ? 'bg-emerald-100 text-emerald-800' :
                        log.action.startsWith('delete') || log.action === 'authorization_denied' || log.action === 'login_failed' || log.action === 'rate_limited' ? 'bg-rose-100 text-rose-800' :
                        'bg-blue-100 text-blue-800'
                      }`}>
                        {log.action.toUpperCase()}
                      </span>
                    </TableCell>
                    <TableCell className="text-sm">
                      <span className="capitalize">{log.entityType.replace(/_/g, ' ')}</span>
                      {log.entityId && <span className="text-muted-foreground ml-1">#{log.entityId}</span>}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {log.action === 'authorization_denied' || log.action === 'login_failed' || log.action === 'rate_limited' ? "Denied" : "Success"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" onClick={() => setDetailEntry(log)}>
                        <Eye className="w-4 h-4 mr-1" /> View
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
        {data && data.total > 0 && (
          <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/10">
            <div className="text-sm text-muted-foreground">
              Showing {((page - 1) * pageSize) + 1} to {Math.min(page * pageSize, data.total)} of {data.total}
            </div>
            <div className="flex items-center space-x-2">
              <Button variant="outline" size="sm" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}><ChevronLeft className="w-4 h-4" /></Button>
              <div className="text-sm font-medium px-2">{page}</div>
              <Button variant="outline" size="sm" onClick={() => setPage(p => p + 1)} disabled={page * pageSize >= data.total}><ChevronRight className="w-4 h-4" /></Button>
            </div>
          </div>
        )}
      </Card>

      <Dialog open={!!detailEntry} onOpenChange={(o) => { if (!o) setDetailEntry(null); }}>
        <DialogContent className="sm:max-w-[550px]">
          <DialogHeader>
            <DialogTitle>Audit Entry #{detailEntry?.id}</DialogTitle>
            <DialogDescription>
              {detailEntry && format(parseISO(detailEntry.timestamp), "MMM d, yyyy HH:mm:ss")} by {detailEntry?.userName || "System"}
            </DialogDescription>
          </DialogHeader>
          {detailEntry && (() => {
            const previous = parseJson(detailEntry.previousValue);
            const next = parseJson(detailEntry.newValue);
            const diff = buildFieldDiff(previous, next);
            return (
              <div className="space-y-4 py-2 max-h-[60vh] overflow-auto">
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div><span className="text-muted-foreground">Action:</span> <span className="font-mono">{detailEntry.action}</span></div>
                  <div><span className="text-muted-foreground">Entity:</span> {detailEntry.entityType}{detailEntry.entityId ? ` #${detailEntry.entityId}` : ""}</div>
                  <div className="col-span-2">
                    <span className="text-muted-foreground">Correlation ID:</span>{" "}
                    <span className="font-mono text-xs break-all">{detailEntry.correlationId || "—"}</span>
                  </div>
                  <div className="col-span-2">
                    <span className="text-muted-foreground">IP Address:</span>{" "}
                    <span className="font-mono text-xs">{detailEntry.ipAddress || "—"}</span>
                  </div>
                </div>
                <div>
                  <h4 className="text-sm font-semibold mb-2">Changes</h4>
                  {diff.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {previous || next ? "No field-level differences to show." : "No before/after values recorded for this action."}
                    </p>
                  ) : (
                    <div className="border rounded-md divide-y">
                      {diff.map((row) => (
                        <div key={row.field} className="p-2 text-xs grid grid-cols-[100px_1fr]">
                          <span className="font-medium text-muted-foreground">{row.field}</span>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-rose-600 dark:text-rose-500 line-through break-all">{formatCellValue(row.before)}</span>
                            <span className="text-emerald-600 dark:text-emerald-500 break-all">{formatCellValue(row.after)}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })()}
        </DialogContent>
      </Dialog>
    </div>
  );
}
