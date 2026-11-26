import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQueryClient } from '@/test/test-utils';
import FsBudSyncTrialPage from './fs-bud-sync-trial';

const noDataStatus = {
  sourceMaxSyncedAt: null, sourcePlanCount: 0, matchedPlanCount: 0, unmatchedPlanCount: 0, lastSuccessfulSyncAt: null,
};

const activeStatus = {
  sourceMaxSyncedAt: '2026-06-01T00:00:00Z', sourcePlanCount: 12, matchedPlanCount: 10, unmatchedPlanCount: 2,
  lastSuccessfulSyncAt: null,
};

const defaultSummary = { newCount: 1, changedCount: 1, unmatchedCount: 1, blockedByClearCount: 1, lastSuccessfulSyncAt: null };

function makeItem(overrides: Partial<Record<string, unknown>>) {
  return {
    id: 1, syncJobId: 9, budLearningPlanId: 'plan-1', internalLearnerId: 42, matchStatus: 'new',
    proposedMaths: true, proposedEnglish: false, existingMaths: null, existingEnglish: null,
    existingStatus: null, existingSource: null,
    sourceSubjectRows: [
      { subject: 'math', level: 1, status: '54%', statusCategory: 'percentage' },
      { subject: 'math', level: 2, status: 'Exempt', statusCategory: 'exempt' },
    ],
    warnings: [], approved: false, applied: false, outcome: null, processedAt: null,
    createdAt: '2026-07-01T09:00:00Z', learnerName: 'Grace Hopper', learnerRef: 'REF-1', learnerStatus: 'active',
    ...overrides,
  };
}

const newItem = makeItem({ id: 2, budLearningPlanId: 'plan-2', matchStatus: 'new', learnerName: 'Grace Hopper' });
const changedItem = makeItem({
  id: 3, budLearningPlanId: 'plan-3', matchStatus: 'changed', learnerName: 'Ada Lovelace',
  existingMaths: false, existingEnglish: false, existingSource: 'manual_upload',
  warnings: ['This will overwrite an existing manually-uploaded requirement for this learner.'],
});
const blockedItem = makeItem({
  id: 4, budLearningPlanId: 'plan-4', matchStatus: 'blocked_by_clear', learnerName: 'Rosa Parks', existingStatus: 'cleared',
});
const unmatchedItem = makeItem({ id: 5, budLearningPlanId: 'plan-5', matchStatus: 'unmatched', internalLearnerId: null, learnerName: null, learnerRef: null });
const unchangedItem = makeItem({ id: 6, budLearningPlanId: 'plan-6', matchStatus: 'unchanged', learnerName: 'Unchanged Learner' });
const unknownStatusItem = makeItem({
  id: 7, budLearningPlanId: 'plan-7', matchStatus: 'new', learnerName: 'Unclear Case',
  sourceSubjectRows: [
    { subject: 'english', level: 1, status: null, statusCategory: 'unknown' },
    { subject: 'english', level: 2, status: null, statusCategory: 'unknown' },
  ],
});

let currentStatus: typeof noDataStatus | typeof activeStatus = noDataStatus;
let currentJob: any = null;
let currentSummary: any = defaultSummary;
let itemsByStatus: Record<string, any[]> = { new: [newItem], changed: [changedItem], blocked_by_clear: [blockedItem], unmatched: [unmatchedItem], unchanged: [unchangedItem] };
const mutateSpies = { preview: vi.fn(), bulkApprove: vi.fn(), commit: vi.fn() };

vi.mock('@workspace/api-client-react', () => ({
  useGetFsBudSyncStatus: () => ({ data: currentStatus, isLoading: false }),
  useCreateFsBudSyncPreview: () => ({ mutate: mutateSpies.preview, isPending: false }),
  useBulkApproveFsBudSyncJobItems: () => ({ mutate: mutateSpies.bulkApprove, isPending: false }),
  useCommitFsBudSyncJob: () => ({ mutate: mutateSpies.commit, isPending: false }),
  useGetFsBudSyncJob: () => ({ data: currentJob }),
  useGetFsBudSyncJobSummary: () => ({ data: currentSummary }),
  useListFsBudSyncJobItems: (_jobId: number, params: { matchStatus?: string; page?: number; pageSize?: number }) => {
    const items = itemsByStatus[params?.matchStatus ?? ''] ?? [];
    return { data: { items, total: items.length, page: params?.page ?? 1, pageSize: params?.pageSize ?? 25 }, isLoading: false };
  },
  getGetFsBudSyncStatusQueryKey: () => ['fs-bud-sync-status'],
  getGetFsBudSyncJobQueryKey: (id: number) => ['fs-bud-sync-job', id],
  getGetFsBudSyncJobSummaryQueryKey: (id: number) => ['fs-bud-sync-job-summary', id],
  getListFsBudSyncJobItemsQueryKey: (id: number) => ['fs-bud-sync-job-items', id],
}));

