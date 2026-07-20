import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import LatenessReportPage from './lateness';

function makeMetrics(overrides: Record<string, any> = {}) {
  return {
    periodStart: '2026-07-01', periodEnd: '2026-07-31',
    expectedMinutes: 420, attendedMinutes: 400,
    authorisedAbsenceMinutes: 0, authorisedAbsenceSessions: 0,
    unauthorisedAbsenceMinutes: 0, unauthorisedAbsenceSessions: 0,
    lateMinutes: 20, lateSessionCount: 1, averageMinutesLate: 20,
    missingRecordCount: 0, completedRegisterRowCount: 2,
    attendancePercentage: 95.2, attendanceDataCompleteness: 100.0,
    insufficientData: false, calculatedAt: '2026-07-17T10:00:00Z',
    ...overrides,
  };
}

const latenessRow = {
  learnerId: 1, learnerName: 'Ada Lovelace', learnerRef: 'L-001',
  sessionId: 5, sessionDate: '2026-07-10', plannedStartTime: '09:00',
  cohortId: 2, cohortName: 'Cohort A', tutorName: 'Sam Tutor',
  minutesLate: 20, hoursAttended: 5.5,
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
  useGetLatenessReport: () => mockReport,
  exportLatenessReport: vi.fn().mockResolvedValue('learnerName\nAda Lovelace\n'),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/lateness', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/lateness" component={LatenessReportPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
  mockTutors = { data: [] };
  mockCohorts = { data: [] };
  mockReport = { data: { items: [latenessRow], total: 1, page: 1, pageSize: 25, metrics: makeMetrics() }, isLoading: false, isError: false };
});

describe('LatenessReportPage', () => {
  it('shows late-arrival rows with minutes late', () => {
    renderPage();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('20')).toBeInTheDocument();
  });

  it('shows a loading state while fetching', () => {
    mockReport = { data: undefined, isLoading: true, isError: false };
    renderPage();
    expect(document.querySelector('.animate-spin')).toBeInTheDocument();
  });

  it('shows an empty state when there are no late arrivals', () => {
    mockReport = { data: { items: [], total: 0, page: 1, pageSize: 25, metrics: makeMetrics({ lateSessionCount: 0 }) }, isLoading: false, isError: false };
    renderPage();
    expect(screen.getByText(/no late arrivals found/i)).toBeInTheDocument();
  });
});
