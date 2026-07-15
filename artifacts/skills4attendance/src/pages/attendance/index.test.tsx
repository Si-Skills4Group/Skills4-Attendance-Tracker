import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { Router } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import AttendancePage from './index';

const cohortA = {
  id: 1, name: 'Cohort A', programme: 'Data Analyst', level: '3', tutorId: 10, tutorName: 'Tam Tutor',
  deliveryDay: 'monday', sessionStartTime: '09:00:00', sessionEndTime: '16:00:00',
  startDate: '2026-01-01', endDate: null, active: true, externalSystemId: null,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  activeLearnerCount: 5, upcomingSessionCount: 2, outstandingRegisterCount: 1,
};
const cohortB = {
  ...cohortA, id: 2, name: 'Cohort B', activeLearnerCount: 3, upcomingSessionCount: 0, outstandingRegisterCount: 0,
};

let mockSummary: { data: any[]; isLoading: boolean; isError: boolean; refetch: () => void };

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => ({ data: { role: 'admin', firstName: 'A', lastName: 'B' } }),
  useListCohortSummary: () => mockSummary,
  useListTutors: () => ({ data: [] }),
  getListCohortSummaryQueryKey: (params: unknown) => ['listCohortSummary', params],
  getListTutorsQueryKey: (params: unknown) => ['listTutors', params],
}));

function renderAtLocation(searchPath = '') {
  const location = memoryLocation({ path: '/attendance', searchPath, record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <AttendancePage />
    </Router>,
  );
  return location;
}

describe('AttendancePage cohort overview', () => {
  it('shows one card per cohort, each appearing exactly once', () => {
    mockSummary = { data: [cohortA, cohortB], isLoading: false, isError: false, refetch: vi.fn() };
    renderAtLocation();

    expect(screen.getAllByText('Cohort A')).toHaveLength(1);
    expect(screen.getAllByText('Cohort B')).toHaveLength(1);
  });

  it('links a cohort card to its attendance session page, not the cohort edit form', () => {
    mockSummary = { data: [cohortA], isLoading: false, isError: false, refetch: vi.fn() };
    renderAtLocation();

    const link = screen.getByRole('link', { name: /Cohort A/ });
    const href = link.getAttribute('href') ?? '';
    expect(href).toMatch(/^\/attendance\/cohorts\/1(\?|$)/);
    // The cohort *edit* form lives at /cohorts/:id -- confirm this link is
    // not that (a bare /cohorts/1 with no /attendance prefix).
    expect(href).not.toMatch(/^\/cohorts\/1(\?|$)/);
  });

  it('is keyboard accessible -- the cohort card is a real, focusable link', () => {
    mockSummary = { data: [cohortA], isLoading: false, isError: false, refetch: vi.fn() };
    renderAtLocation();

    const link = screen.getByRole('link', { name: /Cohort A/ });
    expect(link.tagName).toBe('A');
    link.focus();
    expect(link).toHaveFocus();
  });

  it('shows a loading state', () => {
    mockSummary = { data: [], isLoading: true, isError: false, refetch: vi.fn() };
    renderAtLocation();

    expect(screen.queryByText('No cohorts found')).not.toBeInTheDocument();
    expect(document.querySelector('.animate-spin')).toBeInTheDocument();
  });

  it('shows an empty state when there are no cohorts', () => {
    mockSummary = { data: [], isLoading: false, isError: false, refetch: vi.fn() };
    renderAtLocation();

    expect(screen.getByText('No cohorts found')).toBeInTheDocument();
  });

  it('shows an error state when the cohort list fails to load', () => {
    mockSummary = { data: [], isLoading: false, isError: true, refetch: vi.fn() };
    renderAtLocation();

    expect(screen.getByText("Couldn't load cohorts")).toBeInTheDocument();
  });
});
