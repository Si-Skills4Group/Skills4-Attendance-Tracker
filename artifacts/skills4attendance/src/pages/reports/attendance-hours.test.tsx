import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import AttendanceHoursReportPage from './attendance-hours';

function makeMetrics(overrides: Record<string, any> = {}) {
  return {
    periodStart: '2026-07-01', periodEnd: '2026-07-31',
    expectedMinutes: 420, attendedMinutes: 420,
    authorisedAbsenceMinutes: 0, authorisedAbsenceSessions: 0,
    unauthorisedAbsenceMinutes: 0, unauthorisedAbsenceSessions: 0,
    lateMinutes: 0, lateSessionCount: 0, averageMinutesLate: null,
    missingRecordCount: 0, completedRegisterRowCount: 2,
    attendancePercentage: 100.0, attendanceDataCompleteness: 100.0,
    insufficientData: false, calculatedAt: '2026-07-17T10:00:00Z',
    ...overrides,
  };
}

let mockCurrentUser: any;
let mockTutors: any;
let mockCohorts: any;
let mockReport: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useListTutors: () => mockTutors,
  getListTutorsQueryKey: (p: unknown) => ['listTutors', p],
  useListCohorts: () => mockCohorts,
  useGetAttendanceHoursReport: () => mockReport,
  exportAttendanceHoursReport: vi.fn().mockResolvedValue('label\nCohort A\n'),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/attendance-hours', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/attendance-hours" component={AttendanceHoursReportPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockTutors = { data: [] };
  mockCohorts = { data: [] };
  mockReport = { data: { groupBy: 'cohort', items: [{ key: '2', label: 'Cohort A', metrics: makeMetrics() }] }, isLoading: false, isError: false };
});

describe('AttendanceHoursReportPage', () => {
  it('hides admin-only grouping options for a tutor', () => {
    mockCurrentUser = { data: { firstName: 'Sam', role: 'tutor', tutorId: 10 } };
    renderPage();

    expect(screen.queryByText(/by tutor \(admin only\)/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/by programme \(admin only\)/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/by employer \(admin only\)/i)).not.toBeInTheDocument();
  });

  it('offers a tutor filter for an admin (in addition to the cohort filter every role sees)', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();

    expect(screen.getByText('Tutor')).toBeInTheDocument();
  });

  it('shows the grouped rows returned by the report', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText('Cohort A')).toBeInTheDocument();
    expect(screen.getByText('100.0%')).toBeInTheDocument();
  });
});
