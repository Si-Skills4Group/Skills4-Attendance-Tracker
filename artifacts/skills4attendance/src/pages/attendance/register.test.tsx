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
    registerVersion: 1, completedAt: null, completedBy: null,
    registerLockedAt: null, registerLockedBy: null, lockReason: null,
    ...overrides,
  };
}

function makeEntries(overrides: Record<number, any> = {}) {
  const base = [
    { learnerId: 1, learnerName: 'Ada Lovelace', learnerRef: 'L-001', recordId: 10, status: 'present', hoursAttended: 7, minutesLate: 0, notes: null, overrideReason: null, lastEditedBy: null, lastEditedByName: null },
    { learnerId: 2, learnerName: 'Bob Smith', learnerRef: 'L-002', recordId: null, status: 'absent_unauthorised', hoursAttended: 0, minutesLate: 0, notes: null, overrideReason: null, lastEditedBy: null, lastEditedByName: null },
  ];
  return base.map(e => ({ ...e, ...(overrides[e.learnerId] || {}) }));
}

const entries = makeEntries();

let mockRegister: { data: any; isLoading: boolean };
let mockCurrentUser: { data: any };
const mockUpdateMutate = vi.fn();
const mockCancelMutate = vi.fn();
const mockRefreshMutate = vi.fn();
const mockSaveMutate = vi.fn();
const mockCompleteMutate = vi.fn();
const mockLockMutate = vi.fn();
const mockUnlockMutate = vi.fn();
let mockSavePending = false;
let mockCompletePending = false;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useGetAttendanceSession: () => mockRegister,
  useSaveAttendanceRegister: () => ({ mutate: mockSaveMutate, isPending: mockSavePending }),
  useCompleteRegister: () => ({ mutate: mockCompleteMutate, isPending: mockCompletePending }),
  useLockAttendanceRegister: () => ({ mutate: mockLockMutate, isPending: false }),
  useUnlockAttendanceRegister: () => ({ mutate: mockUnlockMutate, isPending: false }),
  useUpdateAttendanceSession: () => ({ mutate: mockUpdateMutate, isPending: false }),
  useCancelAttendanceSession: () => ({ mutate: mockCancelMutate, isPending: false }),
  useRefreshSessionRegister: () => ({ mutate: mockRefreshMutate, isPending: false }),
  getGetAttendanceSessionQueryKey: (id: number) => ['getAttendanceSession', id],
}));

