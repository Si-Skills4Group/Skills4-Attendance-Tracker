import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import AllocationReconciliationPage from './allocation-reconciliation';

function makeRow(overrides: Record<string, any> = {}) {
  return {
    learnerName: 'Test Learner', learnerRef: 'REF-1', internalLearnerId: 1, budLearningPlanId: 'PLAN-1',
    internalStatus: 'active', budStatus: 'In Progress', internalTutorId: 10, internalTutorName: 'Tam Tutor',
    budTutorName: 'Tam Tutor (Bud)', programme: 'Pharmacy Technician', programmeSource: 'internal',
    homeCohortId: 5, homeCohortName: 'Cohort A', homeCohortActive: true, homeCohortDeleted: false,
    homeCohortMembershipType: 'primary', classification: 'assigned', reviewReason: null, issueFlags: [],
    ...overrides,
  };
}

let mockCurrentUser: any;
let mockTutors: any;
let mockResult: any;
let mockPopulationSummary: any;
let mockPlanBreakdown: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useListTutors: () => mockTutors,
  getListTutorsQueryKey: (p: unknown) => ['listTutors', p],
  useGetAllocationReconciliation: () => mockResult,
  getGetAllocationReconciliationQueryKey: (p: unknown) => ['getAllocationReconciliation', p],
  useGetAllocationReconciliationPopulationSummary: () => mockPopulationSummary,
  getGetAllocationReconciliationPopulationSummaryQueryKey: () => ['populationSummary'],
  useGetAllocationReconciliationMultiplePlanBreakdown: () => mockPlanBreakdown,
  getGetAllocationReconciliationMultiplePlanBreakdownQueryKey: () => ['multiplePlanBreakdown'],
}));

