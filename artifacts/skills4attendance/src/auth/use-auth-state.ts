import { InteractionStatus } from "@azure/msal-browser";
import { useIsAuthenticated, useMsal } from "@azure/msal-react";

export function useAuthState() {
  const { accounts, inProgress, instance } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const account = instance.getActiveAccount() ?? accounts[0] ?? null;
  const isResolving = inProgress !== InteractionStatus.None;

  return { account, isAuthenticated, isResolving, inProgress };
}
