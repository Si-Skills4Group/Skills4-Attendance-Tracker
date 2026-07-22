import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQueryClient } from '@/test/test-utils';
import BudSyncTrialPage from './bud-sync-trial';

const noActiveBaselineStatus = {
  sourceMaxSyncedAt: null,
  sourceRowCount: 0,
  matchedLearnerCount: 0,
  unmatchedLearnerCount: 0,
  activeBaseline: null,
};

const activeBaselineStatus = {
  sourceMaxSyncedAt: '2026-06-01T00:00:00Z',
  sourceRowCount: 12,
  matchedLearnerCount: 5,
  unmatchedLearnerCount: 7,
  activeBaseline: { id: 3, establishedAt: '2026-06-01T09:00:00Z', establishedBy: 1, status: 'active' },
};

const settings = {
  organisationName: 'Skills4Group',
  lowAttendanceThreshold: 85,
  budSyncMaxLearnerCreations: 10,
  budSyncMaxLearnerUpdates: 25,
  budSyncMaxCohortCreations: 5,
  budSyncMaxTutorTransfers: 5,
};

const conflictItem = {
  id: 1, syncJobId: 9, sourceIdentifier: 'PLAN-1', matchStatus: 'conflict', actionType: 'create_learner',
  internalLearnerId: null, proposedValues: {}, previousValues: {}, warnings: ['tutor_unmatched'], reason: 'tutor_unmatched',
  approved: false, applied: false, outcome: null, errorCode: null, processedAt: null,
  sourceLearnerReference: 'BUD-REF-1', sourceFirstName: 'Ada', sourceLastName: 'Lovelace',
};

const newItem = {
  id: 2, syncJobId: 9, sourceIdentifier: 'PLAN-2', matchStatus: 'new', actionType: 'create_learner',
  internalLearnerId: null,
  proposedValues: {
    learner: { learnerRef: 'BUD-1', level: '3', firstName: 'Grace', lastName: 'Hopper', startDate: '2026-08-01' },
    tutor: { budTutorId: 'T1', internalTutorId: 5 },
    cohort: { action: 'reuse', cohortId: 8, syncKey: 'bud:5:2026-08-01' },
    allocation: { effectiveDate: '2026-08-01', immediate: false },
  },
  previousValues: {}, warnings: [], reason: 'new_bud_record_after_baseline',
  approved: false, applied: false, outcome: null, errorCode: null, processedAt: null,
  sourceLearnerReference: 'BUD-REF-2', sourceFirstName: 'Grace', sourceLastName: 'Hopper',
};

let currentStatus: typeof noActiveBaselineStatus | typeof activeBaselineStatus = noActiveBaselineStatus;
let currentJob: any = null;
let currentItems: any[] = [conflictItem, newItem];
const mutateSpies = {
  establish: vi.fn(),
  reset: vi.fn(),
  preview: vi.fn(),
  updateItem: vi.fn(),
  commit: vi.fn(),
};

vi.mock('@workspace/api-client-react', () => ({
  useGetBudSyncStatus: () => ({ data: currentStatus, isLoading: false }),
  useGetSettings: () => ({ data: settings }),
  useEstablishBudSyncBaseline: () => ({ mutate: mutateSpies.establish, isPending: false }),
  useResetBudSyncBaseline: () => ({ mutate: mutateSpies.reset, isPending: false }),
  useCreateBudSyncPreview: () => ({ mutate: mutateSpies.preview, isPending: false }),
  useUpdateBudSyncJobItem: () => ({ mutate: mutateSpies.updateItem, isPending: false }),
  useCommitBudSyncJob: () => ({ mutate: mutateSpies.commit, isPending: false }),
  useGetBudSyncJob: () => ({ data: currentJob }),
  useListBudSyncJobItems: () => ({ data: { items: currentItems, total: currentItems.length, page: 1, pageSize: 200 }, isLoading: false }),
  getGetBudSyncStatusQueryKey: () => ['bud-sync-status'],
  getGetBudSyncJobQueryKey: (id: number) => ['bud-sync-job', id],
  getListBudSyncJobItemsQueryKey: (id: number) => ['bud-sync-job-items', id],
}));

