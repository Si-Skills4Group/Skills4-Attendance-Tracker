import * as React from "react";
import { useGetTutor, useCreateTutor, useUpdateTutor, getGetTutorQueryKey } from "@workspace/api-client-react";
import { useLocation, useParams } from "wouter";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage, FormDescription } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Save, ArrowLeft } from "lucide-react";

const baseTutorSchema = z.object({
  firstName: z.string().min(1, "First name is required"),
  lastName: z.string().min(1, "Last name is required"),
  email: z.string().email("Valid email required"),
  employeeRef: z.string().min(1, "Employee Reference is required"),
  active: z.boolean().default(true),
  externalSystemId: z.string().optional(),
});

export default function TutorDetailPage() {
  const params = useParams();
  const isNew = !params.id || params.id === "new";
  const tutorId = isNew ? 0 : Number(params.id);
  
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  
  const { data: tutor, isLoading: isLoadingTutor } = useGetTutor(tutorId, {
    query: { enabled: !isNew, queryKey: getGetTutorQueryKey(tutorId) }
  });
  
  const createMutation = useCreateTutor();
  const updateMutation = useUpdateTutor();
  const isSaving = createMutation.isPending || updateMutation.isPending;

  const schema = isNew ? baseTutorSchema.extend({
    password: z.string().min(8, "Password must be at least 8 characters for new tutors")
  }) : baseTutorSchema.extend({
    password: z.string().min(8, "Password must be at least 8 characters").optional().or(z.literal(""))
  });

  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: {
      firstName: "",
      lastName: "",
      email: "",
      employeeRef: "",
      password: "",
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
        employeeRef: tutor.employeeRef,
        active: tutor.active,
        externalSystemId: tutor.externalSystemId || "",
        password: "", // never populate password
      });
    }
  }, [tutor, tutorId, form]);

  const onSubmit = (values: z.infer<typeof schema>) => {
    const payload = {
      ...values,
      // If password is empty string on update, remove it from payload
      password: values.password === "" ? undefined : values.password,
    };

    if (isNew) {
      createMutation.mutate({ data: payload as any }, {
        onSuccess: (res) => {
          toast({ title: "Tutor created", description: "The tutor profile has been created successfully." });
          setLocation("/tutors");
        },
        onError: (err: any) => toast({ title: "Error", description: err.error, variant: "destructive" })
      });
    } else {
      updateMutation.mutate({ id: tutorId, data: payload }, {
        onSuccess: () => {
          toast({ title: "Tutor updated", description: "Changes saved successfully." });
          setLocation("/tutors");
        },
        onError: (err: any) => toast({ title: "Error", description: err.error, variant: "destructive" })
      });
    }
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
            {isNew ? "Set up a new teaching staff account." : "Update details and access for this tutor."}
          </p>
        </div>
      </div>

      <div className="page-transition-enter stagger-1">
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
                <FormField control={form.control} name="employeeRef" render={({ field }) => (
                  <FormItem>
                    <FormLabel>Employee Reference</FormLabel>
                    <FormControl><Input {...field} placeholder="e.g. EMP-1002" /></FormControl>
                    <FormMessage />
                  </FormItem>
                )} />
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardHeader className="border-b bg-muted/10 pb-4">
                <CardTitle className="text-lg">System Access</CardTitle>
                <CardDescription>Authentication and integration settings.</CardDescription>
              </CardHeader>
              <CardContent className="pt-6 space-y-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <FormField control={form.control} name="password" render={({ field }) => (
                    <FormItem>
                      <FormLabel>Password {isNew ? "" : "(Leave blank to keep current)"}</FormLabel>
                      <FormControl><Input type="password" {...field} placeholder="••••••••" /></FormControl>
                      <FormMessage />
                    </FormItem>
                  )} />
                  <FormField control={form.control} name="externalSystemId" render={({ field }) => (
                    <FormItem>
                      <FormLabel>External System ID (Optional)</FormLabel>
                      <FormControl><Input {...field} placeholder="ID in HR system" /></FormControl>
                      <FormMessage />
                    </FormItem>
                  )} />
                </div>
                
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
              </CardContent>
            </Card>

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
    </div>
  );
}
