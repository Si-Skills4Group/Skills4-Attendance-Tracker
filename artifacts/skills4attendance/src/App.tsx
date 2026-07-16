import * as React from "react";
import { Route, Switch, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";

// App Components
import { Shell } from "@/components/shell";
import { PageLoadingSpinner } from "@/components/page-loading-spinner";

// Pages
import LoginPage from "@/pages/login";
const DashboardPage = React.lazy(() => import("@/pages/dashboard"));
const TutorsPage = React.lazy(() => import("@/pages/tutors/index"));
const TutorDetailPage = React.lazy(() => import("@/pages/tutors/detail"));
const TutorImportPage = React.lazy(() => import("@/pages/tutors/import"));
const UsersPage = React.lazy(() => import("@/pages/users"));
const LearnersPage = React.lazy(() => import("@/pages/learners/index"));
const LearnerDetailPage = React.lazy(() => import("@/pages/learners/detail"));
const LearnerImportPage = React.lazy(() => import("@/pages/learners/import"));
const CohortsPage = React.lazy(() => import("@/pages/cohorts/index"));
const CohortDetailPage = React.lazy(() => import("@/pages/cohorts/detail"));
const AllocationPage = React.lazy(() => import("@/pages/allocation"));
const AttendancePage = React.lazy(() => import("@/pages/attendance/index"));
const CohortSessionsPage = React.lazy(() => import("@/pages/attendance/cohort-sessions"));
const RegisterPage = React.lazy(() => import("@/pages/attendance/register"));
const ReportsPage = React.lazy(() => import("@/pages/reports"));
const AuditLogPage = React.lazy(() => import("@/pages/audit-log"));
const SettingsPage = React.lazy(() => import("@/pages/settings"));
const NotFound = React.lazy(() => import("@/pages/not-found"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});

function ProtectedRouter() {
  return (
    <Shell>
      <React.Suspense fallback={<PageLoadingSpinner />}>
        <Switch>
          <Route path="/dashboard" component={DashboardPage} />
          <Route path="/tutors" component={TutorsPage} />
          <Route path="/tutors/import" component={TutorImportPage} />
          <Route path="/tutors/:id" component={TutorDetailPage} />
          <Route path="/users" component={UsersPage} />
          <Route path="/learners" component={LearnersPage} />
          <Route path="/learners/import" component={LearnerImportPage} />
          <Route path="/learners/:id" component={LearnerDetailPage} />
          <Route path="/cohorts" component={CohortsPage} />
          <Route path="/cohorts/:id" component={CohortDetailPage} />
          <Route path="/allocation" component={AllocationPage} />
          <Route path="/attendance" component={AttendancePage} />
          <Route path="/attendance/cohorts/:id" component={CohortSessionsPage} />
          <Route path="/attendance/:id" component={RegisterPage} />
          <Route path="/reports" component={ReportsPage} />
          <Route path="/audit-log" component={AuditLogPage} />
          <Route path="/settings" component={SettingsPage} />
          <Route path="/" component={() => {
            window.location.href = "/dashboard";
            return null;
          }} />
          <Route component={NotFound} />
        </Switch>
      </React.Suspense>
    </Shell>
  );
}

function AppRouter() {
  return (
    <Switch>
      <Route path="/login" component={LoginPage} />
      <Route component={ProtectedRouter} />
    </Switch>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <AppRouter />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
