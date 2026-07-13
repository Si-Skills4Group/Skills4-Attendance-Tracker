import * as React from "react";
import { useGetSettings, useUpdateSettings } from "@workspace/api-client-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage, FormDescription } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/use-toast";
import { Loader2, Save, Building2 } from "lucide-react";

const settingsSchema = z.object({
  organisationName: z.string().min(1, "Organisation name is required"),
  lowAttendanceThreshold: z.coerce.number().min(0).max(100, "Threshold must be between 0 and 100")
});

export default function SettingsPage() {
  const { toast } = useToast();
  const { data: settings, isLoading } = useGetSettings();
  const updateMutation = useUpdateSettings();

  const form = useForm<z.infer<typeof settingsSchema>>({
    resolver: zodResolver(settingsSchema),
    defaultValues: {
      organisationName: "",
      lowAttendanceThreshold: 80
    }
  });

  const initializedRef = React.useRef(false);
  React.useEffect(() => {
    if (settings && !initializedRef.current) {
      initializedRef.current = true;
      form.reset({
        organisationName: settings.organisationName,
        lowAttendanceThreshold: settings.lowAttendanceThreshold
      });
    }
  }, [settings, form]);

  const onSubmit = (values: z.infer<typeof settingsSchema>) => {
    updateMutation.mutate({ data: values }, {
      onSuccess: () => {
        toast({ title: "Settings updated successfully" });
      },
      onError: (err: any) => {
        toast({ title: "Failed to update settings", description: err.error, variant: "destructive" });
      }
    });
  };

  if (isLoading) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;
  }

  return (
    <div className="p-6 md:p-8 max-w-4xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Settings" }]} />
      
      <div className="mb-8 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground">System Settings</h1>
        <p className="text-muted-foreground mt-1">Configure organisation-wide preferences.</p>
      </div>

      <div className="page-transition-enter stagger-1">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
            <Card className="shadow-sm">
              <CardHeader className="border-b bg-muted/10 pb-4">
                <CardTitle className="text-lg flex items-center gap-2">
                  <Building2 className="w-5 h-5 text-primary" /> General Configuration
                </CardTitle>
                <CardDescription>Basic details and global thresholds.</CardDescription>
              </CardHeader>
              <CardContent className="pt-6 space-y-6">
                <FormField control={form.control} name="organisationName" render={({ field }) => (
                  <FormItem>
                    <FormLabel>Organisation Name</FormLabel>
                    <FormControl><Input {...field} /></FormControl>
                    <FormDescription>Displayed in reports and exports.</FormDescription>
                    <FormMessage />
                  </FormItem>
                )} />
                <FormField control={form.control} name="lowAttendanceThreshold" render={({ field }) => (
                  <FormItem>
                    <FormLabel>Low Attendance Threshold (%)</FormLabel>
                    <FormControl>
                      <div className="relative max-w-[200px]">
                        <Input type="number" {...field} className="pr-8 font-mono" />
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground">%</span>
                      </div>
                    </FormControl>
                    <FormDescription>
                      Learners falling below this percentage will be flagged on dashboards.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )} />
              </CardContent>
            </Card>

            <div className="flex justify-end">
              <Button type="submit" disabled={updateMutation.isPending} className="hover-elevate shadow-sm">
                {updateMutation.isPending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                Save Settings
              </Button>
            </div>
          </form>
        </Form>
      </div>
    </div>
  );
}