describe('BudSyncTrialPage', () => {
  beforeEach(() => {
    currentStatus = noActiveBaselineStatus;
    currentJob = null;
    currentItems = [conflictItem, newItem];
    Object.values(mutateSpies).forEach((spy) => spy.mockReset());
  });

  it('shows the trial banner', () => {
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.getByText(/Existing unmatched Bud learners are excluded/i)).toBeInTheDocument();
  });

  it('offers Establish Trial Baseline when no baseline is active, and disables Run Preview', () => {
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.getByRole('button', { name: /Establish Trial Baseline/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run Preview/i })).toBeDisabled();
  });

  it('shows baseline status and a Reset button, and enables Run Preview, once a baseline is active', () => {
    currentStatus = activeBaselineStatus;
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.getByText(/Baseline #3/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reset Baseline/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run Preview/i })).not.toBeDisabled();
  });

  it('no item is approved by default and the Commit button reflects zero selected', () => {
    currentStatus = activeBaselineStatus;
    currentJob = { id: 9, baselineId: 3, status: 'ready', totalSourceRowsExamined: 2, newLearnersDetected: 1, learnerUpdatesDetected: 0, conflictCount: 1, skippedCount: 0 };
    renderWithQueryClient(<BudSyncTrialPage />);
    const checkboxes = screen.getAllByRole('checkbox');
    checkboxes.forEach((cb) => expect(cb).not.toBeChecked());
    expect(screen.getByRole('button', { name: /Commit 0 Approved Changes/i })).toBeDisabled();
  });

  it('shows the ID (learner reference), first name, and last name columns instead of the raw source identifier', () => {
    currentStatus = activeBaselineStatus;
    currentJob = { id: 9, baselineId: 3, status: 'ready', totalSourceRowsExamined: 2, newLearnersDetected: 1, learnerUpdatesDetected: 0, conflictCount: 1, skippedCount: 0 };
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.getByText('BUD-REF-1')).toBeInTheDocument();
    expect(screen.getByText('Ada')).toBeInTheDocument();
    expect(screen.getByText('Lovelace')).toBeInTheDocument();
    expect(screen.queryByText('PLAN-1')).not.toBeInTheDocument();
    expect(screen.queryByText('Identifier')).not.toBeInTheDocument();
  });

  it('conflicted rows cannot be selected', () => {
    currentStatus = activeBaselineStatus;
    currentJob = { id: 9, baselineId: 3, status: 'ready', totalSourceRowsExamined: 2, newLearnersDetected: 1, learnerUpdatesDetected: 0, conflictCount: 1, skippedCount: 0 };
    renderWithQueryClient(<BudSyncTrialPage />);
    const rows = screen.getAllByRole('row');
    const conflictRow = rows.find((r) => within(r).queryByText('BUD-REF-1'));
    expect(conflictRow).toBeTruthy();
    const checkbox = within(conflictRow!).getByRole('checkbox');
    expect(checkbox).toBeDisabled();
  });

  it('opens the review dialog instead of approving directly when required fields are missing', async () => {
    const user = userEvent.setup();
    currentStatus = activeBaselineStatus;
    currentJob = { id: 9, baselineId: 3, status: 'ready', totalSourceRowsExamined: 2, newLearnersDetected: 1, learnerUpdatesDetected: 0, conflictCount: 1, skippedCount: 0 };
    // newItem already has level/learnerRef/cohort.action=reuse -- fully complete, so remove learnerRef to force the missing-field path.
    currentItems = [conflictItem, { ...newItem, proposedValues: { ...newItem.proposedValues, learner: { ...newItem.proposedValues.learner, learnerRef: null } } }];
    renderWithQueryClient(<BudSyncTrialPage />);

    const rows = screen.getAllByRole('row');
    const newRow = rows.find((r) => within(r).queryByText('BUD-REF-2'));
    await user.click(within(newRow!).getByRole('checkbox'));

    expect(await screen.findByText(/Review Grace Hopper/i)).toBeInTheDocument();
    expect(screen.getByLabelText('learner.learnerRef')).toBeInTheDocument();
    expect(mutateSpies.updateItem).not.toHaveBeenCalled();
  });

  it('the commit confirmation dialog always shows zero historical attendance changes', async () => {
    const user = userEvent.setup();
    currentStatus = activeBaselineStatus;
    currentJob = { id: 9, baselineId: 3, status: 'ready', totalSourceRowsExamined: 2, newLearnersDetected: 1, learnerUpdatesDetected: 0, conflictCount: 1, skippedCount: 0 };
    currentItems = [conflictItem, { ...newItem, approved: true }];
    renderWithQueryClient(<BudSyncTrialPage />);

    await user.click(screen.getByRole('button', { name: /Commit 1 Approved Change/i }));
    expect(await screen.findByText('Historical attendance changes: 0')).toBeInTheDocument();
  });

  it('establishing a baseline calls the mutation', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<BudSyncTrialPage />);
    await user.click(screen.getByRole('button', { name: /Establish Trial Baseline/i }));
    expect(mutateSpies.establish).toHaveBeenCalled();
  });
});
