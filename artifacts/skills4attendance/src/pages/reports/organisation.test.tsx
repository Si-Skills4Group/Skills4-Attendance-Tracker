import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import OrganisationReportPage from './organisation';

function makeMetrics(overrides: Record<string, any> = {}) {
  return {
    periodStart: '2026-07-01', periodEnd: '2026-07-31',
    expectedMinutes: 4200, attendedMinutes: 3800,
    authorisedAbsenceMinutes: 100, authorisedAbsenceSessions: 2,
    unauthorisedAbsenceMinutes: 300, unauthorisedAbsenceSessions: 3,
    lateMinutes: 30, lateSessionCount: 2, averageMinutesLate: 15,
    missingRecordCount: 0, completedRegisterRowCount: 40,
    attendancePercentage: 90.5, attendanceDataCompleteness: 100.0,
    insufficientData: false, calculatedAt: '2026-07-17T10:00:00Z',
    ...overrides,
  };
}

let mockCurrentUser: any;
let mockOrgReport: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useGetOrganisationReportV2: () => mockOrgReport,
  getGetOrganisationReportV2QueryKey: (p: unknown) => ['getOrganisationReportV2', p],
  exportOrganisationReport: vi.fn().mockResolvedValue('tutorName\nSam Tutor\n'),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/organisation', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/organisation" component={OrganisationReportPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockOrgReport = {
    data: {
      activeLearners: 100, activeTutors: 10, activeCohorts: 12, sessionsInPeriod: 200,
      metrics: makeMetrics(),
      registerCompletion: { periodStart: '2026-07-01', periodEnd: '2026-07-31', notStarted: 1, inProgress: 1, completed: 38, locked: 0, outstanding: 1, completionPercentage: 95 },
      tutorBreakdown: [{ tutorId: 10, tutorName: 'Sam Tutor', metrics: makeMetrics() }],
      cohortBreakdown: [{ cohort: { id: 2, name: 'Cohort A' }, metrics: makeMetrics() }],
      programmeBreakdown: [{ programme: 'Pharmacy', metrics: makeMetrics() }],
      levelBreakdown: [{ level: '3', metrics: makeMetrics() }],
      employerBreakdown: [{ employer: 'Acme Ltd', metrics: makeMetrics() }],
    },
    isLoading: false, isError: false,
  };
});

describe('OrganisationReportPage', () => {
  it('is not shown to a tutor', () => {
    mockCurrentUser = { data: { firstName: 'Sam', role: 'tutor', tutorId: 10 } };
    renderPage();
    expect(screen.getByText(/administrator access required/i)).toBeInTheDocument();
    expect(screen.queryByText('Active Learners')).not.toBeInTheDocument();
  });

  it('shows organisation-wide totals and breakdown tabs for an admin', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText('100')).toBeInTheDocument();
    expect(screen.getByText('Sam Tutor')).toBeInTheDocument();
  });
});
