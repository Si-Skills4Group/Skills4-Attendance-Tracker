import * as React from "react";
import { PeriodParamParameter } from "@workspace/api-client-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const PRESETS: { value: PeriodParamParameter; label: string }[] = [
  { value: "current_week", label: "This Week" },
  { value: "current_month", label: "This Month" },
  { value: "previous_month", label: "Previous Month" },
  { value: "last_30_days", label: "Last 30 Days" },
];

export type DateFilterValue = {
  period: PeriodParamParameter;
  dateFrom?: string;
  dateTo?: string;
};

/** Preset buttons for the common cases the brief asks for, plus the
 * existing two-date-input custom-range pattern already used on
 * reports.tsx/cohort-sessions.tsx -- reused rather than reinvented. */
export function DashboardDateFilter({ value, onChange }: { value: DateFilterValue; onChange: (next: DateFilterValue) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 bg-card p-2 rounded-lg border shadow-sm">
      {PRESETS.map((preset) => (
        <Button
          key={preset.value}
          size="sm"
          variant={value.period === preset.value ? "default" : "outline"}
          onClick={() => onChange({ period: preset.value, dateFrom: undefined, dateTo: undefined })}
        >
          {preset.label}
        </Button>
      ))}
      <div className="flex items-center gap-1.5 ml-2 pl-2 border-l">
        <Input
          type="date"
          className="h-8 w-36"
          value={value.dateFrom ?? ""}
          onChange={(e) => onChange({ period: "custom", dateFrom: e.target.value, dateTo: value.dateTo })}
        />
        <span className="text-muted-foreground text-sm">to</span>
        <Input
          type="date"
          className="h-8 w-36"
          value={value.dateTo ?? ""}
          onChange={(e) => onChange({ period: "custom", dateFrom: value.dateFrom, dateTo: e.target.value })}
        />
      </div>
    </div>
  );
}
