import * as React from "react";
import { useListTutors, useListCohorts, useActivateTutor, useDeactivateTutor, Tutor } from "@workspace/api-client-react";
import { Link } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
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
import { Search, Plus, Upload, Mail, BookOpen, User } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

export default function TutorsPage() {
  const [searchQuery, setSearchQuery] = React.useState("");
  const [showActiveOnly, setShowActiveOnly] = React.useState(true);
  const [pendingDeactivation, setPendingDeactivation] = React.useState<{ tutor: Tutor; cohorts: { id: number; name: string }[] } | null>(null);

  const { data: tutors = [], isLoading, refetch } = useListTutors({
    active: showActiveOnly ? true : undefined
  });
  const { data: cohorts = [] } = useListCohorts({ active: true });

  const activateTutor = useActivateTutor();
  const deactivateTutor = useDeactivateTutor();
  const { toast } = useToast();

  const cohortCountByTutor = React.useMemo(() => {
    const counts = new Map<number, number>();
    for (const cohort of cohorts) {
      if (cohort.tutorId != null) {
        counts.set(cohort.tutorId, (counts.get(cohort.tutorId) ?? 0) + 1);
      }
    }
    return counts;
  }, [cohorts]);

  const filteredTutors = React.useMemo(() => {
    if (!searchQuery) return tutors;
    const lowerQuery = searchQuery.toLowerCase();
    return tutors.filter(t =>
      t.firstName.toLowerCase().includes(lowerQuery) ||
      t.lastName.toLowerCase().includes(lowerQuery) ||
      t.email.toLowerCase().includes(lowerQuery)
    );
  }, [tutors, searchQuery]);

  const handleToggleActive = (tutor: Tutor, newActive: boolean, confirm = false) => {
    if (newActive) {
      activateTutor.mutate({ id: tutor.id }, {
        onSuccess: () => {
          toast({ title: "Tutor activated", description: `${tutor.firstName} is now active.` });
          refetch();
        },
        onError: (err: any) => toast({ title: "Activation failed", description: err?.data?.error || err.message, variant: "destructive" }),
      });
      return;
    }

    deactivateTutor.mutate({ id: tutor.id, params: { confirm } }, {
      onSuccess: () => {
        setPendingDeactivation(null);
        toast({ title: "Tutor deactivated", description: `${tutor.firstName} is now inactive.` });
        refetch();
      },
      onError: (err: any) => {
        const data = err?.data;
        if (err?.status === 409 && data?.cohorts) {
          setPendingDeactivation({ tutor, cohorts: data.cohorts });
          return;
        }
        toast({ title: "Deactivation failed", description: data?.error || err.message, variant: "destructive" });
      },
    });
  };

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Tutors" }]} />

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Tutors</h1>
          <p className="text-muted-foreground mt-1">Manage teaching staff and their system access.</p>
        </div>
        <div className="flex gap-2">
          <Link href="/tutors/import">
            <Button variant="outline" className="shadow-sm">
              <Upload className="w-4 h-4 mr-2" /> Import CSV
            </Button>
          </Link>
          <Link href="/tutors/new">
            <Button className="hover-elevate shadow-sm">
              <Plus className="w-4 h-4 mr-2" /> Add Tutor
            </Button>
          </Link>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 mb-6 page-transition-enter stagger-1">
        <div className="relative w-full sm:w-96">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search tutors by name or email..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 h-10 bg-card"
          />
        </div>
        <div className="flex items-center space-x-2 shrink-0">
          <Switch
            id="active-only"
            checked={showActiveOnly}
            onCheckedChange={setShowActiveOnly}
          />
          <Label htmlFor="active-only" className="cursor-pointer">Active Only</Label>
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-20">
          <div className="w-8 h-8 rounded-full border-4 border-primary border-t-transparent animate-spin"></div>
        </div>
      ) : filteredTutors.length === 0 ? (
        <Card className="border-dashed bg-muted/10 page-transition-enter stagger-2">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <User className="w-12 h-12 text-muted-foreground/30 mb-4" />
            <h3 className="text-lg font-semibold text-foreground mb-1">No tutors found</h3>
            <p className="text-sm text-muted-foreground max-w-sm">
              {searchQuery ? "No tutors match your search criteria. Try a different query." : "No tutors have been added yet."}
            </p>
            {!searchQuery && (
              <Link href="/tutors/new" className="mt-4">
                <Button variant="outline">Add your first tutor</Button>
              </Link>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 page-transition-enter stagger-2">
          {filteredTutors.map((tutor) => (
            <Card key={tutor.id} className={`overflow-hidden transition-all hover:border-primary/30 hover:shadow-md ${!tutor.active ? 'opacity-70 bg-muted/20' : ''}`}>
              <div className="p-5">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold shrink-0 ${tutor.active ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>
                      {tutor.firstName[0]}{tutor.lastName[0]}
                    </div>
                    <div>
                      <Link href={`/tutors/${tutor.id}`}>
                        <h3 className="font-semibold text-base hover:text-primary hover:underline cursor-pointer">
                          {tutor.firstName} {tutor.lastName}
                        </h3>
                      </Link>
                      <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1">
                        <BookOpen className="w-3 h-3" /> {cohortCountByTutor.get(tutor.id) ?? 0} cohort{(cohortCountByTutor.get(tutor.id) ?? 0) === 1 ? "" : "s"}
                      </p>
                    </div>
                  </div>
                  <Switch
                    checked={tutor.active}
                    onCheckedChange={(v) => handleToggleActive(tutor, v)}
                    aria-label="Toggle active status"
                  />
                </div>

                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Mail className="w-4 h-4 shrink-0" />
                  <a href={`mailto:${tutor.email}`} className="hover:text-foreground truncate">{tutor.email}</a>
                </div>
              </div>
              <div className="bg-muted/30 px-5 py-3 border-t text-xs flex justify-between items-center">
                <span className={tutor.active ? "text-emerald-600 font-medium" : "text-muted-foreground"}>
                  {tutor.active ? "Active" : "Inactive"}
                </span>
                <Link href={`/tutors/${tutor.id}`} className="text-primary font-medium hover:underline">
                  Edit Profile
                </Link>
              </div>
            </Card>
          ))}
        </div>
      )}

      <AlertDialog open={!!pendingDeactivation} onOpenChange={(open) => !open && setPendingDeactivation(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Deactivate {pendingDeactivation?.tutor.firstName}?</AlertDialogTitle>
            <AlertDialogDescription>
              This tutor has {pendingDeactivation?.cohorts.length} active cohort{pendingDeactivation?.cohorts.length === 1 ? "" : "s"} assigned:
              <span className="block mt-2 font-medium text-foreground">
                {pendingDeactivation?.cohorts.map((c) => c.name).join(", ")}
              </span>
              These cohorts will keep their tutor assignment even though the tutor becomes inactive. Continue?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => pendingDeactivation && handleToggleActive(pendingDeactivation.tutor, false, true)}>
              Deactivate anyway
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
