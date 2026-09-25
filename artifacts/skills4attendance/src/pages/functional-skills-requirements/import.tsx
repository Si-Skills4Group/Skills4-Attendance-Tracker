import * as React from "react";
import {
  useGetFsRequirementImportTemplate,
  useUploadFsRequirementImport,
  useGetFsRequirementImportJob,
  useListFsRequirementImportJobRows,
  useConfirmFsRequirementImportJob,
  useCancelFsRequirementImportJob,
  getGetFsRequirementImportTemplateQueryKey,
  getGetFsRequirementImportJobQueryKey,
  getListFsRequirementImportJobRowsQueryKey,
  type FsRequirementImportJobRow,
  type FsRequirementRowOutcome,
} from "@workspace/api-client-react";
import { useLocation, useSearchParams } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { downloadCsv } from "@/lib/csv-download";
import { SummaryStat } from "@/components/import-summary-stat";
import {
  ArrowLeft, Download, Upload, Loader2, FileText, CheckCircle2, AlertCircle, AlertTriangle,
  ChevronLeft, ChevronRight, RotateCcw,
} from "lucide-react";

const allValue = "__all__";

const AIM_LABELS: Record<string, string> = { math: "Maths", english: "English", both: "Maths and English" };

function requirementLabel(maths: boolean | null | undefined, english: boolean | null | undefined, status: string | null | undefined): string {
  if (status !== "recorded") return "None";
  if (maths && english) return AIM_LABELS.both;
  if (maths) return AIM_LABELS.math;
  if (english) return AIM_LABELS.english;
  return "None";
}

const OUTCOME_META: Record<FsRequirementRowOutcome, { label: string; className: string }> = {
  new: { label: "New", className: "bg-emerald-100 text-emerald-800 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-400" },
  changed: { label: "Changed", className: "bg-blue-100 text-blue-800 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400" },
  unchanged: { label: "Unchanged", className: "bg-slate-100 text-slate-800 border-slate-200 dark:bg-slate-900/30 dark:text-slate-400" },
  warning: { label: "Warning", className: "bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-900/30 dark:text-amber-400" },
  error: { label: "Error", className: "bg-rose-100 text-rose-800 border-rose-200 dark:bg-rose-900/30 dark:text-rose-400" },
};

function OutcomeBadge({ outcome }: { outcome: FsRequirementRowOutcome }) {
  const meta = OUTCOME_META[outcome];
  return <Badge variant="outline" className={`${meta.className} text-[10px] font-semibold`}>{meta.label}</Badge>;
}

