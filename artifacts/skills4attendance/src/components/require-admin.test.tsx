import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithQueryClient } from '@/test/test-utils';
import { RequireAdmin } from './require-admin';

let mockCurrentUser: { data: any; isLoading: boolean };

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
}));

describe('RequireAdmin', () => {
  it('shows a loading state while the current user is still loading', () => {
    mockCurrentUser = { data: undefined, isLoading: true };
    renderWithQueryClient(<RequireAdmin><div>Admin-only content</div></RequireAdmin>);
    expect(screen.queryByText('Admin-only content')).not.toBeInTheDocument();
    expect(document.querySelector('.animate-spin')).toBeInTheDocument();
  });

  it('renders the protected content for an admin', () => {
    mockCurrentUser = { data: { role: 'admin' }, isLoading: false };
    renderWithQueryClient(<RequireAdmin><div>Admin-only content</div></RequireAdmin>);
    expect(screen.getByText('Admin-only content')).toBeInTheDocument();
  });

  it('shows a clean "not authorized" state for a tutor, without rendering the protected content', () => {
    mockCurrentUser = { data: { role: 'tutor' }, isLoading: false };
    renderWithQueryClient(<RequireAdmin><div>Admin-only content</div></RequireAdmin>);
    expect(screen.queryByText('Admin-only content')).not.toBeInTheDocument();
    expect(screen.getByText('Not authorized')).toBeInTheDocument();
  });
});
