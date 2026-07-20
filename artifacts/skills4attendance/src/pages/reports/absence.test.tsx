import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import AbsenceReportPage from './absence';

function makeMetrics(overrides: Record<string, any> = {}) {
  return {
    periodStart: '2026-07-01', periodEnd: '2026-07-31',
    expectedMinutes: 420, attendedMinutes: 210,
    authorisedAbsenceMinutes: 0, authorisedAbsenceSessions: 0,
    unauthorisedAbsenceMinutes: 210, unauthorisedAbsenceSessions: 1,
    lateMinutes: 0, lateSessionCount: 0, averageMinutesLate: null,
    missingRecordCount: 0, completedRegisterRowCount: 2,
    attendancePercentage: 50.0, attendanceDataCompleteness: 100.0,
    insufficientData: false, calculatedAt: '2026-07-17T10:00:00Z',
    ...overrides,
  };
}

const absenceRow = {
  learnerId: 1, learnerName: 'Ada Lovelace', learnerRef: 'L-001', employer: 'Acme Ltd',
  sessionId: 5, sessionDate: '2026-07-10', cohortId: 2, cohortName: 'Cohort A',
  tutorName: 'Sam Tutor', status: 'absent_unauthorised', plannedDurationHours: 6,
};

let mockCurrentUser: any;
let mockTutors: any;
let mockCohorts: any;
let mockAbsenceReport: any;
let exportAbsenceReportMock: ReturnType<typeof vi.fn>;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useListTutors: () => mockTutors,
  getListTutorsQueryKey: (p: unknown) => ['listTutors', p],
  useListCohorts: () => mockCohorts,
  useGetAbsenceReport: () => mockAbsenceReport,
  exportAbsenceReport: (...args: unknown[]) => exportAbsenceReportMock(...args),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/absence', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/absence" component={AbsenceReportPage} />
    </Router>,
  );
  return location;
}

beforeEach(() => {
  mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
  mockTutors = { data: [{ id: 10, firstName: 'Sam', lastName: 'Tutor' }] };
  mockCohorts = { data: [{ id: 2, name: 'Cohort A' }] };
  mockAbsenceReport = { data: { items: [absenceRow], total: 1, page: 1, pageSize: 25, metrics: makeMetrics() }, isLoading: false, isError: false };
  exportAbsenceReportMock = vi.fn().mockResolvedValue('learnerName,sessionDate\nAda Lovelace,2026-07-10\n');
});

describe('AbsenceReportPage', () => {
  it('shows the metrics summary and absence rows', () => {
    renderPage();

    expect(screen.getByText('50.0%')).toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('Acme Ltd')).toBeInTheDocument();
    // Never shows an employeeRef-style column -- absence rows carry no
    // employee/tutor identifiers beyond a display name.
    expect(screen.queryByText(/employeeRef/i)).not.toBeInTheDocument();
  });

  it('shows a loading state while the report is fetching', () => {
    mockAbsenceReport = { data: undefined, isLoading: true, isError: false };
    renderPage();
    expect(document.querySelector('.animate-spin')).toBeInTheDocument();
  });

  it('shows an error state when the report fails to load', () => {
    mockAbsenceReport = { data: undefined, isLoading: false, isError: true };
    renderPage();
    expect(screen.getByText(/could not load the absence report/i)).toBeInTheDocument();
  });

  it('shows an empty state when there are no absences for the current filters', () => {
    mockAbsenceReport = { data: { items: [], total: 0, page: 1, pageSize: 25, metrics: makeMetrics({ unauthorisedAbsenceSessions: 0 }) }, isLoading: false, isError: false };
    renderPage();
    expect(screen.getByText(/no unauthorised absences found/i)).toBeInTheDocument();
  });

  it('hides the tutor filter for a tutor (server already scopes their data)', () => {
    mockCurrentUser = { data: { firstName: 'Sam', role: 'tutor', tutorId: 10 } };
    renderPage();
    expect(screen.queryByText('All tutors')).not.toBeInTheDocument();
    // Cohort filter is still offered -- a tutor can narrow to one of their own cohorts.
    expect(screen.getByText('All cohorts')).toBeInTheDocument();
  });

  it('shows pagination controls reflecting total row count', () => {
    mockAbsenceReport = { data: { items: [absenceRow], total: 50, page: 1, pageSize: 25, metrics: makeMetrics() }, isLoading: false, isError: false };
    renderPage();
    expect(screen.getByText(/showing 1 to 25 of 50/i)).toBeInTheDocument();
  });

  it('exports a CSV via the generated client and triggers a browser download', async () => {
    const { downloadCsv } = await import('@/lib/csv-download');
    renderPage();

    await userEvent.click(screen.getByRole('button', { name: /export csv/i }));

    await waitFor(() => expect(exportAbsenceReportMock).toHaveBeenCalled());
    expect(downloadCsv).toHaveBeenCalledWith(
      'learnerName,sessionDate\nAda Lovelace,2026-07-10\n',
      'absence-unauthorised-report.csv',
    );
  });
});
