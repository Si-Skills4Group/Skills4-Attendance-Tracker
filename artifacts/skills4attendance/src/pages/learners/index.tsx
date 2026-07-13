import * as React from "react";
import { useListLearners, LearnerStatus, useGetSettings, useGetCurrentUser } from "@workspace/api-client-react";
import { Link } from "wouter";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Search, Plus, Upload, GraduationCap, Building2, User, ChevronLeft, ChevronRight } from "lucide-react";
import { LearnerStatusBadge } from "@/components/status-badges";
import { useDebounce } from "@/hooks/use-debounce";

export default function LearnersPage() {
  const { data: user } = useGetCurrentUser();
  const isAdmin = user?.role === 'admin';
  
  const [searchQuery, setSearchQuery] = React.useState("");
  const debouncedSearch = useDebounce(searchQuery, 300);
  const [statusFilter, setStatusFilter] = React.useState<LearnerStatus | "all">("all");
  const [page, setPage] = React.useState(1);
  const pageSize = 20;

  const { data: learnersData, isLoading } = useListLearners({
    search: debouncedSearch || undefined,
    status: statusFilter !== "all" ? statusFilter : undefined,
    page,
    pageSize
  });

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto w-full">
      <Breadcrumbs items={[{ label: "Learners" }]} />
      
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8 page-transition-enter">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Learners</h1>
          <p className="text-muted-foreground mt-1">View and manage apprenticeship learners.</p>
        </div>
        {isAdmin && (
          <div className="flex gap-2">
            <Link href="/learners/import">
              <Button variant="outline" className="shadow-sm">
                <Upload className="w-4 h-4 mr-2" /> Import CSV
              </Button>
            </Link>
            <Link href="/learners/new">
              <Button className="hover-elevate shadow-sm">
                <Plus className="w-4 h-4 mr-2" /> Add Learner
              </Button>
            </Link>
          </div>
        )}
      </div>

      <Card className="shadow-sm page-transition-enter stagger-1 mb-6">
        <CardContent className="p-4 flex flex-col sm:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input 
              placeholder="Search learners by name, ref, employer..." 
              value={searchQuery}
              onChange={(e) => { setSearchQuery(e.target.value); setPage(1); }}
              className="pl-9 h-10 bg-background"
            />
          </div>
          <div className="w-full sm:w-[200px]">
            <Select 
              value={statusFilter} 
              onValueChange={(val: any) => { setStatusFilter(val); setPage(1); }}
            >
              <SelectTrigger className="h-10 bg-background">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                <SelectItem value="active">Active</SelectItem>
                <SelectItem value="paused">Paused</SelectItem>
                <SelectItem value="completed">Completed</SelectItem>
                <SelectItem value="withdrawn">Withdrawn</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <div className="bg-card rounded-lg border shadow-sm overflow-hidden page-transition-enter stagger-2">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader className="bg-muted/30">
              <TableRow>
                <TableHead className="w-[250px]">Learner</TableHead>
                <TableHead>Programme</TableHead>
                <TableHead>Employer</TableHead>
                <TableHead>Tutor / Cohort</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={5} className="h-32 text-center">
                    <div className="flex justify-center"><div className="w-6 h-6 rounded-full border-2 border-primary border-t-transparent animate-spin"></div></div>
                  </TableCell>
                </TableRow>
              ) : learnersData?.items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="h-32 text-center text-muted-foreground">
                    No learners found matching your criteria.
                  </TableCell>
                </TableRow>
              ) : (
                learnersData?.items.map((learner) => (
                  <TableRow key={learner.id} className="hover:bg-muted/20 transition-colors group">
                    <TableCell>
                      <Link href={`/learners/${learner.id}`}>
                        <div className="font-semibold text-foreground group-hover:text-primary transition-colors cursor-pointer">
                          {learner.firstName} {learner.lastName}
                        </div>
                      </Link>
                      <div className="text-xs text-muted-foreground font-mono mt-0.5">{learner.learnerRef}</div>
                    </TableCell>
                    <TableCell>
                      <div className="text-sm font-medium">{learner.programme}</div>
                      <div className="text-xs text-muted-foreground">Level {learner.level}</div>
                    </TableCell>
                    <TableCell>
                      {learner.employer ? (
                        <div className="flex items-center gap-1.5 text-sm">
                          <Building2 className="w-3.5 h-3.5 text-muted-foreground" />
                          <span className="truncate max-w-[150px]">{learner.employer}</span>
                        </div>
                      ) : (
                        <span className="text-muted-foreground text-xs italic">Unassigned</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="text-sm flex items-center gap-1.5">
                        <User className="w-3.5 h-3.5 text-muted-foreground" />
                        <span className="truncate max-w-[120px]">{learner.tutorName || <span className="text-muted-foreground italic text-xs">Unallocated</span>}</span>
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1.5">
                        <GraduationCap className="w-3.5 h-3.5" />
                        <span className="truncate max-w-[120px]">{learner.cohortName || <span className="italic">Unallocated</span>}</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <LearnerStatusBadge status={learner.status} />
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
        
        {/* Pagination */}
        {learnersData && learnersData.total > 0 && (
          <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/10">
            <div className="text-sm text-muted-foreground">
              Showing <span className="font-medium text-foreground">{((page - 1) * pageSize) + 1}</span> to <span className="font-medium text-foreground">{Math.min(page * pageSize, learnersData.total)}</span> of <span className="font-medium text-foreground">{learnersData.total}</span> learners
            </div>
            <div className="flex items-center space-x-2">
              <Button 
                variant="outline" 
                size="sm" 
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1}
              >
                <ChevronLeft className="w-4 h-4" />
              </Button>
              <div className="text-sm font-medium px-2">{page}</div>
              <Button 
                variant="outline" 
                size="sm" 
                onClick={() => setPage(p => p + 1)}
                disabled={page * pageSize >= learnersData.total}
              >
                <ChevronRight className="w-4 h-4" />
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
