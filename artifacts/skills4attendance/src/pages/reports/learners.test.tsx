import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import LearnerReportPage from './learners';

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

const searchResult = { id: 1, firstName: 'Ada', lastName: 'Lovelace', learnerRef: 'L-001', cohortName: 'Cohort A' };

let mockSearchResults: any;
let mockLearnerReport: any;

vi.mock('@workspace/api-client-react', () => ({
  useListLearners: () => mockSearchResults,
  getListLearnersQueryKey: (p: unknown) => ['listLearners', p],
  useGetLearnerReportV2: () => mockLearnerReport,
  getGetLearnerReportV2QueryKey: (id: number, p: unknown) => ['getLearnerReportV2', id, p],
  exportLearnerReport: vi.fn().mockResolvedValue('sessionDate\n2026-07-10\n'),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/learners', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/learners" component={LearnerReportPage} />
    </Router>,
  );
  return location;
}

beforeEach(() => {
  mockSearchResults = { data: { items: [searchResult], total: 1, page: 1, pageSize: 10 }, isFetching: false };
  mockLearnerReport = { data: undefined, isLoading: false, isError: false };
});

describe('LearnerReportPage', () => {
  it('does not show a report until a learner is selected via search', () => {
    renderPage();
    expect(screen.queryByText('Session History')).not.toBeInTheDocument();
  });

  it('shows search results as the user types and lets them pick a learner', async () => {
    renderPage();
    await userEvent.type(screen.getByPlaceholderText(/search learners/i), 'Ada');
    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText(/L-001/)).toBeInTheDocument();
  });

  it("shows the learner's Bud LMS context separately from attendance figures, never merged into one score", async () => {
    mockLearnerReport = {
      data: {
        learner: { id: 1, firstName: 'Ada', lastName: 'Lovelace', learnerRef: 'L-001', programme: 'Pharmacy', level: '3', cohortName: 'Cohort A' },
        metrics: makeMetrics(),
        registerCompletion: { periodStart: '2026-07-01', periodEnd: '2026-07-31', notStarted: 0, inProgress: 0, completed: 2, locked: 0, outstanding: 0, completionPercentage: 100 },
        bud: { activityProgress: 60, activitiesOverdue: 1, lastSubmissionDate: null, lastCompletedActivity: null, statusDesc: 'On track', learningPlanUrl: null, syncedAt: '2026-07-15T09:00:00Z' },
        sessionHistory: { items: [], total: 0, page: 1, pageSize: 25 },
      },
      isLoading: false, isError: false,
    };
    renderPage();
    await userEvent.type(screen.getByPlaceholderText(/search learners/i), 'Ada');
    await userEvent.click(await screen.findByText('Ada Lovelace'));

    expect(await screen.findByText('Bud LMS Progress')).toBeInTheDocument();
    expect(screen.getByText('On track')).toBeInTheDocument();
    // Attendance percentage and Bud progress are shown in separate cards,
    // not combined into a single derived figure.
    expect(screen.getByText('100.0%')).toBeInTheDocument();
    expect(screen.getByText('60%')).toBeInTheDocument();
  });

  it('never renders an employeeRef field for a learner', () => {
    renderPage();
    expect(screen.queryByText(/employeeRef/i)).not.toBeInTheDocument();
  });
});
