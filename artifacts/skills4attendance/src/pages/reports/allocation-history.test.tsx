import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import AllocationHistoryReportPage from './allocation-history';

const transferRow = {
  id: 1, learnerId: 5, learnerName: 'Ada Lovelace',
  previousTutorId: 10, previousTutorName: 'Sam Tutor',
  newTutorId: 11, newTutorName: 'Jo Tutor',
  previousCohortId: 2, previousCohortName: 'Cohort A',
  newCohortId: 3, newCohortName: 'Cohort B',
  effectiveDate: '2026-06-01', effectiveTo: null,
  transferReason: 'Cohort restructure', changedBy: 1, changedByName: 'Alex Admin',
  changedDate: '2026-05-28T09:00:00Z',
};

let mockCurrentUser: any;
let mockTutors: any;
let mockCohorts: any;
let mockAllocationReport: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useListTutors: () => mockTutors,
  getListTutorsQueryKey: (p: unknown) => ['listTutors', p],
  useListCohorts: () => mockCohorts,
  getListCohortsQueryKey: (p: unknown) => ['listCohorts', p],
  useGetAllocationHistoryReport: () => mockAllocationReport,
  getGetAllocationHistoryReportQueryKey: (p: unknown) => ['getAllocationHistoryReport', p],
  exportAllocationHistoryReport: vi.fn().mockResolvedValue('learnerName\nAda Lovelace\n'),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/allocation-history', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/allocation-history" component={AllocationHistoryReportPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockTutors = { data: [] };
  mockCohorts = { data: [] };
  mockAllocationReport = {
    data: { items: [transferRow], total: 1, page: 1, pageSize: 25, notice: 'Cohort transfers change allocation prospectively and do not transfer historical attendance.' },
    isLoading: false, isError: false,
  };
});

describe('AllocationHistoryReportPage', () => {
  it('is admin-only', () => {
    mockCurrentUser = { data: { firstName: 'Sam', role: 'tutor', tutorId: 10 } };
    renderPage();
    expect(screen.getByText(/administrator access required/i)).toBeInTheDocument();
  });

  it('shows the historical-attendance notice and transfer rows for an admin', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText(/do not transfer historical attendance/i)).toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('Sam Tutor')).toBeInTheDocument();
    expect(screen.getByText('Jo Tutor')).toBeInTheDocument();
  });

  it('links a transferred learner to their learner detail page', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    const link = screen.getByText('Ada Lovelace').closest('a');
    expect(link).toHaveAttribute('href', '/learners/5');
  });
});
