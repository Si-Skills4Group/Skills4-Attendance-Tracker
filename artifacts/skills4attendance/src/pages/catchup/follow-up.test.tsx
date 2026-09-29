import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import CatchupFollowUpPage from './follow-up';

let mockCurrentUser: any;
let mockCohorts: any;
let mockFollowUpList: any;
let mockListParamCalls: any[];
let mockRecordMutate: any;
let mockCorrectMutate: any;
let mockRevokeMutate: any;
let mockHistory: any;
let recordResult: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useListCohorts: () => ({ data: mockCohorts }),
  useListCatchupFollowUp: (params: any) => { mockListParamCalls.push(params); return mockFollowUpList; },
  getListCatchupFollowUpQueryKey: () => ['listCatchupFollowUp'],
  useRecordCatchup: () => ({
    mutate: (vars: any, opts: any) => { mockRecordMutate(vars); opts?.onSuccess?.(recordResult); },
    isPending: false,
  }),
  useCorrectCatchup: () => ({
    mutate: (vars: any, opts: any) => { mockCorrectMutate(vars); opts?.onSuccess?.(recordResult); },
    isPending: false,
  }),
  useRevokeCatchup: () => ({
    mutate: (vars: any, opts: any) => { mockRevokeMutate(vars); opts?.onSuccess?.(recordResult); },
    isPending: false,
  }),
  useGetCatchupHistory: () => mockHistory,
}));

function renderPage() {
  const location = memoryLocation({ path: '/catchup/follow-up', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/catchup/follow-up" component={CatchupFollowUpPage} />
    </Router>,
  );
}

const OUTSTANDING_ITEM = {
  learnerId: 5, learnerName: 'Jamie Lee', sessionId: 100, cohortId: 3, cohortName: 'Maths AM',
  sessionDate: '2026-01-06', sessionTitle: 'Week 2', originalStatus: 'absent_authorised',
  catchupId: null, catchupStatus: null, completionDate: null, method: null, note: null, effective: false,
};

const COMPLETED_ITEM = {
  learnerId: 6, learnerName: 'Priya Shah', sessionId: 101, cohortId: 3, cohortName: 'Maths AM',
  sessionDate: '2026-01-07', sessionTitle: null, originalStatus: 'absent_unauthorised',
  catchupId: 9, catchupStatus: 'recorded', completionDate: '2026-01-08', method: 'recording_watched',
  note: 'Watched it', effective: true,
};

beforeEach(() => {
  mockRecordMutate = vi.fn();
  mockCorrectMutate = vi.fn();
  mockRevokeMutate = vi.fn();
  mockListParamCalls = [];
  mockCohorts = [{ id: 3, name: 'Maths AM' }];
  mockFollowUpList = { data: { items: [OUTSTANDING_ITEM, COMPLETED_ITEM], total: 2, page: 1, pageSize: 25 }, isLoading: false, isError: false };
  mockHistory = { data: [{ id: 1, userId: 2, userName: 'Alex Admin', action: 'catchup_recorded', newValue: { completionDate: '2026-01-08', method: 'recording_watched', note: 'Watched it' }, timestamp: '2026-01-08T09:00:00Z' }], isLoading: false };
  recordResult = { catchup: { id: 10 }, effective: true, ineligibleReason: null };
});

describe('CatchupFollowUpPage', () => {
  it('lists outstanding and completed absences with the right actions', () => {
    mockCurrentUser = { data: { role: 'tutor', tutorId: 7 } };
    renderPage();

    expect(screen.getByText('Jamie Lee')).toBeInTheDocument();
    expect(screen.getByText('Priya Shah')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /record catch-up/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /correct/i })).toBeInTheDocument();
  });

  it('hides the cohort filter for a tutor', () => {
    mockCurrentUser = { data: { role: 'tutor', tutorId: 7 } };
    renderPage();
    expect(screen.queryByPlaceholderText('Cohort')).not.toBeInTheDocument();
  });

  it('opens the Record Catch-up dialog, which stays disabled until method and note are filled in', async () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /record catch-up/i }));
    expect(await screen.findByRole('heading', { name: 'Record Catch-up' })).toBeInTheDocument();

    const confirmButton = screen.getByRole('button', { name: 'Record Catch-up' });
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByLabelText(/evidence/i), 'Watched the recording on Friday');
    // Method still unset -- still disabled.
    expect(confirmButton).toBeDisabled();
  });

  it('submits a correction with the reason field required', async () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /correct/i }));
    expect(await screen.findByRole('heading', { name: 'Correct Catch-up' })).toBeInTheDocument();

    const saveButton = screen.getByRole('button', { name: /save correction/i });
    // Prefilled from the existing record, but no reason yet -- still disabled.
    expect(saveButton).toBeDisabled();

    await user.type(screen.getByLabelText(/reason for correction/i), 'Wrong method recorded originally');
    expect(saveButton).not.toBeDisabled();
    await user.click(saveButton);

    expect(mockCorrectMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: 101,
        learnerId: 6,
        data: expect.objectContaining({ reason: 'Wrong method recorded originally' }),
      }),
    );
  });

  it('requires a reason before revoking an existing catch-up', async () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Revoke' }));
    expect(await screen.findByRole('heading', { name: 'Revoke Catch-up' })).toBeInTheDocument();

    const revokeButton = screen.getByRole('button', { name: 'Revoke Catch-up' });
    expect(revokeButton).toBeDisabled();

    await user.type(screen.getByLabelText(/^reason$/i), 'Learner disputes this was ever completed');
    expect(revokeButton).not.toBeDisabled();
    await user.click(revokeButton);

    expect(mockRevokeMutate).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 101, learnerId: 6, data: { reason: 'Learner disputes this was ever completed' } }),
    );
  });

  it('shows catch-up history when the history action is used', async () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'History' }));
    expect(await screen.findByText('Catch-up recorded')).toBeInTheDocument();
    expect(screen.getByText(/watched it/i)).toBeInTheDocument();
  });

  it('navigates to the previous week and back to the current week', async () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();
    const user = userEvent.setup();

    expect(screen.queryByText('This week')).not.toBeInTheDocument();
    await user.click(screen.getByLabelText('Previous week'));
    expect(screen.getByText('This week')).toBeInTheDocument();
    await user.click(screen.getByText('This week'));
    expect(screen.queryByText('This week')).not.toBeInTheDocument();
  });

  it('sends the debounced search text to the server and resets to page 1, instead of filtering client-side', async () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();
    const user = userEvent.setup();

    await user.type(screen.getByPlaceholderText('Search learner...'), 'jamie');

    await waitFor(() => {
      const last = mockListParamCalls.at(-1);
      expect(last.search).toBe('jamie');
      expect(last.page).toBe(1);
    }, { timeout: 1000 });
  });

  it('requests the next page from the server and disables Previous on page 1', async () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    mockFollowUpList = { data: { items: [OUTSTANDING_ITEM, COMPLETED_ITEM], total: 60, page: 1, pageSize: 25 }, isLoading: false, isError: false };
    renderPage();
    const user = userEvent.setup();

    expect(screen.getByText('Showing 1 to 25 of 60')).toBeInTheDocument();
    const [prevButton, nextButton] = screen.getAllByRole('button', { name: '' }).filter((b) => b.querySelector('svg'));
    expect(prevButton).toBeDisabled();

    await user.click(nextButton);
    await waitFor(() => expect(mockListParamCalls.at(-1).page).toBe(2));
  });
});
