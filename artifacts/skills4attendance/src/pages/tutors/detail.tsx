import * as React from "react";
import { useGetTutor, useCreateTutor, useUpdateTutor, useActivateTutor, useDeactivateTutor, useListCohorts, getGetTutorQueryKey, getListCohortsQueryKey } from "@workspace/api-client-react";
import { useLocation, useParams, Link } from "wouter";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage, FormDescription } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useToast } from "@/hooks/use-toast";
import { getErrorMessage } from "@/lib/errors";
import { Loader2, Save, ArrowLeft, BookOpen } from "lucide-react";

const baseTutorSchema = z.object({
  firstName: z.string().min(1, "First name is required"),
  lastName: z.string().min(1, "Last name is required"),
  email: z.string().email("Valid email required"),
  employeeRef: z.string().optional(),
  phone: z.string().optional(),
  active: z.boolean().default(true),
  externalSystemId: z.string().optional(),
});

export default function TutorDetailPage() {
  const params = useParams();
  const isNew = !params.id || params.id === "new";
  const tutorId = isNew ? 0 : Number(params.id);

  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const [pendingDeactivation, setPendingDeactivation] = React.useState<{ id: number; name: string }[] | null>(null);

  const { data: tutor, isLoading: isLoadingTutor } = useGetTutor(tutorId, {
    query: { enabled: !isNew, queryKey: getGetTutorQueryKey(tutorId) }
  });
  const { data: assignedCohorts = [] } = useListCohorts({ tutorId }, {
    query: { enabled: !isNew, queryKey: getListCohortsQueryKey({ tutorId }) },
  });

  const createMutation = useCreateTutor();
  const updateMutation = useUpdateTutor();
  const activateMutation = useActivateTutor();
  const deactivateMutation = useDeactivateTutor();
  const isSaving = createMutation.isPending || updateMutation.isPending;

  const schema = baseTutorSchema;

  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: {
      firstName: "",
      lastName: "",
      email: "",
      employeeRef: "",
      phone: "",
      active: true,
      externalSystemId: "",
    }
  });

  // Populate form when data loads
  const initializedForId = React.useRef<number | null>(null);
  React.useEffect(() => {
    if (tutor && initializedForId.current !== tutorId) {
      initializedForId.current = tutorId;
      form.reset({
        firstName: tutor.firstName,
        lastName: tutor.lastName,
        email: tutor.email,
        employeeRef: tutor.employeeRef || "",
        phone: tutor.phone || "",
        active: tutor.active,
        externalSystemId: tutor.externalSystemId || "",
      });
    }
  }, [tutor, tutorId, form]);

  const onSubmit = (values: z.infer<typeof schema>) => {
    const payload = {
      ...values,
      employeeRef: values.employeeRef?.trim() || undefined,
      phone: values.phone?.trim() || undefined,
    };

    if (isNew) {
      createMutation.mutate({ data: payload as any }, {
        onSuccess: (res) => {
          toast({ title: "Tutor created", description: "The tutor profile has been created successfully." });
          setLocation("/tutors");
        },
        onError: (err) => toast({ title: "Error", description: getErrorMessage(err), variant: "destructive" })
      });
    } else {
      const { active: _active, ...rest } = payload;
      updateMutation.mutate({ id: tutorId, data: rest }, {
        onSuccess: () => {
          toast({ title: "Tutor updated", description: "Changes saved successfully." });
          setLocation("/tutors");
        },
        onError: (err) => toast({ title: "Error", description: getErrorMessage(err), variant: "destructive" })
      });
    }
  };

  const handleToggleActive = (newActive: boolean, confirm = false) => {
    if (newActive) {
      activateMutation.mutate({ id: tutorId }, {
        onSuccess: () => {
          toast({ title: "Tutor activated" });
        },
        onError: (err) => toast({ title: "Activation failed", description: getErrorMessage(err), variant: "destructive" }),
      });
      return;
    }

    deactivateMutation.mutate({ id: tutorId, params: { confirm } }, {
      onSuccess: () => {
        setPendingDeactivation(null);
        toast({ title: "Tutor deactivated" });
      },
      onError: (err) => {
        const data = (err as { data?: { cohorts?: { id: number; name: string }[] } } | undefined)?.data;
        const status = (err as { status?: number } | undefined)?.status;
        if (status === 409 && data?.cohorts) {
          setPendingDeactivation(data.cohorts);
          return;
        }
        toast({ title: "Deactivation failed", description: getErrorMessage(err), variant: "destructive" });
      },
    });
  };

  if (!isNew && isLoadingTutor) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;
  }

  return (
    <div className="p-6 md:p-8 max-w-4xl mx-auto w-full">
      <Breadcrumbs items={[
        { label: "Tutors", href: "/tutors" },
        { label: isNew ? "New Tutor" : `${tutor?.firstName} ${tutor?.lastName}` }
      ]} />

      <div className="flex items-center gap-4 mb-8 page-transition-enter">
        <Button variant="outline" size="icon" onClick={() => setLocation("/tutors")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            {isNew ? "Create Tutor Profile" : "Edit Tutor Profile"}
          </h1>
          <p className="text-muted-foreground mt-1">
            {isNew ? "Set up a new tutor profile." : "Update details for this tutor."}
          </p>
        </div>
      </div>

      <div className="page-transition-enter stagger-1 space-y-6">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
            <Card className="shadow-sm">
              <CardHeader className="border-b bg-muted/10 pb-4">
                <CardTitle className="text-lg">Personal Information</CardTitle>
                <CardDescription>Basic contact and identification details.</CardDescription>
              </CardHeader>
              <CardContent className="pt-6 grid grid-cols-1 md:grid-cols-2 gap-6">
                <FormField control={form.control} name="firstName" render={({ field }) => (
                  <FormItem>
                    <FormLabel>First Name</FormLabel>
                    <FormControl><Input {...field} /></FormControl>
                    <FormMessage />
                  </FormItem>
                )} />
                <FormField control={form.control} name="lastName" render={({ field }) => (
                  <FormItem>
                    <FormLabel>Last Name</FormLabel>
                    <FormControl><Input {...field} /></FormControl>
                    <FormMessage />
                  </FormItem>
                )} />
                <FormField control={form.control} name="email" render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email Address</FormLabel>
                    <FormControl><Input type="email" {...field} /></FormControl>
                    <FormMessage />
                  </FormItem>
                )} />
                <FormField control={form.control} name="phone" render={({ field }) => (
                  <FormItem>
                    <FormLabel>Telephone (Optional)</FormLabel>
                    <FormControl><Input {...field} placeholder="e.g. 07700 900123" /></FormControl>
                    <FormMessage />
                  </FormItem>
                )} />
                <FormField control={form.control} name="employeeRef" render={({ field }) => (
                  <FormItem>
                    <FormLabel>Employee Reference (Optional)</FormLabel>
                    <FormControl><Input {...field} placeholder="e.g. EMP-1002" /></FormControl>
                    <FormMessage />
                  </FormItem>
                )} />
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardHeader className="border-b bg-muted/10 pb-4">
                <CardTitle className="text-lg">Status & Integration</CardTitle>
                <CardDescription>Application status and external-system reference.</CardDescription>
              </CardHeader>
              <CardContent className="pt-6 space-y-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <FormField control={form.control} name="externalSystemId" render={({ field }) => (
                    <FormItem>
                      <FormLabel>External System ID (Optional)</FormLabel>
                      <FormControl><Input {...field} placeholder="ID in HR system" /></FormControl>
                      <FormMessage />
                    </FormItem>
                  )} />
                </div>

                {isNew ? (
                  <FormField control={form.control} name="active" render={({ field }) => (
                    <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4 bg-card">
                      <div className="space-y-0.5">
                        <FormLabel className="text-base">Active Account</FormLabel>
                        <FormDescription>
                          Inactive tutors cannot log in or be assigned new cohorts.
                        </FormDescription>
                      </div>
                      <FormControl>
                        <Switch checked={field.value} onCheckedChange={field.onChange} />
                      </FormControl>
                    </FormItem>
                  )} />
                ) : (
                  <div className="flex flex-row items-center justify-between rounded-lg border p-4 bg-card">
                    <div className="space-y-0.5">
                      <p className="text-base font-medium">Active Account</p>
                      <p className="text-sm text-muted-foreground">
                        Inactive tutors cannot log in or be assigned new cohorts.
                      </p>
                    </div>
                    <Switch
                      checked={tutor?.active ?? false}
                      onCheckedChange={handleToggleActive}
                      aria-label="Toggle active status"
                    />
                  </div>
                )}
              </CardContent>
            </Card>

            {!isNew && (
              <Card className="shadow-sm">
                <CardHeader className="border-b bg-muted/10 pb-4">
                  <CardTitle className="text-lg flex items-center gap-2">
                    <BookOpen className="w-4 h-4" /> Assigned Cohorts
                  </CardTitle>
                  <CardDescription>Cohorts this tutor currently delivers.</CardDescription>
                </CardHeader>
                <CardContent className="pt-6">
                  {assignedCohorts.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No cohorts assigned yet.</p>
                  ) : (
                    <div className="space-y-2">
                      {assignedCohorts.map((cohort) => (
                        <Link key={cohort.id} href={`/cohorts/${cohort.id}`}>
                          <div className="flex items-center justify-between p-3 rounded-md border hover:border-primary/30 hover:bg-muted/20 cursor-pointer">
                            <div>
                              <p className="font-medium text-sm">{cohort.name}</p>
                              <p className="text-xs text-muted-foreground">{cohort.programme} &middot; {cohort.level}</p>
                            </div>
                            <Badge variant={cohort.active ? "default" : "secondary"}>
                              {cohort.active ? "Active" : "Inactive"}
                            </Badge>
                          </div>
                        </Link>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            <div className="flex justify-end gap-4">
              <Button type="button" variant="outline" onClick={() => setLocation("/tutors")}>
                Cancel
              </Button>
              <Button type="submit" disabled={isSaving} className="hover-elevate shadow-sm min-w-[120px]">
                {isSaving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                {isNew ? "Create Tutor" : "Save Changes"}
              </Button>
            </div>
          </form>
        </Form>
      </div>

      <AlertDialog open={!!pendingDeactivation} onOpenChange={(open) => !open && setPendingDeactivation(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Deactivate this tutor?</AlertDialogTitle>
            <AlertDialogDescription>
              This tutor has {pendingDeactivation?.length} active cohort{pendingDeactivation?.length === 1 ? "" : "s"} assigned:
              <span className="block mt-2 font-medium text-foreground">
                {pendingDeactivation?.map((c) => c.name).join(", ")}
              </span>
              These cohorts will keep their tutor assignment even though the tutor becomes inactive. Continue?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => handleToggleActive(false, true)}>
              Deactivate anyway
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
