import * as React from "react";
import { useGetLearner, useCreateLearner, useUpdateLearner, useChangeLearnerStatus, useGetLearnerAllocationHistory, LearnerStatus, getGetLearnerQueryKey, getGetLearnerAllocationHistoryQueryKey } from "@workspace/api-client-react";
import { useLocation, useParams } from "wouter";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Save, ArrowLeft, History, Calendar, RefreshCw } from "lucide-react";
import { LearnerStatusBadge } from "@/components/status-badges";
import { format, parseISO } from "date-fns";

const learnerSchema = z.object({
  learnerRef: z.string().min(1, "Learner Reference is required"),
  uln: z.string().optional(),
  firstName: z.string().min(1, "First name is required"),
  lastName: z.string().min(1, "Last name is required"),
  email: z.string().email("Valid email required").optional().or(z.literal("")),
  employer: z.string().optional(),
  programme: z.string().min(1, "Programme is required"),
  level: z.string().min(1, "Level is required"),
  startDate: z.string().min(1, "Start date is required"),
  plannedEndDate: z.string().optional(),
  status: z.enum(["active", "withdrawn", "completed", "paused"]).default("active"),
  externalSystemId: z.string().optional(),
}).refine((data) => !data.plannedEndDate || data.plannedEndDate >= data.startDate, {
  message: "Planned end date cannot be before start date",
  path: ["plannedEndDate"],
});

const statusChangeSchema = z.object({
  status: z.enum(["active", "withdrawn", "completed", "paused"]),
  actualEndDate: z.string().optional(),
  withdrawalDate: z.string().optional(),
  reason: z.string().optional(),
}).refine((data) => data.status !== "withdrawn" || !!data.withdrawalDate, {
  message: "Withdrawal date is required when withdrawing a learner",
  path: ["withdrawalDate"],
}).refine((data) => data.status !== "completed" || !!data.actualEndDate, {
  message: "Actual end date is required when completing a learner",
  path: ["actualEndDate"],
});

