import os

import pytest
from fastapi import HTTPException

from pyapp.auth import EntraIdentity, _load_entra_user
from pyapp.routers.users import _apply_user_updates, _ensure_tutor_not_linked_elsewhere, _validate_role_mapping


def _make_unlinked_tutor(db) -> dict:
    """A tutor record whose own synthetic user is inactive, so it doesn't
    count as "linked to an active user" -- distinct from tutor_factory(),
    whose synthetic user is always active. Tests that need a *valid but
    available* tutorId (not tripping the tutor-already-linked-elsewhere
    check) use this instead. tutors.user_id is NOT NULL, so a tutor
    always needs some user row, just not an active one."""
    suffix = os.urandom(4).hex()
    email = f"unlinked-tutor-{suffix}@example.com"
    db.execute(
        "INSERT INTO users (first_name, last_name, email, role, active) VALUES ('Unlinked', 'Tutor', %s, 'tutor', false) RETURNING id",
        (email,),
    )
    user_id = db.fetchone()["id"]
    db.execute(
        "INSERT INTO tutors (user_id, first_name, last_name, email, active) VALUES (%s, 'Unlinked', 'Tutor', %s, true) RETURNING id",
        (user_id, email),
    )
    tutor_id = db.fetchone()["id"]
    db.execute("UPDATE users SET tutor_id = %s WHERE id = %s", (tutor_id, user_id))
    return {"tutorId": tutor_id, "userId": user_id}


def _cleanup_unlinked_tutor(db, tutor: dict) -> None:
    db.execute("DELETE FROM tutors WHERE id = %s", (tutor["tutorId"],))
    db.execute("DELETE FROM users WHERE id = %s", (tutor["userId"],))


def test_role_change_requires_admin_or_tutor(db, request_factory, admin_user):
    with pytest.raises(HTTPException):
        _validate_role_mapping("superuser", None)


def test_tutor_role_requires_tutor_link(db, request_factory, admin_user):
    with pytest.raises(HTTPException) as exc:
        _apply_user_updates(db, admin_user, admin_user["userId"], {"role": "tutor"})
    assert exc.value.status_code == 400
    assert "linked to a tutor" in str(exc.value.detail)


def test_final_active_admin_cannot_be_deactivated(db, request_factory, admin_user):
    """admin_user is the only active admin created by this test (isolated
    test DB), so demoting/deactivating it must be refused."""
    with pytest.raises(HTTPException) as exc:
        _apply_user_updates(db, admin_user, admin_user["userId"], {"active": False})
    assert exc.value.status_code == 400
    assert "final active administrator" in str(exc.value.detail)


def test_final_active_admin_cannot_be_demoted_to_tutor(db, admin_user):
    """The acting session must belong to someone other than admin_user --
    otherwise the self-role-change guard fires first and masks the
    final-admin check this test targets. _apply_user_updates only reads
    session["userId"] for that comparison, so a synthetic session dict
    (no real row) is enough -- it deliberately does NOT create a second
    real admin user, which would itself satisfy the ">1 active admin"
    condition and mask the check just as badly."""
    tutor = _make_unlinked_tutor(db)
    acting_session = {"userId": admin_user["userId"] + 999_999, "role": "admin", "tutorId": None}
    try:
        with pytest.raises(HTTPException) as exc:
            _apply_user_updates(db, acting_session, admin_user["userId"], {"role": "tutor", "tutorId": tutor["tutorId"]})
        assert exc.value.status_code == 400
        assert "final active administrator" in str(exc.value.detail)
    finally:
        _cleanup_unlinked_tutor(db, tutor)


def test_second_admin_can_be_deactivated(db, admin_user):
    db.execute(
        "INSERT INTO users (first_name, last_name, email, role, active) VALUES ('Second', 'Admin', %s, 'admin', true) RETURNING id",
        ("second-admin@example.com",),
    )
    second_admin_id = db.fetchone()["id"]
    try:
        _, updated = _apply_user_updates(db, admin_user, second_admin_id, {"active": False})
        assert updated["active"] is False
    finally:
        db.execute("DELETE FROM users WHERE id = %s", (second_admin_id,))


