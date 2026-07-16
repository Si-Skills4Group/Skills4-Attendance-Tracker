import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, fireEvent, within } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import TutorImportPage from './import';

const readyJob = {
  id: 7,
  filename: 'tutors.csv',
  uploadedBy: 1,
  status: 'ready',
  totalRows: 2,
  newCount: 1,
  exactExistingCount: 1,
  probableDuplicateCount: 0,
  identifierConflictCount: 0,
  invalidCount: 0,
  resultSummary: null,
  lastError: null,
  startedImportingAt: null,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  expiresAt: '2026-01-04T00:00:00Z',
};

const newRow = {
  id: 101, jobId: 7, rowNumber: 1,
  rawData: { first_name: 'Grace', last_name: 'Hopper', email: 'grace@example.com', employee_ref: '', phone: '', active: '', external_system_id: '' },
  classification: 'new', proposedAction: 'create', resolution: null, resolvedBy: null, resolvedAt: null,
  matchDetails: {}, matchedTutorId: null, matchedTutorName: null,
  errors: [], warnings: [], importResult: null, importError: null, createdAt: '2026-01-01T00:00:00Z',
};

const duplicateRow = {
  id: 102, jobId: 7, rowNumber: 2,
  rawData: { first_name: 'Ada', last_name: 'Lovelace', email: 'ada@example.com', employee_ref: '', phone: '', active: '', external_system_id: '' },
  classification: 'exact_existing', proposedAction: 'skip', resolution: null, resolvedBy: null, resolvedAt: null,
  matchDetails: { matchedOn: 'email' }, matchedTutorId: 42, matchedTutorName: 'Ada Lovelace',
  errors: [], warnings: [], importResult: null, importError: null, createdAt: '2026-01-01T00:00:00Z',
};

let mockJob: { data: any; isError: boolean };
let mockRows: { data: any; isLoading: boolean };
let uploadMutate: ReturnType<typeof vi.fn>;
let resolveMutate: ReturnType<typeof vi.fn>;
let confirmMutate: ReturnType<typeof vi.fn>;
let cancelMutate: ReturnType<typeof vi.fn>;
let templateRefetch: ReturnType<typeof vi.fn>;
let errorsRefetch: ReturnType<typeof vi.fn>;

vi.mock('@workspace/api-client-react', () => ({
  useGetTutorImportTemplate: () => ({ isFetching: false, refetch: templateRefetch }),
  useUploadTutorImport: () => ({ mutate: uploadMutate, isPending: false }),
  useGetTutorImportJob: () => mockJob,
  useListTutorImportJobRows: () => mockRows,
  useResolveTutorImportJobRow: () => ({ mutate: resolveMutate, isPending: false }),
  useConfirmTutorImportJob: () => ({ mutate: confirmMutate, isPending: false }),
  useCancelTutorImportJob: () => ({ mutate: cancelMutate, isPending: false }),
  useDownloadTutorImportErrors: () => ({ isFetching: false, refetch: errorsRefetch }),
  getGetTutorImportTemplateQueryKey: () => ['getTutorImportTemplate'],
  getGetTutorImportJobQueryKey: (id: number) => ['getTutorImportJob', id],
  getListTutorImportJobRowsQueryKey: (id: number, params: unknown) => ['listTutorImportJobRows', id, params],
  getDownloadTutorImportErrorsQueryKey: (id: number) => ['downloadTutorImportErrors', id],
}));

function renderAtLocation(searchPath = '') {
  const location = memoryLocation({ path: '/tutors/import', searchPath, record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/tutors/import" component={TutorImportPage} />
    </Router>,
  );
  return location;
}

beforeEach(() => {
  mockJob = { data: undefined, isError: false };
  mockRows = { data: undefined, isLoading: false };
  uploadMutate = vi.fn();
  resolveMutate = vi.fn();
  confirmMutate = vi.fn();
  cancelMutate = vi.fn();
  templateRefetch = vi.fn().mockResolvedValue({ data: { csv: 'a,b\n', filename: 'template.csv' } });
  errorsRefetch = vi.fn().mockResolvedValue({ data: { csv: 'a,b\n', filename: 'errors.csv' } });
});

describe('TutorImportPage', () => {
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
    const file = new File(['first_name\nGrace'], 'tutors.csv', { type: 'text/csv' });

    fireEvent.change(input, { target: { files: [file] } });

    expect(uploadMutate).toHaveBeenCalledWith({ data: { file } }, expect.anything());
    expect(location.history?.at(-1)).toContain('job=7');
  });

  it('shows classification counts and rows for a ready job', () => {
    mockJob = { data: readyJob, isError: false };
    mockRows = { data: { items: [newRow, duplicateRow], total: 2, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=7');

    expect(screen.getAllByText('New').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('Existing match').length).toBeGreaterThanOrEqual(2);
    // "Ada Lovelace" legitimately renders twice for this row: once as the
    // CSV row's own name, once as the existing tutor it matched against.
    expect(screen.getAllByText('Ada Lovelace').length).toBe(2);
    expect(screen.getByRole('button', { name: /confirm import/i })).toBeInTheDocument();
  });

  it('lets an admin resolve a duplicate row to update', () => {
    mockJob = { data: readyJob, isError: false };
    mockRows = { data: { items: [duplicateRow], total: 1, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=7');

    const updateRadio = screen.getByRole('radio', { name: 'Update' });
    fireEvent.click(updateRadio);

    expect(resolveMutate).toHaveBeenCalledWith(
      { jobId: 7, rowId: 102, data: { resolution: 'update' } },
      expect.anything(),
    );
  });

  it('does not offer a resolution choice for a new row -- it always creates', () => {
    mockJob = { data: readyJob, isError: false };
    mockRows = { data: { items: [newRow], total: 1, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=7');

    expect(screen.getByText('Will create')).toBeInTheDocument();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
  });

  it('opens a confirmation dialog before confirming the import', () => {
    mockJob = { data: readyJob, isError: false };
    mockRows = { data: { items: [newRow], total: 1, page: 1, pageSize: 25 }, isLoading: false };
    renderAtLocation('job=7');

    fireEvent.click(screen.getByRole('button', { name: /confirm import/i }));

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/cannot be undone/i)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: /confirm import/i }));
    expect(confirmMutate).toHaveBeenCalledWith({ jobId: 7 }, expect.anything());
  });

  it('shows the completed results step with counts and an error report download', () => {
    mockJob = {
      data: { ...readyJob, status: 'completed', invalidCount: 1, resultSummary: { totalRows: 2, created: 1, updated: 0, skipped: 1 } },
      isError: false,
    };
    renderAtLocation('job=7');

    expect(screen.getByText('Import complete')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download error report/i })).toBeInTheDocument();
  });

  it('shows a cancelled state with a way to start over', () => {
    mockJob = { data: { ...readyJob, status: 'cancelled' }, isError: false };
    renderAtLocation('job=7');

    expect(screen.getByText('Import cancelled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /start new import/i })).toBeInTheDocument();
  });

  it('shows a not-found state when the job id in the URL no longer exists', () => {
    mockJob = { data: undefined, isError: true };
    renderAtLocation('job=999');

    expect(screen.getByText('Import job not found')).toBeInTheDocument();
  });
});
