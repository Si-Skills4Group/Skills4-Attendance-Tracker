import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQueryClient } from '@/test/test-utils';
import TutorDetailPage from './detail';

vi.mock('wouter', () => ({
  useParams: () => ({}),
  useLocation: () => ['/tutors/new', vi.fn()],
  Link: ({ href, children }: any) => <a href={href}>{children}</a>,
}));

const mockCreateMutate = vi.fn();

vi.mock('@workspace/api-client-react', () => ({
  useGetTutor: () => ({ data: undefined, isLoading: false }),
  useListCohorts: () => ({ data: [] }),
  useCreateTutor: () => ({ mutate: mockCreateMutate, isPending: false }),
  useUpdateTutor: () => ({ mutate: vi.fn(), isPending: false }),
  useActivateTutor: () => ({ mutate: vi.fn() }),
  useDeactivateTutor: () => ({ mutate: vi.fn() }),
  getGetTutorQueryKey: (id: number) => ['getTutor', id],
  getListCohortsQueryKey: (params: unknown) => ['listCohorts', params],
}));

async function fillRequiredFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('First Name'), 'Ada');
  await user.type(screen.getByLabelText('Last Name'), 'Lovelace');
  await user.type(screen.getByLabelText('Email Address'), 'ada@example.com');
}

describe('TutorDetailPage create form', () => {
  beforeEach(() => {
    mockCreateMutate.mockReset();
  });

  it('submits successfully with the employee reference left blank', async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<TutorDetailPage />);

    await fillRequiredFields(user);
    expect(screen.getByLabelText('Employee Reference (Optional)')).toHaveValue('');

    await user.click(screen.getByRole('button', { name: 'Create Tutor' }));

    await waitFor(() => expect(mockCreateMutate).toHaveBeenCalledTimes(1));
    const [payload] = mockCreateMutate.mock.calls[0];
    expect(payload.data.employeeRef).toBeUndefined();
    expect(payload.data.firstName).toBe('Ada');
  });

  it('keeps entered values in the form after a failed save', async () => {
    mockCreateMutate.mockImplementation((_payload, { onError }: any) => {
      onError({ data: { error: 'Email already in use' } });
    });
    const user = userEvent.setup();
    renderWithQueryClient(<TutorDetailPage />);

    await fillRequiredFields(user);
    await user.type(screen.getByLabelText('Employee Reference (Optional)'), 'EMP-42');
    await user.click(screen.getByRole('button', { name: 'Create Tutor' }));

    await waitFor(() => expect(mockCreateMutate).toHaveBeenCalledTimes(1));

    expect(screen.getByLabelText('First Name')).toHaveValue('Ada');
    expect(screen.getByLabelText('Last Name')).toHaveValue('Lovelace');
    expect(screen.getByLabelText('Email Address')).toHaveValue('ada@example.com');
    expect(screen.getByLabelText('Employee Reference (Optional)')).toHaveValue('EMP-42');
  });
});
