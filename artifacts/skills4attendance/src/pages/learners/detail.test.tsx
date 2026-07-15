import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithQueryClient } from '@/test/test-utils';
import LearnerDetailPage from './detail';

vi.mock('wouter', () => ({
  useParams: () => ({ id: '42' }),
  useLocation: () => ['/learners/42', vi.fn()],
  Link: ({ href, children }: any) => <a href={href}>{children}</a>,
}));

const learner = {
  id: 42, learnerRef: 'L-42', uln: null, firstName: 'Ada', lastName: 'Lovelace',
  email: null, employer: null, programme: 'Data Analyst', level: '4',
  startDate: '2026-01-01', plannedEndDate: null, actualEndDate: null, withdrawalDate: null,
  status: 'active', tutorId: null, tutorName: null, cohortId: null, cohortName: null,
  externalSystemId: null, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
};

vi.mock('@workspace/api-client-react', () => ({
  useGetLearner: () => ({ data: learner, isLoading: false }),
  useGetLearnerAllocationHistory: () => ({ data: [] }),
  useCreateLearner: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateLearner: () => ({ mutate: vi.fn(), isPending: false }),
  useChangeLearnerStatus: () => ({ mutate: vi.fn(), isPending: false }),
  getGetLearnerQueryKey: (id: number) => ['getLearner', id],
  getGetLearnerAllocationHistoryQueryKey: (id: number) => ['getLearnerAllocationHistory', id],
}));

describe('LearnerDetailPage for an existing learner', () => {
  it('renders the read-only status section without crashing', () => {
    // Regression test: the read-only "Status" block previously used
    // <FormLabel> outside a <FormField>/<FormItem> wrapper, which throws
    // ("useFormField should be used within <FormField>") and blanked the
    // whole page whenever an existing learner was opened.
    renderWithQueryClient(<LearnerDetailPage />);

    expect(screen.getByRole('heading', { name: 'Ada Lovelace' })).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText(/Use the "Change Status" action/)).toBeInTheDocument();
  });
});
