import * as React from "react";
import { Link } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ClipboardList, History, UserRoundSearch, SearchX, type LucideIcon } from "lucide-react";

const ADMIN_TOOLS: { name: string; href: string; icon: LucideIcon; description: string }[] = [
  {
    name: "Allocation Reconciliation",
    href: "/allocation-reconciliation",
    icon: ClipboardList,
    description: "Review and resolve learner allocation history gaps.",
  },
  {
    name: "Audit Log",
    href: "/audit-log",
    icon: History,
    description: "Search the full history of changes made across the system.",
  },
  {
    name: "Tutor Identity",
    href: "/tutor-identity",
    icon: UserRoundSearch,
    description: "Resolve ambiguous or conflicting Bud tutor identity mappings.",
  },
  {
    name: "Missing Source Records",
    href: "/bud-missing-source",
    icon: SearchX,
    description: "Investigate records Bud no longer reports that Attendance still tracks.",
  },
];

export default function AdminPage() {
  return (
    <div className="p-6 md:p-8 max-w-5xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Admin" }]} />

      <div className="mb-8 page-transition-enter">
        <h1 className="text-3xl font-bold tracking-tight text-foreground">Admin Tools</h1>
        <p className="text-muted-foreground mt-1">Diagnostic and reconciliation tools used occasionally, not day-to-day.</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 page-transition-enter stagger-1">
        {ADMIN_TOOLS.map((tool) => (
          <Link key={tool.name} href={tool.href}>
            <Card className="shadow-sm cursor-pointer transition-all hover:border-primary/30 hover:shadow-md h-full">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <tool.icon className="w-5 h-5 text-primary shrink-0" />
                  {tool.name}
                </CardTitle>
                <CardDescription>{tool.description}</CardDescription>
              </CardHeader>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
