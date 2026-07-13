import * as React from "react";
import { 
  useGetCurrentUser, 
  useGetAdminDashboard, 
  useGetTutorDashboard,
  AdminDashboard,
  TutorDashboard,
  getGetAdminDashboardQueryKey,
  getGetTutorDashboardQueryKey
} from "@workspace/api-client-react";
import { Link } from "wouter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { 
  Users, GraduationCap, BookOpen, CalendarDays, 
  AlertTriangle, Clock, Activity, ArrowUpRight
} from "lucide-react";
import { format, parseISO } from "date-fns";

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

function AdminDashboardView({ data }: { data: AdminDashboard }) {
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
            <p className="text-xs text-muted-foreground mt-2">Weekly: {data.attendancePercentageWeek.toFixed(1)}%</p>
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
             {data.lowAttendanceLearners.length === 0 ? (
              <div className="p-8 text-center text-muted-foreground">No learners currently flagged for low attendance.</div>
            ) : (
              <div className="divide-y">
                {data.lowAttendanceLearners.map(learner => (
                  <div key={learner.learnerId} className="p-4 flex items-center justify-between hover:bg-muted/30 transition-colors">
                    <div>
                      <Link href={`/learners/${learner.learnerId}`} className="font-semibold text-sm hover:underline hover:text-primary transition-colors">
                        {learner.learnerName}
                      </Link>
                      <p className="text-xs text-muted-foreground mt-1">{learner.learnerRef}</p>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="text-right">
                        <div className="text-sm font-mono font-bold text-destructive">{learner.totals.attendancePercentage.toFixed(1)}%</div>
                        <div className="text-xs text-muted-foreground">{learner.totals.attendedHours} / {learner.totals.scheduledHours} hrs</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function TutorDashboardView({ data }: { data: TutorDashboard }) {
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
                  <h3 className="text-2xl font-bold text-foreground">{data.nextSession.cohortName}</h3>
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
                      <p className="font-semibold text-sm group-hover:text-primary transition-colors">{session.cohortName}</p>
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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 page-transition-enter stagger-3">
        <Card className="shadow-sm">
          <CardHeader className="border-b bg-muted/20 pb-4">
            <CardTitle className="text-base flex items-center gap-2">
              <BookOpen className="w-4 h-4 text-primary" />
              My Cohorts
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
             {data.cohorts.length === 0 ? (
              <div className="p-8 text-center text-muted-foreground">No active cohorts assigned.</div>
            ) : (
              <div className="divide-y">
                {data.cohorts.map(c => (
                  <div key={c.cohort.id} className="p-4 flex items-center justify-between hover:bg-muted/30 transition-colors">
                    <div>
                      <Link href={`/cohorts/${c.cohort.id}`} className="font-semibold text-sm hover:underline hover:text-primary">
                        {c.cohort.name}
                      </Link>
                      <p className="text-xs text-muted-foreground mt-1">{c.cohort.programme}</p>
                    </div>
                    <div className="flex items-center gap-4 text-sm">
                      <div className="text-center">
                        <div className="font-mono font-medium">{c.learnerCount}</div>
                        <div className="text-[10px] text-muted-foreground uppercase">Learners</div>
                      </div>
                      <div className="text-center w-16">
                        <div className="font-mono font-medium">{c.attendancePercentage.toFixed(0)}%</div>
                        <div className="text-[10px] text-muted-foreground uppercase">Attend.</div>
                      </div>
                    </div>
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
              My Learners to Watch
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0 flex-1 overflow-auto max-h-[400px]">
             {data.lowAttendanceLearners.length === 0 ? (
              <div className="p-8 text-center text-muted-foreground">No learners in your cohorts are flagged for low attendance.</div>
            ) : (
              <div className="divide-y">
                {data.lowAttendanceLearners.map(learner => (
                  <div key={learner.learnerId} className="p-4 flex items-center justify-between hover:bg-muted/30 transition-colors">
                    <div>
                      <Link href={`/learners/${learner.learnerId}`} className="font-semibold text-sm hover:underline hover:text-primary">
                        {learner.learnerName}
                      </Link>
                      <p className="text-xs text-muted-foreground mt-1">{learner.learnerRef}</p>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-mono font-bold text-destructive">{learner.totals.attendancePercentage.toFixed(1)}%</div>
                      <div className="text-xs text-muted-foreground">{learner.totals.attendedHours} / {learner.totals.scheduledHours} hrs</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === 'admin';

  // These requests will automatically 403 server-side if called by the wrong role,
  // but we can optionally disable them to prevent the network call entirely.
  const adminQuery = useGetAdminDashboard({ query: { enabled: isAdmin, queryKey: getGetAdminDashboardQueryKey() } });
  const tutorQuery = useGetTutorDashboard({ query: { enabled: !isAdmin, queryKey: getGetTutorDashboardQueryKey() } });

  const isLoading = isAdmin ? adminQuery.isLoading : tutorQuery.isLoading;

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
        <AdminDashboardView data={adminQuery.data} />
      ) : !isAdmin && tutorQuery.data ? (
        <TutorDashboardView data={tutorQuery.data} />
      ) : (
        <div className="p-8 text-center text-muted-foreground border rounded-lg bg-card">
          Unable to load dashboard data.
        </div>
      )}
    </div>
  );
}
