import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderWithQueryClient } from '@/test/test-utils';
import { render } from '@testing-library/react';
import CohortSessionsPage from './cohort-sessions';

const cohort = {
  id: 5, name: 'Cohort A', programme: 'Data Analyst', level: '3', tutorId: 10, tutorName: 'Tam Tutor',
  deliveryDay: 'monday', sessionStartTime: '09:00:00', sessionEndTime: '16:00:00',
  startDate: '2026-01-01', endDate: '2026-06-30', active: true, externalSystemId: null,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
};

const sessionA1 = {
  id: 100, cohortId: 5, cohortName: 'Cohort A', tutorId: 10, tutorName: 'Tam Tutor',
  sessionDate: '2026-02-01', plannedStartTime: '09:00:00', plannedEndTime: '16:00:00',
  plannedDurationHours: 7, title: null, notes: null, createdBy: 1,
  status: 'scheduled', cancelledAt: null, cancellationReason: null, overrideReason: null,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  recordedCount: 3, expectedCount: 5, registerStatus: 'in_progress',
};

let mockCohort: { data: any; isLoading: boolean; isError: boolean };
let mockSessions: { data: any[]; isLoading: boolean; isError: boolean };
let mockCurrentUser: { data: any };
const mockCreateMutate = vi.fn();

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useGetCohort: () => mockCohort,
  useListAttendanceSessions: () => mockSessions,
  useCreateAttendanceSession: () => ({ mutate: mockCreateMutate, isPending: false }),
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

function renderWithSpiedQueryClient(searchPath = '') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
  const location = memoryLocation({ path: '/attendance/cohorts/5', searchPath, record: true });
  render(
    <QueryClientProvider client={queryClient}>
      <Router hook={location.hook} searchHook={location.searchHook}>
        <Route path="/attendance/cohorts/:id" component={CohortSessionsPage} />
      </Router>
    </QueryClientProvider>,
  );
  return { invalidateSpy };
}

