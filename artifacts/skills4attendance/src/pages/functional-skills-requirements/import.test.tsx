import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, fireEvent, within } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import FsRequirementImportPage from './import';

const readyJob = {
  id: 9,
  filename: 'fs-requirements.csv',
  uploadedBy: 1,
  status: 'ready',
  totalRows: 3,
  newCount: 1,
  changedCount: 0,
  unchangedCount: 0,
  warningCount: 1,
  errorCount: 0,
  resultSummary: null,
  lastError: null,
  startedImportingAt: null,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  expiresAt: '2026-01-04T00:00:00Z',
};

const newRow = {
  id: 201, jobId: 9, rowNumber: 1,
  rawData: { learnerID: 'REF-1', AIM: 'Math' },
  normalizedAim: 'math', matchedLearnerId: 10, matchedLearnerName: 'Jane Doe', matchedLearnerStatus: 'active',
  outcome: 'new', existingMaths: null, existingEnglish: null, existingStatus: null,
  proposedMaths: true, proposedEnglish: false,
  errors: [], warnings: [], importResult: null, createdAt: '2026-01-01T00:00:00Z',
};

const warningRow = {
  id: 202, jobId: 9, rowNumber: 2,
  rawData: { learnerID: 'REF-2', AIM: 'Both' },
  normalizedAim: 'both', matchedLearnerId: 11, matchedLearnerName: 'Sam Withdrawn', matchedLearnerStatus: 'withdrawn',
  outcome: 'warning', existingMaths: null, existingEnglish: null, existingStatus: null,
  proposedMaths: true, proposedEnglish: true,
  errors: [], warnings: ["Learner status is 'withdrawn' -- the requirement will still be recorded, but this does not reactivate them or add them to the active allocation population."],
  importResult: null, createdAt: '2026-01-01T00:00:00Z',
};

const errorRow = {
  id: 203, jobId: 9, rowNumber: 3,
  rawData: { learnerID: 'UNKNOWN', AIM: 'Math' },
  normalizedAim: 'math', matchedLearnerId: null, matchedLearnerName: null, matchedLearnerStatus: null,
  outcome: 'error', existingMaths: null, existingEnglish: null, existingStatus: null,
  proposedMaths: null, proposedEnglish: null,
  errors: ["learnerID 'UNKNOWN' does not match exactly one existing, non-deleted learner"],
  warnings: [], importResult: null, createdAt: '2026-01-01T00:00:00Z',
};

let mockJob: { data: any; isError: boolean; refetch: ReturnType<typeof vi.fn> };
let mockRows: { data: any; isLoading: boolean };
let uploadMutate: ReturnType<typeof vi.fn>;
let confirmMutate: ReturnType<typeof vi.fn>;
let cancelMutate: ReturnType<typeof vi.fn>;
let templateRefetch: ReturnType<typeof vi.fn>;
let toastSpy: ReturnType<typeof vi.fn>;

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastSpy }),
}));

vi.mock('@workspace/api-client-react', () => ({
  useGetFsRequirementImportTemplate: () => ({ isFetching: false, refetch: templateRefetch }),
  useUploadFsRequirementImport: () => ({ mutate: uploadMutate, isPending: false }),
  useGetFsRequirementImportJob: () => mockJob,
  useListFsRequirementImportJobRows: () => mockRows,
  useConfirmFsRequirementImportJob: () => ({ mutate: confirmMutate, isPending: false }),
  useCancelFsRequirementImportJob: () => ({ mutate: cancelMutate, isPending: false }),
  getGetFsRequirementImportTemplateQueryKey: () => ['getFsRequirementImportTemplate'],
  getGetFsRequirementImportJobQueryKey: (id: number) => ['getFsRequirementImportJob', id],
  getListFsRequirementImportJobRowsQueryKey: (id: number, params: unknown) => ['listFsRequirementImportJobRows', id, params],
}));

