import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { useLogin } from "@workspace/api-client-react";
import { useLocation } from "wouter";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { GraduationCap, ArrowRight, Loader2 } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

const loginSchema = z.object({
  email: z.string().email("Please enter a valid email address"),
  password: z.string().min(1, "Password is required"),
});

export default function LoginPage() {
  const loginMutation = useLogin();
  const [, setLocation] = useLocation();
  const { toast } = useToast();

  const form = useForm<z.infer<typeof loginSchema>>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  const onSubmit = (values: z.infer<typeof loginSchema>) => {
    loginMutation.mutate({ data: values }, {
      onSuccess: () => {
        setLocation("/dashboard");
      },
      onError: (err: any) => {
        toast({
          variant: "destructive",
          title: "Login Failed",
          description: err.error || "Please check your credentials and try again.",
        });
      }
    });
  };

  return (
    <div className="min-h-screen bg-background flex flex-col sm:flex-row">
      {/* Brand Side */}
      <div className="hidden sm:flex sm:w-1/2 lg:w-3/5 bg-sidebar flex-col justify-between p-12 text-sidebar-foreground relative overflow-hidden">
        <div className="absolute inset-0 z-0 opacity-10" style={{ 
          backgroundImage: `url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='1'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E")`
        }}></div>
        <div className="relative z-10">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 bg-sidebar-primary rounded-xl flex items-center justify-center text-sidebar shadow-lg">
              <GraduationCap className="w-7 h-7" />
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-sidebar-primary-foreground">Skills4Group</h1>
          </div>
        </div>
        <div className="relative z-10 max-w-lg mt-auto">
          <h2 className="text-4xl lg:text-5xl font-bold tracking-tight text-white mb-6 leading-tight">
            Attendance & Allocation Management
          </h2>
          <p className="text-lg text-sidebar-foreground/80 font-medium">
            A dependable workspace for tutors and administrators to track learner progress, manage allocations, and record attendance with precision.
          </p>
        </div>
      </div>

      {/* Login Side */}
      <div className="flex-1 flex flex-col justify-center px-8 sm:px-12 lg:px-24 xl:px-32 relative">
        <div className="sm:hidden flex items-center gap-2 mb-12">
          <div className="w-10 h-10 bg-primary rounded-lg flex items-center justify-center text-primary-foreground shadow-md">
            <GraduationCap className="w-6 h-6" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">Skills4Group</h1>
        </div>

        <div className="w-full max-w-md mx-auto page-transition-enter">
          <div className="mb-8">
            <h2 className="text-3xl font-bold tracking-tight text-foreground mb-2">Welcome back</h2>
            <p className="text-muted-foreground">Sign in to your account to continue</p>
          </div>

          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm font-semibold">Email Address</FormLabel>
                    <FormControl>
                      <Input 
                        placeholder="name@example.com" 
                        {...field} 
                        className="h-12 bg-card text-base"
                        autoComplete="email"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm font-semibold flex justify-between">
                      Password
                    </FormLabel>
                    <FormControl>
                      <Input 
                        type="password" 
                        placeholder="••••••••" 
                        {...field} 
                        className="h-12 bg-card text-base"
                        autoComplete="current-password"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button 
                type="submit" 
                className="w-full h-12 text-base font-semibold group hover-elevate shadow-sm" 
                disabled={loginMutation.isPending}
              >
                {loginMutation.isPending ? (
                  <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                ) : (
                  <>Sign In <ArrowRight className="w-4 h-4 ml-2 group-hover:translate-x-1 transition-transform" /></>
                )}
              </Button>
            </form>
          </Form>
        </div>
      </div>
    </div>
  );
}
