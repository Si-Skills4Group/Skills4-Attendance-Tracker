import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
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

const defaultSummary = {
  statusChangesCount: 1,
  newLearnersCount: 1,
  conflictsCount: 1,
  statusChangesAppliedToday: 0,
  learnersCreatedToday: 0,
  lastSuccessfulSyncAt: null,
};

const conflictItem = {
  id: 1, syncJobId: 9, sourceIdentifier: 'PLAN-1', matchStatus: 'conflict', actionType: 'none',
  internalLearnerId: null, proposedValues: {}, previousValues: {}, warnings: ['No active internal Tutor is linked to this Bud tutor_id.'], reason: 'tutor_unmatched',
  approved: false, applied: false, outcome: null, errorCode: null, processedAt: null,
  sourceLearnerReference: 'BUD-REF-1', sourceFirstName: 'Ada', sourceLastName: 'Lovelace', createdAt: '2026-07-01T09:00:00Z',
};

const newItem = {
  id: 2, syncJobId: 9, sourceIdentifier: 'PLAN-2', matchStatus: 'new', actionType: 'create_learner',
  internalLearnerId: null,
  proposedValues: {
    learner: { learnerRef: 'BUD-1', level: '3', firstName: 'Grace', lastName: 'Hopper', startDate: '2026-08-01' },
    tutor: { budTutorId: 'T1', internalTutorId: 5 },
    cohort: { action: 'reuse', cohortId: 8, syncKey: 'bud:5:2026-08-01' },
    allocation: { effectiveDate: '2026-08-01', immediate: false },
    budStatus: 'In Progress',
  },
  previousValues: {}, warnings: [], reason: 'new_bud_record_after_baseline',
  approved: false, applied: false, outcome: null, errorCode: null, processedAt: null,
  sourceLearnerReference: 'BUD-REF-2', sourceFirstName: 'Grace', sourceLastName: 'Hopper', createdAt: '2026-07-01T09:00:00Z',
};

const statusChangeItem = {
  id: 3, syncJobId: 9, sourceIdentifier: 'PLAN-3', matchStatus: 'status_change', actionType: 'change_status',
  internalLearnerId: 42,
  proposedValues: {
    statusChange: {
      previousAcceptedStatusDesc: 'In Progress', currentStatusDesc: 'On Break', currentLearnerStatus: 'active',
      kind: 'automatic', targetStatus: 'paused', dateField: null, effectiveDate: null,
    },
  },
  previousValues: {}, warnings: [], reason: 'post_baseline_bud_change',
  approved: true, applied: false, outcome: null, errorCode: null, processedAt: null,
  sourceLearnerReference: 'BUD-REF-3', sourceFirstName: 'Rosa', sourceLastName: 'Parks', createdAt: '2026-07-02T10:00:00Z',
};

const needsDateStatusChangeItem = {
  ...statusChangeItem,
  id: 4, sourceIdentifier: 'PLAN-4', approved: false,
  proposedValues: {
    statusChange: {
      previousAcceptedStatusDesc: 'In Progress', currentStatusDesc: 'Withdrawn', currentLearnerStatus: 'active',
      kind: 'needs_date', targetStatus: 'withdrawn', dateField: 'withdrawalDate', effectiveDate: null,
    },
  },
  sourceLearnerReference: 'BUD-REF-4', sourceFirstName: 'Marie', sourceLastName: 'Curie',
};

let currentStatus: typeof noActiveBaselineStatus | typeof activeBaselineStatus = noActiveBaselineStatus;
let currentJob: any = null;
let currentSummary: any = defaultSummary;
let statusChangeItems: any[] = [statusChangeItem];
let newLearnerItems: any[] = [newItem];
let conflictItems: any[] = [conflictItem];
const mutateSpies = {
  establish: vi.fn(),
  reset: vi.fn(),
  preview: vi.fn(),
  updateItem: vi.fn(),
  commit: vi.fn(),
  linkExisting: vi.fn(),
};

