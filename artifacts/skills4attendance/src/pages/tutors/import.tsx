import * as React from "react";
import { useGetTutorCsvTemplate, usePreviewTutorCsv, useImportTutorCsv, CsvPreviewResult, getGetTutorCsvTemplateQueryKey } from "@workspace/api-client-react";
import { useLocation } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Download, Upload, Loader2, AlertCircle, FileText, CheckCircle2, ArrowLeft, Check } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

export default function TutorImportPage() {
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const [file, setFile] = React.useState<File | null>(null);
  const [previewResult, setPreviewResult] = React.useState<CsvPreviewResult | null>(null);

  const getTemplateMutation = useGetTutorCsvTemplate({
    query: { enabled: false, queryKey: getGetTutorCsvTemplateQueryKey() }
  });
  const previewMutation = usePreviewTutorCsv();
  const importMutation = useImportTutorCsv();

  const handleDownloadTemplate = async () => {
    try {
      const result = await getTemplateMutation.refetch();
      if (result.data) {
        const blob = new Blob([result.data.csv], { type: "text/csv" });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = result.data.filename || "tutors_template.csv";
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        a.remove();
      }
    } catch {
      toast({ title: "Error downloading template", variant: "destructive" });
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setPreviewResult(null);
    }
  };

  const handlePreview = async () => {
    if (!file) return;
    const csv = await readFileAsText(file);
    previewMutation.mutate({ data: { csv, filename: file.name } }, {
      onSuccess: (data) => setPreviewResult(data),
      onError: (err: any) => toast({ title: "Preview failed", description: err.error, variant: "destructive" }),
    });
  };

  const handleImport = () => {
    if (!previewResult) return;
    importMutation.mutate({ data: { rows: previewResult.rows.map((row) => row.data) } }, {
      onSuccess: (data) => {
        toast({
          title: "Import complete",
          description: `Successfully imported ${data.imported} tutors. Skipped ${data.skipped}.`,
        });
        setLocation("/tutors");
      },
      onError: (err: any) => toast({ title: "Import failed", description: err.error, variant: "destructive" }),
    });
  };

  return (
    <div className="p-6 md:p-8 max-w-5xl mx-auto w-full">
      <Breadcrumbs items={[
        { label: "Tutors", href: "/tutors" },
        { label: "Import from CSV" }
      ]} />

      <div className="flex items-center gap-4 mb-8 page-transition-enter">
        <Button variant="outline" size="icon" onClick={() => setLocation("/tutors")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Import Tutors</h1>
          <p className="text-muted-foreground mt-1">Bulk upload tutors from a CSV file.</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 page-transition-enter stagger-1">
        <div className="lg:col-span-1 space-y-6">
          <Card className="shadow-sm">
            <CardHeader className="bg-muted/10 border-b pb-4">
              <CardTitle className="text-base flex items-center gap-2">
                <FileText className="w-4 h-4 text-primary" /> Step 1: Template
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-6">
              <p className="text-sm text-muted-foreground mb-4">
                Download the exact CSV format required. Do not change the column headers.
              </p>
              <Button variant="outline" className="w-full" onClick={handleDownloadTemplate} disabled={getTemplateMutation.isFetching}>
                {getTemplateMutation.isFetching ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />}
                Download Template
              </Button>
            </CardContent>
          </Card>

          <Card className="shadow-sm">
            <CardHeader className="bg-muted/10 border-b pb-4">
              <CardTitle className="text-base flex items-center gap-2">
                <Upload className="w-4 h-4 text-primary" /> Step 2: Upload
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-6">
              <div className="border-2 border-dashed border-muted-foreground/20 rounded-lg p-6 text-center hover:bg-muted/10 transition-colors">
                <Input
                  type="file"
                  accept=".csv"
                  className="hidden"
                  id="csv-upload"
                  onChange={handleFileChange}
                />
                <label htmlFor="csv-upload" className="cursor-pointer flex flex-col items-center">
                  <FileText className={`w-8 h-8 mb-2 ${file ? "text-primary" : "text-muted-foreground/40"}`} />
                  <span className="text-sm font-medium text-foreground">{file ? file.name : "Select CSV File"}</span>
                  <span className="text-xs text-muted-foreground mt-1">Click to browse</span>
                </label>
              </div>
              <Button
                className="w-full mt-4"
                disabled={!file || previewMutation.isPending}
                onClick={handlePreview}
              >
                {previewMutation.isPending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : "Preview Data"}
              </Button>
            </CardContent>
          </Card>
        </div>

        <div className="lg:col-span-2">
          <Card className="shadow-sm h-full flex flex-col min-h-[400px]">
            <CardHeader className="bg-muted/10 border-b pb-4">
              <CardTitle className="text-base flex items-center gap-2 justify-between">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-primary" /> Step 3: Validate & Import
                </div>
                {previewResult && (
                  <Button size="sm" onClick={handleImport} disabled={importMutation.isPending || previewResult.validRows === 0} className="hover-elevate shadow-sm">
                    {importMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                    Import {previewResult.validRows} Valid Rows
                  </Button>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-6 flex-1 flex flex-col">
              {!previewResult ? (
                <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
                  Upload a file to preview data before importing.
                </div>
              ) : (
                <div className="space-y-6">
                  <div className="grid grid-cols-3 gap-4">
                    <div className="bg-emerald-50 dark:bg-emerald-950/20 border border-emerald-100 dark:border-emerald-900 p-4 rounded-lg text-center">
                      <div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">{previewResult.validRows}</div>
                      <div className="text-xs font-medium text-emerald-800 dark:text-emerald-500 uppercase mt-1">Ready</div>
                    </div>
                    <div className="bg-rose-50 dark:bg-rose-950/20 border border-rose-100 dark:border-rose-900 p-4 rounded-lg text-center">
                      <div className="text-2xl font-bold text-rose-600 dark:text-rose-400">{previewResult.invalidRows}</div>
                      <div className="text-xs font-medium text-rose-800 dark:text-rose-500 uppercase mt-1">Invalid</div>
                    </div>
                    <div className="bg-amber-50 dark:bg-amber-950/20 border border-amber-100 dark:border-amber-900 p-4 rounded-lg text-center">
                      <div className="text-2xl font-bold text-amber-600 dark:text-amber-400">{previewResult.duplicateRows}</div>
                      <div className="text-xs font-medium text-amber-800 dark:text-amber-500 uppercase mt-1">Duplicates</div>
                    </div>
                  </div>

                  {previewResult.invalidRows > 0 && (
                    <Alert variant="destructive" className="bg-rose-50 text-rose-900 border-rose-200 dark:bg-rose-950/30 dark:text-rose-200 dark:border-rose-900">
                      <AlertCircle className="h-4 w-4" />
                      <AlertTitle>Validation Errors</AlertTitle>
                      <AlertDescription>
                        Rows with errors will be skipped during import. Fix them in your CSV and re-upload, or proceed to import only the valid rows.
                      </AlertDescription>
                    </Alert>
                  )}

                  <div className="border rounded-md overflow-x-auto">
                    <Table>
                      <TableHeader className="bg-muted/30">
                        <TableRow>
                          <TableHead className="w-12 text-center">Row</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead>Tutor</TableHead>
                          <TableHead>Email</TableHead>
                          <TableHead>Issues</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {previewResult.rows.slice(0, 50).map((row, idx) => {
                          const hasErrors = row.errors.length > 0;
                          return (
                            <TableRow key={idx} className={hasErrors ? "bg-rose-50/50 dark:bg-rose-950/10" : row.isDuplicate ? "bg-amber-50/50 dark:bg-amber-950/10" : ""}>
                              <TableCell className="text-center text-muted-foreground font-mono text-xs">{row.rowNumber}</TableCell>
                              <TableCell>
                                {hasErrors ? (
                                  <Badge variant="destructive" className="text-[10px]">Error</Badge>
                                ) : row.isDuplicate ? (
                                  <Badge variant="outline" className="bg-amber-100 text-amber-800 border-amber-200 text-[10px]">Duplicate</Badge>
                                ) : (
                                  <Badge variant="outline" className="bg-emerald-100 text-emerald-800 border-emerald-200 text-[10px]"><Check className="w-3 h-3 mr-1" /> OK</Badge>
                                )}
                              </TableCell>
                              <TableCell className="font-medium text-sm">
                                {row.data.firstName} {row.data.lastName}
                              </TableCell>
                              <TableCell className="font-mono text-xs text-muted-foreground">
                                {row.data.email}
                              </TableCell>
                              <TableCell className="text-xs text-rose-600 dark:text-rose-400">
                                {hasErrors ? row.errors.join(", ") : row.isDuplicate ? row.duplicateReason : "-"}
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                    {previewResult.rows.length > 50 && (
                      <div className="p-3 text-center text-xs text-muted-foreground bg-muted/10 border-t">
                        Showing first 50 rows only.
                      </div>
                    )}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