export default function LearnerDetailPage() {
  const params = useParams();
  const isNew = !params.id || params.id === "new";
  const learnerId = isNew ? 0 : Number(params.id);

  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const [statusDialogOpen, setStatusDialogOpen] = React.useState(false);

  const { data: learner, isLoading: isLoadingLearner } = useGetLearner(learnerId, {
    query: { enabled: !isNew, queryKey: getGetLearnerQueryKey(learnerId) }
  });

  const { data: history } = useGetLearnerAllocationHistory(learnerId, {
    query: { enabled: !isNew, queryKey: getGetLearnerAllocationHistoryQueryKey(learnerId) }
  });

  const createMutation = useCreateLearner();
  const updateMutation = useUpdateLearner();
  const changeStatusMutation = useChangeLearnerStatus();
  const isSaving = createMutation.isPending || updateMutation.isPending;

  const form = useForm<z.infer<typeof learnerSchema>>({
    resolver: zodResolver(learnerSchema),
    defaultValues: {
      learnerRef: "",
      uln: "",
      firstName: "",
      lastName: "",
      email: "",
      employer: "",
      programme: "",
      level: "",
      startDate: format(new Date(), "yyyy-MM-dd"),
      plannedEndDate: "",
      status: "active",
      externalSystemId: "",
    }
  });

  const statusForm = useForm<z.infer<typeof statusChangeSchema>>({
    resolver: zodResolver(statusChangeSchema),
    defaultValues: { status: "active", actualEndDate: "", withdrawalDate: "", reason: "" },
  });
  const watchedStatus = statusForm.watch("status");

  const initializedForId = React.useRef<number | null>(null);
  React.useEffect(() => {
    if (learner && initializedForId.current !== learnerId) {
      initializedForId.current = learnerId;
      form.reset({
        learnerRef: learner.learnerRef,
        uln: learner.uln || "",
        firstName: learner.firstName,
        lastName: learner.lastName,
        email: learner.email || "",
        employer: learner.employer || "",
        programme: learner.programme,
        level: learner.level,
        startDate: learner.startDate.split('T')[0],
        plannedEndDate: learner.plannedEndDate ? learner.plannedEndDate.split('T')[0] : "",
        status: learner.status,
        externalSystemId: learner.externalSystemId || "",
      });
      statusForm.reset({
        status: learner.status,
        actualEndDate: learner.actualEndDate ? learner.actualEndDate.split('T')[0] : "",
        withdrawalDate: learner.withdrawalDate ? learner.withdrawalDate.split('T')[0] : "",
        reason: "",
      });
    }
  }, [learner, learnerId, form, statusForm]);

  const onSubmit = (values: z.infer<typeof learnerSchema>) => {
    // Note: Date fields are already strings "YYYY-MM-DD" from input[type=date]
    // which the API accepts. Empty strings for optional fields become undefined.
    const payload = {
      ...values,
      uln: values.uln || undefined,
      email: values.email || undefined,
      employer: values.employer || undefined,
      plannedEndDate: values.plannedEndDate || undefined,
      externalSystemId: values.externalSystemId || undefined,
    };

    if (isNew) {
      createMutation.mutate({ data: payload as any }, {
        onSuccess: () => {
          toast({ title: "Learner created" });
          setLocation("/learners");
        },
        onError: (err: any) => toast({ title: "Error", description: err.error, variant: "destructive" })
      });
    } else {
      const { status: _status, ...rest } = payload;
      updateMutation.mutate({ id: learnerId, data: rest }, {
        onSuccess: () => {
          toast({ title: "Learner updated" });
          setLocation("/learners");
        },
        onError: (err: any) => toast({ title: "Error", description: err.error, variant: "destructive" })
      });
    }
  };

  const onChangeStatus = (values: z.infer<typeof statusChangeSchema>) => {
    changeStatusMutation.mutate({
      id: learnerId,
      data: {
        status: values.status,
        actualEndDate: values.actualEndDate || undefined,
        withdrawalDate: values.withdrawalDate || undefined,
        reason: values.reason || undefined,
      },
    }, {
      onSuccess: () => {
        toast({ title: "Status updated" });
        setStatusDialogOpen(false);
      },
      onError: (err: any) => toast({ title: "Error", description: err?.data?.error || err.message, variant: "destructive" }),
    });
  };

  if (!isNew && isLoadingLearner) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;
  }

  return (
    <div className="p-6 md:p-8 max-w-5xl mx-auto w-full">
      <Breadcrumbs items={[
        { label: "Learners", href: "/learners" },
        { label: isNew ? "New Learner" : `${learner?.firstName} ${learner?.lastName}` }
      ]} />

      <div className="flex items-center justify-between gap-4 mb-8 page-transition-enter">
        <div className="flex items-center gap-4">
          <Button variant="outline" size="icon" onClick={() => setLocation("/learners")}>
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-3xl font-bold tracking-tight text-foreground">
                {isNew ? "Add Learner" : `${learner?.firstName} ${learner?.lastName}`}
              </h1>
              {!isNew && learner && <LearnerStatusBadge status={learner.status} />}
            </div>
            <p className="text-muted-foreground mt-1">
              {isNew ? "Register a new apprentice onto the system." : `Ref: ${learner?.learnerRef} • Programme: ${learner?.programme}`}
            </p>
          </div>
        </div>
        <div className="hidden sm:flex gap-2">
          {!isNew && (
            <Button variant="outline" onClick={() => setStatusDialogOpen(true)}>
              <RefreshCw className="w-4 h-4 mr-2" /> Change Status
            </Button>
          )}
          <Button onClick={() => form.handleSubmit(onSubmit)()} disabled={isSaving} className="hover-elevate shadow-sm">
            {isSaving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
            {isNew ? "Create Learner" : "Save Changes"}
          </Button>
        </div>
      </div>

      <div className="page-transition-enter stagger-1">
        <Tabs defaultValue="details" className="w-full">
          {!isNew && (
            <TabsList className="grid w-full grid-cols-2 max-w-md mb-6">
              <TabsTrigger value="details">Profile Details</TabsTrigger>
              <TabsTrigger value="history">Allocation History</TabsTrigger>
            </TabsList>
          )}

          <TabsContent value="details" className="space-y-6">
            <Form {...form}>
              <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
                <Card className="shadow-sm">
                  <CardHeader className="border-b bg-muted/10 pb-4">
                    <CardTitle className="text-lg">Identity & Contact</CardTitle>
                  </CardHeader>
                  <CardContent className="pt-6 grid grid-cols-1 md:grid-cols-2 gap-6">
                    <FormField control={form.control} name="firstName" render={({ field }) => (
                      <FormItem><FormLabel>First Name</FormLabel><FormControl><Input {...field} /></FormControl><FormMessage /></FormItem>
                    )} />
                    <FormField control={form.control} name="lastName" render={({ field }) => (
                      <FormItem><FormLabel>Last Name</FormLabel><FormControl><Input {...field} /></FormControl><FormMessage /></FormItem>
                    )} />
                    <FormField control={form.control} name="learnerRef" render={({ field }) => (
                      <FormItem><FormLabel>Learner Reference</FormLabel><FormControl><Input {...field} className="font-mono text-sm" /></FormControl><FormMessage /></FormItem>
                    )} />
                    <FormField control={form.control} name="uln" render={({ field }) => (
                      <FormItem><FormLabel>ULN (Optional)</FormLabel><FormControl><Input {...field} className="font-mono text-sm" /></FormControl><FormMessage /></FormItem>
                    )} />
                    <FormField control={form.control} name="email" render={({ field }) => (
                      <FormItem><FormLabel>Email Address (Optional)</FormLabel><FormControl><Input type="email" {...field} /></FormControl><FormMessage /></FormItem>
                    )} />
                    <FormField control={form.control} name="employer" render={({ field }) => (
                      <FormItem><FormLabel>Employer (Optional)</FormLabel><FormControl><Input {...field} /></FormControl><FormMessage /></FormItem>
                    )} />
                  </CardContent>
                </Card>

                <Card className="shadow-sm">
                  <CardHeader className="border-b bg-muted/10 pb-4">
                    <CardTitle className="text-lg">Programme Details</CardTitle>
                  </CardHeader>
                  <CardContent className="pt-6 grid grid-cols-1 md:grid-cols-2 gap-6">
                    <FormField control={form.control} name="programme" render={({ field }) => (
                      <FormItem><FormLabel>Programme</FormLabel><FormControl><Input {...field} placeholder="e.g. Data Analyst" /></FormControl><FormMessage /></FormItem>
                    )} />
                    <FormField control={form.control} name="level" render={({ field }) => (
                      <FormItem><FormLabel>Level</FormLabel><FormControl><Input {...field} placeholder="e.g. 4" /></FormControl><FormMessage /></FormItem>
                    )} />
                    <FormField control={form.control} name="startDate" render={({ field }) => (
                      <FormItem><FormLabel>Start Date</FormLabel><FormControl><Input type="date" {...field} /></FormControl><FormMessage /></FormItem>
                    )} />
                    <FormField control={form.control} name="plannedEndDate" render={({ field }) => (
                      <FormItem><FormLabel>Planned End Date (Optional)</FormLabel><FormControl><Input type="date" {...field} /></FormControl><FormMessage /></FormItem>
                    )} />
                    {isNew ? (
                      <FormField control={form.control} name="status" render={({ field }) => (
                        <FormItem>
                          <FormLabel>Status</FormLabel>
                          <Select value={field.value} onValueChange={field.onChange}>
                            <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                            <SelectContent>
                              <SelectItem value="active">Active</SelectItem>
                              <SelectItem value="paused">Paused (BIL)</SelectItem>
                              <SelectItem value="completed">Completed</SelectItem>
                              <SelectItem value="withdrawn">Withdrawn</SelectItem>
                            </SelectContent>
                          </Select>
                          <FormMessage />
                        </FormItem>
                      )} />
                    ) : (
                      <div>
                        <FormLabel>Status</FormLabel>
                        <p className="text-sm text-muted-foreground mt-2">
                          Use the "Change Status" action above to update this learner's status.
                        </p>
                      </div>
                    )}
                    <FormField control={form.control} name="externalSystemId" render={({ field }) => (
                      <FormItem><FormLabel>External System ID (Optional)</FormLabel><FormControl><Input {...field} /></FormControl><FormMessage /></FormItem>
                    )} />
                  </CardContent>
                </Card>

                <div className="flex justify-end gap-4 sm:hidden">
                  <Button type="button" variant="outline" onClick={() => setLocation("/learners")}>Cancel</Button>
                  {!isNew && (
                    <Button type="button" variant="outline" onClick={() => setStatusDialogOpen(true)}>
                      Change Status
                    </Button>
                  )}
                  <Button type="submit" disabled={isSaving}>
                    {isSaving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                    Save
                  </Button>
                </div>
              </form>
            </Form>
          </TabsContent>

          {!isNew && (
            <TabsContent value="history">
              <Card className="shadow-sm">
                <CardHeader>
                  <CardTitle className="text-lg flex items-center gap-2">
                    <History className="w-5 h-5 text-primary" /> Allocation History
                  </CardTitle>
                  <CardDescription>Record of tutor and cohort transfers.</CardDescription>
                </CardHeader>
                <CardContent>
                  {!history || history.length === 0 ? (
                    <div className="text-center py-10 text-muted-foreground bg-muted/10 rounded-lg border-dashed border">
                      No allocation history recorded for this learner.
                    </div>
                  ) : (
                    <div className="relative border-l-2 border-muted ml-3 space-y-8 pb-4">
                      {history.map((entry, idx) => (
                        <div key={entry.id} className="relative pl-6">
                          <div className="absolute w-3 h-3 bg-primary rounded-full -left-[7px] top-1.5 ring-4 ring-card"></div>
                          <div className="flex flex-col sm:flex-row sm:items-baseline gap-1 sm:gap-3 mb-2">
                            <h4 className="font-semibold text-foreground">Allocation Change</h4>
                            <span className="text-xs text-muted-foreground flex items-center gap-1">
                              <Calendar className="w-3 h-3" />
                              {format(parseISO(entry.effectiveDate), "MMM d, yyyy")}
                            </span>
                          </div>

                          <div className="bg-muted/20 border rounded-md p-4 text-sm space-y-3">
                            {entry.previousTutorId !== entry.newTutorId && (
                              <div className="flex items-center gap-2">
                                <span className="text-muted-foreground w-16">Tutor:</span>
                                <span className="line-through text-muted-foreground/70">{entry.previousTutorName || "None"}</span>
                                <span>→</span>
                                <span className="font-medium text-primary">{entry.newTutorName || "None"}</span>
                              </div>
                            )}
                            {entry.previousCohortId !== entry.newCohortId && (
                              <div className="flex items-center gap-2">
                                <span className="text-muted-foreground w-16">Cohort:</span>
                                <span className="line-through text-muted-foreground/70">{entry.previousCohortName || "None"}</span>
                                <span>→</span>
                                <span className="font-medium text-primary">{entry.newCohortName || "None"}</span>
                              </div>
                            )}
                            {entry.transferReason && (
                              <div className="pt-2 border-t mt-2">
                                <span className="text-muted-foreground text-xs block mb-1">Reason:</span>
                                <span className="italic">{entry.transferReason}</span>
                              </div>
                            )}
                            <div className="text-xs text-muted-foreground/60 pt-2 flex items-center gap-1">
                              Recorded by {entry.changedByName} on {format(parseISO(entry.changedDate), "MMM d, yyyy HH:mm")}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          )}
        </Tabs>
      </div>

      <Dialog open={statusDialogOpen} onOpenChange={setStatusDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Change Learner Status</DialogTitle>
            <DialogDescription>
              Withdrawn learners require a withdrawal date; completed learners require an actual end date.
            </DialogDescription>
          </DialogHeader>
          <Form {...statusForm}>
            <form onSubmit={statusForm.handleSubmit(onChangeStatus)} className="space-y-4">
              <FormField control={statusForm.control} name="status" render={({ field }) => (
                <FormItem>
                  <FormLabel>New Status</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                    <SelectContent>
                      <SelectItem value="active">Active</SelectItem>
                      <SelectItem value="paused">Paused (BIL)</SelectItem>
                      <SelectItem value="completed">Completed</SelectItem>
                      <SelectItem value="withdrawn">Withdrawn</SelectItem>
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )} />
              {watchedStatus === "withdrawn" && (
                <FormField control={statusForm.control} name="withdrawalDate" render={({ field }) => (
                  <FormItem><FormLabel>Withdrawal Date</FormLabel><FormControl><Input type="date" {...field} /></FormControl><FormMessage /></FormItem>
                )} />
              )}
              {watchedStatus === "completed" && (
                <FormField control={statusForm.control} name="actualEndDate" render={({ field }) => (
                  <FormItem><FormLabel>Actual End Date</FormLabel><FormControl><Input type="date" {...field} /></FormControl><FormMessage /></FormItem>
                )} />
              )}
              <FormField control={statusForm.control} name="reason" render={({ field }) => (
                <FormItem>
                  <FormLabel>Reason (Optional)</FormLabel>
                  <FormControl><Textarea {...field} placeholder="Why is this status changing?" rows={3} /></FormControl>
                  <FormMessage />
                </FormItem>
              )} />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setStatusDialogOpen(false)}>Cancel</Button>
                <Button type="submit" disabled={changeStatusMutation.isPending}>
                  {changeStatusMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                  Confirm Change
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