def test_user_cannot_change_own_role(db, admin_user):
    tutor = _make_unlinked_tutor(db)
    try:
        with pytest.raises(HTTPException) as exc:
            _apply_user_updates(db, admin_user, admin_user["userId"], {"role": "tutor", "tutorId": tutor["tutorId"]})
        assert exc.value.status_code == 400
        assert "cannot change your own role" in str(exc.value.detail)
    finally:
        _cleanup_unlinked_tutor(db, tutor)


def test_tutor_link_must_be_unique_across_active_users(db, tutor_factory, admin_user):
    tutor = tutor_factory()
    # tutor_factory() already links its own synthetic user to the tutor record,
    # so re-linking a *different* user (our admin) to the same tutor should fail.
    with pytest.raises(HTTPException) as exc:
        _ensure_tutor_not_linked_elsewhere(db, tutor["tutorId"], exclude_user_id=admin_user["userId"])
    assert exc.value.status_code == 400


def test_tutor_link_excludes_self(db, tutor_factory):
    tutor = tutor_factory()
    # A user re-saving their own existing link should not trip the uniqueness check.
    _ensure_tutor_not_linked_elsewhere(db, tutor["tutorId"], exclude_user_id=tutor["userId"])


def test_inactive_user_denied_entra_login(db, request_factory):
    db.execute(
        "INSERT INTO users (first_name, last_name, email, role, active, entra_object_id, entra_tenant_id) "
        "VALUES ('Inactive', 'Person', %s, 'tutor', false, %s, %s) RETURNING id",
        ("inactive-entra-user@example.com", "test-oid-inactive", "test-tid-inactive"),
    )
    user_id = db.fetchone()["id"]
    try:
        identity = EntraIdentity(
            object_id="test-oid-inactive",
            tenant_id="test-tid-inactive",
            subject="test-oid-inactive",
            email="inactive-entra-user@example.com",
            first_name="Inactive",
            last_name="Person",
            display_name="Inactive Person",
            claims={},
        )
        with pytest.raises(HTTPException) as exc:
            _load_entra_user(identity, request_factory())
        assert exc.value.status_code == 403
    finally:
        db.execute("DELETE FROM users WHERE id = %s", (user_id,))


def test_unrecognised_entra_identity_is_denied(db, request_factory):
    identity = EntraIdentity(
        object_id="no-such-object-id",
        tenant_id="no-such-tenant-id",
        subject="no-such-object-id",
        email="ghost@example.com",
        first_name=None,
        last_name=None,
        display_name=None,
        claims={},
    )
    with pytest.raises(HTTPException) as exc:
        _load_entra_user(identity, request_factory())
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "access_not_provisioned"


def test_entra_login_auto_links_unlinked_user_by_email(db, request_factory):
    """A user provisioned by email only (e.g. bulk CSV import, before their
    first Entra sign-in) should be linked automatically on first login."""
    db.execute(
        "INSERT INTO users (first_name, last_name, email, role, active) "
        "VALUES ('Not', 'YetLinked', %s, 'tutor', true) RETURNING id",
        ("not-yet-linked@example.com",),
    )
    user_id = db.fetchone()["id"]
    try:
        identity = EntraIdentity(
            object_id="fresh-oid",
            tenant_id="fresh-tid",
            subject="fresh-oid",
            email="not-yet-linked@example.com",
            first_name="Not",
            last_name="YetLinked",
            display_name="Not YetLinked",
            claims={},
        )
        session = _load_entra_user(identity, request_factory())
        assert session["userId"] == user_id

        db.execute("SELECT entra_object_id, entra_tenant_id FROM users WHERE id = %s", (user_id,))
        row = db.fetchone()
        assert row["entra_object_id"] == "fresh-oid"
        assert row["entra_tenant_id"] == "fresh-tid"
    finally:
        db.execute("DELETE FROM users WHERE id = %s", (user_id,))
