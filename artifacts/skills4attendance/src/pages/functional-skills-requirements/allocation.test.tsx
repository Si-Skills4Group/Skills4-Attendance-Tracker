import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import FsRequirementAllocationPage from './allocation';

const coveredRow = {
  id: 1, learnerRef: 'REF-1', learnerName: 'Jane Doe', status: 'active',
  maths: true, english: false, requirementRecorded: true, requirementStatus: 'recorded',
  source: 'manual_upload', importBatchId: 5, updatedBy: 1, updatedAt: '2026-01-01T00:00:00Z',
  missingSubjects: [], missingTutorSubjects: [],
  activeSecondaryCohorts: [{ cohortId: 10, cohortName: 'Maths AM', subject: 'math', tutorId: 3, tutorName: 'Alex Tutor', tutorActive: true }],
};

const missingCohortRow = {
  id: 2, learnerRef: 'REF-2', learnerName: 'Sam Gaps', status: 'active',
  maths: true, english: true, requirementRecorded: true, requirementStatus: 'recorded',
  source: 'manual_upload', importBatchId: 5, updatedBy: 1, updatedAt: '2026-01-02T00:00:00Z',
  missingSubjects: ['english'], missingTutorSubjects: [],
  activeSecondaryCohorts: [{ cohortId: 10, cohortName: 'Maths AM', subject: 'math', tutorId: 3, tutorName: 'Alex Tutor', tutorActive: true }],
};

const missingTutorRow = {
  id: 3, learnerRef: 'REF-3', learnerName: 'Pat Coverage', status: 'active',
  maths: true, english: false, requirementRecorded: true, requirementStatus: 'recorded',
  source: 'manual_upload', importBatchId: 5, updatedBy: 1, updatedAt: '2026-01-03T00:00:00Z',
  missingSubjects: [], missingTutorSubjects: ['math'],
  activeSecondaryCohorts: [{ cohortId: 11, cohortName: 'Maths PM', subject: 'math', tutorId: null, tutorName: null, tutorActive: null }],
};

const noRequirementRow = {
  id: 4, learnerRef: 'REF-4', learnerName: 'No Data', status: 'active',
  maths: false, english: false, requirementRecorded: false, requirementStatus: null,
  source: null, importBatchId: null, updatedBy: null, updatedAt: null,
  missingSubjects: [], missingTutorSubjects: [],
  activeSecondaryCohorts: [{ cohortId: 10, cohortName: 'Maths AM', subject: 'math', tutorId: 3, tutorName: 'Alex Tutor', tutorActive: true }],
};

let mockAllocation: { data: any; isLoading: boolean; isError: boolean };

vi.mock('@workspace/api-client-react', () => ({
  useGetFsRequirementAllocation: () => mockAllocation,
}));

function renderAtLocation() {
  const location = memoryLocation({ path: '/functional-skills-requirements/allocation', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/functional-skills-requirements/allocation" component={FsRequirementAllocationPage} />
    </Router>,
  );
  return location;
}

beforeEach(() => {
  mockAllocation = { data: { items: [], total: 0, page: 1, pageSize: 25 }, isLoading: false, isError: false };
});

describe('FsRequirementAllocationPage', () => {
  it('shows a covered learner with no missing-coverage badges', () => {
    mockAllocation = { data: { items: [coveredRow], total: 1, page: 1, pageSize: 25 }, isLoading: false, isError: false };
    renderAtLocation();

    expect(screen.getByText('Jane Doe')).toBeInTheDocument();
    expect(screen.getByText('Covered')).toBeInTheDocument();
    expect(screen.queryByText(/Missing cohort/i)).not.toBeInTheDocument();
  });

  it('flags a missing subject cohort separately from a missing/inactive tutor', () => {
    mockAllocation = { data: { items: [missingCohortRow, missingTutorRow], total: 2, page: 1, pageSize: 25 }, isLoading: false, isError: false };
    renderAtLocation();

    expect(screen.getByText(/Missing cohort: English/)).toBeInTheDocument();
    expect(screen.getByText(/Missing\/inactive tutor: Maths/)).toBeInTheDocument();
  });

  it('labels an existing secondary enrollment with no uploaded requirement as "No requirement uploaded", not unfunded/inappropriate', () => {
    mockAllocation = { data: { items: [noRequirementRow], total: 1, page: 1, pageSize: 25 }, isLoading: false, isError: false };
    renderAtLocation();

    expect(screen.getByText('No requirement uploaded')).toBeInTheDocument();
  });

  it('navigates to the upload page from the header action', () => {
    const location = renderAtLocation();
    fireEvent.click(screen.getByRole('button', { name: /upload requirements/i }));
    expect(location.history?.at(-1)).toBe('/functional-skills-requirements/import');
  });

  it('shows an empty state when no learners match the filters', () => {
    renderAtLocation();
    expect(screen.getByText(/no learners match these filters/i)).toBeInTheDocument();
  });

  it('shows an error state when the allocation query fails', () => {
    mockAllocation = { data: undefined, isLoading: false, isError: true };
    renderAtLocation();
    expect(screen.getByText(/could not load the allocation view/i)).toBeInTheDocument();
  });
});
