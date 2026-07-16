import * as React from "react";
import { useGetCohort, useCreateCohort, useUpdateCohort, useActivateCohort, useDeactivateCohort, useGetCohortLearners, useListTutors, useGetCurrentUser, getGetCohortQueryKey, getGetCohortLearnersQueryKey, getListTutorsQueryKey } from "@workspace/api-client-react";
import { useLocation, useParams, Link } from "wouter";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage, FormDescription } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Save, ArrowLeft, Users } from "lucide-react";
import { LearnerStatusBadge } from "@/components/status-badges";
import { format, parseISO } from "date-fns";

const cohortSchema = z.object({
  name: z.string().min(1, "Cohort name is required"),
  programme: z.string().min(1, "Programme is required"),
  level: z.string().min(1, "Level is required"),
  tutorId: z.coerce.number().optional(),
  deliveryDay: z.enum(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]),
  sessionStartTime: z.string().min(1, "Start time is required"),
  sessionEndTime: z.string().min(1, "End time is required"),
  startDate: z.string().min(1, "Start date is required"),
  endDate: z.string().optional(),
  active: z.boolean().default(true),
  externalSystemId: z.string().optional()
}).refine((data) => data.sessionEndTime > data.sessionStartTime, {
  message: "End time must be after start time",
  path: ["sessionEndTime"],
}).refine((data) => !data.endDate || data.endDate > data.startDate, {
  message: "End date cannot be before start date",
  path: ["endDate"],
});

