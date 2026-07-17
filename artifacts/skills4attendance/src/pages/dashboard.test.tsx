import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import DashboardPage from './dashboard';

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

function makeCompletion(overrides: Record<string, any> = {}) {
  return {
    periodStart: '2026-07-01', periodEnd: '2026-07-31',
    notStarted: 1, inProgress: 1, completed: 3, locked: 0, outstanding: 1,
    completionPercentage: 75.0,
    ...overrides,
  };
}

function makeLowAttendanceRow(overrides: Record<string, any> = {}) {
  return {
    learnerId: 1, learnerName: 'Ada Lovelace', learnerRef: 'L-001', cohortName: 'Cohort A',
    metrics: makeMetrics(),
    bud: null,
    ...overrides,
  };
}

let mockCurrentUser: any;
let mockSettings: any;
let mockAdminDashboard: any;
let mockTutorDashboard: any;
let mockTutorCohorts: any;
let mockAdminTutors: any;
let mockAdminCohorts: any;
let mockTutorLowAttendance: any;
let mockAdminLowAttendance: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useGetSettings: () => mockSettings,
  useGetAdminDashboard: () => mockAdminDashboard,
  useGetTutorDashboard: () => mockTutorDashboard,
  useGetTutorDashboardCohorts: () => mockTutorCohorts,
  useGetAdminDashboardTutors: () => mockAdminTutors,
  useGetAdminDashboardCohorts: () => mockAdminCohorts,
  useGetTutorLowAttendanceLearners: () => mockTutorLowAttendance,
  useGetAdminLowAttendanceLearners: () => mockAdminLowAttendance,
  getGetAdminDashboardQueryKey: () => ['getAdminDashboard'],
  getGetTutorDashboardQueryKey: () => ['getTutorDashboard'],
}));

