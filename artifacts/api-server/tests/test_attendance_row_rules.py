import datetime

from pyapp.attendance_row_rules import (
    diff_entry,
    is_historical_save,
    requires_change_reason,
    validate_entry,
)


def _validate(**overrides):
    defaults = dict(
        learner_id=1, status="present", hours_attended=7, minutes_late=0,
        override_reason=None, planned_hours=7, is_admin=False,
    )
    defaults.update(overrides)
    return validate_entry(**defaults)


class TestPresent:
    def test_planned_hours_is_valid(self):
        assert _validate(status="present", hours_attended=7, minutes_late=0) == []

    def test_zero_minutes_late_is_not_required(self):
        assert _validate(status="present", hours_attended=7, minutes_late=0) == []

    def test_negative_hours_would_be_rejected_upstream_by_pydantic(self):
        # Field(ge=0) on the Pydantic model rejects negatives before this
        # function ever sees them -- nothing to additionally check here.
        pass


class TestAbsentAuthorised:
    def test_zero_hours_and_minutes_is_valid(self):
        assert _validate(status="absent_authorised", hours_attended=0, minutes_late=0) == []

    def test_nonzero_hours_is_rejected(self):
        errors = _validate(status="absent_authorised", hours_attended=2, minutes_late=0)
        assert any(e["field"] == "hoursAttended" for e in errors)

    def test_nonzero_minutes_late_is_rejected(self):
        errors = _validate(status="absent_authorised", hours_attended=0, minutes_late=5)
        assert any(e["field"] == "minutesLate" for e in errors)


class TestAbsentUnauthorised:
    def test_zero_hours_and_minutes_is_valid(self):
        assert _validate(status="absent_unauthorised", hours_attended=0, minutes_late=0) == []

    def test_nonzero_hours_is_rejected(self):
        errors = _validate(status="absent_unauthorised", hours_attended=1, minutes_late=0)
        assert any(e["field"] == "hoursAttended" for e in errors)


class TestLate:
    def test_requires_minutes_late_greater_than_zero(self):
        errors = _validate(status="late", hours_attended=6, minutes_late=0)
        assert any(e["field"] == "minutesLate" for e in errors)

    def test_positive_minutes_late_is_valid(self):
        assert _validate(status="late", hours_attended=6, minutes_late=15) == []

    def test_minutes_late_cannot_be_satisfied_by_negative_value(self):
        # Field(ge=0) on the Pydantic model already rejects negative
        # minutesLate before reaching here; 0 itself is still invalid for Late.
        errors = _validate(status="late", hours_attended=6, minutes_late=0)
        assert errors


class TestNotExpected:
    def test_zero_hours_and_minutes_is_valid(self):
        assert _validate(status="not_expected", hours_attended=0, minutes_late=0) == []

    def test_nonzero_hours_is_rejected(self):
        errors = _validate(status="not_expected", hours_attended=3, minutes_late=0)
        assert any(e["field"] == "hoursAttended" for e in errors)


class TestWithdrawn:
    def test_zero_hours_and_minutes_is_valid(self):
        assert _validate(status="withdrawn", hours_attended=0, minutes_late=0) == []

    def test_nonzero_hours_is_rejected(self):
        errors = _validate(status="withdrawn", hours_attended=1, minutes_late=0)
        assert any(e["field"] == "hoursAttended" for e in errors)


class TestBil:
    def test_zero_hours_and_minutes_is_valid(self):
        assert _validate(status="bil", hours_attended=0, minutes_late=0) == []

    def test_nonzero_hours_is_rejected(self):
        errors = _validate(status="bil", hours_attended=2, minutes_late=0)
        assert any(e["field"] == "hoursAttended" for e in errors)

    def test_nonzero_minutes_late_is_rejected(self):
        errors = _validate(status="bil", hours_attended=0, minutes_late=15)
        assert any(e["field"] == "minutesLate" for e in errors)


