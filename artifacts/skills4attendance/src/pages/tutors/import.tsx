import * as React from "react";
import {
  useGetTutorImportTemplate,
  useUploadTutorImport,
  useGetTutorImportJob,
  useListTutorImportJobRows,
  useResolveTutorImportJobRow,
  useConfirmTutorImportJob,
  useCancelTutorImportJob,
  useDownloadTutorImportErrors,
  getGetTutorImportTemplateQueryKey,
  getGetTutorImportJobQueryKey,
  getListTutorImportJobRowsQueryKey,
  getDownloadTutorImportErrorsQueryKey,
  type TutorImportJobRow,
  type TutorImportRowClassification,
} from "@workspace/api-client-react";
import { useLocation, useSearchParams } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { downloadCsv } from "@/lib/csv-download";
import { SummaryStat } from "@/components/import-summary-stat";
import {
  ArrowLeft,
  Download,
  Upload,
  Loader2,
  FileText,
  CheckCircle2,
  AlertCircle,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  RotateCcw,
} from "lucide-react";

const allValue = "__all__";

const CLASSIFICATION_META: Record<
  TutorImportRowClassification,
  { label: string; className: string }
> = {
  new: { label: "New", className: "bg-emerald-100 text-emerald-800 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-400" },
  exact_existing: { label: "Existing match", className: "bg-blue-100 text-blue-800 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400" },
  probable_duplicate: { label: "Probable duplicate", className: "bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-900/30 dark:text-amber-400" },
  identifier_conflict: { label: "Identifier conflict", className: "bg-rose-100 text-rose-800 border-rose-200 dark:bg-rose-900/30 dark:text-rose-400" },
  invalid: { label: "Invalid", className: "bg-rose-100 text-rose-800 border-rose-200 dark:bg-rose-900/30 dark:text-rose-400" },
};

function ClassificationBadge({ classification }: { classification: TutorImportRowClassification }) {
  const meta = CLASSIFICATION_META[classification];
  return <Badge variant="outline" className={`${meta.className} text-[10px] font-semibold`}>{meta.label}</Badge>;
}

function isResolvableRow(row: TutorImportJobRow): boolean {
  return row.proposedAction !== "blocked" && row.classification !== "new";
}

