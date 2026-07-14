import * as React from "react";
import { Route, Switch, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";

// App Components
import { Shell } from "@/components/shell";

// Pages
import LoginPage from "@/pages/login";
import DashboardPage from "@/pages/dashboard";
import TutorsPage from "@/pages/tutors/index";
import TutorDetailPage from "@/pages/tutors/detail";
import TutorImportPage from "@/pages/tutors/import";
import UsersPage from "@/pages/users";
import LearnersPage from "@/pages/learners/index";
import LearnerDetailPage from "@/pages/learners/detail";
import LearnerImportPage from "@/pages/learners/import";
import CohortsPage from "@/pages/cohorts/index";
import CohortDetailPage from "@/pages/cohorts/detail";
import AllocationPage from "@/pages/allocation";
import AttendancePage from "@/pages/attendance/index";
import RegisterPage from "@/pages/attendance/register";
import ReportsPage from "@/pages/reports";
import AuditLogPage from "@/pages/audit-log";
import SettingsPage from "@/pages/settings";
import NotFound from "@/pages/not-found";

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
