import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQueryClient } from '@/test/test-utils';
import AllocationPage from './allocation';

const tutors = [
  { id: 10, firstName: 'Tam', lastName: 'Tutor' },
  { id: 20, firstName: 'Cara', lastName: 'Cover' },
];

const cohorts = [{ id: 5, name: 'Cohort A' }];

function makeLearner(overrides: Record<string, any> = {}) {
  return {
    id: 1, learnerRef: 'L-1', firstName: 'Ada', lastName: 'Lovelace',
    programme: 'Data Analyst', level: '4', tutorId: null, tutorName: null,
    cohortId: null, cohortName: null, status: 'active',
    ...overrides,
  };
}

let mockListLearnersResult: { data: any; isLoading: boolean; refetch: ReturnType<typeof vi.fn> };
const mockListLearners = vi.fn();
let mockScheduled: { data: any; isLoading: boolean; refetch: ReturnType<typeof vi.fn> };
const mockAllocateMutate = vi.fn();
const mockCancelScheduledMutate = vi.fn();

vi.mock('@workspace/api-client-react', () => ({
  useListLearners: (params: unknown) => { mockListLearners(params); return mockListLearnersResult; },
  useListTutors: () => ({ data: tutors }),
  useListCohorts: () => ({ data: cohorts }),
  useAllocateLearners: () => ({ mutate: mockAllocateMutate, isPending: false }),
  useListScheduledAllocations: () => mockScheduled,
  useCancelScheduledAllocation: () => ({ mutate: mockCancelScheduledMutate, isPending: false }),
}));

function renderPage() {
  return renderWithQueryClient(<AllocationPage />);
}

describe('AllocationPage', () => {
  beforeEach(() => {
    mockListLearners.mockClear();
    mockAllocateMutate.mockReset();
    mockCancelScheduledMutate.mockReset();
    mockListLearnersResult = {
      data: { items: [makeLearner()], total: 1, page: 1, pageSize: 20 },
      isLoading: false,
      refetch: vi.fn(),
    };
    mockScheduled = { data: [], isLoading: false, refetch: vi.fn() };
  });

  it('searches and filters learners, including the Unallocated filter', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByPlaceholderText(/search learners/i), 'Ada');
    await waitFor(() => {
      expect(mockListLearners).toHaveBeenCalledWith(expect.objectContaining({ search: 'Ada' }));
    });

    await user.click(screen.getByRole('combobox', { name: /filter by tutor/i }));
    await user.click(await screen.findByRole('option', { name: 'Unallocated' }));

    await waitFor(() => {
      expect(mockListLearners).toHaveBeenCalledWith(
        expect.objectContaining({ unallocated: true, tutorId: undefined }),
      );
    });
  });

  it('selecting a specific tutor filter sends tutorId, not unallocated', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('combobox', { name: /filter by tutor/i }));
    await user.click(await screen.findByText('Tam Tutor'));

    await waitFor(() => {
      expect(mockListLearners).toHaveBeenCalledWith(
        expect.objectContaining({ tutorId: 10, unallocated: undefined }),
      );
    });
  });

  it('keeps a learner selected after the filters change to a different result set', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('checkbox', { name: /select ada lovelace/i }));
    expect(screen.getByRole('checkbox', { name: /select ada lovelace/i })).toBeChecked();

    // Changing the search re-queries (a different result set in real usage),
    // but the selection itself is independent local state and must survive.
    await user.type(screen.getByPlaceholderText(/search learners/i), 'x');
    await waitFor(() => expect(mockListLearners).toHaveBeenCalled());

    expect(screen.getByRole('checkbox', { name: /select ada lovelace/i })).toBeChecked();
  });

  it('shows "Select at least one learner" until a learner is selected, then "Choose a new tutor or cohort" until a target is picked', async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByText('Select at least one learner.')).toBeInTheDocument();
    const applyButton = screen.getByRole('button', { name: /apply allocation/i });
    expect(applyButton).toBeDisabled();

    await user.click(screen.getByRole('checkbox', { name: /select ada lovelace/i }));
    expect(screen.getByText('Choose a new tutor or cohort.')).toBeInTheDocument();
    expect(applyButton).toBeDisabled();

    await user.click(screen.getByRole('combobox', { name: /target tutor/i }));
    await user.click(await screen.findByText('Cara Cover'));
    expect(screen.queryByText('Choose a new tutor or cohort.')).not.toBeInTheDocument();
    expect(applyButton).toBeEnabled();
  });

  it('executes a transfer with the selected learner ids and chosen target', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('checkbox', { name: /select ada lovelace/i }));
    await user.click(screen.getByRole('combobox', { name: /target tutor/i }));
    await user.click(await screen.findByText('Cara Cover'));
    await user.click(screen.getByRole('button', { name: /apply allocation/i }));

    expect(mockAllocateMutate).toHaveBeenCalledWith(
      { data: expect.objectContaining({ learnerIds: [1], tutorId: 20 }) },
      expect.anything(),
    );
  });

  it('clears the selection after a successful allocation', async () => {
    mockAllocateMutate.mockImplementation((_payload, { onSuccess }: any) => onSuccess({ updated: 1, scheduled: 0 }));
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('checkbox', { name: /select ada lovelace/i }));
    await user.click(screen.getByRole('combobox', { name: /target tutor/i }));
    await user.click(await screen.findByText('Cara Cover'));
    await user.click(screen.getByRole('button', { name: /apply allocation/i }));

    await waitFor(() => expect(screen.getByText('Select at least one learner.')).toBeInTheDocument());
  });

  it('renders Pending Transfers and cancels one', async () => {
    mockListLearnersResult = { data: { items: [], total: 0, page: 1, pageSize: 20 }, isLoading: false, refetch: vi.fn() };
    mockScheduled = {
      data: [{ id: 7, learnerName: 'Ada Lovelace', newTutorName: 'Cara Cover', newCohortName: null, effectiveDate: '2026-09-01' }],
      isLoading: false,
      refetch: vi.fn(),
    };
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByText('Pending Transfers')).toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();

    await user.click(screen.getByTitle(/cancel scheduled transfer/i));
    expect(mockCancelScheduledMutate).toHaveBeenCalledWith({ id: 7 }, expect.anything());
  });
});
