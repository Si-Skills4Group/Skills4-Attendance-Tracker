import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQueryClient } from '@/test/test-utils';
import LearnerDetailPage from './detail';

const mockSetLocation = vi.fn();

vi.mock('wouter', () => ({
  useParams: () => ({ id: '42' }),
  useLocation: () => ['/learners/42', mockSetLocation],
  Link: ({ href, children }: any) => <a href={href}>{children}</a>,
}));

const learner = {
  id: 42, learnerRef: 'L-42', uln: null, firstName: 'Ada', lastName: 'Lovelace',
  email: null, employer: null, programme: 'Data Analyst', level: '4',
  startDate: '2026-01-01', plannedEndDate: null, actualEndDate: null, withdrawalDate: null,
  status: 'active', tutorId: null, tutorName: null, cohortId: null, cohortName: null,
  externalSystemId: null, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
};

let mockCurrentUser: { data: any };
const mockDeleteMutate = vi.fn();

vi.mock('@workspace/api-client-react', () => ({
  useGetLearner: () => ({ data: learner, isLoading: false }),
  useGetLearnerAllocationHistory: () => ({ data: [] }),
  useCreateLearner: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateLearner: () => ({ mutate: vi.fn(), isPending: false }),
  useChangeLearnerStatus: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteLearner: () => ({ mutate: mockDeleteMutate, isPending: false }),
  useGetCurrentUser: () => mockCurrentUser,
  getGetLearnerQueryKey: (id: number) => ['getLearner', id],
  getGetLearnerAllocationHistoryQueryKey: (id: number) => ['getLearnerAllocationHistory', id],
}));

describe('LearnerDetailPage for an existing learner', () => {
  beforeEach(() => {
    mockCurrentUser = { data: { id: 1, role: 'admin' } };
    mockDeleteMutate.mockReset();
    mockSetLocation.mockReset();
  });

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

  it('offers a Delete Learner action for admins', () => {
    renderWithQueryClient(<LearnerDetailPage />);
    expect(screen.getAllByRole('button', { name: /delete learner/i }).length).toBeGreaterThan(0);
  });

  it('hides the Delete Learner action for tutors', () => {
    mockCurrentUser = { data: { id: 2, role: 'tutor' } };
    renderWithQueryClient(<LearnerDetailPage />);
    expect(screen.queryByRole('button', { name: /delete learner/i })).not.toBeInTheDocument();
  });

  it('requires a reason before the delete can be confirmed', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<LearnerDetailPage />);

    await user.click(screen.getAllByRole('button', { name: /delete learner/i })[0]);
    const confirmButton = screen.getByRole('button', { name: /^delete learner$/i });
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByLabelText('Reason'), 'Duplicate record');
    expect(confirmButton).toBeEnabled();

    await user.click(confirmButton);
    expect(mockDeleteMutate).toHaveBeenCalledWith(
      { id: 42, data: { reason: 'Duplicate record' } },
      expect.anything(),
    );
  });

  it('navigates back to the learner list after a successful delete', async () => {
    mockDeleteMutate.mockImplementation((_payload, { onSuccess }: any) => onSuccess());
    const user = userEvent.setup();
    renderWithQueryClient(<LearnerDetailPage />);

    await user.click(screen.getAllByRole('button', { name: /delete learner/i })[0]);
    await user.type(screen.getByLabelText('Reason'), 'Duplicate record');
    await user.click(screen.getByRole('button', { name: /^delete learner$/i }));

    await waitFor(() => expect(mockSetLocation).toHaveBeenCalledWith('/learners'));
  });
});
