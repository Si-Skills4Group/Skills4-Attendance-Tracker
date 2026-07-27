"""Pure unit tests for the Bud status_desc -> learners.status transition
matrix -- no DB fixtures needed, this module has no DB access."""
from pyapp.bud_status_mapping import classify_status_transition


class TestActiveOnBreakTransitions:
    def test_in_progress_to_on_break_is_automatic(self):
        result = classify_status_transition("active", "On Break")
        assert result == {"kind": "automatic", "targetStatus": "paused", "dateField": None}

    def test_on_break_to_in_progress_is_automatic(self):
        result = classify_status_transition("paused", "In Progress")
        assert result == {"kind": "automatic", "targetStatus": "active", "dateField": None}

    def test_end_point_assessment_maps_to_active(self):
        result = classify_status_transition("paused", "In End Point Assessment")
        assert result == {"kind": "automatic", "targetStatus": "active", "dateField": None}


class TestTerminalTransitionsRequireADate:
    def test_in_progress_to_withdrawn_needs_a_date(self):
        result = classify_status_transition("active", "Withdrawn")
        assert result == {"kind": "needs_date", "targetStatus": "withdrawn", "dateField": "withdrawalDate"}

    def test_in_progress_to_completed_needs_a_date(self):
        result = classify_status_transition("active", "Completed")
        assert result == {"kind": "needs_date", "targetStatus": "completed", "dateField": "actualEndDate"}

    def test_on_break_to_withdrawn_needs_a_date(self):
        result = classify_status_transition("paused", "Withdrawn")
        assert result == {"kind": "needs_date", "targetStatus": "withdrawn", "dateField": "withdrawalDate"}


class TestInformationalOnlyStatuses:
    def test_pending_is_informational(self):
        assert classify_status_transition("active", "Pending")["kind"] == "informational"

    def test_break_requested_is_informational(self):
        assert classify_status_transition("active", "Break Requested")["kind"] == "informational"

    def test_break_return_requested_is_informational(self):
        assert classify_status_transition("paused", "Break Return Requested")["kind"] == "informational"

    def test_withdrawal_requested_is_informational(self):
        assert classify_status_transition("active", "Withdrawal Requested")["kind"] == "informational"

    def test_informational_statuses_never_carry_a_target_status(self):
        result = classify_status_transition("active", "Pending")
        assert result["targetStatus"] is None
        assert result["dateField"] is None


class TestNoChange:
    def test_same_status_is_no_change(self):
        assert classify_status_transition("active", "In Progress")["kind"] == "no_change"

    def test_same_status_is_no_change_for_paused(self):
        assert classify_status_transition("paused", "On Break")["kind"] == "no_change"

    def test_missing_status_desc_is_no_change(self):
        assert classify_status_transition("active", None)["kind"] == "no_change"


class TestUnrecognised:
    def test_unknown_status_desc_is_unrecognised(self):
        result = classify_status_transition("active", "Some Future Bud Status")
        assert result["kind"] == "unrecognised"
        assert result["targetStatus"] is None
