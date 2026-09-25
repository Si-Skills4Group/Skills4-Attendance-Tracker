import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import BudMissingSourcePage from './bud-missing-source';

let mockCurrentUser: any;
let mockExceptions: any;
let mockRefreshMutate: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useGetBudMissingSourceExceptions: () => mockExceptions,
  getGetBudMissingSourceExceptionsQueryKey: (p: unknown) => ['missingSourceExceptions', p],
  useRefreshBudMissingSource: (opts: any) => ({
    mutate: () => mockRefreshMutate(opts),
    isPending: false,
  }),
}));

function renderPage() {
  const location = memoryLocation({ path: '/bud-missing-source', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/bud-missing-source" component={BudMissingSourcePage} />
    </Router>,
  );
}

beforeEach(() => {
  mockRefreshMutate = vi.fn();
  mockExceptions = {
    data: {
      items: [{
        id: 1, internalLearnerId: 42, budLearningPlanId: 'PLAN-MISSING-1', learnerReference: 'REF-1',
        status: 'open', firstDetectedAt: '2026-09-20T09:00:00Z', lastConfirmedMissingAt: '2026-09-23T09:00:00Z',
        resolvedAt: null, learnerName: 'Jordan Example', learnerStatus: 'active',
        otherPlansForPersonStillPresent: false, personEntirelyAbsentFromSource: true, sourceReferenceUnknown: false,
      }],
      sourceInfo: {
        sourceMaxSyncedAt: '2026-09-23T06:00:00Z', sourceRowCount: 7982,
        latestAppSyncJob: { id: 80, status: 'ready', startedAt: '2026-09-23T08:29:00Z', completedAt: null },
        sourceCompletenessNote: 'learner_progress is populated by a separate external Bud sync service.',
      },
      refreshStatus: {
        lastAttemptedAt: '2026-09-23T09:00:00Z', lastTriggeredBy: 1,
        lastSucceededAt: '2026-09-23T09:00:00Z', lastNewlyOpened: 1, lastResolved: 0, lastError: null,
      },
    },
    isLoading: false, isError: false,
  };
});

describe('BudMissingSourcePage', () => {
  it('is not shown to a tutor', () => {
    mockCurrentUser = { data: { firstName: 'Sam', role: 'tutor', tutorId: 10 } };
    renderPage();
    expect(screen.getByText(/administrator access required/i)).toBeInTheDocument();
  });

  it('shows an open exception with its classification', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText('Jordan Example')).toBeInTheDocument();
    expect(screen.getByText('PLAN-MISSING-1')).toBeInTheDocument();
    expect(screen.getByText(/person entirely absent from source/i)).toBeInTheDocument();
  });

  it('shows the source freshness and completeness note', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText(/7982 learning-plan rows/i)).toBeInTheDocument();
    expect(screen.getByText(/populated by a separate external Bud sync service/i)).toBeInTheDocument();
  });

  it('shows an empty state when there are no exceptions', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    mockExceptions.data.items = [];
    renderPage();
    expect(screen.getByText(/no open exceptions/i)).toBeInTheDocument();
  });

  it('shows the last successful detection time and triggers a refresh', async () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText(/last successful detection/i)).toBeInTheDocument();
    expect(screen.getByText(/1 newly opened, 0 resolved/i)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /refresh detection/i }));
    expect(mockRefreshMutate).toHaveBeenCalled();
  });

  it('shows a refresh failure without hiding the last successful results', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    mockExceptions.data.refreshStatus.lastError = 'simulated detection failure';
    mockExceptions.data.refreshStatus.lastAttemptedAt = '2026-09-23T10:00:00Z';
    renderPage();
    // The failure is shown...
    expect(screen.getByText(/simulated detection failure/i)).toBeInTheDocument();
    // ...but the last successful results and the exception list are still visible.
    expect(screen.getByText(/last successful detection/i)).toBeInTheDocument();
    expect(screen.getByText('Jordan Example')).toBeInTheDocument();
  });
});
