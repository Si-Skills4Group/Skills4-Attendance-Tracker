import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQueryClient } from '@/test/test-utils';
import AuditLogPage from './audit-log';

const users = [
  { id: 1, firstName: 'Ada', lastName: 'Lovelace' },
  { id: 2, firstName: 'Tam', lastName: 'Tutor' },
];

const logEntry = {
  id: 500,
  userId: 1,
  userName: 'Ada Lovelace',
  action: 'update',
  entityType: 'learner',
  entityId: 42,
  previousValue: JSON.stringify({ status: 'active', firstName: 'Ada' }),
  newValue: JSON.stringify({ status: 'withdrawn', firstName: 'Ada' }),
  timestamp: '2026-02-01T10:15:00Z',
  ipAddress: '203.0.113.7',
  correlationId: 'corr-abc-123',
};

let mockListAuditLogParams: any = null;
let mockLogItems: any[] = [logEntry];

vi.mock('@workspace/api-client-react', () => ({
  useListUsers: () => ({ data: users }),
  useListAuditLog: (params: any) => {
    mockListAuditLogParams = params;
    return { data: { items: mockLogItems, total: mockLogItems.length, page: 1, pageSize: 20 }, isLoading: false };
  },
}));

describe('AuditLogPage', () => {
  beforeEach(() => {
    mockListAuditLogParams = null;
    mockLogItems = [logEntry];
  });

  it('renders audit entries from the backend', () => {
    renderWithQueryClient(<AuditLogPage />);
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('UPDATE')).toBeInTheDocument();
    expect(screen.getByText('learner')).toBeInTheDocument();
  });

  it('offers a user filter populated from the user list', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<AuditLogPage />);

    await user.click(screen.getByRole('combobox', { name: /user/i }));
    await user.click(await screen.findByText('Tam Tutor'));
    await waitFor(() => expect(mockListAuditLogParams.userId).toBe(2));
  });

  it('offers date-range and entity-ID filters that reach the backend query', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<AuditLogPage />);

    await user.type(screen.getByLabelText('Entity ID'), '42');
    await waitFor(() => expect(mockListAuditLogParams.entityId).toBe(42));

    await user.type(screen.getByLabelText('From'), '2026-01-01');
    await waitFor(() => expect(mockListAuditLogParams.dateFrom).toBe('2026-01-01'));
  });

  it('shows a field-level diff, the correlation ID, and the IP address in the detail dialog', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<AuditLogPage />);

    await user.click(screen.getByRole('button', { name: /view/i }));

    expect(screen.getByText('corr-abc-123')).toBeInTheDocument();
    expect(screen.getByText('203.0.113.7')).toBeInTheDocument();
    // Only `status` changed between previousValue and newValue --
    // firstName is identical on both sides and must not appear as a diff row.
    expect(screen.getByText('status')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('withdrawn')).toBeInTheDocument();
    expect(screen.queryByText('firstName')).not.toBeInTheDocument();
  });

  it('shows a no-differences message when previous and new values are identical', async () => {
    mockLogItems = [{
      ...logEntry, id: 501,
      previousValue: JSON.stringify({ status: 'active' }),
      newValue: JSON.stringify({ status: 'active' }),
    }];
    const user = userEvent.setup();
    renderWithQueryClient(<AuditLogPage />);

    await user.click(screen.getByRole('button', { name: /view/i }));
    expect(screen.getByText('No field-level differences to show.')).toBeInTheDocument();
  });
});
