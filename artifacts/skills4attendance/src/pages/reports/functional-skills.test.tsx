import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import FunctionalSkillsReportPage from './functional-skills';

function makeMetrics(overrides: Record<string, any> = {}) {
  return {
    periodStart: '2026-07-01', periodEnd: '2026-07-31',
    expectedMinutes: 600, attendedMinutes: 300,
    authorisedAbsenceMinutes: 0, authorisedAbsenceSessions: 0,
    unauthorisedAbsenceMinutes: 300, unauthorisedAbsenceSessions: 5,
    lateMinutes: 0, lateSessionCount: 0, averageMinutesLate: null,
    missingRecordCount: 0, completedRegisterRowCount: 10,
    attendancePercentage: 50.0, attendanceDataCompleteness: 100.0,
    insufficientData: false, calculatedAt: '2026-07-17T10:00:00Z',
    ...overrides,
  };
}

const mockExport = vi.fn().mockResolvedValue('cohortName\nFS Maths\n');

let mockCurrentUser: any;
let mockTutors: any;
let mockReport: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useListTutors: () => mockTutors,
  getListTutorsQueryKey: (p: unknown) => ['listTutors', p],
  useGetFunctionalSkillsReport: () => mockReport,
  getGetFunctionalSkillsReportQueryKey: (p: unknown) => ['getFunctionalSkillsReport', p],
  exportFunctionalSkillsReport: (...args: any[]) => mockExport(...args),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/functional-skills', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/functional-skills" component={FunctionalSkillsReportPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockExport.mockClear();
  mockTutors = { data: [{ id: 20, firstName: 'Fran', lastName: 'FSTutor' }] };
  mockReport = {
    data: {
      activeFsCohorts: 2, activeFsLearners: 3, lowAttendanceThreshold: 85,
      metrics: makeMetrics(),
      registerCompletion: { periodStart: '2026-07-01', periodEnd: '2026-07-31', notStarted: 0, inProgress: 1, completed: 4, locked: 0, outstanding: 0, completionPercentage: 100 },
      subjectBreakdown: [{ subject: 'math', metrics: makeMetrics() }],
      cohortBreakdown: [
        {
          cohort: { id: 5, name: 'FS Maths', subject: 'math', tutorName: 'Fran FSTutor' },
          activeLearnerCount: 3,
          metrics: makeMetrics(),
          registerCompletion: { periodStart: '2026-07-01', periodEnd: '2026-07-31', notStarted: 0, inProgress: 0, completed: 4, locked: 0, outstanding: 0, completionPercentage: 100 },
        },
      ],
      atRiskLearners: [
        { learnerId: 1, learnerName: 'Lee Learner', learnerRef: 'L-001', cohortId: 5, cohortName: 'FS Maths', subject: 'math', tutorName: 'Fran FSTutor', metrics: makeMetrics(), bud: null },
      ],
    },
    isLoading: false, isError: false,
  };
});

describe('FunctionalSkillsReportPage', () => {
  it('is not shown to a tutor', () => {
    mockCurrentUser = { data: { firstName: 'Sam', role: 'tutor', tutorId: 10 } };
    renderPage();
    expect(screen.getByText(/administrator access required/i)).toBeInTheDocument();
    expect(screen.queryByText('Active FS Cohorts')).not.toBeInTheDocument();
  });

  it('shows summary cards and the cohort breakdown by default for an admin', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('FS Maths')).toBeInTheDocument();
    expect(screen.getByText('Fran FSTutor')).toBeInTheDocument();
  });

  it('shows the subject breakdown tab', async () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('tab', { name: /subject breakdown/i }));
    expect(screen.getByText('Math')).toBeInTheDocument();
  });

  it('shows the at-risk learners tab', async () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('tab', { name: /at-risk learners/i }));
    expect(screen.getByText('Lee Learner')).toBeInTheDocument();
  });

  it('exports the currently active breakdown', async () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('button', { name: /export cohort breakdown/i }));

    expect(mockExport).toHaveBeenCalledWith(expect.objectContaining({ breakdown: 'cohort' }));
  });
});
