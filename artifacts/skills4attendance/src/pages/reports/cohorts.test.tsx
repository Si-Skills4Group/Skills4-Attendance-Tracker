import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import CohortReportPage from './cohorts';

function makeMetrics(overrides: Record<string, any> = {}) {
  return {
    periodStart: '2026-07-01', periodEnd: '2026-07-31',
    expectedMinutes: 420, attendedMinutes: 420,
    authorisedAbsenceMinutes: 0, authorisedAbsenceSessions: 0,
    unauthorisedAbsenceMinutes: 0, unauthorisedAbsenceSessions: 0,
    lateMinutes: 0, lateSessionCount: 0, averageMinutesLate: null,
    missingRecordCount: 0, completedRegisterRowCount: 2,
    attendancePercentage: 100.0, attendanceDataCompleteness: 100.0,
    insufficientData: false, calculatedAt: '2026-07-17T10:00:00Z',
    ...overrides,
  };
}

let mockCohorts: any;
let mockCohortReport: any;
let exportCohortReportMock: ReturnType<typeof vi.fn>;

vi.mock('@workspace/api-client-react', () => ({
  useListCohorts: () => mockCohorts,
  useGetCohortReportV2: () => mockCohortReport,
  getGetCohortReportV2QueryKey: (id: number, p: unknown) => ['getCohortReportV2', id, p],
  exportCohortReport: (...args: unknown[]) => exportCohortReportMock(...args),
}));

vi.mock('@/lib/csv-download', () => ({ downloadCsv: vi.fn() }));

function renderPage() {
  const location = memoryLocation({ path: '/reports/cohorts', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/cohorts" component={CohortReportPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockCohorts = { data: [{ id: 2, name: 'Cohort A' }] };
  mockCohortReport = { data: undefined, isLoading: false, isError: false };
  exportCohortReportMock = vi.fn().mockResolvedValue('learnerName\nAda Lovelace\n');
});

describe('CohortReportPage', () => {
  it('shows nothing until a cohort is picked', () => {
    renderPage();
    expect(screen.queryByText('Learner Breakdown')).not.toBeInTheDocument();
  });

  it('shows the per-learner breakdown once a cohort is selected, summing to the cohort total', async () => {
    mockCohortReport = {
      data: {
        cohort: { id: 2, name: 'Cohort A', programme: 'Pharmacy', level: '3' },
        activeLearnerCount: 2,
        metrics: makeMetrics({ attendedMinutes: 700, expectedMinutes: 840 }),
        registerCompletion: { periodStart: '2026-07-01', periodEnd: '2026-07-31', notStarted: 0, inProgress: 0, completed: 2, locked: 0, outstanding: 0, completionPercentage: 100 },
        learnerBreakdown: {
          items: [
            { learnerId: 1, learnerName: 'Ada Lovelace', learnerRef: 'L-001', metrics: makeMetrics({ attendedMinutes: 420, expectedMinutes: 420 }) },
            { learnerId: 2, learnerName: 'Bob Smith', learnerRef: 'L-002', metrics: makeMetrics({ attendedMinutes: 280, expectedMinutes: 420, attendancePercentage: 66.7 }) },
          ],
          total: 2, page: 1, pageSize: 20,
        },
      },
      isLoading: false, isError: false,
    };
    renderPage();

    await userEvent.click(screen.getByRole('combobox'));
    await userEvent.click(await screen.findByText('Cohort A'));

    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('Bob Smith')).toBeInTheDocument();
  });

  it('exports the cohort breakdown as CSV', async () => {
    mockCohortReport = {
      data: {
        cohort: { id: 2, name: 'Cohort A', programme: 'Pharmacy', level: '3' },
        activeLearnerCount: 1,
        metrics: makeMetrics(),
        registerCompletion: { periodStart: '2026-07-01', periodEnd: '2026-07-31', notStarted: 0, inProgress: 0, completed: 1, locked: 0, outstanding: 0, completionPercentage: 100 },
        learnerBreakdown: { items: [], total: 0, page: 1, pageSize: 20 },
      },
      isLoading: false, isError: false,
    };
    renderPage();
    await userEvent.click(screen.getByRole('combobox'));
    await userEvent.click(await screen.findByText('Cohort A'));

    await userEvent.click(await screen.findByRole('button', { name: /export csv/i }));
    await waitFor(() => expect(exportCohortReportMock).toHaveBeenCalledWith(2, expect.any(Object)));
  });
});
