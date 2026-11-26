import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQueryClient } from '@/test/test-utils';
import AptemSyncTrialPage from './aptem-sync-trial';

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
  aptemSyncMaxLearnerCreations: 10,
  aptemSyncMaxLearnerUpdates: 25,
  aptemSyncMaxCohortCreations: 5,
};

const defaultSummary = {
  statusChangesCount: 1,
  newLearnersCount: 1,
  conflictsCount: 1,
  learnersCreatedToday: 0,
  lastSuccessfulSyncAt: null,
};

const conflictItem = {
  id: 1, syncJobId: 9, sourceIdentifier: '501', matchStatus: 'conflict', actionType: 'none',
  internalLearnerId: null, proposedValues: {}, previousValues: {}, warnings: [], reason: 'uln_and_email_disagree',
  approved: false, applied: false, outcome: null, errorCode: null, processedAt: null,
  sourceFirstName: 'Ada', sourceLastName: 'Lovelace', sourceEmail: 'ada@example.com', createdAt: '2026-07-01T09:00:00Z',
};

const newItem = {
  id: 2, syncJobId: 9, sourceIdentifier: '502', matchStatus: 'new', actionType: 'create_learner',
  internalLearnerId: null,
  proposedValues: {
    learner: { learnerRef: null, level: null, firstName: 'Grace', lastName: 'Hopper', startDate: '2026-08-01', programme: 'Pharmacy Technician' },
    aptemStatus: { episodeStatus: 'Active', episodeIsActive: true },
  },
  previousValues: {}, warnings: ['learnerRef and level must be supplied before this can be approved.'], reason: 'new_aptem_record',
  approved: false, applied: false, outcome: null, errorCode: null, processedAt: null,
  sourceFirstName: 'Grace', sourceLastName: 'Hopper', sourceEmail: 'grace@example.com', createdAt: '2026-07-01T09:00:00Z',
};

const statusChangeItem = {
  id: 3, syncJobId: 9, sourceIdentifier: '503', matchStatus: 'status_change', actionType: 'none',
  internalLearnerId: 42,
  proposedValues: {
    statusChange: {
      previousAcceptedEpisodeIsActive: true, currentEpisodeIsActive: false,
      currentEpisodeStatus: 'Withdrawn', currentLearnerStatus: 'active',
    },
  },
  previousValues: {}, warnings: ['Aptem reports this learner\'s episode active flag changed to False -- status mapping is not yet implemented for Aptem, review and change the learner\'s status manually via the Learner record if appropriate.'],
  reason: 'post_baseline_aptem_change',
  approved: false, applied: false, outcome: null, errorCode: null, processedAt: null,
  sourceFirstName: 'Rosa', sourceLastName: 'Parks', sourceEmail: 'rosa@example.com', createdAt: '2026-07-02T10:00:00Z',
};

let currentStatus: typeof noActiveBaselineStatus | typeof activeBaselineStatus = noActiveBaselineStatus;
let currentJob: any = null;
let currentSummary: any = defaultSummary;
let statusChangeItems: any[] = [statusChangeItem];
let newLearnerItems: any[] = [newItem];
let conflictItems: any[] = [conflictItem];
let newLearnersTotalOverride: number | null = null;
let newLearnerItemsByPage: Record<number, any[]> | null = null;
const mutateSpies = {
  establish: vi.fn(),
  reset: vi.fn(),
  preview: vi.fn(),
  updateItem: vi.fn(),
  bulkApprove: vi.fn(),
  commit: vi.fn(),
  linkExisting: vi.fn(),
};

