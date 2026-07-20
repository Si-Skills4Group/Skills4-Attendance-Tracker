import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import TutorReportPage from './tutors';

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
let mockTutorReport: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useListTutors: () => mockTutors,
  getListTutorsQueryKey: (p: unknown) => ['listTutors', p],
  useGetTutorReportV2: () => mockTutorReport,
  getGetTutorReportV2QueryKey: (id: number, p: unknown) => ['getTutorReportV2', id, p],
  exportTutorReport: vi.fn().mockResolvedValue('cohortName\nCohort A\n'),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/tutors', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/tutors" component={TutorReportPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockTutors = { data: [{ id: 10, firstName: 'Sam', lastName: 'Tutor' }] };
});

describe('TutorReportPage', () => {
  it("shows a tutor their own report directly, with no tutor picker", () => {
    mockCurrentUser = { data: { firstName: 'Sam', role: 'tutor', tutorId: 10 } };
    mockTutorReport = {
      data: {
        tutor: { id: 10, firstName: 'Sam', lastName: 'Tutor' },
        activeCohorts: 2, activeLearners: 15,
        metrics: makeMetrics(),
        registerCompletion: { periodStart: '2026-07-01', periodEnd: '2026-07-31', notStarted: 0, inProgress: 0, completed: 2, locked: 0, outstanding: 0, completionPercentage: 100 },
        cohortBreakdown: [{ cohort: { id: 2, name: 'Cohort A' }, metrics: makeMetrics() }],
        lowAttendanceLearners: [],
      },
      isLoading: false, isError: false,
    };
    renderPage();

    expect(screen.queryByPlaceholderText(/select a tutor/i)).not.toBeInTheDocument();
    expect(screen.getByText('Sam Tutor')).toBeInTheDocument();
  });

  it('requires an admin to pick a tutor before showing a report', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    mockTutorReport = { data: undefined, isLoading: false, isError: false };
    renderPage();

    expect(screen.getByText(/select a tutor/i)).toBeInTheDocument();
    expect(screen.queryByText('Cohort Breakdown')).not.toBeInTheDocument();
  });

  it('flags an unlinked tutor account instead of crashing', () => {
    mockCurrentUser = { data: { firstName: 'Sam', role: 'tutor', tutorId: null } };
    mockTutorReport = { data: undefined, isLoading: false, isError: false };
    renderPage();

    expect(screen.getByText(/not linked to a tutor profile/i)).toBeInTheDocument();
  });
});
