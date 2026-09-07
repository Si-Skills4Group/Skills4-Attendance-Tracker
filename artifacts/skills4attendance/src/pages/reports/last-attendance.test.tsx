import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import LastAttendanceReportPage from './last-attendance';

const attendedRow = {
  learnerId: 1, learnerName: 'Ada Lovelace', learnerRef: 'L-001', status: 'active',
  cohortId: 2, cohortName: 'Cohort A', tutorName: 'Sam Tutor', lastAttendedDate: '2026-07-10',
};

const neverAttendedRow = {
  learnerId: 2, learnerName: 'Bob Smith', learnerRef: 'L-002', status: 'active',
  cohortId: 2, cohortName: 'Cohort A', tutorName: 'Sam Tutor', lastAttendedDate: null,
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
  useGetLastAttendanceReport: () => mockReport,
  exportLastAttendanceReport: vi.fn().mockResolvedValue('learnerName\nAda Lovelace\n'),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/last-attendance', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/last-attendance" component={LastAttendanceReportPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
  mockTutors = { data: [] };
  mockCohorts = { data: [] };
  mockReport = { data: { items: [attendedRow, neverAttendedRow], total: 2, page: 1, pageSize: 25 }, isLoading: false, isError: false };
});

describe('LastAttendanceReportPage', () => {
  it('shows each learner with their last attended date', () => {
    renderPage();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('10 Jul 2026')).toBeInTheDocument();
  });

  it('shows a distinct label for a learner who has never attended, not a blank cell', () => {
    renderPage();
    expect(screen.getByText('Bob Smith')).toBeInTheDocument();
    expect(screen.getByText('Never attended')).toBeInTheDocument();
  });

  it('shows a loading state while fetching', () => {
    mockReport = { data: undefined, isLoading: true, isError: false };
    renderPage();
    expect(document.querySelector('.animate-spin')).toBeInTheDocument();
  });

  it('shows an empty state when there are no learners for these filters', () => {
    mockReport = { data: { items: [], total: 0, page: 1, pageSize: 25 }, isLoading: false, isError: false };
    renderPage();
    expect(screen.getByText(/no learners found/i)).toBeInTheDocument();
  });

  it('hides the tutor picker for a tutor, since they are always scoped to their own learners', () => {
    mockCurrentUser = { data: { firstName: 'Tam', role: 'tutor', tutorId: 10 } };
    renderPage();
    expect(screen.queryByText('All tutors')).not.toBeInTheDocument();
    // Cohort filter is still offered -- a tutor can narrow to one of their own cohorts.
    expect(screen.getByText('All cohorts')).toBeInTheDocument();
  });
});
