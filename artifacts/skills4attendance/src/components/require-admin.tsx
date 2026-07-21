import * as React from "react";
import { useGetCurrentUser } from "@workspace/api-client-react";
import { Card, CardContent } from "@/components/ui/card";
import { ShieldAlert, Loader2 } from "lucide-react";

/**
 * Wraps an admin-only page. Backend routes already reject a non-admin with
 * a 403 regardless -- this exists so a Tutor hitting an admin URL directly
 * sees a clean "not authorized" state instead of the real page rendering
 * and then erroring out on every data fetch.
 */
export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { data: currentUser, isLoading } = useGetCurrentUser();

  if (isLoading) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;
  }

  if (currentUser?.role !== "admin") {
    return (
      <div className="p-6 md:p-8 max-w-5xl mx-auto w-full">
        <Card className="shadow-sm">
          <CardContent className="pt-6 flex flex-col items-center text-center py-16">
            <ShieldAlert className="w-10 h-10 text-muted-foreground mb-4" />
            <h1 className="text-xl font-semibold text-foreground">Not authorized</h1>
            <p className="text-muted-foreground mt-2 max-w-sm">
              This page is only available to Administrators. Contact an Administrator if you believe you should have access.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return <>{children}</>;
}
