import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import CohortsPage from './index';

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => ({ data: { role: 'admin', firstName: 'A', lastName: 'B' } }),
  useListCohorts: () => ({ data: [] }),
  useListCohortSummary: () => ({
    data: [
      {
        id: 1, name: 'Cohort One', programme: 'P', level: '3', tutorId: null, tutorName: null,
        deliveryDay: 'monday', sessionStartTime: '09:00:00', sessionEndTime: '16:00:00',
        startDate: '2026-01-01', endDate: null, active: true, externalSystemId: null,
        createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
        activeLearnerCount: 5, upcomingSessionCount: 2, outstandingRegisterCount: 1,
      },
    ],
    isLoading: false,
    refetch: vi.fn(),
  }),
  useListTutors: () => ({ data: [] }),
  useActivateCohort: () => ({ mutate: vi.fn() }),
  useDeactivateCohort: () => ({ mutate: vi.fn() }),
  getListCohortSummaryQueryKey: (params: unknown) => ['listCohortSummary', params],
}));

function renderAtLocation(searchPath: string) {
  const location = memoryLocation({ path: '/cohorts', searchPath, record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <CohortsPage />
    </Router>,
  );
  return location;
}

describe('CohortsPage filter state', () => {
  it('reads initial filters from the URL', () => {
    renderAtLocation('q=Cohort&active=all');

    expect(screen.getByPlaceholderText('Search cohorts...')).toHaveValue('Cohort');
    // "Active Only" switch reflects active=all -> unchecked (showing all, not just active).
    expect(screen.getByRole('switch', { name: /active only/i })).not.toBeChecked();
  });

  it('updates the URL when the search filter changes, so a later back navigation restores it', async () => {
    const user = userEvent.setup();
    const location = renderAtLocation('');

    await user.type(screen.getByPlaceholderText('Search cohorts...'), 'Cohort One');

    expect(location.history?.at(-1)).toContain('q=Cohort');
  });

  it('renders cohort summary stats and links to the cohort edit form, not a sessions page', () => {
    // This admin list's job is cohort administration -- clicking through must
    // land on the edit form (/cohorts/:id), never on attendance/session
    // navigation, which lives under /attendance now.
    renderAtLocation('');

    expect(screen.getByText('Cohort One')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument(); // activeLearnerCount
    const manageLink = screen.getByText('Manage Cohort').closest('a');
    expect(manageLink).toHaveAttribute('href', '/cohorts/1');
  });
});
