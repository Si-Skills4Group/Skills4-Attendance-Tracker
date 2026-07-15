import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQueryClient } from '@/test/test-utils';
import CohortDetailPage from './detail';

vi.mock('wouter', () => ({
  useParams: () => ({}),
  useLocation: () => ['/cohorts/new', vi.fn()],
  Link: ({ href, children }: any) => <a href={href}>{children}</a>,
}));

const tutors = Array.from({ length: 30 }, (_, i) => ({
  id: i + 1,
  firstName: `Tutor${i}`,
  lastName: `Surname${i}`,
}));
// A tutor that would be well past whatever a non-scrolling, non-searchable
// list could show -- the regression this test guards against.
tutors.push({ id: 999, firstName: "Zack", lastName: "Ziegler" });

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => ({ data: { role: 'admin' } }),
  useGetCohort: () => ({ data: undefined, isLoading: false }),
  useGetCohortLearners: () => ({ data: [] }),
  useListTutors: () => ({ data: tutors }),
  useCreateCohort: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateCohort: () => ({ mutate: vi.fn(), isPending: false }),
  useActivateCohort: () => ({ mutate: vi.fn() }),
  useDeactivateCohort: () => ({ mutate: vi.fn() }),
  getGetCohortQueryKey: (id: number) => ['getCohort', id],
  getGetCohortLearnersQueryKey: (id: number) => ['getCohortLearners', id],
  getListTutorsQueryKey: (params: unknown) => ['listTutors', params],
}));

describe('CohortDetailPage primary tutor field', () => {
  it('lets a tutor far down a long list be found by typing, instead of only scrolling a fixed-height list', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<CohortDetailPage />);

    await user.click(screen.getByRole('combobox', { name: /primary tutor/i }));
    await user.type(screen.getByPlaceholderText('Search tutors...'), 'Ziegler');

    await waitFor(() => {
      expect(screen.getByText('Zack Ziegler')).toBeInTheDocument();
      expect(screen.queryByText('Tutor0 Surname0')).not.toBeInTheDocument();
    });

    await user.click(screen.getByText('Zack Ziegler'));
    expect(screen.getByRole('combobox', { name: /primary tutor/i })).toHaveTextContent('Zack Ziegler');
  });

  it('defaults to Unassigned and offers it as an explicit option', async () => {
    renderWithQueryClient(<CohortDetailPage />);
    expect(screen.getByRole('combobox', { name: /primary tutor/i })).toHaveTextContent('Unassigned');
  });
});
