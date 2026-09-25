import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import EngagementRecencyReportPage from './engagement-recency';

let mockCurrentUser: any;
let mockTutors: any;
let mockList: any;
let mockListParamCalls: any[];

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useListTutors: () => ({ data: mockTutors }),
  getListTutorsQueryKey: () => ['listTutors'],
  useListEngagementRecency: (params: any) => { mockListParamCalls.push(params); return mockList; },
  exportEngagementRecency: vi.fn().mockResolvedValue('learnerRef\nL-001\n'),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/engagement-recency', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/engagement-recency" component={EngagementRecencyReportPage} />
    </Router>,
  );
}

const WITH_ENGAGEMENT = {
  id: 1, learnerRef: 'L-001', learnerName: 'Jamie Lee', tutorName: 'Alex Tutor', programme: 'Business Admin',
  lastLiveAttendance: '2026-09-10', lastBudSubmission: null, lastBudCompletedActivity: null, lastCatchupCompletion: '2026-09-17',
  latestEngagementDate: '2026-09-17', sources: ['catchup'], daysSinceEngagement: 6,
  budStatus: 'not_linked', budPlanCurrency: null, hasIncompleteSourceCoverage: true, dataQualityIssues: [],
  sourceLimitations: ['No confirmed Bud learning-plan link exists for this learner -- Bud engagement is unavailable, not zero.'],
};

const NO_ENGAGEMENT = {
  id: 2, learnerRef: 'L-002', learnerName: 'Priya Shah', tutorName: 'Alex Tutor', programme: 'Business Admin',
  lastLiveAttendance: null, lastBudSubmission: null, lastBudCompletedActivity: null, lastCatchupCompletion: null,
  latestEngagementDate: null, sources: [], daysSinceEngagement: null,
  budStatus: 'resolved', budPlanCurrency: 'current', hasIncompleteSourceCoverage: false, dataQualityIssues: [],
  sourceLimitations: [],
};

beforeEach(() => {
  mockListParamCalls = [];
  mockTutors = [{ id: 5, firstName: 'Alex', lastName: 'Tutor' }];
  mockList = {
    data: { items: [WITH_ENGAGEMENT, NO_ENGAGEMENT], total: 2, page: 1, pageSize: 25, calculatedAt: '2026-09-23T10:00:00Z', scopeLabel: 'Based on engagement visible to you' },
    isLoading: false, isError: false,
  };
});

describe('EngagementRecencyReportPage', () => {
  it('shows each learner with their latest recorded engagement and source, and "No recorded engagement" separately', () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();

    expect(screen.getByText('Jamie Lee')).toBeInTheDocument();
    expect(screen.getByText('17 Sep 2026')).toBeInTheDocument();
    expect(screen.getByText('Catch-up')).toBeInTheDocument();
    expect(screen.getByText('Priya Shah')).toBeInTheDocument();
    expect(screen.getByText('No recorded engagement')).toBeInTheDocument();
  });

  it('shows the scope label and calculation time', () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText('Based on engagement visible to you')).toBeInTheDocument();
    expect(screen.getByText(/Calculated 23 Sep 2026/)).toBeInTheDocument();
  });

  it('flags a Bud status that is not resolved', () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText('No Bud link')).toBeInTheDocument();
    expect(screen.getByText('Resolved (unverified dates)')).toBeInTheDocument();
  });

  it('shows a distinct "not available to you" badge when Bud is withheld for permission reasons', () => {
    mockCurrentUser = { data: { role: 'tutor', tutorId: 7 } };
    mockList = {
      data: {
        items: [{ ...WITH_ENGAGEMENT, budStatus: 'not_authorized', sourceLimitations: ['Bud activity is not shown because you do not have permission to view it for this learner.'] }],
        total: 1, page: 1, pageSize: 25, calculatedAt: '2026-09-23T10:00:00Z', scopeLabel: 'Based on engagement visible to you',
      },
      isLoading: false, isError: false,
    };
    renderPage();
    expect(screen.getByText('Not available to you')).toBeInTheDocument();
  });

  it('sends the no-recorded-engagement toggle to the server and disables the min-days input while it is on', async () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('No recorded engagement only'));
    await waitFor(() => expect(mockListParamCalls.at(-1).noEngagementOnly).toBe(true));
    expect(screen.getByPlaceholderText('e.g. 30')).toBeDisabled();
  });

  it('sends the debounced min-days-since filter to the server, resetting to page 1', async () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();
    const user = userEvent.setup();

    await user.type(screen.getByPlaceholderText('e.g. 30'), '30');
    await waitFor(() => {
      const last = mockListParamCalls.at(-1);
      expect(last.minDaysSince).toBe(30);
      expect(last.page).toBe(1);
    }, { timeout: 1000 });
  });

  it('hides the tutor filter for a tutor', () => {
    mockCurrentUser = { data: { role: 'tutor', tutorId: 7 } };
    renderPage();
    expect(screen.queryByText('Tutor', { selector: 'label' })).not.toBeInTheDocument();
  });
});
