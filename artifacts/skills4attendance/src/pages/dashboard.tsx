import * as React from "react";
import {
  useGetCurrentUser,
  useGetAdminDashboard,
  useGetTutorDashboard,
  useGetTutorDashboardCohorts,
  useGetAdminDashboardTutors,
  useGetAdminDashboardCohorts,
  useGetTutorLowAttendanceLearners,
  useGetAdminLowAttendanceLearners,
  useGetSettings,
  AdminDashboard,
  TutorDashboard,
  getGetAdminDashboardQueryKey,
  getGetTutorDashboardQueryKey,
} from "@workspace/api-client-react";
import { Link } from "wouter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { Breadcrumbs } from "@/components/breadcrumbs";
import {
  Users, GraduationCap, BookOpen, CalendarDays,
  AlertTriangle, Clock, Activity, ArrowUpRight, ChevronRight,
} from "lucide-react";
import { format, parseISO } from "date-fns";
import { DashboardDateFilter, DateFilterValue } from "@/components/dashboard/dashboard-date-filter";
import { LowAttendanceTable } from "@/components/dashboard/low-attendance-table";
import { RegisterCompletionSummaryView } from "@/components/dashboard/register-completion-summary";
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

function StatCard({ title, value, icon: Icon, description, delayCls = "" }: any) {
  return (
    <Card className={`shadow-sm page-transition-enter ${delayCls}`}>
      <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="w-4 h-4 text-muted-foreground/60" />
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-bold text-foreground font-mono">{value}</div>
        {description && <p className="text-xs text-muted-foreground mt-1">{description}</p>}
      </CardContent>
    </Card>
  );
}

