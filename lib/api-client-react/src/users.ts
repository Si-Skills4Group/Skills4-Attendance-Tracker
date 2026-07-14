import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";
import type { AuditLogEntry, AuthUser, UserRole } from "./generated/api.schemas";

export interface UserProvisionInput {
  entraObjectId: string;
  entraTenantId: string;
  email: string;
  firstName: string;
  lastName?: string;
  displayName?: string | null;
  role: UserRole;
  tutorId?: number | null;
  active?: boolean;
}

export interface UserUpdateInput {
  email?: string;
  firstName?: string;
  lastName?: string;
  displayName?: string | null;
  role?: UserRole;
  tutorId?: number | null;
  active?: boolean;
}

export function useListUsers(params: { search?: string; role?: UserRole; active?: boolean } = {}) {
  return useQuery({
    queryKey: ["users", params],
    queryFn: () => {
      const search = new URLSearchParams();
      if (params.search) search.set("search", params.search);
      if (params.role) search.set("role", params.role);
      if (params.active !== undefined) search.set("active", String(params.active));
      const query = search.toString();
      return customFetch<AuthUser[]>(`/api/users${query ? `?${query}` : ""}`, { responseType: "json" });
    },
  });
}

export function useProvisionUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: UserProvisionInput) =>
      customFetch<AuthUser>("/api/users", {
        method: "POST",
        body: JSON.stringify(data),
        responseType: "json",
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: UserUpdateInput }) =>
      customFetch<AuthUser>(`/api/users/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
        responseType: "json",
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useGetUserAudit(id: number) {
  return useQuery({
    queryKey: ["users", id, "audit"],
    queryFn: () => customFetch<AuditLogEntry[]>(`/api/users/${id}/audit`, { responseType: "json" }),
    enabled: Number.isFinite(id),
  });
}
