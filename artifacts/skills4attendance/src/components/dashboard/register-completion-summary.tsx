import * as React from "react";
import { RegisterCompletionSummary } from "@workspace/api-client-react";
import { Badge } from "@/components/ui/badge";

/** Deliberately separate from attendance percentage -- this describes
 * whether registers have been filled in, not whether learners attended. */
export function RegisterCompletionSummaryView({ completion }: { completion: RegisterCompletionSummary }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-muted-foreground">Register completion</span>
        <span className="text-lg font-bold font-mono">
          {completion.completionPercentage != null ? `${completion.completionPercentage.toFixed(0)}%` : "—"}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5 text-xs">
        <Badge variant="outline" className="bg-slate-50 text-slate-700 border-slate-200">Not started: {completion.notStarted}</Badge>
        <Badge variant="outline" className="bg-amber-50 text-amber-700 border-amber-200">In progress: {completion.inProgress}</Badge>
        <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200">Completed: {completion.completed}</Badge>
        <Badge variant="outline" className="bg-violet-50 text-violet-700 border-violet-200">Locked: {completion.locked}</Badge>
        {completion.outstanding > 0 && (
          <Badge variant="outline" className="bg-rose-50 text-rose-700 border-rose-200">{completion.outstanding} outstanding</Badge>
        )}
      </div>
    </div>
  );
}
