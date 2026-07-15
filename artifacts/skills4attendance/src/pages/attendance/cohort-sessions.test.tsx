import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import CohortSessionsPage from './cohort-sessions';

const cohort = {
  id: 5, name: 'Cohort A', programme: 'Data Analyst', level: '3', tutorId: 10, tutorName: 'Tam Tutor',
  deliveryDay: 'monday', sessionStartTime: '09:00:00', sessionEndTime: '16:00:00',
  startDate: '2026-01-01', endDate: null, active: true, externalSystemId: null,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
};

const sessionA1 = {
  id: 100, cohortId: 5, cohortName: 'Cohort A', tutorId: 10, tutorName: 'Tam Tutor',
  sessionDate: '2026-02-01', plannedStartTime: '09:00:00', plannedEndTime: '16:00:00',
  plannedDurationHours: 7, title: null, notes: null, createdBy: 1,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  recordedCount: 3, expectedCount: 5,
};

let mockCohort: { data: any; isLoading: boolean; isError: boolean };
let mockSessions: { data: any[]; isLoading: boolean; isError: boolean };

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => ({ data: { role: 'admin' } }),
  useGetCohort: () => mockCohort,
  useListAttendanceSessions: () => mockSessions,
  useCreateAttendanceSession: () => ({ mutate: vi.fn(), isPending: false }),
  getGetCohortQueryKey: (id: number) => ['getCohort', id],
  getListAttendanceSessionsQueryKey: (params: unknown) => ['listAttendanceSessions', params],
}));

function renderAtLocation(searchPath = '') {
  const location = memoryLocation({ path: '/attendance/cohorts/5', searchPath, record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/attendance/cohorts/:id" component={CohortSessionsPage} />
    </Router>,
  );
  return location;
}

describe('CohortSessionsPage', () => {
  it('shows only the selected cohort\'s sessions, with an explicit completion label', () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [sessionA1], isLoading: false, isError: false };
    renderAtLocation();

    expect(screen.getAllByText('Cohort A').length).toBeGreaterThan(0);
    expect(screen.getByText('Register incomplete')).toBeInTheDocument();
    expect(screen.getByText('3 of 5 learners recorded')).toBeInTheDocument();
  });

  it('shows a visible, functional back button to the cohort list', () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    renderAtLocation();

    const backLink = screen.getByRole('link', { name: /back to all cohorts/i });
    expect(backLink).toBeVisible();
    expect(backLink).toHaveAttribute('href', '/attendance');
  });

  it('preserves the originating cohort-list filters in the back link', () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    renderAtLocation('from=q%3DFoo%26active%3Dall');

    const backLink = screen.getByRole('link', { name: /back to all cohorts/i });
    expect(backLink).toHaveAttribute('href', '/attendance?q=Foo&active=all');
  });

  it('shows a loading state while the cohort is loading', () => {
    mockCohort = { data: undefined, isLoading: true, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    renderAtLocation();

    expect(document.querySelector('.animate-spin')).toBeInTheDocument();
  });

  it('shows an empty state when the cohort has no sessions', () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    renderAtLocation();

    expect(screen.getByText('No sessions yet')).toBeInTheDocument();
  });

  it('shows an error state when sessions fail to load', () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: true };
    renderAtLocation();

    expect(screen.getByText("Couldn't load sessions")).toBeInTheDocument();
  });

  it('offers a New Session action for the authenticated user viewing this cohort', () => {
    // Permission is enforced by the backend (require_cohort_access) --
    // anyone who can reach this page (tutor owning the cohort, or admin)
    // is allowed to create a session for it, so the action is always
    // visible here rather than hidden behind a separate frontend role check.
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    renderAtLocation();

    expect(screen.getByRole('button', { name: /new session/i })).toBeInTheDocument();
  });
});
