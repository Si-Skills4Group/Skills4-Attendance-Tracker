import { createRoot } from 'react-dom/client';
import { MsalProvider } from '@azure/msal-react';

import App from './App';
import { msalInstance } from './auth/msal';

import './index.css';

async function start() {
  await msalInstance.initialize();
  const accounts = msalInstance.getAllAccounts();
  if (!msalInstance.getActiveAccount() && accounts.length > 0) {
    msalInstance.setActiveAccount(accounts[0]);
  }

  createRoot(document.getElementById('root')!).render(
    <MsalProvider instance={msalInstance}>
      <App />
    </MsalProvider>,
  );
}

void start();
