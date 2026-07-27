import * as React from "react";
import { LearnerStatus, AttendanceStatus, SessionStatus, RegisterStatus } from "@workspace/api-client-react";
import { Badge } from "@/components/ui/badge";

export function LearnerStatusBadge({ status }: { status: LearnerStatus }) {
  const variants: Record<LearnerStatus, { className: string, label: string }> = {
    active: { className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400 hover:bg-emerald-100", label: "Active" },
    completed: { className: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400 hover:bg-blue-100", label: "Completed" },
    paused: { className: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 hover:bg-amber-100", label: "Paused" },
    withdrawn: { className: "bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-400 hover:bg-rose-100", label: "Withdrawn" },
  };

  const v = variants[status] || variants.active;
  return <Badge variant="secondary" className={`${v.className} border-0 shadow-none font-semibold px-2.5 py-0.5`}>{v.label}</Badge>;
}

export function AttendanceStatusBadge({ status }: { status: AttendanceStatus }) {
  const variants: Record<AttendanceStatus, { className: string, label: string }> = {
    present: { className: "bg-emerald-100 text-emerald-800 border-emerald-200", label: "Present" },
    late: { className: "bg-amber-100 text-amber-800 border-amber-200", label: "Late" },
    absent_authorised: { className: "bg-blue-100 text-blue-800 border-blue-200", label: "Absent (Auth)" },
    absent_unauthorised: { className: "bg-rose-100 text-rose-800 border-rose-200", label: "Absent (Unauth)" },
    not_expected: { className: "bg-slate-100 text-slate-800 border-slate-200", label: "Not Expected" },
    withdrawn: { className: "bg-zinc-100 text-zinc-600 border-zinc-200", label: "Withdrawn" },
    bil: { className: "bg-indigo-100 text-indigo-800 border-indigo-200", label: "BIL" },
  };

  const v = variants[status] || variants.present;
  return <Badge variant="outline" className={`${v.className} bg-opacity-50 font-medium px-2 py-0`}>{v.label}</Badge>;
}

export function SessionStatusBadge({ status }: { status: SessionStatus }) {
  const variants: Record<SessionStatus, { className: string, label: string }> = {
    scheduled: { className: "bg-slate-100 text-slate-800 border-slate-200", label: "Scheduled" },
    cancelled: { className: "bg-rose-100 text-rose-800 border-rose-200", label: "Cancelled" },
  };

  const v = variants[status] || variants.scheduled;
  return <Badge variant="outline" className={`${v.className} bg-opacity-50 font-medium px-2 py-0`}>{v.label}</Badge>;
}

export function RegisterStatusBadge({ status }: { status: RegisterStatus }) {
  const variants: Record<RegisterStatus, { className: string, label: string }> = {
    not_started: { className: "bg-slate-100 text-slate-800 border-slate-200", label: "Not started" },
    in_progress: { className: "bg-amber-100 text-amber-800 border-amber-200", label: "In progress" },
    completed: { className: "bg-emerald-100 text-emerald-800 border-emerald-200", label: "Register complete" },
    cancelled: { className: "bg-rose-100 text-rose-800 border-rose-200", label: "Session cancelled" },
    locked: { className: "bg-violet-100 text-violet-800 border-violet-200", label: "Locked" },
  };

  const v = variants[status] || variants.not_started;
  return <Badge variant="outline" className={`${v.className} bg-opacity-50 font-medium px-2 py-0`}>{v.label}</Badge>;
}
