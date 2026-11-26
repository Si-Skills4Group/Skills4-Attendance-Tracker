import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithQueryClient } from '@/test/test-utils';
import AdminPage from './admin';

vi.mock('wouter', () => ({
  Link: ({ href, children }: any) => <a href={href}>{children}</a>,
}));

describe('AdminPage', () => {
  it('links to all four admin tools', () => {
    renderWithQueryClient(<AdminPage />);

    expect(screen.getByRole('link', { name: /Allocation Reconciliation/ })).toHaveAttribute('href', '/allocation-reconciliation');
    expect(screen.getByRole('link', { name: /Audit Log/ })).toHaveAttribute('href', '/audit-log');
    expect(screen.getByRole('link', { name: /Tutor Identity/ })).toHaveAttribute('href', '/tutor-identity');
    expect(screen.getByRole('link', { name: /Missing Source Records/ })).toHaveAttribute('href', '/bud-missing-source');
  });
});