vi.mock('@workspace/api-client-react', () => ({
  useGetAptemSyncStatus: () => ({ data: currentStatus, isLoading: false }),
  useGetSettings: () => ({ data: settings }),
  useEstablishAptemSyncBaseline: () => ({ mutate: mutateSpies.establish, isPending: false }),
  useResetAptemSyncBaseline: () => ({ mutate: mutateSpies.reset, isPending: false }),
  useCreateAptemSyncPreview: () => ({ mutate: mutateSpies.preview, isPending: false }),
  useUpdateAptemSyncJobItem: () => ({ mutate: mutateSpies.updateItem, isPending: false }),
  useBulkApproveAptemSyncJobItems: () => ({ mutate: mutateSpies.bulkApprove, isPending: false }),
  useCommitAptemSyncJob: () => ({ mutate: mutateSpies.commit, isPending: false }),
  useLinkAptemSyncJobItemToExistingLearner: () => ({ mutate: mutateSpies.linkExisting, isPending: false }),
  useGetAptemSyncJob: () => ({ data: currentJob }),
  useGetAptemSyncJobSummary: () => ({ data: currentSummary }),
  useListLearners: () => ({ data: { items: [], total: 0, page: 1, pageSize: 10 } }),
  useListAptemSyncJobItems: (_jobId: number, params: { matchStatus?: string; page?: number; pageSize?: number }) => {
    const items = params?.matchStatus === 'status_change' ? statusChangeItems
      : params?.matchStatus === 'new' ? (newLearnerItemsByPage ? (newLearnerItemsByPage[params?.page ?? 1] ?? []) : newLearnerItems)
      : params?.matchStatus === 'conflict' ? conflictItems
      : [];
    const total = params?.matchStatus === 'new' && newLearnersTotalOverride !== null ? newLearnersTotalOverride : items.length;
    return { data: { items, total, page: params?.page ?? 1, pageSize: params?.pageSize ?? 200 }, isLoading: false };
  },
  getGetAptemSyncStatusQueryKey: () => ['aptem-sync-status'],
  getGetAptemSyncJobQueryKey: (id: number) => ['aptem-sync-job', id],
  getGetAptemSyncJobSummaryQueryKey: (id: number) => ['aptem-sync-job-summary', id],
  getListAptemSyncJobItemsQueryKey: (id: number) => ['aptem-sync-job-items', id],
  getListLearnersQueryKey: () => ['learners'],
}));