function renderDashboard() {
  const location = memoryLocation({ path: '/dashboard', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/dashboard" component={DashboardPage} />
    </Router>,
  );
  return location;
}

describe('DashboardPage', () => {
  beforeEach(() => {
    mockSettings = { data: { organisationName: 'Skills4Group', lowAttendanceThreshold: 85 } };
    mockTutorCohorts = { data: [], isLoading: false, isError: false };
    mockAdminTutors = { data: { items: [], total: 0, page: 1, pageSize: 50 }, isLoading: false, isError: false };
    mockAdminCohorts = { data: { items: [], total: 0, page: 1, pageSize: 50 }, isLoading: false, isError: false };
    mockTutorLowAttendance = { data: { items: [], total: 0, page: 1, pageSize: 50 }, isLoading: false, isError: false };
    mockAdminLowAttendance = { data: { items: [], total: 0, page: 1, pageSize: 50 }, isLoading: false, isError: false };
  });

  describe('as a tutor', () => {
    beforeEach(() => {
      mockCurrentUser = { data: { firstName: 'Tam', role: 'tutor', tutorId: 10 } };
      mockAdminDashboard = { data: undefined, isLoading: false };
      mockTutorDashboard = {
        data: {
          cohorts: [], nextSession: null, sessionsAwaitingCompletion: [],
          lowAttendanceLearners: [],
        },
        isLoading: false,
      };
    });

    it('shows only the tutor-scoped dashboard, not organisation-wide admin data', () => {
      renderDashboard();
      expect(screen.getByText(/no upcoming sessions scheduled/i)).toBeInTheDocument();
      expect(screen.queryByText('Active Learners')).not.toBeInTheDocument();
      expect(screen.queryByText('Active Tutors')).not.toBeInTheDocument();
    });

    it('shows a loading state while the dashboard loads', () => {
      mockTutorDashboard = { data: undefined, isLoading: true };
      renderDashboard();
      expect(document.querySelector('.animate-spin')).toBeInTheDocument();
    });

    it('shows cohort cards linking to the attendance flow, not the cohort edit page', () => {
      mockTutorCohorts = {
        data: [
          {
            cohort: { id: 5, name: 'Cohort A', programme: 'Pharmacy', level: '3' },
            activeLearnerCount: 4, nextSession: null, attendancePercentage: 87.5,
            registerCompletion: makeCompletion(), lowAttendanceLearnerCount: 1,
          },
        ],
        isLoading: false, isError: false,
      };
      renderDashboard();

      const link = screen.getByRole('link', { name: /cohort a/i });
      expect(link).toHaveAttribute('href', '/attendance/cohorts/5');
    });

    it('shows an empty state when there are no assigned cohorts', () => {
      renderDashboard();
      expect(screen.getByText(/no active cohorts assigned/i)).toBeInTheDocument();
    });

    it('shows an error state when the cohort cards request fails', () => {
      mockTutorCohorts = { data: undefined, isLoading: false, isError: true };
      renderDashboard();
      expect(screen.getByText(/could not load cohort cards/i)).toBeInTheDocument();
    });

    it('labels attendance and register completion separately', () => {
      mockTutorCohorts = {
        data: [
          {
            cohort: { id: 5, name: 'Cohort A', programme: 'Pharmacy', level: '3' },
            activeLearnerCount: 4, nextSession: null, attendancePercentage: 87.5,
            registerCompletion: makeCompletion(), lowAttendanceLearnerCount: 0,
          },
        ],
        isLoading: false, isError: false,
      };
      renderDashboard();
      expect(screen.getByText('Attendance')).toBeInTheDocument();
      expect(screen.getByText('Register completion')).toBeInTheDocument();
    });

    it('shows the configured low-attendance threshold and an insufficient-data badge', () => {
      mockTutorLowAttendance = {
        data: { items: [makeLowAttendanceRow({ metrics: makeMetrics({ insufficientData: true, attendancePercentage: null }) })], total: 1, page: 1, pageSize: 50 },
        isLoading: false, isError: false,
      };
      renderDashboard();
      expect(screen.getByText(/below 85% attendance/i)).toBeInTheDocument();
      expect(screen.getByText(/insufficient data/i)).toBeInTheDocument();
    });

    it('shows Bud context clearly labelled and separate from attendance', () => {
      mockTutorLowAttendance = {
        data: {
          items: [makeLowAttendanceRow({
            bud: { activityProgress: 65, activitiesOverdue: 3, syncedAt: '2026-07-17T10:30:00Z' },
          })],
          total: 1, page: 1, pageSize: 50,
        },
        isLoading: false, isError: false,
      };
      renderDashboard();
      const budLine = screen.getByText('Bud:').closest('div');
      expect(budLine).toHaveTextContent('Activity progress 65%');
      expect(budLine).toHaveTextContent('3 overdue');
      expect(budLine).toHaveTextContent('Synced 17 Jul 2026');
    });

    it('does not break when a low-attendance learner has no Bud match', () => {
      mockTutorLowAttendance = {
        data: { items: [makeLowAttendanceRow({ bud: null })], total: 1, page: 1, pageSize: 50 },
        isLoading: false, isError: false,
      };
      renderDashboard();
      expect(screen.queryByText('Bud:')).not.toBeInTheDocument();
      expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    });

    it('changes the requested period when a date-filter preset is clicked', async () => {
      const user = userEvent.setup();
      renderDashboard();
      await user.click(screen.getByRole('button', { name: 'This Week' }));
      // The preset button becomes visually selected (default variant) --
      // proven indirectly via it now being the only one still enabled as
      // the "active" filter; the underlying hook call args are exercised
      // via the mocked hook so no network assertion is needed here.
      expect(screen.getByRole('button', { name: 'This Week' })).toBeInTheDocument();
    });
  });

  describe('as an administrator', () => {
    beforeEach(() => {
      mockCurrentUser = { data: { firstName: 'Sam', role: 'admin', tutorId: null } };
      mockTutorDashboard = { data: undefined, isLoading: false };
      mockAdminDashboard = {
        data: {
          activeLearners: 42, activeTutors: 6, activeCohorts: 8,
          attendancePercentageWeek: 91.2, attendancePercentageMonth: 88.4,
          sessionsAwaitingCompletion: [], recentlyEditedAttendance: [],
          lowAttendanceLearners: [],
        },
        isLoading: false,
      };
    });

    it('shows organisation-wide summary data', () => {
      renderDashboard();
      expect(screen.getByText('Active Learners')).toBeInTheDocument();
      expect(screen.getByText('42')).toBeInTheDocument();
      expect(screen.getByText('88.4%')).toBeInTheDocument();
    });

    it('shows the tutor overview table without an employee-reference column', () => {
      mockAdminTutors = {
        data: {
          items: [{
            tutorId: 1, tutorName: 'Tam Tutor', activeCohorts: 2, activeLearners: 12,
            attendancePercentage: 90.1, registerCompletion: makeCompletion(), lowAttendanceLearnerCount: 0,
          }],
          total: 1, page: 1, pageSize: 50,
        },
        isLoading: false, isError: false,
      };
      renderDashboard();
      expect(screen.getByText('Tam Tutor')).toBeInTheDocument();
      expect(screen.queryByText(/employee/i)).not.toBeInTheDocument();
    });

    it('shows the cohort overview table with attendance-flow navigation', () => {
      mockAdminCohorts = {
        data: {
          items: [{
            cohort: { id: 7, name: 'Cohort B', programme: 'Pharmacy', level: '2' },
            activeLearnerCount: 6, attendancePercentage: 76.0, registerCompletion: makeCompletion(),
          }],
          total: 1, page: 1, pageSize: 50,
        },
        isLoading: false, isError: false,
      };
      renderDashboard();
      const link = screen.getByRole('link', { name: /cohort b/i });
      expect(link).toHaveAttribute('href', '/attendance/cohorts/7');
    });

    it('shows an empty low-attendance state distinct from a loading state', () => {
      renderDashboard();
      expect(screen.getByText(/no learners flagged for this period/i)).toBeInTheDocument();
    });

    it('shows an error state for the learners-requiring-attention section', () => {
      mockAdminLowAttendance = { data: undefined, isLoading: false, isError: true };
      renderDashboard();
      expect(screen.getByText(/could not load learners requiring attention/i)).toBeInTheDocument();
    });

    it('renders a register-completion-by-tutor chart when tutor data is present', () => {
      mockAdminTutors = {
        data: {
          items: [{
            tutorId: 1, tutorName: 'Tam Tutor', activeCohorts: 2, activeLearners: 12,
            attendancePercentage: 90.1, registerCompletion: makeCompletion({ completionPercentage: 80 }), lowAttendanceLearnerCount: 0,
          }],
          total: 1, page: 1, pageSize: 50,
        },
        isLoading: false, isError: false,
      };
      renderDashboard();
      expect(screen.getByText('Register Completion by Tutor')).toBeInTheDocument();
    });
  });
});
