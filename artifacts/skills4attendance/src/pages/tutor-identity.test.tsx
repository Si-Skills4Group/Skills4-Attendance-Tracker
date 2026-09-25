import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import TutorIdentityPage from './tutor-identity';

let mockCurrentUser: any;
let mockDiagnostics: any;
let mockPreviewMutate: any;
let mockCommitMutate: any;
let previewResult: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useGetTutorIdentityDiagnostics: () => mockDiagnostics,
  getGetTutorIdentityDiagnosticsQueryKey: () => ['tutorIdentityDiagnostics'],
  usePreviewTutorMappingCorrection: (opts: any) => ({
    mutate: (args: any) => { mockPreviewMutate(args); opts.mutation.onSuccess(previewResult); },
    isPending: false,
  }),
  useCommitTutorMappingCorrection: (opts: any) => ({
    mutate: (args: any) => { mockCommitMutate(args); opts.mutation.onSuccess({}); },
    isPending: false,
  }),
}));

function renderPage() {
  const location = memoryLocation({ path: '/tutor-identity', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/tutor-identity" component={TutorIdentityPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockPreviewMutate = vi.fn();
  mockCommitMutate = vi.fn();
  previewResult = {
    sourceTutor: { id: 82, firstName: 'Ellie', lastName: 'Frost', email: 'elouise.frost@example.com', active: false, externalSystemId: 'BUD-ELLIE', employeeRef: null, updatedAt: '2026-01-01T00:00:00Z' },
    targetTutor: { id: 19, firstName: 'Ellie', lastName: 'Frost', email: 'ellie.frost@example.com', active: true, externalSystemId: null, employeeRef: null, updatedAt: '2026-01-01T00:00:00Z' },
    budTutorId: 'BUD-ELLIE',
    nameMatch: true,
    supportingSignals: [],
    requiresManualIdentityConfirmation: true,
    conflictingIdentifierOwnership: [],
    fieldChanges: [
      { tutorId: 82, field: 'externalSystemId', before: 'BUD-ELLIE', after: null },
      { tutorId: 19, field: 'externalSystemId', before: null, after: 'BUD-ELLIE' },
    ],
    downstreamEffects: ['67 Bud learning-plan row(s) would resolve to tutor 19.'],
    remainingUnresolved: ['Existing learners under tutor 82 are not moved by this correction.'],
    preview: { sourceTutorUpdatedAt: '2026-01-01T00:00:00Z', targetTutorUpdatedAt: '2026-01-01T00:00:00Z' },
  };
  mockDiagnostics = {
    data: {
      unmatchedBudTutorIds: [{ budTutorId: 'BUD-X', budTutorName: 'Nobody', affectedLearnerReferences: 2, affectedBudLearningPlanRows: 2 }],
      budIdsHeldOnlyByInactiveTutor: [{
        budTutorId: 'BUD-ELLIE',
        budTutorName: 'Elouise Frost',
        inactiveTutor: { id: 82, firstName: 'Ellie', lastName: 'Frost', email: 'elouise.frost@example.com', active: false, externalSystemId: 'BUD-ELLIE', employeeRef: null, updatedAt: '2026-01-01T00:00:00Z' },
        affectedLearnerReferences: 63, affectedBudLearningPlanRows: 67,
      }],
      budIdAttachedToMultipleTutors: [],
      duplicateTutorCandidates: [{
        normalizedName: 'ellie frost',
        tutors: [
          { id: 19, firstName: 'Ellie', lastName: 'Frost', email: 'ellie.frost@example.com', active: true, externalSystemId: null, employeeRef: null, updatedAt: '2026-01-01T00:00:00Z' },
          { id: 82, firstName: 'Ellie', lastName: 'Frost', email: 'elouise.frost@example.com', active: false, externalSystemId: 'BUD-ELLIE', employeeRef: null, updatedAt: '2026-01-01T00:00:00Z' },
        ],
      }],
      totals: { distinctAffectedLearnerReferences: 65, affectedBudLearningPlanRows: 69 },
    },
    isLoading: false, isError: false,
  };
});

describe('TutorIdentityPage', () => {
  it('is not shown to a tutor', () => {
    mockCurrentUser = { data: { firstName: 'Sam', role: 'tutor', tutorId: 10 } };
    renderPage();
    expect(screen.getByText(/administrator access required/i)).toBeInTheDocument();
  });

  it('shows unmatched, inactive-only, and duplicate-candidate diagnostics for an admin', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    expect(screen.getByText('BUD-X')).toBeInTheDocument();
    expect(screen.getByText('BUD-ELLIE')).toBeInTheDocument();
    expect(screen.getByText('ellie frost')).toBeInTheDocument();
  });

  it('requires both a reason and explicit identity confirmation when no supporting signals exist', async () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByText(/propose correction to ellie frost/i));
    expect(await screen.findByText('Propose tutor mapping correction')).toBeInTheDocument();
    expect(screen.getByText(/identical/i)).toBeInTheDocument();
    expect(screen.getByText('Identity confirmation required')).toBeInTheDocument();
    expect(screen.getByText(/none found \(no shared phone/i)).toBeInTheDocument();

    const confirmButton = screen.getByRole('button', { name: /confirm correction/i });
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByLabelText(/reason/i), 'Confirmed same person via employee_ref');
    expect(confirmButton).toBeDisabled(); // reason alone is not enough -- identity must also be confirmed

    await user.click(screen.getByText(/i have independently confirmed/i));
    expect(confirmButton).not.toBeDisabled();
    await user.click(confirmButton);

    expect(mockCommitMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({ sourceTutorId: 82, targetTutorId: 19, budTutorId: 'BUD-ELLIE', identityConfirmedByAdmin: true }),
      }),
    );
  });

  it('still requires the identity checkbox even when supporting signals exist -- evidence never bypasses confirmation', async () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    previewResult.supportingSignals = ['Same phone number on record (supporting signal only, not proof).'];
    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByText(/propose correction to ellie frost/i));
    expect(await screen.findByText('Propose tutor mapping correction')).toBeInTheDocument();
    // The signal is shown, but the confirmation gate still appears -- it's
    // never conditional on what supportingSignals contains.
    expect(screen.getByText(/same phone number on record/i)).toBeInTheDocument();
    expect(screen.getByText('Identity confirmation required')).toBeInTheDocument();

    const confirmButton = screen.getByRole('button', { name: /confirm correction/i });
    await user.type(screen.getByLabelText(/reason/i), 'Confirmed via shared phone number');
    expect(confirmButton).toBeDisabled();

    await user.click(screen.getByText(/i have independently confirmed/i));
    expect(confirmButton).not.toBeDisabled();
  });
});
