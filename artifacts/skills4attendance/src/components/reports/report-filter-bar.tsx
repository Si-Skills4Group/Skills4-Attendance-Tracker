import * as React from "react";
import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DashboardDateFilter, DateFilterValue } from "@/components/dashboard/dashboard-date-filter";
import { useDebounce } from "@/hooks/use-debounce";
import { X } from "lucide-react";

const ALL = "__all__";

export interface ReportFilters extends DateFilterValue {
  tutorId?: number;
  cohortId?: number;
  programme?: string;
  level?: string;
  employer?: string;
}

interface TutorOption {
  id: number;
  firstName: string;
  lastName: string;
}

interface CohortOption {
  id: number;
  name: string;
}

interface ReportFilterBarProps {
  value: ReportFilters;
  onChange: (next: ReportFilters) => void;
  tutors?: TutorOption[];
  cohorts?: CohortOption[];
  /** Tutors are always scoped server-side to their own data -- hide the
   * tutor picker entirely for them rather than showing a filter that can
   * only ever resolve to themselves. */
  showTutor?: boolean;
  showCohort?: boolean;
  showProgrammeLevelEmployer?: boolean;
}

/** Shared filter chrome for the Phase 9 list-style reports (absence,
 * lateness, register completion, attendance hours) -- date range/preset,
 * tutor/cohort pickers (searchable, never a plain <select> loading every
 * row), optional programme/level/employer text filters, a Clear action,
 * and a summary of which filters are currently active. */
export function ReportFilterBar({
  value,
  onChange,
  tutors = [],
  cohorts = [],
  showTutor = true,
  showCohort = true,
  showProgrammeLevelEmployer = false,
}: ReportFilterBarProps) {
  const [programmeInput, setProgrammeInput] = React.useState(value.programme ?? "");
  const [levelInput, setLevelInput] = React.useState(value.level ?? "");
  const [employerInput, setEmployerInput] = React.useState(value.employer ?? "");
  const debouncedProgramme = useDebounce(programmeInput, 300);
  const debouncedLevel = useDebounce(levelInput, 300);
  const debouncedEmployer = useDebounce(employerInput, 300);

  const valueRef = React.useRef(value);
  valueRef.current = value;

  React.useEffect(() => {
    onChange({ ...valueRef.current, programme: debouncedProgramme || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedProgramme]);
  React.useEffect(() => {
    onChange({ ...valueRef.current, level: debouncedLevel || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedLevel]);
  React.useEffect(() => {
    onChange({ ...valueRef.current, employer: debouncedEmployer || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedEmployer]);

  const activeChips: { key: string; label: string; onClear: () => void }[] = [];
  if (showTutor && value.tutorId != null) {
    const tutor = tutors.find((t) => t.id === value.tutorId);
    activeChips.push({
      key: "tutor",
      label: `Tutor: ${tutor ? `${tutor.firstName} ${tutor.lastName}` : value.tutorId}`,
      onClear: () => onChange({ ...value, tutorId: undefined }),
    });
  }
  if (showCohort && value.cohortId != null) {
    const cohort = cohorts.find((c) => c.id === value.cohortId);
    activeChips.push({
      key: "cohort",
      label: `Cohort: ${cohort ? cohort.name : value.cohortId}`,
      onClear: () => onChange({ ...value, cohortId: undefined }),
    });
  }
  if (value.programme) {
    activeChips.push({ key: "programme", label: `Programme: ${value.programme}`, onClear: () => { setProgrammeInput(""); onChange({ ...value, programme: undefined }); } });
  }
  if (value.level) {
    activeChips.push({ key: "level", label: `Level: ${value.level}`, onClear: () => { setLevelInput(""); onChange({ ...value, level: undefined }); } });
  }
  if (value.employer) {
    activeChips.push({ key: "employer", label: `Employer: ${value.employer}`, onClear: () => { setEmployerInput(""); onChange({ ...value, employer: undefined }); } });
  }

  const clearAll = () => {
    setProgrammeInput("");
    setLevelInput("");
    setEmployerInput("");
    onChange({ period: "current_month", dateFrom: undefined, dateTo: undefined });
  };

  return (
    <div className="space-y-3 mb-6">
      <DashboardDateFilter value={value} onChange={(next) => onChange({ ...value, ...next })} />
      <div className="flex flex-wrap items-center gap-3">
        {showTutor && (
          <Combobox
            className="w-48"
            options={[{ value: ALL, label: "All tutors" }, ...tutors.map((t) => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` }))]}
            value={value.tutorId != null ? String(value.tutorId) : ALL}
            onValueChange={(v) => onChange({ ...value, tutorId: v === ALL ? undefined : Number(v) })}
            placeholder="Tutor"
            searchPlaceholder="Search tutors..."
          />
        )}
        {showCohort && (
          <Combobox
            className="w-48"
            options={[{ value: ALL, label: "All cohorts" }, ...cohorts.map((c) => ({ value: String(c.id), label: c.name }))]}
            value={value.cohortId != null ? String(value.cohortId) : ALL}
            onValueChange={(v) => onChange({ ...value, cohortId: v === ALL ? undefined : Number(v) })}
            placeholder="Cohort"
            searchPlaceholder="Search cohorts..."
          />
        )}
        {showProgrammeLevelEmployer && (
          <>
            <Input className="w-36 h-9" placeholder="Programme" value={programmeInput} onChange={(e) => setProgrammeInput(e.target.value)} />
            <Input className="w-28 h-9" placeholder="Level" value={levelInput} onChange={(e) => setLevelInput(e.target.value)} />
            <Input className="w-36 h-9" placeholder="Employer" value={employerInput} onChange={(e) => setEmployerInput(e.target.value)} />
          </>
        )}
        {activeChips.length > 0 && (
          <Button variant="ghost" size="sm" onClick={clearAll}>Clear filters</Button>
        )}
      </div>
      {activeChips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {activeChips.map((chip) => (
            <Badge key={chip.key} variant="outline" className="gap-1 pr-1">
              {chip.label}
              <button type="button" onClick={chip.onClear} className="ml-1 rounded-full hover:bg-muted p-0.5" aria-label={`Remove ${chip.label} filter`}>
                <X className="w-3 h-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