function renderPage() {
  const location = memoryLocation({ path: '/allocation-reconciliation', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/allocation-reconciliation" component={AllocationReconciliationPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockTutors = { data: [{ id: 10, firstName: 'Tam', lastName: 'Tutor' }] };
  mockResult = {
    data: {
      items: [makeRow()],
      total: 1, page: 1, pageSize: 25,
      counts: {
        confirmedAssigned: 1, confirmedUnassigned: 2,
        needsReview: { recordCount: 3, distinctPeopleCount: 2, budLearningPlanRowCount: 3 },
      },
      availableFilters: { budTutorNames: ['Tam Tutor (Bud)'], budProgrammes: ['Pharmacy Technician'] },
      sourceInfo: {
        sourceMaxSyncedAt: '2026-09-22T08:00:00Z', sourceRowCount: 7966,
        latestAppSyncJob: { id: 79, status: 'ready', startedAt: '2026-09-22T12:08:00Z', completedAt: null },
        sourceCompletenessNote: 'learner_progress is populated by a separate external Bud sync service.',
      },
      ambiguousPlanBreakdown: {
        distinctLearnerReferences: 0, affectedBudLearningPlans: 0, affectedInternalLearners: 0,
        withMultipleInProgressPlans: 0, withOneInProgressPlusHistorical: 0, withOnlyHistoricalPlans: 0,
      },
      calculatedAt: '2026-09-22T12:30:00Z',
    },
    isLoading: false, isError: false,
  };
  mockPopulationSummary = {
    data: {
      calculatedAt: '2026-09-22T12:30:00Z',
      confirmedAssigned: 1904, confirmedUnassigned: 188,
      needsReview: { recordCount: 1448, distinctPeopleCount: 810, budLearningPlanRowCount: 1447 },
      broaderActiveWithoutActiveHomeCohort: 429, budCorroboratedActiveWithoutActiveHomeCohort: 429,
      exclusionBreakdown: [
        { reason: 'confirmed_unassigned', count: 188 },
        { reason: 'bud_status_not_in_progress_but_internally_active', count: 153 },
        { reason: 'ambiguous_multiple_plans', count: 88 },
      ],
      sourceInfo: {
        sourceMaxSyncedAt: '2026-09-22T08:00:00Z', sourceRowCount: 7966,
        latestAppSyncJob: { id: 79, status: 'ready', startedAt: '2026-09-22T12:08:00Z', completedAt: null },
        sourceCompletenessNote: 'learner_progress is populated by a separate external Bud sync service.',
      },
    },
    isLoading: false,
  };
  mockPlanBreakdown = {
    data: {
      linkPointsToCurrentPlan: 36, linkPointsToHistoricalPlan: 0, noExistingLink: 284, conflictingOrUnresolved: 0,
      details: [],
    },
  };
});

describe('AllocationReconciliationPage', () => {
  it('is not shown to a tutor', () => {
    mockCurrentUser = { data: { firstName: 'Sam', role: 'tutor', tutorId: 10 } };
    renderPage();
    expect(screen.getByText(/administrator access required/i)).toBeInTheDocument();
  });

  it('shows the tab counts and the confirmed row for an admin', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByRole('tab', { name: /assigned \(1\)/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /unassigned \(2\)/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /needs review \(3\)/i })).toBeInTheDocument();
    expect(screen.getByText('Test Learner')).toBeInTheDocument();
    expect(screen.getByText('Assigned')).toBeInTheDocument();
  });

  it('shows the source freshness banner with the app sync job and source timestamps kept separate', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText(/7966 learning-plan rows/i)).toBeInTheDocument();
    expect(screen.getByText(/#79/)).toBeInTheDocument();
    expect(screen.getByText(/populated by a separate external Bud sync service/i)).toBeInTheDocument();
  });

  it('shows a review reason for a needs-review row', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    mockResult.data.items = [makeRow({
      classification: 'needs_review', reviewReason: 'ambiguous_multiple_plans',
      issueFlags: ['ambiguous_multiple_plans', 'detail:learner_reference_matches_multiple_bud_rows'],
    })];
    renderPage();
    expect(screen.getByText('Needs Review')).toBeInTheDocument();
    expect(screen.getByText(/multiple bud learning plans/i)).toBeInTheDocument();
    expect(screen.getByText(/\+1 more flag/i)).toBeInTheDocument();
  });

  it('shows an empty state when there are no rows for the current filters', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    mockResult.data.items = [];
    mockResult.data.total = 0;
    renderPage();
    expect(screen.getByText(/no records for these filters/i)).toBeInTheDocument();
  });

  it('expands the corrected Stage 1 population summary panel and shows the exclusion breakdown', async () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    const user = userEvent.setup();
    await user.click(screen.getByText('Stage 1 population summary (corrected)'));
    expect(await screen.findByText('Broader active, no home cohort (any Bud status)')).toBeInTheDocument();
    expect(screen.getByText('429')).toBeInTheDocument();
    expect(screen.getByText('Counted in Confirmed Unassigned')).toBeInTheDocument();
    expect(screen.getByText('Link → current plan')).toBeInTheDocument();
  });

  it('makes clear that Needs Review records and distinct people are different numbers', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText(/3 review records/i)).toBeInTheDocument();
    expect(screen.getByText(/covering 2 distinct identifiable learners/i)).toBeInTheDocument();
  });

  it('shows the ambiguous-plan breakdown when there are ambiguous cases', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    mockResult.data.ambiguousPlanBreakdown = {
      distinctLearnerReferences: 5, affectedBudLearningPlans: 11, affectedInternalLearners: 5,
      withMultipleInProgressPlans: 2, withOneInProgressPlusHistorical: 1, withOnlyHistoricalPlans: 2,
    };
    renderPage();
    expect(screen.getByText(/5 people, 11 Bud plans/i)).toBeInTheDocument();
    expect(screen.getByText(/2 with more than one concurrent In Progress plan/i)).toBeInTheDocument();
  });

  it('flags an inactive home cohort distinctly in the table', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    mockResult.data.items = [makeRow({
      classification: 'unassigned', reviewReason: 'cohort_inactive',
      homeCohortActive: false, issueFlags: ['cohort_inactive'],
    })];
    renderPage();
    expect(screen.getByText('Unassigned')).toBeInTheDocument();
    expect(screen.getByText(/\(inactive\)/i)).toBeInTheDocument();
    expect(screen.getByText(/home cohort inactive/i)).toBeInTheDocument();
  });
});
