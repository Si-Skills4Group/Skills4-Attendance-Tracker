import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQueryClient } from '@/test/test-utils';
import { Toaster } from '@/components/ui/toaster';
import CohortDetailPage from './detail';

let mockParams: Record<string, string> = {};
const mockSetLocation = vi.fn();

vi.mock('wouter', () => ({
  useParams: () => mockParams,
  useLocation: () => ['/cohorts/5', mockSetLocation],
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

const cohort = {
  id: 5, name: 'Cohort A', programme: 'Data Analyst', level: '4', tutorId: null,
  deliveryDay: 'monday', sessionStartTime: '09:00:00', sessionEndTime: '16:00:00',
  startDate: '2026-01-01', endDate: null, active: true, externalSystemId: null,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
};

let mockCurrentUser: { data: any };
let mockCohort: { data: any; isLoading: boolean };
const mockDeleteMutate = vi.fn();
const mockUpdateMutate = vi.fn();

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useGetCohort: () => mockCohort,
  useGetCohortLearners: () => ({ data: [] }),
  useListTutors: () => ({ data: tutors }),
  useCreateCohort: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateCohort: () => ({ mutate: mockUpdateMutate, isPending: false }),
  useActivateCohort: () => ({ mutate: vi.fn() }),
  useDeactivateCohort: () => ({ mutate: vi.fn() }),
  useDeleteCohort: () => ({ mutate: mockDeleteMutate, isPending: false }),
  getGetCohortQueryKey: (id: number) => ['getCohort', id],
  getGetCohortLearnersQueryKey: (id: number) => ['getCohortLearners', id],
  getListTutorsQueryKey: (params: unknown) => ['listTutors', params],
}));

describe('CohortDetailPage primary tutor field', () => {
  beforeEach(() => {
    mockParams = {};
    mockCurrentUser = { data: { role: 'admin' } };
    mockCohort = { data: undefined, isLoading: false };
  });

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

describe('CohortDetailPage delete action', () => {
  beforeEach(() => {
    mockParams = { id: '5' };
    mockCurrentUser = { data: { role: 'admin' } };
    mockCohort = { data: cohort, isLoading: false };
    mockDeleteMutate.mockReset();
    mockSetLocation.mockReset();
  });

  it('offers a Delete Cohort action for admins', () => {
    renderWithQueryClient(<CohortDetailPage />);
    expect(screen.getByRole('button', { name: /delete cohort/i })).toBeInTheDocument();
  });

  it('hides the Delete Cohort action for tutors', () => {
    mockCurrentUser = { data: { role: 'tutor' } };
    renderWithQueryClient(<CohortDetailPage />);
    expect(screen.queryByRole('button', { name: /delete cohort/i })).not.toBeInTheDocument();
  });

  it('requires a reason before the delete can be confirmed', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<CohortDetailPage />);

    await user.click(screen.getByRole('button', { name: /delete cohort/i }));
    const confirmButton = screen.getByRole('button', { name: /^delete cohort$/i });
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByLabelText('Reason'), 'No longer needed');
    expect(confirmButton).toBeEnabled();

    await user.click(confirmButton);
    expect(mockDeleteMutate).toHaveBeenCalledWith(
      { id: 5, data: { reason: 'No longer needed' } },
      expect.anything(),
    );
  });

  it('navigates back to the cohort list after a successful delete', async () => {
    mockDeleteMutate.mockImplementation((_payload, { onSuccess }: any) => onSuccess());
    const user = userEvent.setup();
    renderWithQueryClient(<CohortDetailPage />);

    await user.click(screen.getByRole('button', { name: /delete cohort/i }));
    await user.type(screen.getByLabelText('Reason'), 'No longer needed');
    await user.click(screen.getByRole('button', { name: /^delete cohort$/i }));

    await waitFor(() => expect(mockSetLocation).toHaveBeenCalledWith('/cohorts'));
  });

  it('shows the active-learner and session counts from a cohort_not_empty error', async () => {
    mockDeleteMutate.mockImplementation((_payload, { onError }: any) => {
      onError({ status: 409, data: { error: 'cohort_not_empty', activeLearnerCount: 3, sessionCount: 2 } });
    });
    const user = userEvent.setup();
    renderWithQueryClient(<><CohortDetailPage /><Toaster /></>);

    await user.click(screen.getByRole('button', { name: /delete cohort/i }));
    await user.type(screen.getByLabelText('Reason'), 'No longer needed');
    await user.click(screen.getByRole('button', { name: /^delete cohort$/i }));

    await waitFor(() => expect(screen.getByText(/3 active learner/i)).toBeInTheDocument());
    expect(screen.getByText(/2 session/i)).toBeInTheDocument();
  });
});