describe('AptemSyncTrialPage', () => {
  beforeEach(() => {
    currentStatus = noActiveBaselineStatus;
    currentJob = null;
    currentSummary = defaultSummary;
    statusChangeItems = [statusChangeItem];
    newLearnerItems = [newItem];
    conflictItems = [conflictItem];
    newLearnersTotalOverride = null;
    newLearnerItemsByPage = null;
    Object.values(mutateSpies).forEach((spy) => spy.mockReset());
  });

  it('shows the trial banner', () => {
    renderWithQueryClient(<AptemSyncTrialPage />);
    expect(screen.getByText(/Phase 1 \(core ingestion only\)/i)).toBeInTheDocument();
  });

  it('offers Establish Trial Baseline when no baseline is active, and disables Check Aptem for Changes', () => {
    renderWithQueryClient(<AptemSyncTrialPage />);
    expect(screen.getByRole('button', { name: /Establish Trial Baseline/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Check Aptem for Changes/i })).toBeDisabled();
  });

  it('shows baseline status and a Reset button, and enables Check Aptem for Changes, once a baseline is active', () => {
    currentStatus = activeBaselineStatus;
    renderWithQueryClient(<AptemSyncTrialPage />);
    expect(screen.getByText(/Baseline #3/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reset Baseline/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Check Aptem for Changes/i })).not.toBeDisabled();
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
    renderWithQueryClient(<AptemSyncTrialPage />);
    expect(screen.getByText('Status changes requiring review')).toBeInTheDocument();
    expect(screen.getByText('New learners requiring review')).toBeInTheDocument();
    expect(screen.getByText('Conflicts requiring investigation')).toBeInTheDocument();
    expect(screen.getByText('Last successful sync')).toBeInTheDocument();
  });

  it('Status Changes tab displays only status changes', () => {
    activateJob();
    renderWithQueryClient(<AptemSyncTrialPage />);
    expect(screen.getByText('503')).toBeInTheDocument();
    expect(screen.getByText('Rosa')).toBeInTheDocument();
    expect(screen.getByText('Withdrawn')).toBeInTheDocument();
    // New Learners / Conflicts tab content is not simultaneously visible.
    expect(screen.queryByText('Grace')).not.toBeInTheDocument();
  });

  it('a status change not yet approved shows an Awaiting review outcome', () => {
    activateJob();
    renderWithQueryClient(<AptemSyncTrialPage />);
    expect(screen.getByText(/awaiting review/i)).toBeInTheDocument();
  });

  it('New Learners tab shows only unmatched eligible learners with a selection checkbox', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<AptemSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /new learners/i }));
    expect(screen.getByText('502')).toBeInTheDocument();
    expect(screen.getByText('Grace')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    const checkboxes = screen.getAllByRole('checkbox');
    checkboxes.forEach((cb) => expect(cb).not.toBeChecked());
  });

  it('New Learners tab paginates once there are more results than fit on one page', async () => {
    activateJob();
    newLearnersTotalOverride = 30;
    const user = userEvent.setup();
    renderWithQueryClient(<AptemSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /new learners/i }));

    expect(screen.getByTestId('new-learners-pager-summary').textContent).toMatch(/showing 1 to 25 of 30 new learners/i);
    expect(screen.getByRole('button', { name: /previous page of new learners/i })).toBeDisabled();
    const next = screen.getByRole('button', { name: /next page of new learners/i });
    expect(next).not.toBeDisabled();

    await user.click(next);
    expect(screen.getByTestId('new-learners-pager-summary').textContent).toMatch(/showing 26 to 30 of 30 new learners/i);
  });

  it('keeps a selection made on an earlier page when bulk-approving after navigating to a later one', async () => {
    const user = userEvent.setup();
    activateJob();
    const page1Item = { ...newItem, id: 10, sourceIdentifier: '510' };
    const page2Item = { ...newItem, id: 11, sourceIdentifier: '511' };
    newLearnersTotalOverride = 30;
    newLearnerItemsByPage = { 1: [page1Item], 2: [page2Item] };
    renderWithQueryClient(<AptemSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /new learners/i }));

    await user.click(screen.getByRole('checkbox', { name: /select item 10/i }));
    await user.click(screen.getByRole('button', { name: /next page of new learners/i }));

    const refInput10 = screen.queryByLabelText(/learner reference for item 11/i);
    expect(refInput10).toBeInTheDocument();
    await user.type(screen.getByLabelText(/learner reference for item 11/i), 'APT-11');
    await user.type(screen.getByLabelText(/level for item 11/i), '3');
    await user.click(screen.getByRole('checkbox', { name: /select item 11/i }));
    await user.click(screen.getByRole('button', { name: /approve.*create selected/i }));

    expect(mutateSpies.bulkApprove).toHaveBeenCalledWith(
      {
        jobId: 9,
        data: {
          items: [
            { itemId: 10, learnerRef: '', level: '' },
            { itemId: 11, learnerRef: 'APT-11', level: '3' },
          ],
        },
      },
      expect.anything(),
    );
  });

  it('Conflicts tab displays unsafe records with no selection checkboxes or resolve action', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<AptemSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /conflicts/i }));
    expect(screen.getByText('501')).toBeInTheDocument();
    expect(screen.getByText(/uln and email disagree/i)).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /resolve/i })).not.toBeInTheDocument();
  });

  it('Sync History tab shows technical job information', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<AptemSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /sync history/i }));
    expect(screen.getByText('Aptem rows examined')).toBeInTheDocument();
    expect(screen.getByText('corr-abc')).toBeInTheDocument();
  });

  it('New Learners tab lets an admin type the required Ref/Level inline and bulk-approve selected rows', async () => {
    const user = userEvent.setup();
    activateJob();
    renderWithQueryClient(<AptemSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /new learners/i }));

    const refInput = screen.getByLabelText(/learner reference for item 2/i);
    const levelInput = screen.getByLabelText(/level for item 2/i);
    await user.type(refInput, 'APT-NEW-1');
    await user.type(levelInput, '4');
    await user.click(screen.getByRole('checkbox', { name: /select item 2/i }));

    const approveButton = screen.getByRole('button', { name: /approve.*create selected/i });
    expect(approveButton).not.toBeDisabled();
    await user.click(approveButton);

    expect(mutateSpies.bulkApprove).toHaveBeenCalledWith(
      { jobId: 9, data: { items: [{ itemId: 2, learnerRef: 'APT-NEW-1', level: '4' }] } },
      expect.anything(),
    );
  });

  it('Approve & Create Selected is disabled until at least one row is selected', async () => {
    const user = userEvent.setup();
    activateJob();
    renderWithQueryClient(<AptemSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /new learners/i }));
    expect(screen.getByRole('button', { name: /approve.*create selected/i })).toBeDisabled();
  });

  it('the commit confirmation dialog always shows zero historical attendance changes', async () => {
    const user = userEvent.setup();
    activateJob();
    newLearnerItems = [{ ...newItem, approved: true }];
    renderWithQueryClient(<AptemSyncTrialPage />);

    await user.click(screen.getByRole('button', { name: /Commit \d+ Approved Change/i }));
    expect(await screen.findByText('Historical attendance changes: 0')).toBeInTheDocument();
  });

  it('establishing a baseline calls the mutation', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<AptemSyncTrialPage />);
    await user.click(screen.getByRole('button', { name: /Establish Trial Baseline/i }));
    expect(mutateSpies.establish).toHaveBeenCalled();
  });

  it('offers a Mark as already represented action that opens a learner search dialog', async () => {
    const user = userEvent.setup();
    activateJob();
    renderWithQueryClient(<AptemSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /new learners/i }));
    await user.click(screen.getByRole('button', { name: /already represented/i }));
    expect(await screen.findByText(/mark grace hopper as already represented/i)).toBeInTheDocument();
  });
});
