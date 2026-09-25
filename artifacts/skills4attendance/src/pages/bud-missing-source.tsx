import * as React from "react";
import {
  useGetCurrentUser,
  useGetBudMissingSourceExceptions,
  getGetBudMissingSourceExceptionsQueryKey,
  useRefreshBudMissingSource,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { Loader2, SearchX, Info, RefreshCw, AlertTriangle } from "lucide-react";
import { format } from "date-fns";

export default function BudMissingSourcePage() {
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === "admin";
  const [status, setStatus] = React.useState<"open" | "resolved">("open");
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const params = { status };
  const queryKey = getGetBudMissingSourceExceptionsQueryKey(params);
  const { data, isLoading, isError } = useGetBudMissingSourceExceptions(params, {
    query: { enabled: isAdmin, queryKey },
  });

  const refreshMutation = useRefreshBudMissingSource({
    mutation: {
      onSuccess: () => {
        toast({ title: "Detection refreshed" });
        queryClient.invalidateQueries({ queryKey });
      },
      onError: (err) => {
        // The failed attempt is recorded server-side (lastError) but never
        // discards the last successful results -- refetch so the banner
        // picks up the new lastAttemptedAt/lastError without losing the list.
        toast({ title: "Refresh failed", description: getErrorMessage(err), variant: "destructive" });
        queryClient.invalidateQueries({ queryKey });
      },
    },
  });

  if (user && !isAdmin) {
    return (
      <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
        <Breadcrumbs items={[{ label: "Missing Source Records" }]} />
        <Card className="shadow-sm"><CardContent className="p-8 text-center text-muted-foreground">Administrator access required.</CardContent></Card>
      </div>
    );
  }

  const refreshStatus = data?.refreshStatus;

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Missing Source Records" }]} />
      <div className="mb-6 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <SearchX className="w-7 h-7 text-primary" /> Missing Source Records
          </h1>
          <p className="text-muted-foreground mt-1">
            Previously-linked, internally active learners whose linked Bud learning-plan row has gone missing from
            the source. This never infers completion, withdrawal, or deletion -- it is a review exception only, and
            no learner status is changed by this page or by refreshing it.
          </p>
        </div>
        <Button onClick={() => refreshMutation.mutate()} disabled={refreshMutation.isPending}>
          {refreshMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <RefreshCw className="w-4 h-4 mr-2" />}
          Refresh detection
        </Button>
      </div>

      {refreshStatus && (
        <Card className="shadow-sm mb-3 bg-muted/20">
          <CardContent className="p-4 text-sm text-muted-foreground">
            <p>
              Last successful detection:{" "}
              {refreshStatus.lastSucceededAt ? format(new Date(refreshStatus.lastSucceededAt), "d MMM yyyy HH:mm:ss") : "never run yet"}
              {refreshStatus.lastSucceededAt && (
                <> ({refreshStatus.lastNewlyOpened ?? 0} newly opened, {refreshStatus.lastResolved ?? 0} resolved)</>
              )}
              .
            </p>
            {refreshStatus.lastError && (
              <p className="mt-1 flex items-start gap-2 text-rose-600 dark:text-rose-400">
                <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                <span>
                  Last refresh attempt ({refreshStatus.lastAttemptedAt ? format(new Date(refreshStatus.lastAttemptedAt), "d MMM yyyy HH:mm:ss") : "unknown time"}) failed: {refreshStatus.lastError}.
                  The results above are from the last successful run and have not been discarded.
                </span>
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {data && (
        <Card className="shadow-sm mb-6 bg-muted/20">
          <CardContent className="p-4 text-sm text-muted-foreground flex items-start gap-2">
            <Info className="w-4 h-4 mt-0.5 shrink-0" />
            <div>
              <p>
                Bud source: {data.sourceInfo.sourceRowCount} learning-plan rows, last synced{" "}
                {data.sourceInfo.sourceMaxSyncedAt ? format(new Date(data.sourceInfo.sourceMaxSyncedAt), "d MMM yyyy HH:mm") : "unknown"}.
              </p>
              <p className="mt-1">{data.sourceInfo.sourceCompletenessNote}</p>
            </div>
          </CardContent>
        </Card>
      )}

      <Tabs value={status} onValueChange={(v) => setStatus(v as "open" | "resolved")} className="mb-4">
        <TabsList>
          <TabsTrigger value="open">Open</TabsTrigger>
          <TabsTrigger value="resolved">Resolved</TabsTrigger>
        </TabsList>
      </Tabs>

      <Card className="shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">{status === "open" ? "Open exceptions" : "Resolved exceptions"}</CardTitle>
          <CardDescription>
            {status === "open"
              ? "The linked plan is currently absent from the source. Checked fresh on every load."
              : "The source record returned after being missing -- kept for audit history, never deleted."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-primary" /></div>
          ) : isError || !data ? (
            <p className="text-center text-muted-foreground p-8">Could not load missing-source exceptions.</p>
          ) : data.items.length === 0 ? (
            <p className="text-center text-muted-foreground p-8">No {status} exceptions.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Learner</TableHead>
                  <TableHead>Bud plan id</TableHead>
                  <TableHead>First detected</TableHead>
                  <TableHead>{status === "open" ? "Last confirmed missing" : "Resolved at"}</TableHead>
                  <TableHead>Classification</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell className="font-medium">{e.learnerName ?? `Learner #${e.internalLearnerId}`}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{e.budLearningPlanId}</TableCell>
                    <TableCell>{format(new Date(e.firstDetectedAt), "d MMM yyyy HH:mm")}</TableCell>
                    <TableCell>
                      {status === "open"
                        ? format(new Date(e.lastConfirmedMissingAt), "d MMM yyyy HH:mm")
                        : e.resolvedAt ? format(new Date(e.resolvedAt), "d MMM yyyy HH:mm") : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {e.sourceReferenceUnknown ? (
                        <Badge variant="outline">reference unknown</Badge>
                      ) : e.personEntirelyAbsentFromSource ? (
                        <Badge variant="destructive">person entirely absent from source</Badge>
                      ) : e.otherPlansForPersonStillPresent ? (
                        <Badge variant="outline" className="border-amber-400 text-amber-700 dark:text-amber-400">
                          other plans for this person still present
                        </Badge>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