export default function TutorImportPage() {
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
  const [classificationFilter, setClassificationFilter] = React.useState<string>(allValue);
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [dragActive, setDragActive] = React.useState(false);
  const fileInputId = "tutor-import-file-input";
  const pageSize = 25;

  // ---------------------------------------------------------------------
  // Template download
  // ---------------------------------------------------------------------
  const templateQuery = useGetTutorImportTemplate({
    query: { enabled: false, queryKey: getGetTutorImportTemplateQueryKey() },
  });
  const handleDownloadTemplate = async () => {
    const result = await templateQuery.refetch();
    if (result.data) downloadCsv(result.data.csv, result.data.filename ?? "tutor-import-template.csv");
    else toast({ title: "Could not download template", variant: "destructive" });
  };

  // ---------------------------------------------------------------------
  // Upload
  // ---------------------------------------------------------------------
  const uploadMutation = useUploadTutorImport();
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
          setClassificationFilter(allValue);
          setJobId(job.id);
          toast({ title: "File uploaded", description: `${job.totalRows} row(s) classified.` });
        },
        onError: (err) => {
          toast({ title: "Upload failed", description: getErrorMessage(err), variant: "destructive" });
        },
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

  // ---------------------------------------------------------------------
  // Job status (polled while an import is actively being applied)
  // ---------------------------------------------------------------------
  const jobQuery = useGetTutorImportJob(jobId ?? 0, {
    query: {
      queryKey: getGetTutorImportJobQueryKey(jobId ?? 0),
      enabled: jobId != null,
      refetchInterval: (query) => (query.state.data?.status === "importing" ? 1000 : false),
    },
  });
  const job = jobQuery.data;

  // ---------------------------------------------------------------------
  // Row listing (preview & resolve step only)
  // ---------------------------------------------------------------------
  const rowsParams = {
    page,
    pageSize,
    classification: classificationFilter !== allValue ? (classificationFilter as TutorImportRowClassification) : undefined,
  };
  const rowsQuery = useListTutorImportJobRows(jobId ?? 0, rowsParams, {
    query: {
      queryKey: getListTutorImportJobRowsQueryKey(jobId ?? 0, rowsParams),
      enabled: jobId != null && job?.status === "ready",
    },
  });

  const resolveMutation = useResolveTutorImportJobRow();
  const handleResolve = (row: TutorImportJobRow, resolution: "skip" | "update") => {
    if (!jobId) return;
    resolveMutation.mutate(
      { jobId, rowId: row.id, data: { resolution } },
      {
        onError: (err) => {
          toast({ title: "Could not update row", description: getErrorMessage(err), variant: "destructive" });
        },
        onSuccess: () => rowsQuery.refetch(),
      },
    );
  };

  // ---------------------------------------------------------------------
  // Confirm
  // ---------------------------------------------------------------------
  const confirmMutation = useConfirmTutorImportJob();
  const handleConfirm = () => {
    if (!jobId) return;
    confirmMutation.mutate(
      { jobId },
      {
        onSuccess: (result) => {
          setConfirmOpen(false);
          toast({
            title: "Import complete",
            description: `${result.created} created, ${result.updated} updated, ${result.skipped} skipped.`,
          });
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

  // ---------------------------------------------------------------------
  // Cancel / start over
  // ---------------------------------------------------------------------
  const cancelMutation = useCancelTutorImportJob();
  const handleStartOver = () => {
    if (jobId && job?.status === "ready") {
      cancelMutation.mutate({ jobId });
    }
    setJobId(null);
    setPage(1);
    setClassificationFilter(allValue);
  };

  // ---------------------------------------------------------------------
  // Error report
  // ---------------------------------------------------------------------
  const errorsQuery = useDownloadTutorImportErrors(jobId ?? 0, {
    query: { enabled: false, queryKey: getDownloadTutorImportErrorsQueryKey(jobId ?? 0) },
  });
  const handleDownloadErrors = async () => {
    const result = await errorsQuery.refetch();
    if (result.data) downloadCsv(result.data.csv, result.data.filename ?? `tutor-import-${jobId}-errors.csv`);
    else toast({ title: "Could not download error report", variant: "destructive" });
  };

  const status = job?.status;
  const rows = rowsQuery.data?.items ?? [];
  const totalRows = rowsQuery.data?.total ?? 0;
  const problemRowCount = job ? job.invalidCount + job.identifierConflictCount : 0;

  return (
    <div className="p-6 md:p-8 max-w-6xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Tutors", href: "/tutors" }, { label: "Import from CSV" }]} />

      <div className="flex items-center gap-4 mb-8 page-transition-enter">
        <Button variant="outline" size="icon" onClick={() => setLocation("/tutors")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Import Tutors</h1>
          <p className="text-muted-foreground mt-1">Bulk upload tutors from a CSV file.</p>
        </div>
      </div>

      {/* ---------------- Upload step ---------------- */}
      {jobId == null && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-3xl page-transition-enter stagger-1">
          <Card className="shadow-sm">
            <CardHeader className="bg-muted/10 border-b pb-4">
              <CardTitle className="text-base flex items-center gap-2">
                <FileText className="w-4 h-4 text-primary" /> Step 1: Template
              </CardTitle>
              <CardDescription>Download the exact CSV format required. Do not change the column headers.</CardDescription>
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
              <CardTitle className="text-base flex items-center gap-2">
                <Upload className="w-4 h-4 text-primary" /> Step 2: Upload
              </CardTitle>
              <CardDescription>Drag and drop, or click to browse.</CardDescription>
            </CardHeader>
            <CardContent className="pt-6">
              <div
                onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
                onDragLeave={() => setDragActive(false)}
                onDrop={handleDrop}
                className={`border-2 border-dashed rounded-lg p-6 text-center transition-colors ${
                  dragActive ? "border-primary bg-primary/5" : "border-muted-foreground/20 hover:bg-muted/10"
                }`}
              >
                <input
                  type="file"
                  accept=".csv"
                  className="hidden"
                  id={fileInputId}
                  onChange={handleFileInputChange}
                  disabled={uploadMutation.isPending}
                />
                <label htmlFor={fileInputId} className="cursor-pointer flex flex-col items-center">
                  {uploadMutation.isPending ? (
                    <Loader2 className="w-8 h-8 mb-2 text-primary animate-spin" />
                  ) : (
                    <FileText className="w-8 h-8 mb-2 text-muted-foreground/40" />
                  )}
                  <span className="text-sm font-medium text-foreground">
                    {uploadMutation.isPending ? "Uploading & classifying..." : "Select or drop a CSV file"}
                  </span>
                  <span className="text-xs text-muted-foreground mt-1">Up to 5MB, 5,000 rows</span>
                </label>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ---------------- Job-not-found (e.g. expired / deleted) ---------------- */}
      {jobId != null && jobQuery.isError && (
        <Card className="border-dashed border-destructive/40 bg-destructive/5 max-w-2xl">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <AlertCircle className="w-10 h-10 text-destructive/60 mb-3" />
            <h3 className="text-lg font-semibold text-foreground mb-1">Import job not found</h3>
            <p className="text-sm text-muted-foreground max-w-sm mb-4">
              This import may have expired or been removed. Start a new upload.
            </p>
            <Button onClick={handleStartOver}>Start New Import</Button>
          </CardContent>
        </Card>
      )}

      {/* ---------------- Preview & resolve step ---------------- */}
      {job && status === "ready" && (
        <div className="space-y-6 page-transition-enter stagger-1">
          <Card className="shadow-sm">
            <CardContent className="p-5 flex flex-wrap items-center gap-6">
              <SummaryStat label="Total" value={job.totalRows} />
              <SummaryStat label="New" value={job.newCount} className="text-emerald-600 dark:text-emerald-400" />
              <SummaryStat label="Existing match" value={job.exactExistingCount} className="text-blue-600 dark:text-blue-400" />
              <SummaryStat label="Probable dup." value={job.probableDuplicateCount} className="text-amber-600 dark:text-amber-400" />
              <SummaryStat label="Conflicts" value={job.identifierConflictCount} className="text-rose-600 dark:text-rose-400" />
              <SummaryStat label="Invalid" value={job.invalidCount} className="text-rose-600 dark:text-rose-400" />
              <div className="ml-auto flex items-center gap-2">
                <Button variant="outline" onClick={handleStartOver} disabled={cancelMutation.isPending}>
                  <RotateCcw className="w-4 h-4 mr-2" /> Start Over
                </Button>
                <Button onClick={() => setConfirmOpen(true)} disabled={resolveMutation.isPending} className="hover-elevate shadow-sm">
                  Confirm Import
                </Button>
              </div>
            </CardContent>
          </Card>

          {job.invalidCount + job.identifierConflictCount > 0 && (
            <div className="flex items-center gap-2 text-sm text-amber-800 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-md px-4 py-3">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              Rows with errors will be skipped automatically -- fix them in the source file and re-upload if you need them included.
            </div>
          )}

          <Card className="shadow-sm">
            <CardHeader className="bg-muted/10 border-b pb-4 flex-row items-center justify-between space-y-0">
              <CardTitle className="text-base">Row preview</CardTitle>
              <Select
                value={classificationFilter}
                onValueChange={(v) => { setClassificationFilter(v); setPage(1); }}
              >
                <SelectTrigger className="w-56 h-9 bg-background">
                  <SelectValue placeholder="Filter by classification" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={allValue}>All classifications</SelectItem>
                  {Object.entries(CLASSIFICATION_META).map(([key, meta]) => (
                    <SelectItem key={key} value={key}>{meta.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader className="bg-muted/30">
                    <TableRow>
                      <TableHead className="w-12 text-center">Row</TableHead>
                      <TableHead>Classification</TableHead>
                      <TableHead>Tutor</TableHead>
                      <TableHead>Matched against</TableHead>
                      <TableHead>Notes</TableHead>
                      <TableHead className="w-56">Resolution</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rowsQuery.isLoading ? (
                      <TableRow>
                        <TableCell colSpan={6} className="h-24 text-center">
                          <Loader2 className="w-5 h-5 animate-spin mx-auto text-muted-foreground" />
                        </TableCell>
                      </TableRow>
                    ) : rows.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                          No rows match this filter.
                        </TableCell>
                      </TableRow>
                    ) : (
                      rows.map((row) => (
                        <TableRow key={row.id}>
                          <TableCell className="text-center text-muted-foreground font-mono text-xs">{row.rowNumber}</TableCell>
                          <TableCell><ClassificationBadge classification={row.classification} /></TableCell>
                          <TableCell className="text-sm">
                            <div className="font-medium">{row.rawData.first_name} {row.rawData.last_name}</div>
                            <div className="text-xs text-muted-foreground font-mono">{row.rawData.email}</div>
                          </TableCell>
                          <TableCell className="text-sm text-muted-foreground">
                            {row.matchedTutorName ?? "-"}
                          </TableCell>
                          <TableCell className="text-xs text-rose-600 dark:text-rose-400 max-w-[280px]">
                            {[...row.errors, ...row.warnings].join("; ") || "-"}
                          </TableCell>
                          <TableCell>
                            {isResolvableRow(row) ? (
                              <RadioGroup
                                value={row.resolution ?? "skip"}
                                onValueChange={(v) => handleResolve(row, v as "skip" | "update")}
                                className="flex items-center gap-4"
                              >
                                <div className="flex items-center gap-1.5">
                                  <RadioGroupItem value="skip" id={`skip-${row.id}`} />
                                  <Label htmlFor={`skip-${row.id}`} className="text-xs font-normal cursor-pointer">Skip</Label>
                                </div>
                                <div className="flex items-center gap-1.5">
                                  <RadioGroupItem value="update" id={`update-${row.id}`} />
                                  <Label htmlFor={`update-${row.id}`} className="text-xs font-normal cursor-pointer">Update</Label>
                                </div>
                              </RadioGroup>
                            ) : row.classification === "new" ? (
                              <Badge variant="outline" className="bg-emerald-100 text-emerald-800 border-emerald-200 text-[10px]">Will create</Badge>
                            ) : (
                              <span className="text-xs text-muted-foreground italic">Will be skipped</span>
                            )}
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
                    <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>
                      <ChevronLeft className="w-4 h-4" />
                    </Button>
                    <div className="text-sm font-medium px-2">{page}</div>
                    <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * pageSize >= totalRows}>
                      <ChevronRight className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* ---------------- Importing (rare -- only visible on reload/race) ---------------- */}
      {job && status === "importing" && (
        <Card className="shadow-sm max-w-2xl">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <Loader2 className="w-8 h-8 animate-spin text-primary mb-3" />
            <h3 className="text-lg font-semibold text-foreground mb-1">Import in progress</h3>
            <p className="text-sm text-muted-foreground">This will only take a moment.</p>
          </CardContent>
        </Card>
      )}

      {/* ---------------- Results step ---------------- */}
      {job && status === "completed" && (
        <Card className="shadow-sm max-w-2xl page-transition-enter">
          <CardHeader className="bg-muted/10 border-b pb-4">
            <CardTitle className="text-base flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-600" /> Import complete
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-6 space-y-6">
            <div className="grid grid-cols-3 gap-4">
              <div className="bg-emerald-50 dark:bg-emerald-950/20 border border-emerald-100 dark:border-emerald-900 p-4 rounded-lg text-center">
                <div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">{job.resultSummary?.created ?? 0}</div>
                <div className="text-xs font-medium text-emerald-800 dark:text-emerald-500 uppercase mt-1">Created</div>
              </div>
              <div className="bg-blue-50 dark:bg-blue-950/20 border border-blue-100 dark:border-blue-900 p-4 rounded-lg text-center">
                <div className="text-2xl font-bold text-blue-600 dark:text-blue-400">{job.resultSummary?.updated ?? 0}</div>
                <div className="text-xs font-medium text-blue-800 dark:text-blue-500 uppercase mt-1">Updated</div>
              </div>
              <div className="bg-muted/40 border p-4 rounded-lg text-center">
                <div className="text-2xl font-bold text-foreground">{job.resultSummary?.skipped ?? 0}</div>
                <div className="text-xs font-medium text-muted-foreground uppercase mt-1">Skipped</div>
              </div>
            </div>

            {problemRowCount > 0 && (
              <Button variant="outline" className="w-full" onClick={handleDownloadErrors} disabled={errorsQuery.isFetching}>
                {errorsQuery.isFetching ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />}
                Download Error Report ({problemRowCount} row{problemRowCount === 1 ? "" : "s"})
              </Button>
            )}

            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={handleStartOver}>Import Another File</Button>
              <Button className="flex-1" onClick={() => setLocation("/tutors")}>Back to Tutors</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* ---------------- Cancelled ---------------- */}
      {job && status === "cancelled" && (
        <Card className="shadow-sm max-w-2xl">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <AlertCircle className="w-10 h-10 text-muted-foreground/60 mb-3" />
            <h3 className="text-lg font-semibold text-foreground mb-1">Import cancelled</h3>
            <p className="text-sm text-muted-foreground max-w-sm mb-4">No tutors were created or changed.</p>
            <Button onClick={handleStartOver}>Start New Import</Button>
          </CardContent>
        </Card>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Confirm tutor import</DialogTitle>
            <DialogDescription>
              {job?.newCount ?? 0} new tutor{(job?.newCount ?? 0) === 1 ? "" : "s"} will be created. Duplicate rows are
              skipped unless you explicitly chose "Update". This action is applied immediately and cannot be undone from
              this page.
            </DialogDescription>
          </DialogHeader>
          {resolveMutation.isPending && (
            <p className="text-xs text-amber-600 dark:text-amber-500 flex items-center gap-1.5">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Saving your Skip/Update choice for a row -- please wait before confirming.
            </p>
          )}
          <DialogFooter className="mt-2">
            <Button variant="outline" onClick={() => setConfirmOpen(false)} disabled={confirmMutation.isPending}>
              Go Back
            </Button>
            <Button onClick={handleConfirm} disabled={confirmMutation.isPending || resolveMutation.isPending} className="hover-elevate shadow-sm">
              {confirmMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Confirm Import
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
