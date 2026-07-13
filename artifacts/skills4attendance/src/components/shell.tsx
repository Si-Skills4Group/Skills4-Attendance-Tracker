import * as React from "react";
import { Link, useLocation } from "wouter";
import { useGetCurrentUser, useLogout } from "@workspace/api-client-react";
import { 
  LayoutDashboard, 
  Users, 
  GraduationCap, 
  BookOpen, 
  UserPlus, 
  CalendarDays, 
  FileBarChart, 
  History, 
  Settings,
  LogOut,
  Menu,
  X
} from "lucide-react";
import { Button } from "@/components/ui/button";

export function Shell({ children }: { children: React.ReactNode }) {
  const { data: user, isLoading } = useGetCurrentUser();
  const logoutMutation = useLogout();
  const [location, setLocation] = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);

  // Redirect between login and dashboard based on auth state, as a side
  // effect (not during render) to avoid updating routing state while Shell
  // itself is rendering.
  React.useEffect(() => {
    if (user && location === '/login') {
      setLocation('/dashboard');
    } else if (!isLoading && !user && location !== '/login') {
      setLocation('/login');
    }
  }, [user, isLoading, location, setLocation]);

  if (isLoading) {
    return <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="w-8 h-8 rounded-full border-4 border-primary border-t-transparent animate-spin"></div>
    </div>;
  }

  // Allow rendering just the content for unauthenticated users on login
  if (!user) {
    if (location !== '/login') {
      return null;
    }
    return <>{children}</>;
  }

  const handleLogout = () => {
    logoutMutation.mutate(undefined, {
      onSuccess: () => setLocation('/login')
    });
  };

  const navItems = [
    { name: "Dashboard", href: "/dashboard", icon: LayoutDashboard, roles: ['admin', 'tutor'] },
    { name: "Tutors", href: "/tutors", icon: Users, roles: ['admin'] },
    { name: "Learners", href: "/learners", icon: GraduationCap, roles: ['admin', 'tutor'] },
    { name: "Cohorts", href: "/cohorts", icon: BookOpen, roles: ['admin', 'tutor'] },
    { name: "Allocation", href: "/allocation", icon: UserPlus, roles: ['admin'] },
    { name: "Attendance", href: "/attendance", icon: CalendarDays, roles: ['admin', 'tutor'] },
    { name: "Reports", href: "/reports", icon: FileBarChart, roles: ['admin', 'tutor'] },
    { name: "Audit Log", href: "/audit-log", icon: History, roles: ['admin'] },
    { name: "Settings", href: "/settings", icon: Settings, roles: ['admin'] },
  ];

  const filteredNav = navItems.filter(item => item.roles.includes(user.role));

  const NavContent = () => (
    <>
      <div className="p-6">
        <h1 className="text-xl font-bold tracking-tight text-sidebar-primary-foreground flex items-center gap-2">
          <div className="w-8 h-8 bg-sidebar-primary rounded-md flex items-center justify-center text-sidebar shadow-sm">
            <GraduationCap className="w-5 h-5" />
          </div>
          Skills4Group
        </h1>
        <p className="text-sidebar-foreground/60 text-xs mt-1 font-medium ml-10">Attendance & Allocation</p>
      </div>
      <nav className="flex-1 px-4 space-y-1 overflow-y-auto">
        {filteredNav.map((item) => {
          const isActive = location.startsWith(item.href);
          return (
            <Link key={item.name} href={item.href} onClick={() => setMobileMenuOpen(false)}>
              <div
                className={`flex items-center gap-3 px-3 py-2.5 rounded-md transition-all duration-200 cursor-pointer ${
                  isActive 
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground shadow-sm font-medium' 
                    : 'text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground'
                }`}
              >
                <item.icon className={`w-5 h-5 ${isActive ? 'text-sidebar-accent-foreground' : 'text-sidebar-foreground/60'}`} />
                {item.name}
              </div>
            </Link>
          );
        })}
      </nav>
      <div className="p-4 mt-auto border-t border-sidebar-border">
        <div className="flex items-center gap-3 px-3 py-3 bg-sidebar-accent/30 rounded-md">
          <div className="w-8 h-8 rounded-full bg-sidebar-primary/20 flex items-center justify-center text-sidebar-primary-foreground font-bold shrink-0">
            {user.firstName[0]}{user.lastName[0]}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-sidebar-primary-foreground truncate">{user.firstName} {user.lastName}</p>
            <p className="text-xs text-sidebar-foreground/60 capitalize truncate">{user.role}</p>
          </div>
          <Button variant="ghost" size="icon" className="text-sidebar-foreground/60 hover:text-destructive hover:bg-destructive/10 shrink-0" onClick={handleLogout} title="Log out">
            <LogOut className="w-4 h-4" />
          </Button>
        </div>
      </div>
    </>
  );

  return (
    <div className="flex h-[100dvh] bg-background overflow-hidden">
      {/* Desktop Sidebar */}
      <aside className="hidden md:flex flex-col w-64 bg-sidebar border-r border-sidebar-border shadow-sm z-20">
        <NavContent />
      </aside>

      {/* Mobile Header & Nav */}
      <div className="md:hidden fixed top-0 left-0 right-0 h-16 bg-sidebar border-b border-sidebar-border flex items-center justify-between px-4 z-30">
        <h1 className="text-lg font-bold text-sidebar-primary-foreground flex items-center gap-2">
          <div className="w-6 h-6 bg-sidebar-primary rounded flex items-center justify-center text-sidebar">
            <GraduationCap className="w-4 h-4" />
          </div>
          Skills4Group
        </h1>
        <Button variant="ghost" size="icon" className="text-sidebar-foreground" onClick={() => setMobileMenuOpen(!mobileMenuOpen)}>
          {mobileMenuOpen ? <X /> : <Menu />}
        </Button>
      </div>

      {/* Mobile Menu Overlay */}
      {mobileMenuOpen && (
        <div className="md:hidden fixed inset-0 top-16 bg-sidebar z-20 flex flex-col page-transition-enter">
          <NavContent />
        </div>
      )}

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-y-auto mt-16 md:mt-0 relative w-full">
        {children}
      </main>
    </div>
  );
}
