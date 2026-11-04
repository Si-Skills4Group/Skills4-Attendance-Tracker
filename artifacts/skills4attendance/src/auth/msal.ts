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

// MSAL refuses to start a new loginRedirect/acquireTokenRedirect while it
// thinks one is already in progress, tracked via a sessionStorage flag it
// sets before navigating away and clears when handleRedirectPromise
// resolves. If that promise instead *fails* -- a closed tab, back button,
// slow network, a browser extension interfering with the redirect back --
// msal-react's MsalProvider swallows the error (it only unblocks
// `inProgress`) and never clears this flag, so every subsequent sign-in
// attempt in that tab throws BrowserAuthError("interaction_in_progress")
// with no way to recover short of closing the tab. sessionStorage holds
// nothing but MSAL's own cache here, so clearing just this flag (rather
// than the whole cache) is a safe, surgical way to unstick it.
export function clearStuckInteractionState() {
  for (const key of Object.keys(window.sessionStorage)) {
    if (key.endsWith(".interaction.status")) {
      window.sessionStorage.removeItem(key);
    }
  }
}

msalInstance.addEventCallback((event) => {
  if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
    const result = event.payload as AuthenticationResult;
    if (result.account) {
      msalInstance.setActiveAccount(result.account);
    }
  }
  if (event.eventType === EventType.LOGIN_FAILURE || event.eventType === EventType.ACQUIRE_TOKEN_FAILURE) {
    clearStuckInteractionState();
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
