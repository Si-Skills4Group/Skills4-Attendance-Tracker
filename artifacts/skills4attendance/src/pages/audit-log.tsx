import * as React from "react";
import { useListAuditLog } from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { History, Search, ChevronLeft, ChevronRight } from "lucide-react";
import { format, parseISO } from "date-fns";

export default function AuditLogPage() {
  const [page, setPage] = React.useState(1);
  const pageSize = 20;
  
  const [entityType, setEntityType] = React.useState<string>("all");
  const [actionFilter, setActionFilter] = React.useState<string>("all");

  const { data, isLoading } = useListAuditLog({
    page,
    pageSize,
    entityType: entityType !== "all" ? entityType : undefined,
    action: actionFilter !== "all" ? actionFilter : undefined
  });

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
        <CardContent className="p-4 flex flex-col sm:flex-row gap-4">
          <div className="w-full sm:w-[200px]">
            <Select value={entityType} onValueChange={(val: any) => { setEntityType(val); setPage(1); }}>
              <SelectTrigger><SelectValue placeholder="Entity Type" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Entities</SelectItem>
                <SelectItem value="tutor">Tutor</SelectItem>
                <SelectItem value="learner">Learner</SelectItem>
                <SelectItem value="cohort">Cohort</SelectItem>
                <SelectItem value="attendance_session">Session</SelectItem>
                <SelectItem value="allocation">Allocation</SelectItem>
                <SelectItem value="settings">Settings</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="w-full sm:w-[200px]">
            <Select value={actionFilter} onValueChange={(val: any) => { setActionFilter(val); setPage(1); }}>
              <SelectTrigger><SelectValue placeholder="Action" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Actions</SelectItem>
                <SelectItem value="create">Create</SelectItem>
                <SelectItem value="update">Update</SelectItem>
                <SelectItem value="delete">Delete</SelectItem>
                <SelectItem value="login">Login</SelectItem>
              </SelectContent>
            </Select>
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
                <TableHead>Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow><TableCell colSpan={5} className="h-32 text-center">Loading...</TableCell></TableRow>
              ) : data?.items.length === 0 ? (
                <TableRow><TableCell colSpan={5} className="h-32 text-center text-muted-foreground">No logs found matching filters.</TableCell></TableRow>
              ) : (
                data?.items.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                      {format(parseISO(log.timestamp), "MMM d, yyyy HH:mm:ss")}
                    </TableCell>
                    <TableCell className="font-medium text-sm">{log.userName || "System"}</TableCell>
                    <TableCell>
                      <span className={`text-xs px-2 py-1 rounded font-mono ${
                        log.action === 'create' ? 'bg-emerald-100 text-emerald-800' :
                        log.action === 'delete' ? 'bg-rose-100 text-rose-800' :
                        'bg-blue-100 text-blue-800'
                      }`}>
                        {log.action.toUpperCase()}
                      </span>
                    </TableCell>
                    <TableCell className="text-sm">
                      <span className="capitalize">{log.entityType.replace('_', ' ')}</span>
                      {log.entityId && <span className="text-muted-foreground ml-1">#{log.entityId}</span>}
                    </TableCell>
                    <TableCell className="text-xs font-mono max-w-xs truncate text-muted-foreground" title={log.newValue || log.previousValue || ""}>
                      {log.newValue ? "Changed values" : log.previousValue ? "Deleted values" : "-"}
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
    </div>
  );
}
