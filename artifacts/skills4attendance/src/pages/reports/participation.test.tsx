import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import ParticipationReportPage from './participation';

let mockCurrentUser: any;
let mockTutors: any;
let mockCohorts: any;
let mockReport: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
  useListTutors: () => ({ data: mockTutors }),
  getListTutorsQueryKey: () => ['listTutors'],
  useListCohorts: () => ({ data: mockCohorts }),
  useGetParticipationReport: () => mockReport,
  exportParticipationReport: vi.fn(),
}));

function renderPage() {
  const location = memoryLocation({ path: '/reports/participation', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports/participation" component={ParticipationReportPage} />
    </Router>,
  );
}

beforeEach(() => {
  mockTutors = [];
  mockCohorts = [];
  mockReport = {
    data: {
      periodStart: '2026-01-01', periodEnd: '2026-01-31',
      expectedLearnerSessions: 10, liveAttendedLearnerSessions: 7, recordedAbsences: 2,
      caughtUp: 1, absencesWithoutCatchup: 1, attendanceNotRecorded: 1, totalParticipation: 8,
      participationRate: 80.0, calculatedAt: '2026-02-01T10:00:00Z',
    },
    isLoading: false, isError: false,
  };
});

describe('ParticipationReportPage', () => {
  it('shows the acceptance-example figures with a clearly separate label from the attendance percentage', () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    renderPage();

    expect(screen.getByText('Session Participation Including Catch-up', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText('80.0%')).toBeInTheDocument();
    expect(screen.getByText('8 of 10 expected learner-sessions')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument(); // live attended
    expect(screen.getByText('2')).toBeInTheDocument(); // recorded absences
  });

  it('shows no percentage when the report returns null (zero expected sessions)', () => {
    mockCurrentUser = { data: { role: 'admin', tutorId: null } };
    mockReport = { ...mockReport, data: { ...mockReport.data, expectedLearnerSessions: 0, totalParticipation: 0, participationRate: null } };
    renderPage();

    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('hides the tutor filter for a tutor', () => {
    mockCurrentUser = { data: { role: 'tutor', tutorId: 7 } };
    renderPage();
    expect(screen.queryByPlaceholderText('Tutor')).not.toBeInTheDocument();
  });
});
