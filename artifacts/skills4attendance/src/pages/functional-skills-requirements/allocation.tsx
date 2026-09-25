import * as React from "react";
import { useLocation } from "wouter";
import {
  useGetFsRequirementAllocation,
  type FsRequirementAllocationItem,
  type GetFsRequirementAllocationSubject,
} from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useDebounce } from "@/hooks/use-debounce";
import { format } from "date-fns";
import { Loader2, ChevronLeft, ChevronRight, Upload, AlertTriangle, GraduationCap } from "lucide-react";

const allValue = "__all__";
const PAGE_SIZE = 25;

const SUBJECT_LABELS: Record<GetFsRequirementAllocationSubject, string> = { math: "Maths", english: "English", both: "Both" };

function requiredSubjectsLabel(maths: boolean, english: boolean): string {
  if (maths && english) return "Maths and English";
  if (maths) return "Maths";
  if (english) return "English";
  return "None";
}

export default function FsRequirementAllocationPage() {
  const [, setLocation] = useLocation();

  const [search, setSearch] = React.useState("");
  const debouncedSearch = useDebounce(search, 300);
  const [subject, setSubject] = React.useState<string>(allValue);
  const [status, setStatus] = React.useState<string>("active");
  const [missingOnly, setMissingOnly] = React.useState(false);
  const [page, setPage] = React.useState(1);

  React.useEffect(() => {
    setPage(1);
  }, [debouncedSearch, subject, status, missingOnly]);

  const params = {
    search: debouncedSearch.trim() || undefined,
    subject: subject !== allValue ? (subject as GetFsRequirementAllocationSubject) : undefined,
    status: status !== allValue ? (status as "active" | "withdrawn" | "completed" | "paused") : undefined,
    missingOnly: missingOnly || undefined,
    page,
    pageSize: PAGE_SIZE,
  };
  const { data, isLoading, isError } = useGetFsRequirementAllocation(params);
  const items = data?.items ?? [];

  return (
    <TooltipProvider>
      <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
        <Breadcrumbs items={[{ label: "Functional Skills Requirements" }, { label: "Allocation" }]} />

        <div className="flex items-center justify-between gap-4 mb-6 page-transition-enter">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
              <GraduationCap className="w-7 h-7 text-primary" /> Functional Skills Allocation
            </h1>
            <p className="text-muted-foreground mt-1 max-w-2xl">
              Compares each learner's uploaded Functional Skills requirement against their active secondary cohort
              enrollments. An existing secondary enrollment with no uploaded requirement is labelled "Requirement not
              recorded" -- it is not treated as unfunded or inappropriate.
            </p>
          </div>
          <Button onClick={() => setLocation("/functional-skills-requirements/import")} className="hover-elevate shadow-sm shrink-0">
            <Upload className="w-4 h-4 mr-2" /> Upload Requirements
          </Button>
        </div>

        <Card className="shadow-sm mb-4 page-transition-enter stagger-1">
          <CardContent className="p-4 flex flex-wrap items-center gap-3">
            <Input className="w-56 h-9" placeholder="Search learner or reference..." value={search} onChange={(e) => setSearch(e.target.value)} />
            <Select value={subject} onValueChange={setSubject}>
              <SelectTrigger className="w-44 h-9 bg-background"><SelectValue placeholder="Required subject" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={allValue}>All required subjects</SelectItem>
                <SelectItem value="math">Maths</SelectItem>
                <SelectItem value="english">English</SelectItem>
                <SelectItem value="both">Both</SelectItem>
              </SelectContent>
            </Select>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-40 h-9 bg-background"><SelectValue placeholder="Learner status" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="active">Active learners</SelectItem>
                <SelectItem value={allValue}>All statuses</SelectItem>
                <SelectItem value="withdrawn">Withdrawn</SelectItem>
                <SelectItem value="completed">Completed</SelectItem>
                <SelectItem value="paused">Paused</SelectItem>
              </SelectContent>
            </Select>
            <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer select-none ml-auto">
              <Checkbox checked={missingOnly} onCheckedChange={(v) => setMissingOnly(v === true)} />
              Missing subject coverage only
            </label>
          </CardContent>
        </Card>

        {isLoading ? (
          <div className="flex justify-center p-12"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
        ) : isError ? (
          <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Could not load the allocation view.</CardContent></Card>
        ) : (
          <Card className="shadow-sm overflow-hidden page-transition-enter stagger-1">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader className="bg-muted/30">
                  <TableRow>
                    <TableHead>Learner</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Required Subject(s)</TableHead>
                    <TableHead>Active Secondary Cohorts</TableHead>
                    <TableHead>Coverage</TableHead>
                    <TableHead>Requirement Source</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.length === 0 ? (
                    <TableRow><TableCell colSpan={6} className="text-center p-8 text-muted-foreground">No learners match these filters.</TableCell></TableRow>
                  ) : (
                    items.map((row) => <AllocationRow key={row.id} row={row} />)
                  )}
                </TableBody>
              </Table>
            </div>
            {data && data.total > 0 && (
              <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/10">
                <div className="text-sm text-muted-foreground">
                  Showing {(page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, data.total)} of {data.total}
                </div>
                <div className="flex items-center space-x-2">
                  <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}><ChevronLeft className="w-4 h-4" /></Button>
                  <div className="text-sm font-medium px-2">{page}</div>
                  <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * PAGE_SIZE >= data.total}><ChevronRight className="w-4 h-4" /></Button>
                </div>
              </div>
            )}
          </Card>
        )}
      </div>
    </TooltipProvider>
  );
}