describe('CohortDetailPage rename action for tutors', () => {
  beforeEach(() => {
    mockParams = { id: '5' };
    mockCurrentUser = { data: { role: 'tutor', tutorId: 1 } };
    mockCohort = { data: cohort, isLoading: false };
    mockUpdateMutate.mockReset();
    mockSetLocation.mockReset();
  });

  it('lets a tutor edit the cohort name field', () => {
    renderWithQueryClient(<CohortDetailPage />);
    expect(screen.getByLabelText('Cohort Name')).toBeEnabled();
  });

  it('keeps every other field read-only for a tutor', () => {
    renderWithQueryClient(<CohortDetailPage />);
    expect(screen.getByLabelText('Programme')).toBeDisabled();
    expect(screen.getByLabelText('Level')).toBeDisabled();
    expect(screen.getByRole('combobox', { name: /primary tutor/i })).toBeDisabled();
    expect(screen.getByRole('combobox', { name: /cohort type/i })).toBeDisabled();
  });

  it('offers a Save Changes button, but not Delete Cohort, for a tutor', () => {
    renderWithQueryClient(<CohortDetailPage />);
    expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /delete cohort/i })).not.toBeInTheDocument();
  });

  it('submits only the name field when a tutor saves changes', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<CohortDetailPage />);

    await user.clear(screen.getByLabelText('Cohort Name'));
    await user.type(screen.getByLabelText('Cohort Name'), 'Cohort A (renamed)');
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(mockUpdateMutate).toHaveBeenCalledWith(
        { id: 5, data: { name: 'Cohort A (renamed)' } },
        expect.anything(),
      );
    });
  });
});

describe('CohortDetailPage cohort type field', () => {
  beforeEach(() => {
    mockParams = { id: '5' };
    mockCurrentUser = { data: { role: 'admin' } };
    mockCohort = { data: cohort, isLoading: false };
    mockUpdateMutate.mockReset();
  });

  it('defaults to Standard for an existing cohort with no membershipType set', () => {
    renderWithQueryClient(<CohortDetailPage />);
    expect(screen.getByRole('combobox', { name: /cohort type/i })).toHaveTextContent('Standard');
  });

  it('shows Functional Skills for an existing secondary cohort, without ever opening the dropdown', () => {
    // Regression test: setting the Select's value programmatically after
    // mount (an existing cohort's data arriving async) used to trigger a
    // spurious change event from Radix's hidden native <select> mirror,
    // clobbering the value back to "" a render later. Guards against that
    // reappearing -- see the membershipTypeRemountKey comment in detail.tsx.
    mockCohort = { data: { ...cohort, membershipType: 'secondary' }, isLoading: false };
    renderWithQueryClient(<CohortDetailPage />);
    expect(screen.getByRole('combobox', { name: /cohort type/i })).toHaveTextContent('Functional Skills');
  });

  it('saves the unchanged membershipType alongside other fields when an admin saves changes', async () => {
    // Regression coverage for the field actually being wired into the form
    // (as opposed to the dropdown-interaction itself, which Radix's Select
    // does not expose reliably under jsdom): the submitted payload must
    // carry membershipType like every other field, not silently drop it.
    const user = userEvent.setup();
    renderWithQueryClient(<CohortDetailPage />);

    await user.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(mockUpdateMutate).toHaveBeenCalledWith(
        expect.objectContaining({ id: 5, data: expect.objectContaining({ membershipType: 'primary' }) }),
        expect.anything(),
      );
    });
  });

  it('shows the subject for an existing secondary cohort with a subject already set', () => {
    mockCohort = { data: { ...cohort, membershipType: 'secondary', subject: 'math' }, isLoading: false };
    renderWithQueryClient(<CohortDetailPage />);
    expect(screen.getByRole('combobox', { name: /subject/i })).toHaveTextContent('Math');
  });
});
