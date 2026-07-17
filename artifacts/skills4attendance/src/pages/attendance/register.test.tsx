import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import RegisterPage from './register';

function makeSession(overrides: Record<string, any> = {}) {
  return {
    id: 200, cohortId: 5, cohortName: 'Cohort A', tutorId: 10, tutorName: 'Tam Tutor',
    sessionDate: '2026-02-01', plannedStartTime: '09:00:00', plannedEndTime: '16:00:00',
    plannedDurationHours: 7, title: 'Module 1', notes: null, createdBy: 1,
    status: 'scheduled', cancelledAt: null, cancellationReason: null, overrideReason: null,
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    recordedCount: 1, expectedCount: 2, registerStatus: 'in_progress',
    ...overrides,
  };
}

const entries = [
  { learnerId: 1, learnerName: 'Ada Lovelace', learnerRef: 'L-001', recordId: 10, status: 'present', hoursAttended: 7, minutesLate: 0, notes: null, overrideReason: null, lastEditedBy: null, lastEditedByName: null },
  { learnerId: 2, learnerName: 'Bob Smith', learnerRef: 'L-002', recordId: null, status: 'absent_unauthorised', hoursAttended: 0, minutesLate: 0, notes: null, overrideReason: null, lastEditedBy: null, lastEditedByName: null },
];

let mockRegister: { data: any; isLoading: boolean };
let mockCurrentUser: { data: any };
const mockUpdateMutate = vi.fn();
const mockCancelMutate = vi.fn();
const mockRefreshMutate = vi.fn();
const mockSaveMutate = vi.fn();
const mockMarkAllMutate = vi.fn();

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useGetAttendanceSession: () => mockRegister,
  useSaveAttendanceRegister: () => ({ mutate: mockSaveMutate, isPending: false }),
  useMarkAllPresent: () => ({ mutate: mockMarkAllMutate, isPending: false }),
  useUpdateAttendanceSession: () => ({ mutate: mockUpdateMutate, isPending: false }),
  useCancelAttendanceSession: () => ({ mutate: mockCancelMutate, isPending: false }),
  useRefreshSessionRegister: () => ({ mutate: mockRefreshMutate, isPending: false }),
  getGetAttendanceSessionQueryKey: (id: number) => ['getAttendanceSession', id],
}));

function renderAtLocation() {
  const location = memoryLocation({ path: '/attendance/200', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/attendance/:id" component={RegisterPage} />
    </Router>,
  );
  return location;
}

describe('RegisterPage', () => {
  beforeEach(() => {
    mockCurrentUser = { data: { role: 'admin' } };
    mockUpdateMutate.mockReset();
    mockCancelMutate.mockReset();
    mockRefreshMutate.mockReset();
    mockSaveMutate.mockReset();
    mockMarkAllMutate.mockReset();
  });

  it('shows the register status badge and expected/recorded counts', () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    renderAtLocation();

    expect(screen.getByText('In progress')).toBeInTheDocument();
    expect(screen.getByText('1 of 2 learners recorded')).toBeInTheDocument();
  });

  it('opens an Edit dialog pre-filled with the session details', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /edit/i }));

    expect(screen.getByLabelText('Date')).toHaveValue('2026-02-01');
    expect(screen.getByLabelText('Title / Topic')).toHaveValue('Module 1');
  });

  it('shows a confirmation step when an edit conflicts with recorded attendance', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    mockUpdateMutate.mockImplementation((_payload, { onError }: any) => {
      onError({ status: 409, data: { reason: 'attendance_already_recorded' } });
    });
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /edit/i }));
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    expect(await screen.findByText(/already has recorded attendance/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /confirm change/i })).toBeInTheDocument();
  });

  it('offers a Cancel Session action for admins but not tutors', () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    mockCurrentUser = { data: { role: 'admin' } };
    renderAtLocation();
    expect(screen.getByRole('button', { name: /cancel session/i })).toBeInTheDocument();
  });

  it('hides the Cancel Session action for tutors', () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    mockCurrentUser = { data: { role: 'tutor', tutorId: 10 } };
    renderAtLocation();
    expect(screen.queryByRole('button', { name: /cancel session/i })).not.toBeInTheDocument();
  });

  it('requires a reason to cancel and shows a confirmation step when attendance already exists', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    mockCancelMutate.mockImplementation((_payload, { onError }: any) => {
      onError({ status: 409, data: { reason: 'attendance_already_recorded' } });
    });
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /cancel session/i }));
    const confirmButton = screen.getByRole('button', { name: /^cancel session$/i });
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByLabelText('Reason'), 'Tutor unavailable');
    expect(confirmButton).toBeEnabled();
    await user.click(confirmButton);

    expect(await screen.findByText(/already has recorded attendance/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cancel anyway/i })).toBeInTheDocument();
  });

  it('shows a clear banner and read-only register for a cancelled session', () => {
    mockRegister = {
      data: {
        session: makeSession({ status: 'cancelled', registerStatus: 'cancelled', cancellationReason: 'Weather' }),
        entries,
      },
      isLoading: false,
    };
    renderAtLocation();

    expect(screen.getByText('This session has been cancelled')).toBeInTheDocument();
    expect(screen.getByText(/reason: weather/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /mark all present/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^edit$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /cancel session/i })).not.toBeInTheDocument();
  });

  it('offers a Refresh Expected Learners action for admins on a future, incomplete, non-cancelled session', () => {
    mockRegister = {
      data: { session: makeSession({ sessionDate: '2099-01-01', registerStatus: 'in_progress' }), entries },
      isLoading: false,
    };
    renderAtLocation();
    expect(screen.getByRole('button', { name: /refresh expected learners/i })).toBeInTheDocument();
  });

  it('hides the Refresh action for a historical session', () => {
    mockRegister = {
      data: { session: makeSession({ sessionDate: '2020-01-01' }), entries },
      isLoading: false,
    };
    renderAtLocation();
    expect(screen.queryByRole('button', { name: /refresh expected learners/i })).not.toBeInTheDocument();
  });

  it('hides the Refresh action for tutors', () => {
    mockCurrentUser = { data: { role: 'tutor', tutorId: 10 } };
    mockRegister = {
      data: { session: makeSession({ sessionDate: '2099-01-01' }), entries },
      isLoading: false,
    };
    renderAtLocation();
    expect(screen.queryByRole('button', { name: /refresh expected learners/i })).not.toBeInTheDocument();
  });

  it('previews additions and removals before applying a refresh', async () => {
    mockRegister = {
      data: { session: makeSession({ sessionDate: '2099-01-01' }), entries },
      isLoading: false,
    };
    mockRefreshMutate.mockImplementation((payload, { onSuccess }: any) => {
      if (payload.data.confirm) {
        onSuccess({ added: [{ learnerId: 3, learnerName: 'Carol Jones' }], removed: [], blocked: [] });
      } else {
        onSuccess({ toAdd: [{ learnerId: 3, learnerName: 'Carol Jones' }], toRemove: [], blocked: [] });
      }
    });
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /refresh expected learners/i }));

    expect(await screen.findByText('To add (1)')).toBeInTheDocument();
    expect(screen.getByText('Carol Jones')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /apply changes/i }));

    await waitFor(() => {
      expect(mockRefreshMutate).toHaveBeenLastCalledWith(
        { id: 200, data: { confirm: true } },
        expect.anything(),
      );
    });
  });

  it('shows a loading state while the register loads', () => {
    mockRegister = { data: undefined, isLoading: true };
    renderAtLocation();
    expect(document.querySelector('.animate-spin')).toBeInTheDocument();
  });
});
