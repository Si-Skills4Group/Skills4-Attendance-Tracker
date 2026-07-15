import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithQueryClient } from '@/test/test-utils';
import { Shell } from './shell';

vi.mock('@azure/msal-react', () => ({
  useMsal: () => ({ instance: { logoutRedirect: vi.fn(), loginRedirect: vi.fn(), getActiveAccount: () => null } }),
}));

vi.mock('@/auth/use-auth-state', () => ({
  useAuthState: () => ({ account: {}, isAuthenticated: true, isResolving: false, inProgress: 'none' }),
}));

// msal.ts has module-level side effects (constructs a real PublicClientApplication,
// requires VITE_* env vars) that have no place running in a unit test -- shell.tsx
// only needs the loginRequest value it exports.
vi.mock('@/auth/msal', () => ({
  loginRequest: { scopes: ['test-scope'] },
}));

vi.mock('wouter', () => ({
  useLocation: () => ['/dashboard', vi.fn()],
  Link: ({ href, children, onClick }: any) => (
    <a href={href} onClick={onClick}>{children}</a>
  ),
}));

const mockUseGetCurrentUser = vi.fn();
vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: (...args: any[]) => mockUseGetCurrentUser(...args),
  getGetCurrentUserQueryKey: () => ['getCurrentUser'],
}));

// Admin-only nav items per shell.tsx's navItems roles config.
const ADMIN_ONLY = ['Tutors', 'Users', 'Allocation', 'Audit Log', 'Settings'];
const SHARED = ['Dashboard', 'Learners', 'Cohorts', 'Attendance', 'Reports'];

function mockCurrentUser(role: 'admin' | 'tutor') {
  mockUseGetCurrentUser.mockReturnValue({
    data: { firstName: 'Test', lastName: 'User', role },
    isLoading: false,
    error: null,
  });
}

describe('Shell navigation gating', () => {
  it('hides admin-only nav items from a tutor', () => {
    mockCurrentUser('tutor');
    renderWithQueryClient(<Shell><div>content</div></Shell>);

    for (const label of SHARED) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    for (const label of ADMIN_ONLY) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
  });

  it('shows admin-only nav items to an admin', () => {
    mockCurrentUser('admin');
    renderWithQueryClient(<Shell><div>content</div></Shell>);

    for (const label of [...SHARED, ...ADMIN_ONLY]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });
});
