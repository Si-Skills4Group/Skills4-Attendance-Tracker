import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { Router, Route } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import { renderWithQueryClient } from '@/test/test-utils';
import ReportsHubPage from './reports';

let mockCurrentUser: any;

vi.mock('@workspace/api-client-react', () => ({
  useGetCurrentUser: () => mockCurrentUser,
}));

function renderHub() {
  const location = memoryLocation({ path: '/reports', record: true });
  renderWithQueryClient(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Route path="/reports" component={ReportsHubPage} />
    </Router>,
  );
  return location;
}

describe('ReportsHubPage', () => {
  beforeEach(() => {
    mockCurrentUser = { data: { firstName: 'Tam', role: 'tutor', tutorId: 10 } };
  });

  it('hides admin-only report cards for a tutor', () => {
    renderHub();

    expect(screen.getByText('Learner Attendance')).toBeInTheDocument();
    expect(screen.getByText('Cohort Attendance')).toBeInTheDocument();
    expect(screen.getByText('Tutor Attendance')).toBeInTheDocument();
    expect(screen.getByText('Attendance Hours')).toBeInTheDocument();
    expect(screen.getByText('Absence Analysis')).toBeInTheDocument();
    expect(screen.getByText('Late Attendance')).toBeInTheDocument();
    expect(screen.getByText('Register Completion')).toBeInTheDocument();
    expect(screen.queryByText('Organisation Overview')).not.toBeInTheDocument();
    expect(screen.queryByText('Allocation History')).not.toBeInTheDocument();
  });

  it('shows every report card, including admin-only ones, for an admin', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderHub();

    expect(screen.getByText('Organisation Overview')).toBeInTheDocument();
    expect(screen.getByText('Allocation History')).toBeInTheDocument();
  });

  it('links each card to its report route', () => {
    mockCurrentUser = { data: { firstName: 'Alex', role: 'admin', tutorId: null } };
    renderHub();

    const link = screen.getByText('Absence Analysis').closest('a');
    expect(link).toHaveAttribute('href', '/reports/absence');
  });
});
