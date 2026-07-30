import * as React from "react";
import { useListAuditLog } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { format, parseISO } from "date-fns";
import { History } from "lucide-react";

const ACTION_LABELS: Record<string, string> = {
  create: "Session created",
  update: "Session details edited",
  cancel: "Session cancelled",
  duplicate_override: "Duplicate/date-range override",
  refresh_register: "Expected learners refreshed",
  save_register: "Attendance saved",
  complete_register: "Register completed",
  lock_register: "Register locked",
  unlock_register: "Register unlocked",
  cover_tutor_assigned: "Cover Tutor assigned",
  cover_tutor_changed: "Cover Tutor changed",
  cover_tutor_correction: "Cover Tutor corrected (completed register)",
  cover_tutor_removed: "Cover Tutor removed",
};

function describeEntry(action: string, newValue: string | null): string {
  if (action !== "save_register" || !newValue) return "";
  try {
    const parsed = JSON.parse(newValue);
    const changedCount = Array.isArray(parsed.changes) ? parsed.changes.length : 0;
    const createdCount = Array.isArray(parsed.created) ? parsed.created.length : 0;
    const parts: string[] = [];
    if (createdCount > 0) parts.push(`${createdCount} recorded for the first time`);
    if (changedCount > 0) parts.push(`${changedCount} changed`);
    if (parsed.changeReason) parts.push(`reason: "${parsed.changeReason}"`);
    return parts.join(" · ");
  } catch {
    return "";
  }
}

/** Admin-only register history/audit panel -- reuses the existing
 * audit-log endpoint (same permission model as the standalone Audit Log
 * page) scoped to this one session via entityType/entityId, rather than a
 * dedicated endpoint. */
export function RegisterHistoryPanel({ sessionId }: { sessionId: number }) {
  const { data, isLoading } = useListAuditLog({
    entityType: "attendance_session",
    entityId: sessionId,
    pageSize: 50,
  });

  return (
    <Card className="shadow-sm">
      <CardHeader className="border-b bg-muted/10 pb-4">
        <CardTitle className="text-base flex items-center gap-2">
          <History className="w-4 h-4 text-primary" /> Register History
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-4">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading history...</p>
        ) : !data || data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No history recorded for this session yet.</p>
        ) : (
          <ul className="space-y-3">
            {data.items.map((entry) => (
              <li key={entry.id} className="text-sm border-l-2 border-muted pl-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium">{ACTION_LABELS[entry.action] || entry.action}</span>
                  <span className="text-xs text-muted-foreground">
                    {format(parseISO(entry.timestamp), "MMM d, yyyy HH:mm")}
                  </span>
                  <span className="text-xs text-muted-foreground">by {entry.userName || "System"}</span>
                </div>
                {describeEntry(entry.action, entry.newValue) && (
                  <p className="text-xs text-muted-foreground mt-0.5">{describeEntry(entry.action, entry.newValue)}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
