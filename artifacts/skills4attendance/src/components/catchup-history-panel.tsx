import * as React from "react";
import { useGetCatchupHistory, CatchupHistoryEntry } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { format, parseISO } from "date-fns";
import { History } from "lucide-react";

const ACTION_LABELS: Record<string, string> = {
  catchup_recorded: "Catch-up recorded",
  catchup_re_recorded: "Catch-up re-recorded",
  catchup_corrected: "Catch-up corrected",
  catchup_revoked: "Catch-up revoked",
};

const METHOD_LABELS: Record<string, string> = {
  recording_watched: "watched the recording",
  activity_completed: "completed a catch-up activity",
};

function describeEntry(entry: CatchupHistoryEntry): string {
  const newValue = entry.newValue as Record<string, unknown> | null | undefined;
  if (!newValue) return "";
  const parts: string[] = [];
  if (typeof newValue.completionDate === "string") {
    const method = typeof newValue.method === "string" ? METHOD_LABELS[newValue.method] || newValue.method : null;
    parts.push(`Completed ${format(parseISO(newValue.completionDate), "d MMM yyyy")}${method ? ` -- ${method}` : ""}`);
  }
  if (typeof newValue.note === "string" && newValue.note) {
    parts.push(`note: "${newValue.note}"`);
  }
  if (typeof newValue.reason === "string" && newValue.reason) {
    parts.push(`reason: "${newValue.reason}"`);
  }
  return parts.join(" · ");
}

/** Full audit trail for one learner/session's catch-up record -- read-only,
 * gated by the caller's own session-attendance-access (the same GET
 * endpoint every other catch-up read uses), never the admin-only generic
 * Audit Log. Visually mirrors RegisterHistoryPanel, but reads
 * useGetCatchupHistory directly (its own dedicated endpoint, not the
 * generic audit-log one register history uses) since catch-up entries have
 * a different, closed action vocabulary and structured (not JSON-string)
 * previous/new values. */
export function CatchupHistoryPanel({ sessionId, learnerId }: { sessionId: number; learnerId: number }) {
  const { data, isLoading } = useGetCatchupHistory(sessionId, learnerId);

  return (
    <Card className="shadow-sm border-0">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <History className="w-4 h-4 text-primary" /> Catch-up History
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading history...</p>
        ) : !data || data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No catch-up has ever been recorded for this learner and session.</p>
        ) : (
          <ul className="space-y-3 max-h-72 overflow-y-auto pr-1">
            {data.map((entry) => (
              <li key={entry.id} className="text-sm border-l-2 border-muted pl-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium">{ACTION_LABELS[entry.action] || entry.action}</span>
                  <span className="text-xs text-muted-foreground">
                    {format(parseISO(entry.timestamp), "MMM d, yyyy HH:mm")}
                  </span>
                  <span className="text-xs text-muted-foreground">by {entry.userName || "System"}</span>
                </div>
                {describeEntry(entry) && <p className="text-xs text-muted-foreground mt-0.5">{describeEntry(entry)}</p>}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