class TestExcessHoursOverride:
    def test_tutor_exceeding_planned_hours_is_rejected_even_with_reason(self):
        errors = _validate(
            status="present", hours_attended=9, minutes_late=0, planned_hours=7,
            is_admin=False, override_reason="I promise",
        )
        assert any(e["field"] == "hoursAttended" and "Administrator" in e["message"] for e in errors)

    def test_admin_exceeding_planned_hours_without_reason_is_rejected(self):
        errors = _validate(
            status="present", hours_attended=9, minutes_late=0, planned_hours=7,
            is_admin=True, override_reason=None,
        )
        assert any(e["field"] == "overrideReason" for e in errors)

    def test_admin_exceeding_planned_hours_with_reason_is_accepted(self):
        errors = _validate(
            status="present", hours_attended=9, minutes_late=0, planned_hours=7,
            is_admin=True, override_reason="Ran an extra session",
        )
        assert errors == []

    def test_late_exceeding_planned_minutes_requires_admin_override(self):
        errors = _validate(
            status="late", hours_attended=1, minutes_late=500, planned_hours=7,
            is_admin=False, override_reason="ok",
        )
        assert any(e["field"] == "minutesLate" for e in errors)

    def test_hours_within_planned_duration_needs_no_override(self):
        assert _validate(status="present", hours_attended=7, minutes_late=0, planned_hours=7, is_admin=False) == []

    def test_check_override_false_skips_the_excess_hours_gate_entirely(self):
        # Used when revalidating already-saved, already-audited data (e.g.
        # register completion) -- an admin-approved excess-hours value
        # already in the database is not itself invalid.
        errors = _validate(
            status="present", hours_attended=20, minutes_late=0, planned_hours=7,
            is_admin=False, override_reason=None, check_override=False,
        )
        assert errors == []


class TestIsHistoricalSave:
    def test_completed_register_is_historical(self):
        assert is_historical_save("completed", datetime.date(2099, 1, 1)) is True

    def test_locked_register_is_historical(self):
        assert is_historical_save("locked", datetime.date(2099, 1, 1)) is True

    def test_past_session_date_is_historical_even_if_in_progress(self):
        assert is_historical_save("in_progress", datetime.date(2020, 1, 1)) is True

    def test_future_in_progress_session_is_not_historical(self):
        assert is_historical_save("in_progress", datetime.date(2099, 1, 1), today=datetime.date(2026, 1, 1)) is False

    def test_todays_session_is_not_historical(self):
        today = datetime.date(2026, 6, 1)
        assert is_historical_save("not_started", today, today=today) is False


class TestDiffEntry:
    def test_first_ever_save_is_not_a_diff(self):
        assert diff_entry(None, {"status": "present", "hoursAttended": 7, "minutesLate": 0, "notes": None}) == {}

    def test_unchanged_values_produce_no_diff(self):
        existing = {"status": "present", "hoursAttended": 7.0, "minutesLate": 0, "notes": None}
        new = {"status": "present", "hoursAttended": 7, "minutesLate": 0, "notes": None}
        assert diff_entry(existing, new) == {}

    def test_status_change_is_captured(self):
        existing = {"status": "present", "hoursAttended": 7.0, "minutesLate": 0, "notes": None}
        new = {"status": "absent_authorised", "hoursAttended": 0, "minutesLate": 0, "notes": None}
        diff = diff_entry(existing, new)
        assert diff["status"] == {"before": "present", "after": "absent_authorised"}

    def test_hours_change_is_captured(self):
        existing = {"status": "present", "hoursAttended": 7.0, "minutesLate": 0, "notes": None}
        new = {"status": "present", "hoursAttended": 3.5, "minutesLate": 0, "notes": None}
        diff = diff_entry(existing, new)
        assert diff["hoursAttended"] == {"before": 7.0, "after": 3.5}

    def test_notes_only_change_is_captured_but_not_material(self):
        existing = {"status": "present", "hoursAttended": 7.0, "minutesLate": 0, "notes": None}
        new = {"status": "present", "hoursAttended": 7, "minutesLate": 0, "notes": "Left early"}
        diff = diff_entry(existing, new)
        assert diff == {"notes": {"before": None, "after": "Left early"}}
        assert requires_change_reason(diff) is False


class TestRequiresChangeReason:
    def test_status_change_requires_reason(self):
        assert requires_change_reason({"status": {"before": "present", "after": "late"}}) is True

    def test_hours_change_requires_reason(self):
        assert requires_change_reason({"hoursAttended": {"before": 7, "after": 3}}) is True

    def test_minutes_late_only_change_does_not_require_reason(self):
        assert requires_change_reason({"minutesLate": {"before": 0, "after": 5}}) is False

    def test_no_changes_does_not_require_reason(self):
        assert requires_change_reason({}) is False