vi.mock('@/components/register-history-panel', () => ({
  RegisterHistoryPanel: () => null,
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
    mockSavePending = false;
    mockCompletePending = false;
    mockUpdateMutate.mockReset();
    mockCancelMutate.mockReset();
    mockRefreshMutate.mockReset();
    mockSaveMutate.mockReset();
    mockCompleteMutate.mockReset();
    mockLockMutate.mockReset();
    mockUnlockMutate.mockReset();
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
    expect(screen.queryByRole('button', { name: /save draft/i })).not.toBeInTheDocument();
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

  // -----------------------------------------------------------------------
  // Explicit Save Draft / Complete Register (Phase 7)
  // -----------------------------------------------------------------------

  it('Save Draft submits the current register version and full entries payload', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /save draft/i }));

    expect(mockSaveMutate).toHaveBeenCalledWith(
      {
        id: 200,
        data: expect.objectContaining({
          registerVersion: 1,
          changeReason: undefined,
          entries: expect.arrayContaining([
            expect.objectContaining({ learnerId: 1, status: 'present', hoursAttended: 7 }),
            expect.objectContaining({ learnerId: 2, status: 'absent_unauthorised', hoursAttended: 0 }),
          ]),
        }),
      },
      expect.anything(),
    );
  });

  it('Complete Register saves first, then completes with the freshly-saved version', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    const savedSession = makeSession({ registerVersion: 2 });
    mockSaveMutate.mockImplementation((_payload, { onSuccess }: any) => {
      onSuccess({ session: savedSession, entries });
    });
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /complete register/i }));

    await waitFor(() => {
      expect(mockCompleteMutate).toHaveBeenCalledWith(
        { id: 200, data: { registerVersion: 2 } },
        expect.anything(),
      );
    });
  });

  it('Mark All Present dirties every row locally and is reflected in the Save Draft payload', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /mark all present/i }));
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /save draft/i }));

    expect(mockSaveMutate).toHaveBeenCalledWith(
      {
        id: 200,
        data: expect.objectContaining({
          entries: expect.arrayContaining([
            expect.objectContaining({ learnerId: 2, status: 'present', hoursAttended: 7, minutesLate: 0 }),
          ]),
        }),
      },
      expect.anything(),
    );
  });

  it('confirms before a bulk action overwrites unsaved edits', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /mark all present/i }));
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /mark all present/i }));
    expect(await screen.findByRole('heading', { name: /overwrite unsaved changes/i })).toBeInTheDocument();
    expect(screen.getByText(/mark all present will overwrite unsaved changes on 1 row/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^continue$/i }));
    await waitFor(() => {
      expect(screen.queryByText(/overwrite unsaved changes/i)).not.toBeInTheDocument();
    });
  });

  it('shows a reason prompt when saving a historical register with material changes, and resubmits with the reason', async () => {
    mockRegister = {
      data: { session: makeSession({ sessionDate: '2020-01-01' }), entries },
      isLoading: false,
    };
    let callCount = 0;
    mockSaveMutate.mockImplementation((payload, { onError, onSuccess }: any) => {
      callCount += 1;
      if (callCount === 1) {
        onError({ status: 422, data: { errors: [{ learnerId: null, field: 'changeReason', message: 'A reason is required when editing historical attendance' }] } });
      } else {
        expect(payload.data.changeReason).toBe('Backfilled after paper register');
        onSuccess({ session: makeSession({ sessionDate: '2020-01-01', registerVersion: 2 }), entries });
      }
    });
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /save draft/i }));

    expect(await screen.findByText(/reason for historical change/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText('Reason'), 'Backfilled after paper register');
    await user.click(screen.getByRole('button', { name: /^confirm$/i }));

    await waitFor(() => {
      expect(mockSaveMutate).toHaveBeenCalledTimes(2);
    });
  });

  it('shows row-level validation errors returned from a failed save', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    mockSaveMutate.mockImplementation((_payload, { onError }: any) => {
      onError({ status: 422, data: { errors: [{ learnerId: 2, field: 'minutesLate', message: 'Late attendance requires minutes late greater than zero.' }] } });
    });
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /save draft/i }));

    expect(await screen.findByText(/minutes late greater than zero/i)).toBeInTheDocument();
  });

  it('shows a reload prompt on a stale register version conflict', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    mockSaveMutate.mockImplementation((_payload, { onError }: any) => {
      onError({ status: 409, data: { reason: 'stale_register_version', currentVersion: 5 } });
    });
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /save draft/i }));

    expect(await screen.findByText(/this register has changed/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /reload register/i }));
    await waitFor(() => {
      expect(screen.queryByText(/this register has changed/i)).not.toBeInTheDocument();
    });
  });

  it('does not prompt when navigating back with no unsaved changes', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: 'Back to Attendance' }));
    expect(confirmSpy).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
  });

  it('prompts to confirm before leaving with unsaved changes, and stays put when declined', async () => {
    mockRegister = { data: { session: makeSession(), entries }, isLoading: false };
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /mark all present/i }));
    await user.click(screen.getByRole('button', { name: 'Back to Attendance' }));

    expect(confirmSpy).toHaveBeenCalledWith('You have unsaved attendance changes. Leave without saving?');
    expect(screen.getByText('Cohort A')).toBeInTheDocument();

    confirmSpy.mockRestore();
  });

  it('offers Lock Register for admins once the register is completed', async () => {
    mockRegister = {
      data: { session: makeSession({ registerStatus: 'completed', completedAt: '2026-02-01T16:00:00Z' }), entries },
      isLoading: false,
    };
    const user = userEvent.setup();
    renderAtLocation();

    await user.click(screen.getByRole('button', { name: /lock register/i }));
    await user.type(screen.getByLabelText('Reason'), 'End of module sign-off');
    await user.click(screen.getByRole('button', { name: /^lock register$/i }));

    expect(mockLockMutate).toHaveBeenCalledWith(
      { id: 200, data: { reason: 'End of module sign-off', registerVersion: 1 } },
      expect.anything(),
    );
  });

  it('shows a locked banner and Unlock action for admins, and hides editing controls', async () => {
    mockRegister = {
      data: {
        session: makeSession({
          registerStatus: 'locked',
          registerLockedAt: '2026-02-02T09:00:00Z',
          registerLockedBy: 1,
          lockReason: 'Finance sign-off',
        }),
        entries,
      },
      isLoading: false,
    };
    const user = userEvent.setup();
    renderAtLocation();

    expect(screen.getByText('This register is locked')).toBeInTheDocument();
    expect(screen.getByText(/Finance sign-off/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /save draft/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /unlock register/i }));
    await user.type(screen.getByLabelText('Reason'), 'Correcting a recording error');
    await user.click(screen.getByRole('button', { name: /^unlock register$/i }));

    expect(mockUnlockMutate).toHaveBeenCalledWith(
      { id: 200, data: { reason: 'Correcting a recording error', registerVersion: 1 } },
      expect.anything(),
    );
  });

  it('hides Lock/Unlock actions for tutors', () => {
    mockCurrentUser = { data: { role: 'tutor', tutorId: 10 } };
    mockRegister = {
      data: { session: makeSession({ registerStatus: 'completed' }), entries },
      isLoading: false,
    };
    renderAtLocation();
    expect(screen.queryByRole('button', { name: /lock register/i })).not.toBeInTheDocument();
  });
});