describe('CohortSessionsPage', () => {
  beforeEach(() => {
    mockCurrentUser = { data: { role: 'admin' } };
    mockCreateMutate.mockReset();
  });

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

  it('reads the session filters from the URL', () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    renderAtLocation('dateFrom=2026-02-01&status=cancelled');

    expect(screen.getByDisplayValue('2026-02-01')).toBeInTheDocument();
    expect(screen.getByText('Cancelled')).toBeInTheDocument();
  });

  it('updates the URL when a session filter changes, so a later back navigation restores it', async () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    const user = userEvent.setup();
    const location = renderAtLocation();

    const [dateFromInput] = screen.getAllByDisplayValue('');
    await user.type(dateFromInput, '2026-03-01');

    expect(location.history?.at(-1)).toContain('dateFrom=2026-03-01');
  });

  it('hands the current filtered URL to session links, so opening a register and going back restores this view', () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [sessionA1], isLoading: false, isError: false };
    renderAtLocation('status=scheduled');

    const sessionLink = document.querySelector('a[href^="/attendance/100"]');
    expect(sessionLink).toHaveAttribute(
      'href', `/attendance/100?from=${encodeURIComponent('/attendance/cohorts/5?status=scheduled')}`,
    );
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

  it('shows a cancelled session distinctly, excluded from the completion label', () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = {
      data: [{ ...sessionA1, status: 'cancelled', registerStatus: 'cancelled' }],
      isLoading: false, isError: false,
    };
    renderAtLocation();

    expect(screen.getByText('Session cancelled')).toBeInTheDocument();
    expect(screen.queryByText('Register incomplete')).not.toBeInTheDocument();
  });

  it('warns when the chosen date falls outside the cohort\'s start/end dates', async () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /new session/i }));
    const dateInput = screen.getByLabelText('Date');
    await user.clear(dateInput);
    await user.type(dateInput, '2026-08-15');

    expect(screen.getByText(/falls outside the cohort's start\/end dates/i)).toBeInTheDocument();
  });

  it('auto-calculates the duration from start/end time, rounded to the nearest hour', async () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /new session/i }));
    const durationInput = screen.getByLabelText('Duration (Hours)');
    // Cohort defaults (09:00-16:00) start the dialog at exactly 7 hours.
    expect(durationInput).toHaveValue(7);

    const endTimeInput = screen.getByLabelText('End Time');
    await user.clear(endTimeInput);
    await user.type(endTimeInput, '13:40');
    // 09:00-13:40 is 4h40m -- rounds up to 5, not truncated to 4.
    expect(durationInput).toHaveValue(5);

    const startTimeInput = screen.getByLabelText('Start Time');
    await user.clear(startTimeInput);
    await user.type(startTimeInput, '10:20');
    // 10:20-13:40 is 3h20m -- rounds down to 3.
    expect(durationInput).toHaveValue(3);
  });

  it('still lets an admin manually override the auto-calculated duration', async () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /new session/i }));
    const durationInput = screen.getByLabelText('Duration (Hours)');
    fireEvent.change(durationInput, { target: { value: '2.5' } });

    expect(durationInput).toHaveValue(2.5);
  });

  it('shows a conflict dialog on 409 and lets an admin override with a reason', async () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    mockCreateMutate.mockImplementation((_payload, { onError }: any) => {
      onError({ status: 409, data: { reasons: ['duplicate_session'] } });
    });
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /new session/i }));
    await user.type(screen.getByLabelText('Title / Topic'), 'Module 1');
    await user.click(screen.getByRole('button', { name: /create register/i }));

    expect(await screen.findByText('Session Conflict Detected')).toBeInTheDocument();
    expect(screen.getByText(/a session already exists for this cohort/i)).toBeInTheDocument();

    const createAnyway = screen.getByRole('button', { name: /create anyway/i });
    expect(createAnyway).toBeDisabled();

    await user.type(screen.getByPlaceholderText(/explain why/i), 'Second session approved');
    expect(createAnyway).toBeEnabled();
  });

  it('does not let a tutor override a conflict -- no reason field or override button', async () => {
    mockCurrentUser = { data: { role: 'tutor', tutorId: 10 } };
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    mockCreateMutate.mockImplementation((_payload, { onError }: any) => {
      onError({ status: 409, data: { reasons: ['duplicate_session'] } });
    });
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /new session/i }));
    await user.type(screen.getByLabelText('Title / Topic'), 'Module 1');
    await user.click(screen.getByRole('button', { name: /create register/i }));

    expect(await screen.findByText('Session Conflict Detected')).toBeInTheDocument();
    expect(screen.getByText(/only an administrator can confirm/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /create anyway/i })).not.toBeInTheDocument();
  });

  it('refreshes the session list after creating a session, without needing a manual reload', async () => {
    // Regression test: creating a session used to only close the dialog --
    // the new session wouldn't appear until the user manually refreshed the
    // page, since nothing told the sessions-list query it was stale.
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    mockCreateMutate.mockImplementation((_payload, { onSuccess }: any) => onSuccess());
    const user = userEvent.setup();
    const { invalidateSpy } = renderWithSpiedQueryClient();

    await user.click(screen.getByRole('button', { name: /new session/i }));
    await user.type(screen.getByLabelText('Title / Topic'), 'Module 1');
    await user.click(screen.getByRole('button', { name: /create register/i }));

    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledWith(
      { queryKey: ['listAttendanceSessions', undefined] },
    ));
  });

  it('offers session-status and register-status filters', () => {
    mockCohort = { data: cohort, isLoading: false, isError: false };
    mockSessions = { data: [], isLoading: false, isError: false };
    renderAtLocation();

    expect(screen.getByText('All sessions')).toBeInTheDocument();
    expect(screen.getByText('Any register status')).toBeInTheDocument();
  });
});
