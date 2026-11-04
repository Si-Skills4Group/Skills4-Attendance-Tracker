import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserAuthError } from '@azure/msal-browser';
import { renderWithQueryClient } from '@/test/test-utils';
import { Toaster } from '@/components/ui/toaster';
import LoginPage from './login';

const mockLoginRedirect = vi.fn();
vi.mock('@azure/msal-react', () => ({
  useMsal: () => ({ instance: { loginRedirect: mockLoginRedirect }, inProgress: 'none' }),
}));

vi.mock('@/auth/use-auth-state', () => ({
  useAuthState: () => ({ isAuthenticated: false }),
}));

const mockClearStuckInteractionState = vi.fn();
vi.mock('@/auth/msal', () => ({
  loginRequest: { scopes: ['test-scope'] },
  clearStuckInteractionState: () => mockClearStuckInteractionState(),
}));

vi.mock('wouter', () => ({
  useLocation: () => ['/login', vi.fn()],
}));

describe('LoginPage sign-in', () => {
  beforeEach(() => {
    mockLoginRedirect.mockReset();
    mockClearStuckInteractionState.mockReset();
  });

  it('starts a loginRedirect on sign-in click', async () => {
    mockLoginRedirect.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithQueryClient(<><LoginPage /><Toaster /></>);

    await user.click(screen.getByRole('button', { name: /sign in with microsoft/i }));

    expect(mockLoginRedirect).toHaveBeenCalledTimes(1);
    expect(mockClearStuckInteractionState).not.toHaveBeenCalled();
  });

  it('clears a stuck interaction flag and retries once on interaction_in_progress', async () => {
    mockLoginRedirect
      .mockRejectedValueOnce(new BrowserAuthError('interaction_in_progress'))
      .mockResolvedValueOnce(undefined);
    const user = userEvent.setup();
    renderWithQueryClient(<><LoginPage /><Toaster /></>);

    await user.click(screen.getByRole('button', { name: /sign in with microsoft/i }));

    expect(mockClearStuckInteractionState).toHaveBeenCalledTimes(1);
    expect(mockLoginRedirect).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/sign-in could not start/i)).not.toBeInTheDocument();
  });

  it('shows an error toast when loginRedirect fails for another reason', async () => {
    mockLoginRedirect.mockRejectedValue(new Error('network down'));
    const user = userEvent.setup();
    renderWithQueryClient(<><LoginPage /><Toaster /></>);

    await user.click(screen.getByRole('button', { name: /sign in with microsoft/i }));

    expect(mockClearStuckInteractionState).not.toHaveBeenCalled();
    expect(await screen.findByText(/sign-in could not start/i)).toBeInTheDocument();
  });

  it('shows an error toast if the retry after clearing a stuck flag also fails', async () => {
    mockLoginRedirect.mockRejectedValue(new BrowserAuthError('interaction_in_progress'));
    const user = userEvent.setup();
    renderWithQueryClient(<><LoginPage /><Toaster /></>);

    await user.click(screen.getByRole('button', { name: /sign in with microsoft/i }));

    expect(mockClearStuckInteractionState).toHaveBeenCalledTimes(1);
    expect(mockLoginRedirect).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(/sign-in could not start/i)).toBeInTheDocument();
  });
});