export default function FsRequirementImportPage() {
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const jobIdParam = searchParams.get("job");
  const jobId = jobIdParam ? Number(jobIdParam) : null;

  const setJobId = (id: number | null) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (id == null) next.delete("job");
      else next.set("job", String(id));
      return next;
    });
  };

  const [page, setPage] = React.useState(1);
  const [outcomeFilter, setOutcomeFilter] = React.useState<string>(allValue);
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [dragActive, setDragActive] = React.useState(false);
  const fileInputId = "fs-requirement-import-file-input";
  const pageSize = 25;

  const templateQuery = useGetFsRequirementImportTemplate({ query: { enabled: false, queryKey: getGetFsRequirementImportTemplateQueryKey() } });
  const handleDownloadTemplate = async () => {
    const result = await templateQuery.refetch();
    if (result.data) downloadCsv(result.data.csv, result.data.filename ?? "functional-skills-requirement-template.csv");
    else toast({ title: "Could not download template", variant: "destructive" });
  };

  const uploadMutation = useUploadFsRequirementImport();
  const handleFile = (file: File) => {
    if (!file.name.toLowerCase().endsWith(".csv")) {
      toast({ title: "Please select a .csv file", variant: "destructive" });
      return;
    }
    uploadMutation.mutate(
      { data: { file } },
      {
        onSuccess: (job) => {
          setPage(1);
          setOutcomeFilter(allValue);
          setJobId(job.id);
          toast({ title: "File uploaded", description: `${job.totalRows} row(s) classified.` });
        },
        onError: (err) => toast({ title: "Upload failed", description: getErrorMessage(err), variant: "destructive" }),
      },
    );
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = "";
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragActive(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  const jobQuery = useGetFsRequirementImportJob(jobId ?? 0, {
    query: {
      queryKey: getGetFsRequirementImportJobQueryKey(jobId ?? 0),
      enabled: jobId != null,
      refetchInterval: (query) => (query.state.data?.status === "importing" ? 1000 : false),
    },
  });
  const job = jobQuery.data;

  const rowsParams = { page, pageSize, outcome: outcomeFilter !== allValue ? (outcomeFilter as FsRequirementRowOutcome) : undefined };
  const rowsQuery = useListFsRequirementImportJobRows(jobId ?? 0, rowsParams, {
    query: { queryKey: getListFsRequirementImportJobRowsQueryKey(jobId ?? 0, rowsParams), enabled: jobId != null && job?.status === "ready" },
  });

  const confirmMutation = useConfirmFsRequirementImportJob();
  const handleConfirm = () => {
    if (!jobId) return;
    confirmMutation.mutate(
      { jobId },
      {
        onSuccess: () => {
          setConfirmOpen(false);
          toast({ title: "Import complete" });
          jobQuery.refetch();
        },
        onError: (err) => {
          setConfirmOpen(false);
          toast({ title: "Import failed", description: getErrorMessage(err), variant: "destructive" });
          jobQuery.refetch();
        },
      },
    );
  };

  const cancelMutation = useCancelFsRequirementImportJob();
  const handleStartOver = () => {
    if (jobId && job?.status === "ready") cancelMutation.mutate({ jobId });
    setJobId(null);
    setPage(1);
    setOutcomeFilter(allValue);
  };

  const status = job?.status;
  const rows = rowsQuery.data?.items ?? [];
  const totalRows = rowsQuery.data?.total ?? 0;
  const hasErrors = (job?.errorCount ?? 0) > 0;
  const result = job?.resultSummary as { applied?: number } | null | undefined;

  return (
    <div className="p-6 md:p-8 max-w-6xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Functional Skills Requirements" }, { label: "Upload" }]} />

      <div className="flex items-center gap-4 mb-8 page-transition-enter">
        <Button variant="outline" size="icon" onClick={() => setLocation("/functional-skills-requirements/allocation")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Upload Functional Skills Requirements</h1>
          <p className="text-muted-foreground mt-1">
            Interim admin-uploaded source of subject requirements while ILR aim data is unavailable -- never described as
            verified open ILR aims or funding eligibility.
          </p>
        </div>
      </div>

      {jobId == null && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-3xl page-transition-enter stagger-1">
          <Card className="shadow-sm">
            <CardHeader className="bg-muted/10 border-b pb-4">
              <CardTitle className="text-base flex items-center gap-2"><FileText className="w-4 h-4 text-primary" /> Step 1: Template</CardTitle>
              <CardDescription>learnerID,AIM -- AIM is Math, English or Both.</CardDescription>
            </CardHeader>
            <CardContent className="pt-6">
              <Button variant="outline" className="w-full" onClick={handleDownloadTemplate} disabled={templateQuery.isFetching}>
                {templateQuery.isFetching ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />}
                Download Template
              </Button>
            </CardContent>
          </Card>

          <Card className="shadow-sm">
            <CardHeader className="bg-muted/10 border-b pb-4">
              <CardTitle className="text-base flex items-center gap-2"><Upload className="w-4 h-4 text-primary" /> Step 2: Upload</CardTitle>
              <CardDescription>Drag and drop, or click to browse.</CardDescription>
            </CardHeader>
            <CardContent className="pt-6">
              <div
                onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
                onDragLeave={() => setDragActive(false)}
                onDrop={handleDrop}
                className={`border-2 border-dashed rounded-lg p-6 text-center transition-colors ${dragActive ? "border-primary bg-primary/5" : "border-muted-foreground/20 hover:bg-muted/10"}`}
              >
                <input type="file" accept=".csv" className="hidden" id={fileInputId} onChange={handleFileInputChange} disabled={uploadMutation.isPending} />
                <label htmlFor={fileInputId} className="cursor-pointer flex flex-col items-center">
                  {uploadMutation.isPending ? <Loader2 className="w-8 h-8 mb-2 text-primary animate-spin" /> : <FileText className="w-8 h-8 mb-2 text-muted-foreground/40" />}
                  <span className="text-sm font-medium text-foreground">{uploadMutation.isPending ? "Uploading & classifying..." : "Select or drop a CSV file"}</span>
                  <span className="text-xs text-muted-foreground mt-1">Up to 5MB, 5,000 rows</span>
                </label>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {jobId != null && jobQuery.isError && (
        <Card className="border-dashed border-destructive/40 bg-destructive/5 max-w-2xl">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <AlertCircle className="w-10 h-10 text-destructive/60 mb-3" />
            <h3 className="text-lg font-semibold text-foreground mb-1">Import job not found</h3>
            <p className="text-sm text-muted-foreground max-w-sm mb-4">This import may have expired or been removed. Start a new upload.</p>
            <Button onClick={handleStartOver}>Start New Import</Button>
          </CardContent>
        </Card>
      )}

      {job && status === "ready" && (
        <div className="space-y-6 page-transition-enter stagger-1">
          <Card className="shadow-sm">
            <CardContent className="p-5 flex flex-wrap items-center gap-6">
              <SummaryStat label="Total" value={job.totalRows} />
              <SummaryStat label="New" value={job.newCount} className="text-emerald-600 dark:text-emerald-400" />
              <SummaryStat label="Changed" value={job.changedCount} className="text-blue-600 dark:text-blue-400" />
              <SummaryStat label="Unchanged" value={job.unchangedCount} className="text-slate-600 dark:text-slate-400" />
              <SummaryStat label="Warnings" value={job.warningCount} className="text-amber-600 dark:text-amber-400" />
              <SummaryStat label="Errors" value={job.errorCount} className="text-rose-600 dark:text-rose-400" />
              <div className="ml-auto flex items-center gap-2">
                <Button variant="outline" onClick={handleStartOver} disabled={cancelMutation.isPending}>
                  <RotateCcw className="w-4 h-4 mr-2" /> Start Over
                </Button>
                <Button onClick={() => setConfirmOpen(true)} disabled={hasErrors} className="hover-elevate shadow-sm" title={hasErrors ? "Fix all errors and re-upload before committing" : undefined}>
                  Confirm Import
                </Button>
              </div>
            </CardContent>
          </Card>

          {hasErrors && (
            <div className="flex items-center gap-2 text-sm text-rose-800 dark:text-rose-400 bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-800 rounded-md px-4 py-3">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              This file has row-level errors -- commit is blocked until they are fixed and re-uploaded. Warnings alone do not block committing.
            </div>
          )}

          <Card className="shadow-sm">
            <CardHeader className="bg-muted/10 border-b pb-4 flex-row items-center justify-between space-y-0">
              <CardTitle className="text-base">Row preview</CardTitle>
              <Select value={outcomeFilter} onValueChange={(v) => { setOutcomeFilter(v); setPage(1); }}>
                <SelectTrigger className="w-52 h-9 bg-background"><SelectValue placeholder="Filter by outcome" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={allValue}>All outcomes</SelectItem>
                  {Object.entries(OUTCOME_META).map(([key, meta]) => <SelectItem key={key} value={key}>{meta.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader className="bg-muted/30">
                    <TableRow>
                      <TableHead className="w-12 text-center">Row</TableHead>
                      <TableHead>learnerID</TableHead>
                      <TableHead>Matched Learner</TableHead>
                      <TableHead>Existing</TableHead>
                      <TableHead>Proposed</TableHead>
                      <TableHead>Outcome</TableHead>
                      <TableHead>Notes</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rowsQuery.isLoading ? (
                      <TableRow><TableCell colSpan={7} className="h-24 text-center"><Loader2 className="w-5 h-5 animate-spin mx-auto text-muted-foreground" /></TableCell></TableRow>
                    ) : rows.length === 0 ? (
                      <TableRow><TableCell colSpan={7} className="h-24 text-center text-muted-foreground">No rows match this filter.</TableCell></TableRow>
                    ) : (
                      rows.map((row: FsRequirementImportJobRow) => (
                        <TableRow key={row.id}>
                          <TableCell className="text-center text-muted-foreground font-mono text-xs">{row.rowNumber}</TableCell>
                          <TableCell className="font-mono text-xs">{row.rawData.learnerID}</TableCell>
                          <TableCell className="text-sm">
                            {row.matchedLearnerName ? (
                              <span>
                                {row.matchedLearnerName}
                                {row.matchedLearnerStatus && row.matchedLearnerStatus !== "active" && (
                                  <span className="ml-1.5 text-xs text-amber-600 dark:text-amber-400 capitalize">({row.matchedLearnerStatus})</span>
                                )}
                              </span>
                            ) : (
                              <span className="text-muted-foreground">Not matched</span>
                            )}
                          </TableCell>
                          <TableCell className="text-sm">{requirementLabel(row.existingMaths, row.existingEnglish, row.existingStatus)}</TableCell>
                          <TableCell className="text-sm">
                            {row.outcome === "error" ? "-" : requirementLabel(row.proposedMaths, row.proposedEnglish, "recorded")}
                          </TableCell>
                          <TableCell><OutcomeBadge outcome={row.outcome} /></TableCell>
                          <TableCell className="text-xs max-w-[260px]">
                            <span className={row.errors.length > 0 ? "text-rose-600 dark:text-rose-400" : "text-amber-600 dark:text-amber-400"}>
                              {[...row.errors, ...row.warnings].join("; ") || "-"}
                            </span>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>

              {totalRows > 0 && (
                <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/10">
                  <div className="text-sm text-muted-foreground">
                    Showing <span className="font-medium text-foreground">{(page - 1) * pageSize + 1}</span> to{" "}
                    <span className="font-medium text-foreground">{Math.min(page * pageSize, totalRows)}</span> of{" "}
                    <span className="font-medium text-foreground">{totalRows}</span> rows
                  </div>
                  <div className="flex items-center space-x-2">
                    <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}><ChevronLeft className="w-4 h-4" /></Button>
                    <div className="text-sm font-medium px-2">{page}</div>
                    <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * pageSize >= totalRows}><ChevronRight className="w-4 h-4" /></Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {job && status === "importing" && (
        <Card className="shadow-sm max-w-2xl">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <Loader2 className="w-8 h-8 animate-spin text-primary mb-3" />
            <h3 className="text-lg font-semibold text-foreground mb-1">Import in progress</h3>
            <p className="text-sm text-muted-foreground">This will only take a moment.</p>
          </CardContent>
        </Card>
      )}

      {job && status === "completed" && (
        <Card className="shadow-sm max-w-2xl page-transition-enter">
          <CardHeader className="bg-muted/10 border-b pb-4">
            <CardTitle className="text-base flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-600" /> Import complete</CardTitle>
          </CardHeader>
          <CardContent className="pt-6 space-y-6">
            <div className="bg-emerald-50 dark:bg-emerald-950/20 border border-emerald-100 dark:border-emerald-900 p-4 rounded-lg text-center">
              <div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">{result?.applied ?? 0}</div>
              <div className="text-xs font-medium text-emerald-800 dark:text-emerald-500 uppercase mt-1">Learners updated</div>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={handleStartOver}>Upload Another File</Button>
              <Button className="flex-1" onClick={() => setLocation("/functional-skills-requirements/allocation")}>View Allocation</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {job && status === "cancelled" && (
        <Card className="shadow-sm max-w-2xl">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <AlertCircle className="w-10 h-10 text-muted-foreground/60 mb-3" />
            <h3 className="text-lg font-semibold text-foreground mb-1">Import cancelled</h3>
            <p className="text-sm text-muted-foreground max-w-sm mb-4">No requirements were created or changed.</p>
            <Button onClick={handleStartOver}>Start New Import</Button>
          </CardContent>
        </Card>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Confirm Functional Skills requirement upload</DialogTitle>
            <DialogDescription>
              {(job?.newCount ?? 0) + (job?.changedCount ?? 0)} learner requirement(s) will be created or changed.
              Unchanged and warning (collapsed duplicate) rows are not applied again. This action is applied
              immediately and cannot be undone from this page -- use Clear Requirement afterwards if needed.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-2">
            <Button variant="outline" onClick={() => setConfirmOpen(false)} disabled={confirmMutation.isPending}>Go Back</Button>
            <Button onClick={handleConfirm} disabled={confirmMutation.isPending} className="hover-elevate shadow-sm">
              {confirmMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Confirm Import
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
