import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import RegisterCompletionReportPage from './register-completion';

const outstandingRow = {
  sessionId: 5, sessionDate: '2026-07-10', title: null, cohortId: 2, cohortName: 'Cohort A', tutorName: 'Sam Tutor',
  registerStatus: 'not_started', expectedCount: 5, recordedCount: 0, missingRowCount: 5,
  completedAt: null, completedByName: null, registerLockedAt: null, lockedByName: null, outstandingDays: 3,
};

let mockCurrentUser: any;
let mockTutors: any;
let mockCohorts: any;
let mockReport: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useListTutors: () => mockTutors,
  getListTutorsQueryKey: (p: unknown) => ['listTutors', p],
  useListCohorts: () => mockCohorts,
  useGetRegisterCompletionReport: () => mockReport,
  exportRegisterCompletionReport: vi.fn().mockResolvedValue('sessionDate\n2026-07-10\n'),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/register-completion', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/register-completion" component={RegisterCompletionReportPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
  mockTutors = { data: [] };
  mockCohorts = { data: [] };
  mockReport = {
    data: {
      items: [outstandingRow], total: 1, page: 1, pageSize: 25,
      registerCompletion: { periodStart: '2026-07-01', periodEnd: '2026-07-31', notStarted: 1, inProgress: 0, completed: 0, locked: 0, outstanding: 1, completionPercentage: 0 },
    },
    isLoading: false, isError: false,
  };
});

describe('RegisterCompletionReportPage', () => {
  it('shows outstanding registers with how many days overdue', () => {
    renderPage();
    expect(screen.getByText('Cohort A')).toBeInTheDocument();
    expect(screen.getByText('3d')).toBeInTheDocument();
    expect(screen.getByText('0 / 5')).toBeInTheDocument();
  });

  it('links a session row to the register page for that session', () => {
    renderPage();
    const link = screen.getByText('10 Jul 2026').closest('a');
    expect(link).toHaveAttribute('href', '/attendance/5');
  });

  it('shows an empty state when no sessions match the filters', () => {
    mockReport = {
      data: { items: [], total: 0, page: 1, pageSize: 25, registerCompletion: { periodStart: '2026-07-01', periodEnd: '2026-07-31', notStarted: 0, inProgress: 0, completed: 0, locked: 0, outstanding: 0, completionPercentage: null } },
      isLoading: false, isError: false,
    };
    renderPage();
    expect(screen.getByText(/no sessions found/i)).toBeInTheDocument();
  });
});
