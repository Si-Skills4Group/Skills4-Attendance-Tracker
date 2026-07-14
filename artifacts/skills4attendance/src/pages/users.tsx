import * as React from "react";
import {
  useListTutors,
  useListUsers,
  useProvisionUser,
  useUpdateUser,
  type AuthUser,
  type UserRole,
} from "@workspace/api-client-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/hooks/use-toast";
import { ShieldCheck, UserPlus } from "lucide-react";

const noTutor = "__none__";

export default function UsersPage() {
  const [search, setSearch] = React.useState("");
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [form, setForm] = React.useState({
    entraObjectId: "",
    entraTenantId: "",
    email: "",
    firstName: "",
    lastName: "",
    displayName: "",
    role: "tutor" as UserRole,
    tutorId: noTutor,
  });
  const { toast } = useToast();
  const { data: users = [], isLoading } = useListUsers({ search: search || undefined });
  const { data: tutors = [] } = useListTutors();
  const provisionMutation = useProvisionUser();
  const updateMutation = useUpdateUser();

  const provision = () => {
    provisionMutation.mutate(
      {
        data: {
          ...form,
          displayName: form.displayName || undefined,
          tutorId: form.role === "tutor" && form.tutorId !== noTutor ? Number(form.tutorId) : null,
          active: true,
        },
      },
      {
        onSuccess: () => {
          setDialogOpen(false);
          setForm({
            entraObjectId: "",
            entraTenantId: "",
            email: "",
            firstName: "",
            lastName: "",
            displayName: "",
            role: "tutor",
            tutorId: noTutor,
          });
          toast({ title: "User provisioned" });
        },
        onError: (error: any) => {
          toast({
            variant: "destructive",
            title: "Could not provision user",
            description: error?.data?.error || error?.message || "Check the identity details and try again.",
          });
        },
      },
    );
  };

  const updateUser = (user: AuthUser, changes: Partial<AuthUser>) => {
    updateMutation.mutate(
      {
        id: user.id,
        data: {
          role: changes.role,
          active: changes.active,
          tutorId: changes.tutorId,
        },
      },
      {
        onError: (error: any) => {
          toast({
            variant: "destructive",
            title: "Could not update user",
            description: error?.data?.error || error?.message || "The change was not applied.",
          });
        },
      },
    );
  };

  return (
    <div className="p-6 lg:p-8 space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Users</h1>
          <p className="text-muted-foreground mt-1">Provision and manage Skills4Attendance application access.</p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild>
            <Button>
              <UserPlus className="w-4 h-4 mr-2" />
              Provision user
            </Button>
          </DialogTrigger>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>Provision Entra user</DialogTitle>
            </DialogHeader>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Entra object ID" value={form.entraObjectId} onChange={(entraObjectId) => setForm((f) => ({ ...f, entraObjectId }))} />
              <Field label="Entra tenant ID" value={form.entraTenantId} onChange={(entraTenantId) => setForm((f) => ({ ...f, entraTenantId }))} />
              <Field label="Email" value={form.email} onChange={(email) => setForm((f) => ({ ...f, email }))} />
              <Field label="Display name" value={form.displayName} onChange={(displayName) => setForm((f) => ({ ...f, displayName }))} />
              <Field label="First name" value={form.firstName} onChange={(firstName) => setForm((f) => ({ ...f, firstName }))} />
              <Field label="Last name" value={form.lastName} onChange={(lastName) => setForm((f) => ({ ...f, lastName }))} />
              <div className="space-y-2">
                <Label>Role</Label>
                <Select value={form.role} onValueChange={(role: UserRole) => setForm((f) => ({ ...f, role }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">Administrator</SelectItem>
                    <SelectItem value="tutor">Tutor</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Tutor link</Label>
                <Select value={form.tutorId} onValueChange={(tutorId) => setForm((f) => ({ ...f, tutorId }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={noTutor}>None</SelectItem>
                    {tutors.map((tutor) => (
                      <SelectItem key={tutor.id} value={String(tutor.id)}>
                        {tutor.firstName} {tutor.lastName}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <Button onClick={provision} disabled={provisionMutation.isPending}>
              Provision access
            </Button>
          </DialogContent>
        </Dialog>
      </div>

      <Input
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder="Search users by name, email, or Entra object ID"
        className="max-w-md"
      />

      <div className="border border-border rounded-md overflow-hidden bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>User</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Tutor</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Entra object ID</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow><TableCell colSpan={6}>Loading users...</TableCell></TableRow>
            ) : users.length === 0 ? (
              <TableRow><TableCell colSpan={6}>No users found.</TableCell></TableRow>
            ) : (
              users.map((user) => (
                <TableRow key={user.id}>
                  <TableCell>
                    <div className="font-medium">{user.firstName} {user.lastName}</div>
                    <div className="text-sm text-muted-foreground">{user.email}</div>
                  </TableCell>
                  <TableCell>
                    <Select
                      value={user.role}
                      onValueChange={(role: UserRole) => updateUser(user, { role })}
                    >
                      <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="admin">Administrator</SelectItem>
                        <SelectItem value="tutor">Tutor</SelectItem>
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    <Select
                      value={user.tutorId ? String(user.tutorId) : noTutor}
                      onValueChange={(value) => updateUser(user, { tutorId: value === noTutor ? null : Number(value) })}
                    >
                      <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={noTutor}>None</SelectItem>
                        {tutors.map((tutor) => (
                          <SelectItem key={tutor.id} value={String(tutor.id)}>
                            {tutor.firstName} {tutor.lastName}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    <Badge variant={user.active ? "default" : "secondary"}>
                      {user.active ? "Active" : "Inactive"}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs max-w-56 truncate">{user.entraObjectId || "Not mapped"}</TableCell>
                  <TableCell className="text-right">
                    <Button variant="outline" size="sm" onClick={() => updateUser(user, { active: !user.active })}>
                      <ShieldCheck className="w-4 h-4 mr-2" />
                      {user.active ? "Deactivate" : "Activate"}
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <Input value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}