function renderAtLocation(searchPath = '') {
  const location = memoryLocation({ path: '/functional-skills-requirements/import', searchPath, record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/functional-skills-requirements/import" component={FsRequirementImportPage} />
    </Router>,
  );
  return location;
}

beforeEach(() => {
  mockJob = { data: undefined, isError: false, refetch: vi.fn() };
  mockRows = { data: undefined, isLoading: false };
  uploadMutate = vi.fn();
  confirmMutate = vi.fn();
  cancelMutate = vi.fn();
  templateRefetch = vi.fn().mockResolvedValue({ data: { csv: 'learnerID,AIM\n', filename: 'fs-requirement-template.csv' } });
  toastSpy = vi.fn();
});

describe('FsRequirementImportPage', () => {
  it('shows the upload step when no import job is active', () => {
    renderAtLocation();

    expect(screen.getByText('Step 1: Template')).toBeInTheDocument();
    expect(screen.getByLabelText(/select or drop a csv file/i)).toBeInTheDocument();
  });

  it('rejects a non-csv file without calling upload', () => {
    renderAtLocation();
    const input = screen.getByLabelText(/select or drop a csv file/i) as HTMLInputElement;
    const badFile = new File(['not a csv'], 'notes.txt', { type: 'text/plain' });

    fireEvent.change(input, { target: { files: [badFile] } });

    expect(uploadMutate).not.toHaveBeenCalled();
  });

  it('uploads a selected CSV file and moves into the job URL param', () => {
    uploadMutate = vi.fn((_vars, opts) => opts?.onSuccess?.(readyJob));
    const location = renderAtLocation();
    const input = screen.getByLabelText(/select or drop a csv file/i) as HTMLInputElement;
    const file = new File(['learnerID,AIM\nREF-1,Math'], 'fs-requirements.csv', { type: 'text/csv' });

    fireEvent.change(input, { target: { files: [file] } });

    expect(uploadMutate).toHaveBeenCalledWith({ data: { file } }, expect.anything());
    expect(location.history?.at(-1)).toContain('job=9');
  });

  it('shows New/Changed/Unchanged/Warning/Error summary counts and the preview rows for a ready job', () => {
    mockJob = { data: readyJob, isError: false, refetch: vi.fn() };
    mockRows = { data: { items: [newRow, warningRow], total: 2, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=9');

    // "New" appears twice -- once as the summary stat label, once as this row's outcome badge.
    expect(screen.getAllByText('New').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Changed')).toBeInTheDocument();
    expect(screen.getByText('Unchanged')).toBeInTheDocument();
    expect(screen.getByText('Warnings')).toBeInTheDocument();
    expect(screen.getByText('Errors')).toBeInTheDocument();
    expect(screen.getByText('Jane Doe')).toBeInTheDocument();
    expect(screen.getByText('REF-1')).toBeInTheDocument();
  });

  it('never offers a per-row Skip/Update resolution control -- Stage 5 has no per-row admin choice', () => {
    mockJob = { data: readyJob, isError: false, refetch: vi.fn() };
    mockRows = { data: { items: [newRow], total: 1, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=9');

    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
  });

  it('shows a warning for a non-active matched learner without blocking the outcome', () => {
    mockJob = { data: readyJob, isError: false, refetch: vi.fn() };
    mockRows = { data: { items: [warningRow], total: 1, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=9');

    expect(screen.getByText('Sam Withdrawn')).toBeInTheDocument();
    expect(screen.getByText('(withdrawn)')).toBeInTheDocument();
    expect(screen.getByText(/does not reactivate them/i)).toBeInTheDocument();
  });

  it('hard-disables Confirm Import while any row has an error, unlike the learner-import silently-skip pattern', () => {
    mockJob = { data: { ...readyJob, errorCount: 1 }, isError: false, refetch: vi.fn() };
    mockRows = { data: { items: [errorRow], total: 1, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=9');

    expect(screen.getByRole('button', { name: /confirm import/i })).toBeDisabled();
    expect(screen.getByText(/commit is blocked until they are fixed/i)).toBeInTheDocument();
  });

  it('enables Confirm Import when there are no errors, even with warnings present', () => {
    mockJob = { data: readyJob, isError: false, refetch: vi.fn() };
    mockRows = { data: { items: [newRow, warningRow], total: 2, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=9');

    expect(screen.getByRole('button', { name: /confirm import/i })).not.toBeDisabled();
  });

  it('opens a confirmation dialog before confirming the import', () => {
    mockJob = { data: readyJob, isError: false, refetch: vi.fn() };
    mockRows = { data: { items: [newRow], total: 1, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=9');

    fireEvent.click(screen.getByRole('button', { name: /confirm import/i }));

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/cannot be undone/i)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: /confirm import/i }));
    expect(confirmMutate).toHaveBeenCalledWith({ jobId: 9 }, expect.anything());
  });

  it('shows an Import complete toast and refetches the job after a successful confirm', () => {
    confirmMutate = vi.fn((_vars, opts) => opts?.onSuccess?.({}));
    mockJob = { data: readyJob, isError: false, refetch: vi.fn() };
    mockRows = { data: { items: [newRow], total: 1, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=9');

    fireEvent.click(screen.getByRole('button', { name: /confirm import/i }));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /confirm import/i }));

    expect(mockJob.refetch).toHaveBeenCalled();
    expect(toastSpy).toHaveBeenCalledWith(expect.objectContaining({ title: 'Import complete' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows an Import failed toast and refetches the job when confirm errors', () => {
    confirmMutate = vi.fn((_vars, opts) => opts?.onError?.(new Error('boom')));
    mockJob = { data: readyJob, isError: false, refetch: vi.fn() };
    mockRows = { data: { items: [newRow], total: 1, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=9');

    fireEvent.click(screen.getByRole('button', { name: /confirm import/i }));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /confirm import/i }));

    expect(mockJob.refetch).toHaveBeenCalled();
    expect(toastSpy).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Import failed', variant: 'destructive' }),
    );
  });

  it('shows the completed results step with a learners-updated count', () => {
    mockJob = {
      data: { ...readyJob, status: 'completed', resultSummary: { applied: 2 } },
      isError: false,
      refetch: vi.fn(),
    };
    renderAtLocation('job=9');

    expect(screen.getByText('Import complete')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('Learners updated')).toBeInTheDocument();
  });

  it('shows a cancelled state with a way to start over', () => {
    mockJob = { data: { ...readyJob, status: 'cancelled' }, isError: false, refetch: vi.fn() };
    renderAtLocation('job=9');

    expect(screen.getByText('Import cancelled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /start new import/i })).toBeInTheDocument();
  });

  it('shows a not-found state when the job id in the URL no longer exists', () => {
    mockJob = { data: undefined, isError: true, refetch: vi.fn() };
    renderAtLocation('job=999');

    expect(screen.getByText('Import job not found')).toBeInTheDocument();
  });
});