function AdminDashboardView({ data, threshold, filter, onFilterChange }: {
  data: AdminDashboard; threshold: number; filter: DateFilterValue; onFilterChange: (f: DateFilterValue) => void;
}) {
  const tutorsQuery = useGetAdminDashboardTutors({ ...filter, pageSize: 50 });
  const cohortsQuery = useGetAdminDashboardCohorts({ ...filter, pageSize: 50 });
  const lowAttendanceQuery = useGetAdminLowAttendanceLearners({ ...filter, pageSize: 50 });

  const chartData = (tutorsQuery.data?.items ?? []).map((t) => ({
    tutor: t.tutorName,
    completion: t.registerCompletion.completionPercentage ?? 0,
  }));

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard title="Active Learners" value={data.activeLearners} icon={GraduationCap} delayCls="stagger-1" />
        <StatCard title="Active Tutors" value={data.activeTutors} icon={Users} delayCls="stagger-2" />
        <StatCard title="Active Cohorts" value={data.activeCohorts} icon={BookOpen} delayCls="stagger-3" />
        <Card className="shadow-sm page-transition-enter stagger-4">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium text-muted-foreground">Attendance (Month)</CardTitle>
            <Activity className="w-4 h-4 text-muted-foreground/60" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-foreground font-mono">{data.attendancePercentageMonth.toFixed(1)}%</div>
            <Progress value={data.attendancePercentageMonth} className="h-2 mt-3" />
            <p className="text-xs text-muted-foreground mt-2">This week: {data.attendancePercentageWeek.toFixed(1)}%</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 page-transition-enter stagger-5">
        <Card className="shadow-sm flex flex-col">
          <CardHeader className="border-b bg-muted/20 pb-4">
            <CardTitle className="flex items-center gap-2 text-base">
              <Clock className="w-4 h-4 text-amber-500" />
              Sessions Awaiting Completion
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0 flex-1 overflow-auto max-h-[400px]">
            {data.sessionsAwaitingCompletion.length === 0 ? (
              <div className="p-8 text-center text-muted-foreground">All session registers are complete.</div>
            ) : (
              <div className="divide-y">
                {data.sessionsAwaitingCompletion.map(session => (
                  <div key={session.id} className="p-4 flex items-center justify-between hover:bg-muted/30 transition-colors">
                    <div>
                      <p className="font-semibold text-sm">{session.cohortName}</p>
                      <p className="text-xs text-muted-foreground mt-1 flex items-center gap-2">
                        <span>{format(parseISO(session.sessionDate), "MMM d, yyyy")}</span>
                        <span>•</span>
                        <span>{session.tutorName}</span>
                      </p>
                    </div>
                    <Link href={`/attendance/${session.id}`} className="text-primary hover:text-primary/80 bg-primary/10 hover:bg-primary/20 p-2 rounded-md transition-colors">
                      <ArrowUpRight className="w-4 h-4" />
                    </Link>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="shadow-sm flex flex-col">
          <CardHeader className="border-b bg-muted/20 pb-4">
            <CardTitle className="flex items-center gap-2 text-base">
              <AlertTriangle className="w-4 h-4 text-destructive" />
              Low Attendance Watchlist
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0 flex-1 overflow-auto max-h-[400px]">
            <LowAttendanceTable
              rows={data.lowAttendanceLearners}
              threshold={threshold}
              emptyMessage="No learners currently flagged for low attendance."
            />
          </CardContent>
        </Card>
      </div>

      {/* Detailed, date-filterable breakdown -- progressive disclosure below the always-visible summary. */}
      <div className="page-transition-enter">
        <h2 className="text-lg font-semibold mb-3">Detailed Breakdown</h2>
        <DashboardDateFilter value={filter} onChange={onFilterChange} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="shadow-sm">
          <CardHeader className="border-b bg-muted/20 pb-4">
            <CardTitle className="text-base flex items-center gap-2"><Users className="w-4 h-4 text-primary" /> Tutor Overview</CardTitle>
          </CardHeader>
          <CardContent className="p-0 max-h-[420px] overflow-auto">
            {tutorsQuery.isLoading ? (
              <div className="p-8 text-center text-muted-foreground">Loading...</div>
            ) : tutorsQuery.isError ? (
              <div className="p-8 text-center text-destructive">Could not load tutor overview.</div>
            ) : (tutorsQuery.data?.items.length ?? 0) === 0 ? (
              <div className="p-8 text-center text-muted-foreground">No active tutors.</div>
            ) : (
              <div className="divide-y">
                {tutorsQuery.data!.items.map((t) => (
                  <div key={t.tutorId} className="p-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-semibold text-sm">{t.tutorName}</p>
                        <p className="text-xs text-muted-foreground mt-1">{t.activeCohorts} cohorts · {t.activeLearners} learners</p>
                      </div>
                      <div className="text-right">
                        <div className="text-sm font-mono font-bold">{t.attendancePercentage != null ? `${t.attendancePercentage.toFixed(1)}%` : "—"}</div>
                        {t.lowAttendanceLearnerCount > 0 && (
                          <Badge variant="destructive" className="mt-1 text-[10px]">{t.lowAttendanceLearnerCount} flagged</Badge>
                        )}
                      </div>
                    </div>
                    <div className="mt-2"><RegisterCompletionSummaryView completion={t.registerCompletion} /></div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="shadow-sm">
          <CardHeader className="border-b bg-muted/20 pb-4">
            <CardTitle className="text-base flex items-center gap-2"><BookOpen className="w-4 h-4 text-primary" /> Cohort Overview</CardTitle>
          </CardHeader>
          <CardContent className="p-0 max-h-[420px] overflow-auto">
            {cohortsQuery.isLoading ? (
              <div className="p-8 text-center text-muted-foreground">Loading...</div>
            ) : cohortsQuery.isError ? (
              <div className="p-8 text-center text-destructive">Could not load cohort overview.</div>
            ) : (cohortsQuery.data?.items.length ?? 0) === 0 ? (
              <div className="p-8 text-center text-muted-foreground">No active cohorts.</div>
            ) : (
              <div className="divide-y">
                {cohortsQuery.data!.items.map((row) => (
                  <div key={row.cohort.id} className="p-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <Link href={`/attendance/cohorts/${row.cohort.id}`} className="font-semibold text-sm hover:underline hover:text-primary">
                          {row.cohort.name}
                        </Link>
                        <p className="text-xs text-muted-foreground mt-1">{row.cohort.programme} · Level {row.cohort.level} · {row.activeLearnerCount} learners</p>
                      </div>
                      <div className="text-sm font-mono font-bold">{row.attendancePercentage != null ? `${row.attendancePercentage.toFixed(1)}%` : "—"}</div>
                    </div>
                    <div className="mt-2"><RegisterCompletionSummaryView completion={row.registerCompletion} /></div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {chartData.length > 0 && (
        <Card className="shadow-sm">
          <CardHeader className="border-b bg-muted/20 pb-4">
            <CardTitle className="text-base">Register Completion by Tutor</CardTitle>
          </CardHeader>
          <CardContent className="pt-4">
            <ChartContainer config={{ completion: { label: "Completion %", color: "hsl(var(--primary))" } }} className="max-h-64 w-full">
              <BarChart data={chartData}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="tutor" tickLine={false} axisLine={false} fontSize={11} />
                <YAxis domain={[0, 100]} tickLine={false} axisLine={false} fontSize={11} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar dataKey="completion" fill="var(--color-completion)" radius={4} />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>
      )}

      <Card className="shadow-sm">
        <CardHeader className="border-b bg-muted/20 pb-4">
          <CardTitle className="text-base flex items-center gap-2"><AlertTriangle className="w-4 h-4 text-destructive" /> Learners Requiring Attention</CardTitle>
        </CardHeader>
        <CardContent className="p-0 max-h-[420px] overflow-auto">
          {lowAttendanceQuery.isLoading ? (
            <div className="p-8 text-center text-muted-foreground">Loading...</div>
          ) : lowAttendanceQuery.isError ? (
            <div className="p-8 text-center text-destructive">Could not load learners requiring attention.</div>
          ) : (
            <LowAttendanceTable
              rows={lowAttendanceQuery.data?.items ?? []}
              threshold={threshold}
              emptyMessage="No learners flagged for this period."
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function TutorDashboardView({ data, threshold, filter, onFilterChange }: {
  data: TutorDashboard; threshold: number; filter: DateFilterValue; onFilterChange: (f: DateFilterValue) => void;
}) {
  const cohortsQuery = useGetTutorDashboardCohorts(filter);
  const lowAttendanceQuery = useGetTutorLowAttendanceLearners({ ...filter, pageSize: 50 });

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <Card className="shadow-sm page-transition-enter stagger-1 col-span-1 lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Next Upcoming Session</CardTitle>
          </CardHeader>
          <CardContent>
            {data.nextSession ? (
              <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 bg-primary/5 border border-primary/10 rounded-lg p-5">
                <div>
                  <h3 className="text-2xl font-bold text-foreground flex items-center gap-2">
                    {data.nextSession.cohortName}
                    {data.nextSession.isCoverSession && (
                      <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-400">
                        Cover
                      </span>
                    )}
                  </h3>
                  <div className="flex items-center gap-2 text-muted-foreground mt-2 text-sm font-medium">
                    <CalendarDays className="w-4 h-4" />
                    {format(parseISO(data.nextSession.sessionDate), "EEEE, MMMM d, yyyy")}
                  </div>
                </div>
                <Link href={`/attendance/${data.nextSession.id}`} className="shrink-0">
                  <span className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground hover:bg-primary/90 h-10 px-4 py-2 hover-elevate">
                    Open Register
                  </span>
                </Link>
              </div>
            ) : (
              <div className="p-6 text-center border rounded-lg bg-muted/10 border-dashed">
                <p className="text-muted-foreground">No upcoming sessions scheduled.</p>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="shadow-sm page-transition-enter stagger-2 flex flex-col">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center justify-between">
              Awaiting Completion
              {data.sessionsAwaitingCompletion.length > 0 && (
                <Badge variant="destructive" className="ml-2">{data.sessionsAwaitingCompletion.length}</Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex-1 overflow-auto">
            {data.sessionsAwaitingCompletion.length === 0 ? (
              <div className="h-full flex items-center justify-center text-sm text-muted-foreground py-6">All caught up!</div>
            ) : (
              <div className="space-y-3">
                {data.sessionsAwaitingCompletion.map(session => (
                  <Link key={session.id} href={`/attendance/${session.id}`}>
                    <div className="p-3 border rounded-md hover:border-primary/50 hover:bg-primary/5 transition-colors cursor-pointer group">
                      <p className="font-semibold text-sm group-hover:text-primary transition-colors flex items-center gap-1.5">
                        {session.cohortName}
                        {session.isCoverSession && (
                          <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-400">
                            Cover
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-muted-foreground mt-1 flex justify-between">
                        {format(parseISO(session.sessionDate), "MMM d")}
                        <span className="text-destructive font-medium">Draft</span>
                      </p>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="page-transition-enter stagger-3">
        <DashboardDateFilter value={filter} onChange={onFilterChange} />
      </div>

      <div>
        <h2 className="text-lg font-semibold mb-3">My Cohorts</h2>
        {cohortsQuery.isLoading ? (
          <div className="p-8 text-center text-muted-foreground border rounded-lg">Loading cohort cards...</div>
        ) : cohortsQuery.isError ? (
          <div className="p-8 text-center text-destructive border rounded-lg">Could not load cohort cards.</div>
        ) : (cohortsQuery.data?.length ?? 0) === 0 ? (
          <div className="p-8 text-center text-muted-foreground border rounded-lg">No active cohorts assigned.</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {cohortsQuery.data!.map((row) => (
              <Link key={row.cohort.id} href={`/attendance/cohorts/${row.cohort.id}`}>
                <Card className="shadow-sm hover:border-primary/50 hover:shadow-md transition-all cursor-pointer h-full">
                  <CardContent className="p-4 space-y-3">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="font-semibold text-sm">{row.cohort.name}</p>
                        <p className="text-xs text-muted-foreground mt-0.5">{row.cohort.programme} · Level {row.cohort.level}</p>
                      </div>
                      <ChevronRight className="w-4 h-4 text-muted-foreground/50" />
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-center border-t pt-3">
                      <div>
                        <p className="text-lg font-bold font-mono leading-none">{row.activeLearnerCount}</p>
                        <p className="text-[11px] text-muted-foreground mt-1">Learners</p>
                      </div>
                      <div>
                        <p className="text-lg font-bold font-mono leading-none">{row.attendancePercentage != null ? `${row.attendancePercentage.toFixed(0)}%` : "—"}</p>
                        <p className="text-[11px] text-muted-foreground mt-1">Attendance</p>
                      </div>
                    </div>
                    <RegisterCompletionSummaryView completion={row.registerCompletion} />
                    {row.lowAttendanceLearnerCount > 0 && (
                      <Badge variant="destructive" className="text-[10px]">{row.lowAttendanceLearnerCount} low attendance</Badge>
                    )}
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </div>

      <Card className="shadow-sm flex flex-col">
        <CardHeader className="border-b bg-muted/20 pb-4">
          <CardTitle className="flex items-center gap-2 text-base">
            <AlertTriangle className="w-4 h-4 text-destructive" />
            My Learners to Watch
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0 flex-1 overflow-auto max-h-[400px]">
          {lowAttendanceQuery.isLoading ? (
            <div className="p-8 text-center text-muted-foreground">Loading...</div>
          ) : lowAttendanceQuery.isError ? (
            <div className="p-8 text-center text-destructive">Could not load learners to watch.</div>
          ) : (
            <LowAttendanceTable
              rows={lowAttendanceQuery.data?.items ?? data.lowAttendanceLearners}
              threshold={threshold}
              emptyMessage="No learners in your cohorts are flagged for low attendance."
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default function DashboardPage() {
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === 'admin';
  const [filter, setFilter] = React.useState<DateFilterValue>({ period: "current_month" });

  // These requests will automatically 403 server-side if called by the wrong role,
  // but we can optionally disable them to prevent the network call entirely.
  const adminQuery = useGetAdminDashboard({ query: { enabled: isAdmin, queryKey: getGetAdminDashboardQueryKey() } });
  const tutorQuery = useGetTutorDashboard({ query: { enabled: !isAdmin, queryKey: getGetTutorDashboardQueryKey() } });

  const { data: settings } = useGetSettings();
  const isLoading = isAdmin ? adminQuery.isLoading : tutorQuery.isLoading;
  // The single source of truth for this value is app_settings (edited via
  // Settings) -- read here, not duplicated as a separate frontend constant.
  const threshold = settings?.lowAttendanceThreshold ?? 85;

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Dashboard" }]} />

      <div className="mb-8 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground">Welcome back, {user?.firstName}</h1>
        <p className="text-muted-foreground mt-1 text-lg">Here's what's happening with your learners today.</p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-8 h-8 rounded-full border-4 border-primary border-t-transparent animate-spin"></div>
        </div>
      ) : isAdmin && adminQuery.data ? (
        <AdminDashboardView data={adminQuery.data} threshold={threshold} filter={filter} onFilterChange={setFilter} />
      ) : !isAdmin && tutorQuery.data ? (
        <TutorDashboardView data={tutorQuery.data} threshold={threshold} filter={filter} onFilterChange={setFilter} />
      ) : (
        <div className="p-8 text-center text-muted-foreground border rounded-lg bg-card">
          Unable to load dashboard data.
        </div>
      )}
    </div>
  );
}
