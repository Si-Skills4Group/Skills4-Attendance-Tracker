import {
  EventType,
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
  type AuthenticationResult,
  type Configuration,
  type RedirectRequest,
} from "@azure/msal-browser";
import { setAuthTokenGetter, setBaseUrl } from "@workspace/api-client-react";

const required = (name: string) => {
  const value = (import.meta.env as Record<string, string | undefined>)[name];
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
};

export const apiScope = required("VITE_API_SCOPE");
const tenantId = required("VITE_ENTRA_TENANT_ID");

export const loginRequest: RedirectRequest = {
  scopes: [apiScope],
};

export const msalConfig: Configuration = {
  auth: {
    clientId: required("VITE_ENTRA_CLIENT_ID"),
    authority: import.meta.env.VITE_ENTRA_AUTHORITY || `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: import.meta.env.VITE_ENTRA_REDIRECT_URI || window.location.origin,
    postLogoutRedirectUri: import.meta.env.VITE_ENTRA_POST_LOGOUT_REDIRECT_URI || window.location.origin,
    navigateToLoginRequestUrl: true,
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
};

export const msalInstance = new PublicClientApplication(msalConfig);

function chooseAccount(accounts: AccountInfo[]): AccountInfo | null {
  const active = msalInstance.getActiveAccount();
  if (active) return active;
  return accounts[0] ?? null;
}

msalInstance.addEventCallback((event) => {
  if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
    const result = event.payload as AuthenticationResult;
    if (result.account) {
      msalInstance.setActiveAccount(result.account);
    }
  }
});

setBaseUrl(import.meta.env.VITE_API_BASE_URL || null);
setAuthTokenGetter(async () => {
  const account = chooseAccount(msalInstance.getAllAccounts());
  if (!account) return null;
  msalInstance.setActiveAccount(account);
  try {
    const result = await msalInstance.acquireTokenSilent({ ...loginRequest, account });
    return result.accessToken;
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      await msalInstance.acquireTokenRedirect({ ...loginRequest, account });
      return null;
    }
    throw error;
  }
});
