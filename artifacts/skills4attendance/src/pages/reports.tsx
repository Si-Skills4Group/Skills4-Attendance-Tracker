import * as React from "react";
import { Link } from "wouter";
import { useGetCurrentUser } from "@workspace/api-client-react";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import {
  GraduationCap,
  Users2,
  UserCog,
  Building2,
  CalendarClock,
  CalendarX,
  Clock,
  ClipboardCheck,
  History,
  ArrowRight,
} from "lucide-react";

interface ReportCard {
  href: string;
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  adminOnly?: boolean;
}

const CARDS: ReportCard[] = [
  { href: "/reports/learners", title: "Learner Attendance", description: "A single learner's attendance, register history and Bud progress context.", icon: GraduationCap },
  { href: "/reports/cohorts", title: "Cohort Attendance", description: "Attendance totals for a cohort, broken down learner by learner.", icon: Users2 },
  { href: "/reports/tutors", title: "Tutor Attendance", description: "A tutor's attendance totals across their assigned cohorts.", icon: UserCog },
  { href: "/reports/organisation", title: "Organisation Overview", description: "Organisation-wide attendance by tutor, cohort, programme, level and employer.", icon: Building2, adminOnly: true },
  { href: "/reports/attendance-hours", title: "Attendance Hours", description: "Expected vs attended time grouped by learner, cohort, tutor or time period.", icon: CalendarClock },
  { href: "/reports/absence", title: "Absence Analysis", description: "Authorised and unauthorised absences, with the same filters as every other report.", icon: CalendarX },
  { href: "/reports/lateness", title: "Late Attendance", description: "Late arrivals and how many minutes were missed.", icon: Clock },
  { href: "/reports/register-completion", title: "Register Completion", description: "Which registers are outstanding, in progress, completed or locked.", icon: ClipboardCheck },
  { href: "/reports/allocation-history", title: "Allocation History", description: "Learner transfers between tutors and cohorts over time.", icon: History, adminOnly: true },
];

export default function ReportsHubPage() {
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === "admin";
  const cards = CARDS.filter((c) => !c.adminOnly || isAdmin);

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Reports" }]} />

      <div className="mb-8 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground">Reports</h1>
        <p className="text-muted-foreground mt-1">
          {isAdmin
            ? "Attendance reporting and secure CSV export across the organisation."
            : "Attendance reporting and secure CSV export for your cohorts."}
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 page-transition-enter stagger-1">
        {cards.map((card) => (
          <Link key={card.href} href={card.href}>
            <Card className="shadow-sm hover-elevate cursor-pointer h-full">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                    <card.icon className="w-5 h-5 text-primary" />
                  </div>
                  <CardTitle className="text-base">{card.title}</CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <CardDescription>{card.description}</CardDescription>
                <div className="flex items-center text-sm font-medium text-primary mt-3">
                  View report <ArrowRight className="w-3.5 h-3.5 ml-1" />
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