describe('FsBudSyncTrialPage', () => {
  beforeEach(() => {
    currentStatus = noDataStatus;
    currentJob = null;
    currentSummary = defaultSummary;
    itemsByStatus = { new: [newItem], changed: [changedItem], blocked_by_clear: [blockedItem], unmatched: [unmatchedItem], unchanged: [unchangedItem] };
    Object.values(mutateSpies).forEach((spy) => spy.mockReset());
  });

  it('shows the trial banner explaining cleared-is-sticky behaviour', () => {
    renderWithQueryClient(<FsBudSyncTrialPage />);
    expect(screen.getByText(/Blocked by Clear/i)).toBeInTheDocument();
    expect(screen.getByText(/never overwritten automatically/i)).toBeInTheDocument();
  });

  it('shows source status counts and no active baseline gating (Generate Preview is always enabled)', () => {
    currentStatus = activeStatus;
    renderWithQueryClient(<FsBudSyncTrialPage />);
    expect(screen.getByText(/12 plan\(s\) in the mirror table/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Generate Preview/i })).not.toBeDisabled();
  });

  it('calls the preview mutation when Generate Preview is clicked', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<FsBudSyncTrialPage />);
    await user.click(screen.getByRole('button', { name: /Generate Preview/i }));
    expect(mutateSpies.preview).toHaveBeenCalled();
  });

  const activateJob = () => {
    currentStatus = activeStatus;
    currentJob = {
      id: 9, status: 'ready', startedAt: '2026-07-01T09:00:00Z', completedAt: null, startedBy: 1,
      sourceMaxSyncedAt: '2026-06-01T00:00:00Z', totalPlansExamined: 12,
      newCount: 1, changedCount: 1, unchangedCount: 1, unmatchedCount: 1, blockedByClearCount: 1,
      approvedCount: 0, appliedCount: 0, skippedCount: 0, approvalReason: null, correlationId: 'corr-abc', errorSummary: null,
    };
  };

  it('shows summary cards once a job exists', () => {
    activateJob();
    renderWithQueryClient(<FsBudSyncTrialPage />);
    expect(screen.getByText('New requirements')).toBeInTheDocument();
    expect(screen.getByText('Changed requirements')).toBeInTheDocument();
    expect(screen.getByText("Blocked by an admin's clear")).toBeInTheDocument();
    expect(screen.getByText('Unmatched Bud plans')).toBeInTheDocument();
  });

  it('New tab is shown by default and only shows new items', () => {
    activateJob();
    renderWithQueryClient(<FsBudSyncTrialPage />);
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument();
    expect(screen.queryByText('Ada Lovelace')).not.toBeInTheDocument();
  });

  it('switches to the Blocked by Clear tab and shows its items', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<FsBudSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /blocked by clear/i }));
    expect(screen.getByText('Rosa Parks')).toBeInTheDocument();
    expect(screen.queryByText('Grace Hopper')).not.toBeInTheDocument();
  });

  it('switches to the Unmatched tab and shows its items without a selection checkbox', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<FsBudSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /^unmatched$/i }));
    expect(screen.getAllByText('plan-5').length).toBeGreaterThan(0);
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
  });

  it('switches to the Unchanged tab and shows its items', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<FsBudSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /^unchanged$/i }));
    expect(screen.getByText('Unchanged Learner')).toBeInTheDocument();
  });

  it('renders a prominent overwrite warning badge on the Changed tab', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<FsBudSyncTrialPage />);
    await user.click(screen.getByRole('tab', { name: /^changed$/i }));
    expect(screen.getByText(/this will overwrite an existing manually-uploaded requirement/i)).toBeInTheDocument();
  });

  it('visually distinguishes unknown-status source rows from a confident in-progress badge', () => {
    activateJob();
    itemsByStatus = { ...itemsByStatus, new: [unknownStatusItem] };
    renderWithQueryClient(<FsBudSyncTrialPage />);
    expect(screen.getByText('Unclear source data')).toBeInTheDocument();
  });

  it('selects a New item and bulk-approves it', async () => {
    activateJob();
    const user = userEvent.setup();
    renderWithQueryClient(<FsBudSyncTrialPage />);
    await user.click(screen.getByRole('checkbox', { name: /select item 2/i }));
    await user.click(screen.getByRole('button', { name: /approve selected/i }));
    expect(mutateSpies.bulkApprove).toHaveBeenCalledWith(
      expect.objectContaining({ jobId: 9, data: { itemIds: [2] } }),
      expect.anything(),
    );
  });

  it('disables Commit until an approved item is loaded, and submits the commit with an approval reason', async () => {
    activateJob();
    itemsByStatus = { ...itemsByStatus, new: [makeItem({ id: 2, matchStatus: 'new', approved: true, learnerName: 'Grace Hopper' })] };
    const user = userEvent.setup();
    renderWithQueryClient(<FsBudSyncTrialPage />);

    const commitButton = screen.getByRole('button', { name: /Commit 1 Approved Change/i });
    expect(commitButton).not.toBeDisabled();
    await user.click(commitButton);

    const reasonInput = screen.getByLabelText(/approval reason/i);
    await user.type(reasonInput, 'Reviewed and approved');
    await user.click(screen.getByRole('button', { name: /Confirm Commit/i }));

    expect(mutateSpies.commit).toHaveBeenCalledWith(
      expect.objectContaining({ jobId: 9, data: { itemIds: [2], approvalReason: 'Reviewed and approved' } }),
      expect.anything(),
    );
  });
});