export default function CohortDetailPage() {
  const params = useParams();
  const isNew = !params.id || params.id === "new";
  const cohortId = isNew ? 0 : Number(params.id);
  
  const { data: currentUser } = useGetCurrentUser();
  const isAdmin = currentUser?.role === 'admin';

  const [, setLocation] = useLocation();
  const { toast } = useToast();
  
  const { data: cohort, isLoading: isLoadingCohort } = useGetCohort(cohortId, {
    query: { enabled: !isNew, queryKey: getGetCohortQueryKey(cohortId) }
  });

  const { data: learners = [] } = useGetCohortLearners(cohortId, {
    query: { enabled: !isNew, queryKey: getGetCohortLearnersQueryKey(cohortId) }
  });

  const { data: tutors = [] } = useListTutors({ active: true }, {
    query: { enabled: isAdmin, queryKey: getListTutorsQueryKey({ active: true }) } // Only admins need the full list to assign
  });
  
  const createMutation = useCreateCohort();
  const updateMutation = useUpdateCohort();
  const activateMutation = useActivateCohort();
  const deactivateMutation = useDeactivateCohort();
  const isSaving = createMutation.isPending || updateMutation.isPending;

  const handleToggleActive = (newActive: boolean) => {
    const mutation = newActive ? activateMutation : deactivateMutation;
    mutation.mutate({ id: cohortId }, {
      onSuccess: () => toast({ title: newActive ? "Cohort activated" : "Cohort deactivated" }),
      onError: (err: any) => toast({ title: "Update failed", description: err?.data?.error || err.message, variant: "destructive" }),
    });
  };

  const form = useForm<z.infer<typeof cohortSchema>>({
    resolver: zodResolver(cohortSchema),
    defaultValues: {
      name: "",
      programme: "",
      level: "",
      tutorId: undefined,
      deliveryDay: "monday",
      sessionStartTime: "09:00:00",
      sessionEndTime: "16:00:00",
      startDate: format(new Date(), "yyyy-MM-dd"),
      endDate: "",
      active: true,
      externalSystemId: ""
    }
  });

  const initializedForId = React.useRef<number | null>(null);
  React.useEffect(() => {
    if (cohort && initializedForId.current !== cohortId) {
      initializedForId.current = cohortId;
      form.reset({
        name: cohort.name,
        programme: cohort.programme,
        level: cohort.level,
        tutorId: cohort.tutorId || undefined,
        deliveryDay: cohort.deliveryDay as any,
        sessionStartTime: cohort.sessionStartTime,
        sessionEndTime: cohort.sessionEndTime,
        startDate: cohort.startDate.split('T')[0],
        endDate: cohort.endDate ? cohort.endDate.split('T')[0] : "",
        active: cohort.active,
        externalSystemId: cohort.externalSystemId || ""
      });
    }
  }, [cohort, cohortId, form]);

  const onSubmit = (values: z.infer<typeof cohortSchema>) => {
    if (!isAdmin) return; // Prevent submission if not admin, though UI should hide save button
    
    // Convert 09:00 to 09:00:00 for API if needed, standard input time might do HH:mm
    const formatTime = (t: string) => t.length === 5 ? `${t}:00` : t;

    const payload = {
      ...values,
      sessionStartTime: formatTime(values.sessionStartTime),
      sessionEndTime: formatTime(values.sessionEndTime),
      tutorId: values.tutorId ? Number(values.tutorId) : undefined,
      endDate: values.endDate || undefined,
      externalSystemId: values.externalSystemId || undefined
    };

    if (isNew) {
      createMutation.mutate({ data: payload as any }, {
        onSuccess: () => {
          toast({ title: "Cohort created" });
          setLocation("/cohorts");
        },
        onError: (err: any) => toast({ title: "Error", description: err?.data?.error || err.message, variant: "destructive" })
      });
    } else {
      const { active: _active, ...rest } = payload;
      updateMutation.mutate({ id: cohortId, data: rest }, {
        onSuccess: () => {
          toast({ title: "Cohort updated" });
          setLocation("/cohorts");
        },
        onError: (err: any) => toast({ title: "Error", description: err?.data?.error || err.message, variant: "destructive" })
      });
    }
  };

  if (!isNew && isLoadingCohort) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;
  }

  // Tutors cannot edit cohorts
  const readOnly = !isAdmin;

  return (
    <div className="p-6 md:p-8 max-w-5xl mx-auto w-full">
      <Breadcrumbs items={[
        { label: "Cohorts", href: "/cohorts" },
        { label: isNew ? "New Cohort" : cohort?.name || "" }
      ]} />
      
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8 page-transition-enter">
        <div className="flex items-center gap-4">
          <Button variant="outline" size="icon" onClick={() => setLocation("/cohorts")}>
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-foreground">
              {isNew ? "Create Cohort" : cohort?.name}
            </h1>
            <p className="text-muted-foreground mt-1 flex items-center gap-2">
              {!isNew && cohort && (
                <>
                  <span className={cohort.active ? "text-emerald-600 font-medium" : "text-muted-foreground"}>{cohort.active ? "Active" : "Inactive"}</span>
                  <span>•</span>
                  <span>{cohort.programme} (L{cohort.level})</span>
                </>
              )}
              {isNew && "Set up a new delivery group."}
            </p>
          </div>
        </div>
        {!isNew && isAdmin && (
          <Button onClick={() => form.handleSubmit(onSubmit)()} disabled={isSaving} className="hover-elevate shadow-sm">
            {isSaving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
            Save Changes
          </Button>
        )}
      </div>

      <div className="page-transition-enter stagger-1">
        <Tabs defaultValue="details" className="w-full">
          {!isNew && (
            <TabsList className="grid w-full grid-cols-2 max-w-md mb-6">
              <TabsTrigger value="details">Cohort Details</TabsTrigger>
              <TabsTrigger value="roster">Learner Roster ({learners.length})</TabsTrigger>
            </TabsList>
          )}

          <TabsContent value="details" className="space-y-6">
            <Form {...form}>
              <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
                <div className="max-w-2xl">
                  <Card className="shadow-sm">
                    <CardHeader className="border-b bg-muted/10 pb-4">
                      <CardTitle className="text-lg">Group Information</CardTitle>
                    </CardHeader>
                    <CardContent className="pt-6 space-y-4">
                      <FormField control={form.control} name="name" render={({ field }) => (
                        <FormItem><FormLabel>Cohort Name</FormLabel><FormControl><Input {...field} disabled={readOnly} /></FormControl><FormMessage /></FormItem>
                      )} />
                      <div className="grid grid-cols-2 gap-4">
                        <FormField control={form.control} name="programme" render={({ field }) => (
                          <FormItem><FormLabel>Programme</FormLabel><FormControl><Input {...field} disabled={readOnly} /></FormControl><FormMessage /></FormItem>
                        )} />
                        <FormField control={form.control} name="level" render={({ field }) => (
                          <FormItem><FormLabel>Level</FormLabel><FormControl><Input {...field} disabled={readOnly} /></FormControl><FormMessage /></FormItem>
                        )} />
                      </div>
                      <FormField control={form.control} name="tutorId" render={({ field }) => (
                        <FormItem className="flex flex-col">
                          <FormLabel>Primary Tutor</FormLabel>
                          <FormControl>
                            <Combobox
                              options={[
                                { value: "", label: "Unassigned" },
                                ...tutors.map(t => ({ value: String(t.id), label: `${t.firstName} ${t.lastName}` })),
                              ]}
                              value={field.value ? String(field.value) : ""}
                              onValueChange={field.onChange}
                              placeholder="Unassigned"
                              searchPlaceholder="Search tutors..."
                              disabled={readOnly}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )} />
                      {isNew ? (
                        <FormField control={form.control} name="active" render={({ field }) => (
                          <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4 bg-card mt-6">
                            <div className="space-y-0.5">
                              <FormLabel className="text-base">Active Cohort</FormLabel>
                              <FormDescription>Inactive cohorts won't appear in attendance creation.</FormDescription>
                            </div>
                            <FormControl><Switch checked={field.value} onCheckedChange={field.onChange} disabled={readOnly} /></FormControl>
                          </FormItem>
                        )} />
                      ) : (
                        <div className="flex flex-row items-center justify-between rounded-lg border p-4 bg-card mt-6">
                          <div className="space-y-0.5">
                            <p className="text-base font-medium">Active Cohort</p>
                            <p className="text-sm text-muted-foreground">Inactive cohorts won't appear in attendance creation.</p>
                          </div>
                          <Switch checked={cohort?.active ?? false} onCheckedChange={handleToggleActive} disabled={readOnly} aria-label="Toggle active status" />
                        </div>
                      )}
                    </CardContent>
                  </Card>
                </div>

                {isNew && (
                  <div className="flex justify-end gap-4">
                    <Button type="button" variant="outline" onClick={() => setLocation("/cohorts")}>Cancel</Button>
                    <Button type="submit" disabled={isSaving} className="hover-elevate shadow-sm">
                      {isSaving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                      Create Cohort
                    </Button>
                  </div>
                )}
              </form>
            </Form>
          </TabsContent>

          {!isNew && (
            <TabsContent value="roster">
              <Card className="shadow-sm">
                <CardHeader className="border-b bg-muted/10">
                  <CardTitle className="flex items-center justify-between text-lg">
                    <span>Learners ({learners.length})</span>
                    {isAdmin && (
                      <Link href="/allocation">
                        <Button variant="outline" size="sm">Manage Allocation</Button>
                      </Link>
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  {learners.length === 0 ? (
                    <div className="p-12 text-center text-muted-foreground flex flex-col items-center">
                      <Users className="w-12 h-12 text-muted-foreground/30 mb-3" />
                      <p>No learners assigned to this cohort yet.</p>
                      {isAdmin && (
                        <p className="text-sm mt-1">Use the Allocation tool to assign learners.</p>
                      )}
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Learner</TableHead>
                            <TableHead>Status</TableHead>
                            <TableHead className="text-right">Enrolled</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {learners.map(l => (
                            <TableRow key={l.id} className="hover:bg-muted/30">
                              <TableCell>
                                <Link href={`/learners/${l.id}`} className="font-medium hover:text-primary hover:underline">
                                  {l.firstName} {l.lastName}
                                </Link>
                                <div className="text-xs text-muted-foreground font-mono">{l.learnerRef}</div>
                              </TableCell>
                              <TableCell><LearnerStatusBadge status={l.status} /></TableCell>
                              <TableCell className="text-right text-sm text-muted-foreground">
                                {format(parseISO(l.startDate), "MMM d, yyyy")}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          )}
        </Tabs>
      </div>
    </div>
  );
}