vi.mock('@workspace/api-client-react', () => ({
  useGetBudSyncStatus: () => ({ data: currentStatus, isLoading: false }),
  useGetSettings: () => ({ data: settings }),
  useEstablishBudSyncBaseline: () => ({ mutate: mutateSpies.establish, isPending: false }),
  useResetBudSyncBaseline: () => ({ mutate: mutateSpies.reset, isPending: false }),
  useCreateBudSyncPreview: () => ({ mutate: mutateSpies.preview, isPending: false }),
  useUpdateBudSyncJobItem: () => ({ mutate: mutateSpies.updateItem, isPending: false }),
  useCommitBudSyncJob: () => ({ mutate: mutateSpies.commit, isPending: false }),
  useLinkBudSyncJobItemToExistingLearner: () => ({ mutate: mutateSpies.linkExisting, isPending: false }),
  useGetBudSyncJob: () => ({ data: currentJob }),
  useGetBudSyncJobSummary: () => ({ data: currentSummary }),
  useListLearners: () => ({ data: { items: [], total: 0, page: 1, pageSize: 10 } }),
  useListBudSyncJobItems: (_jobId: number, params: { matchStatus?: string }) => {
    const items = params?.matchStatus === 'status_change' ? statusChangeItems
      : params?.matchStatus === 'new' ? newLearnerItems
      : params?.matchStatus === 'conflict' ? conflictItems
      : [];
    return { data: { items, total: items.length, page: 1, pageSize: 200 }, isLoading: false };
  },
  getGetBudSyncStatusQueryKey: () => ['bud-sync-status'],
  getGetBudSyncJobQueryKey: (id: number) => ['bud-sync-job', id],
  getGetBudSyncJobSummaryQueryKey: (id: number) => ['bud-sync-job-summary', id],
  getListBudSyncJobItemsQueryKey: (id: number) => ['bud-sync-job-items', id],
  getListLearnersQueryKey: () => ['learners'],
}));