function AllocationRow({ row }: { row: FsRequirementAllocationItem }) {
  const hasAnyMissing = row.missingSubjects.length > 0 || row.missingTutorSubjects.length > 0;

  return (
    <TableRow>
      <TableCell>
        <div className="font-medium">{row.learnerName}</div>
        <div className="text-xs text-muted-foreground font-mono">{row.learnerRef}</div>
      </TableCell>
      <TableCell>
        <Badge variant="outline" className={row.status === "active" ? "bg-emerald-100 text-emerald-800 border-emerald-200" : "bg-slate-100 text-slate-700 border-slate-200"}>
          {row.status}
        </Badge>
      </TableCell>
      <TableCell>
        {row.requirementRecorded ? (
          <span>{requiredSubjectsLabel(row.maths, row.english)}</span>
        ) : (
          <span className="text-muted-foreground italic">No requirement uploaded</span>
        )}
      </TableCell>
      <TableCell>
        {row.activeSecondaryCohorts.length === 0 ? (
          <span className="text-muted-foreground">None</span>
        ) : (
          <div className="space-y-1">
            {row.activeSecondaryCohorts.map((c) => (
              <div key={c.cohortId} className="text-sm">
                {c.cohortName}
                <span className="text-xs text-muted-foreground ml-1">({SUBJECT_LABELS[c.subject]})</span>
                {c.tutorName ? (
                  c.tutorActive === false && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <AlertTriangle className="inline w-3.5 h-3.5 ml-1.5 text-amber-600 dark:text-amber-400 align-text-bottom" />
                      </TooltipTrigger>
                      <TooltipContent>Tutor {c.tutorName} is inactive</TooltipContent>
                    </Tooltip>
                  )
                ) : (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <AlertTriangle className="inline w-3.5 h-3.5 ml-1.5 text-amber-600 dark:text-amber-400 align-text-bottom" />
                    </TooltipTrigger>
                    <TooltipContent>No tutor assigned</TooltipContent>
                  </Tooltip>
                )}
              </div>
            ))}
          </div>
        )}
      </TableCell>
      <TableCell>
        {!row.requirementRecorded ? (
          <span className="text-muted-foreground">--</span>
        ) : !hasAnyMissing ? (
          <Badge variant="outline" className="bg-emerald-100 text-emerald-800 border-emerald-200">Covered</Badge>
        ) : (
          <div className="space-y-1">
            {row.missingSubjects.length > 0 && (
              <Badge variant="outline" className="bg-rose-100 text-rose-800 border-rose-200 block w-fit">
                Missing cohort: {row.missingSubjects.map((s) => SUBJECT_LABELS[s]).join(", ")}
              </Badge>
            )}
            {row.missingTutorSubjects.length > 0 && (
              <Badge variant="outline" className="bg-amber-100 text-amber-800 border-amber-200 block w-fit">
                Missing/inactive tutor: {row.missingTutorSubjects.map((s) => SUBJECT_LABELS[s]).join(", ")}
              </Badge>
            )}
          </div>
        )}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {row.requirementRecorded ? (
          <>
            Manual upload
            {row.updatedAt && <div>{format(new Date(row.updatedAt), "d MMM yyyy")}</div>}
          </>
        ) : (
          "--"
        )}
      </TableCell>
    </TableRow>
  );
}