describe('BudSyncTrialPage', () => {
  beforeEach(() => {
    currentStatus = noActiveBaselineStatus;
    currentJob = null;
    currentSummary = defaultSummary;
    statusChangeItems = [statusChangeItem];
    newLearnerItems = [newItem];
    conflictItems = [conflictItem];
    Object.values(mutateSpies).forEach((spy) => spy.mockReset());
  });

  it('shows the trial banner', () => {
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.getByText(/Existing unmatched Bud learners from before the trial are excluded/i)).toBeInTheDocument();
  });

  it('offers Establish Trial Baseline when no baseline is active, and disables Check Bud for Changes', () => {
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.getByRole('button', { name: /Establish Trial Baseline/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Check Bud for Changes/i })).toBeDisabled();
  });

  it('shows baseline status and a Reset button, and enables Check Bud for Changes, once a baseline is active', () => {
    currentStatus = activeBaselineStatus;
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.getByText(/Baseline #3/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reset Baseline/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Check Bud for Changes/i })).not.toBeDisabled();
  });

  const activateJob = () => {
    currentStatus = activeBaselineStatus;
    currentJob = {
      id: 9, baselineId: 3, status: 'ready', totalSourceRowsExamined: 12, newLearnersDetected: 1,
      learnerUpdatesDetected: 0, conflictCount: 1, skippedCount: 3, statusChangesDetected: 1,
      appliedCount: 0, errorCount: 0, correlationId: 'corr-abc',
    };
  };

  it('shows actionable summary cards reflecting the backend-computed counts', () => {
    activateJob();
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.getByText('Status changes requiring processing')).toBeInTheDocument();
    expect(screen.getByText('New learners requiring review')).toBeInTheDocument();
    expect(screen.getByText('Conflicts requiring investigation')).toBeInTheDocument();
    expect(screen.getByText('Last successful sync')).toBeInTheDocument();
  });

  it('Status Changes tab displays only status changes, with the transition arrow and no Identifier column', () => {
    activateJob();
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.getByText('BUD-REF-3')).toBeInTheDocument();
    expect(screen.getByText('Rosa')).toBeInTheDocument();
    expect(screen.getByText('In Progress')).toBeInTheDocument();
    expect(screen.getByText('On Break')).toBeInTheDocument();
    expect(screen.queryByText('PLAN-3')).not.toBeInTheDocument();
    expect(screen.queryByText('Identifier')).not.toBeInTheDocument();
    // New Learners / Conflicts tab content is not simultaneously visible.
    expect(screen.queryByText('Grace')).not.toBeInTheDocument();
  });

  it('an automatic status change shows an Applied-pending outcome and no checkbox', () => {
    activateJob();
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.queryByRole('checkbox', { name: /approve item 3/i })).not.toBeInTheDocument();
    expect(screen.getByText(/approved.*pending commit/i)).toBeInTheDocument();
  });

  it('a needs_date status change shows an Awaiting information outcome', () => {
    activateJob();
    statusChangeItems = [needsDateStatusChangeItem];
    renderWithQueryClient(<BudSyncTrialPage />);
    expect(screen.getByText(/awaiting information/i)).toBeInTheDocument();
  });

  it('New Learners tab shows only unmatched eligible learners with a selection checkbox', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<BudSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /new learners/i }));
    expect(screen.getByText('BUD-REF-2')).toBeInTheDocument();
    expect(screen.getByText('Grace')).toBeInTheDocument();
    expect(screen.getByText('In Progress')).toBeInTheDocument();
    const checkboxes = screen.getAllByRole('checkbox');
    checkboxes.forEach((cb) => expect(cb).not.toBeChecked());
  });

  it('Conflicts tab displays unsafe records with no selection checkboxes', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<BudSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /conflicts/i }));
    expect(screen.getByText('BUD-REF-1')).toBeInTheDocument();
    expect(screen.getByText(/tutor unmatched/i)).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('Sync History tab shows technical job information', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<BudSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /sync history/i }));
    expect(screen.getByText('Bud rows examined')).toBeInTheDocument();
    expect(screen.getByText('corr-abc')).toBeInTheDocument();
  });

  it('opens the review dialog instead of approving directly when required fields are missing', async () => {
    const user = userEvent.setup();
    activateJob();
    newLearnerItems = [{ ...newItem, proposedValues: { ...newItem.proposedValues, learner: { ...newItem.proposedValues.learner, learnerRef: null } } }];
    renderWithQueryClient(<BudSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /new learners/i }));

    const rows = screen.getAllByRole('row');
    const newRow = rows.find((r) => within(r).queryByText('BUD-REF-2'));
    await user.click(within(newRow!).getByRole('checkbox'));

    expect(await screen.findByText(/Review Grace Hopper/i)).toBeInTheDocument();
    expect(screen.getByLabelText('learner.learnerRef')).toBeInTheDocument();
    expect(mutateSpies.updateItem).not.toHaveBeenCalled();
  });

  it('the commit confirmation dialog always shows zero historical attendance changes', async () => {
    const user = userEvent.setup();
    activateJob();
    newLearnerItems = [{ ...newItem, approved: true }];
    renderWithQueryClient(<BudSyncTrialPage />);

    await user.click(screen.getByRole('button', { name: /Commit \d+ Approved Change/i }));
    expect(await screen.findByText('Historical attendance changes: 0')).toBeInTheDocument();
  });

  it('establishing a baseline calls the mutation', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<BudSyncTrialPage />);
    await user.click(screen.getByRole('button', { name: /Establish Trial Baseline/i }));
    expect(mutateSpies.establish).toHaveBeenCalled();
  });

  it('offers a Mark as already represented action that opens a learner search dialog', async () => {
    const user = userEvent.setup();
    activateJob();
    renderWithQueryClient(<BudSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /new learners/i }));
    await user.click(screen.getByRole('button', { name: /already represented/i }));
    expect(await screen.findByText(/mark grace hopper as already represented/i)).toBeInTheDocument();
  });
});
