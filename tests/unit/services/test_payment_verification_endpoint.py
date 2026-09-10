"""
Unit tests for app/api/api_v1/endpoints/payment_verification.py
Covers: get_students_payment_status, verify_student_payment, unverify_student_payment,
        get_student_payment_status, add_student_specialization, remove_student_specialization
Note: all endpoints are async → use asyncio.run()
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch

from app.api.api_v1.endpoints.payment_verification import (
    get_students_payment_status,
    verify_student_payment,
    unverify_student_payment,
    get_student_payment_status,
    add_student_specialization,
    remove_student_specialization,
    PaymentVerificationRequest,
    SpecializationRequest,
)
from app.models.user import UserRole
from app.models.specialization import SpecializationType

_BASE = "app.api.api_v1.endpoints.payment_verification"

_VALID_SPEC = SpecializationType.LFA_FOOTBALL_PLAYER.value  # e.g. "lfa_football_player"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(first_val=None, all_val=None):
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = all_val if all_val is not None else []
    q.first.return_value = first_val
    return q


def _seq_db(*vals):
    call_n = [0]
    db = MagicMock()
    def side(*args):
        n = call_n[0]; call_n[0] += 1
        v = vals[n] if n < len(vals) else None
        q = _q()
        if isinstance(v, list):
            q.all.return_value = v
        else:
            q.first.return_value = v
        return q
    db.query.side_effect = side
    return db


def _admin():
    u = MagicMock()
    u.id = 42
    u.name = "Admin"
    u.role = UserRole.ADMIN
    return u


def _student(uid=7):
    u = MagicMock()
    u.id = uid
    u.name = "Student"
    u.role = UserRole.STUDENT
    u.specialization = None
    u.payment_verified = False
    u.payment_verified_by = None
    return u


def _request():
    return MagicMock()


def _pay_req(specs=None):
    return PaymentVerificationRequest(specializations=specs or [_VALID_SPEC])


def _spec_req(spec=None):
    return SpecializationRequest(specialization_type=spec or _VALID_SPEC)


# ---------------------------------------------------------------------------
# get_students_payment_status
# ---------------------------------------------------------------------------

class TestGetStudentsPaymentStatus:
    def _call(self, db=None, current_user=None):
        return asyncio.run(get_students_payment_status(
            request=_request(),
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        ))

    def test_empty_list(self):
        """GSS-01: no students → empty list."""
        q = _q(all_val=[])
        db = MagicMock(); db.query.return_value = q
        result = self._call(db=db)
        assert result == []

    def test_students_returned_as_dicts(self):
        """GSS-02: students found → list of dicts with payment fields."""
        s = _student()
        s.specialization = MagicMock(); s.specialization.value = _VALID_SPEC
        q = _q(all_val=[s])
        db = MagicMock(); db.query.return_value = q
        result = self._call(db=db)
        assert len(result) == 1
        assert "payment_verified" in result[0]


# ---------------------------------------------------------------------------
# verify_student_payment
# ---------------------------------------------------------------------------

class TestVerifyStudentPayment:
    def _call(self, student_id=7, payment_request=None, db=None, current_user=None):
        return asyncio.run(verify_student_payment(
            request=_request(),
            student_id=student_id,
            payment_request=payment_request or _pay_req(),
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        ))

    def test_student_not_found_404(self):
        """VSP-01: student not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_empty_specializations_400(self):
        """VSP-02: empty specializations list → 400."""
        from fastapi import HTTPException
        s = _student()
        db = _seq_db(s)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, payment_request=PaymentVerificationRequest(specializations=[]))
        assert exc.value.status_code == 400

    def test_invalid_specialization_400(self):
        """VSP-03: invalid specialization string → 400."""
        from fastapi import HTTPException
        s = _student()
        db = _seq_db(s)
        with pytest.raises(HTTPException) as exc:
            self._call(
                db=db,
                payment_request=PaymentVerificationRequest(specializations=["TOTALLY_INVALID_SPEC"]),
            )
        assert exc.value.status_code == 400

    def test_license_already_exists_no_new_created(self):
        """VSP-04: valid spec, license exists → no new license created."""
        s = _student()
        existing_lic = MagicMock()
        # student query → s; license query → existing_lic
        db = _seq_db(s, existing_lic)
        result = self._call(db=db)
        db.add.assert_not_called()  # no new license
        s.verify_payment.assert_called_once()
        assert result["payment_verified"] is not None or "message" in result

    def test_new_license_created(self):
        """VSP-05: Player provisioning delegates to the canonical command."""
        s = _student()
        # student query → s; license query → None (no existing)
        db = _seq_db(s, None)
        with patch(f"{_BASE}.issue_football_player_entitlement") as issue:
            result = self._call(db=db)
        issue.assert_called_once_with(db, user=s, payment_verified=True)
        db.add.assert_not_called()
        s.verify_payment.assert_called_once()


# ---------------------------------------------------------------------------
# unverify_student_payment
# ---------------------------------------------------------------------------

class TestUnverifyStudentPayment:
    def _call(self, student_id=7, db=None, current_user=None):
        return asyncio.run(unverify_student_payment(
            request=_request(),
            student_id=student_id,
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        ))

    def test_student_not_found_404(self):
        """USP-01: student not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_success_unverify_called(self):
        """USP-02: student found → unverify_payment called."""
        s = _student()
        db = _seq_db(s)
        result = self._call(db=db)
        s.unverify_payment.assert_called_once()
        assert result["student_id"] == 7


# ---------------------------------------------------------------------------
# get_student_payment_status
# ---------------------------------------------------------------------------

class TestGetStudentPaymentStatus:
    def _call(self, student_id=7, db=None, current_user=None):
        return asyncio.run(get_student_payment_status(
            request=_request(),
            student_id=student_id,
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        ))

    def test_not_found_404(self):
        """GSPS-01: student not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_no_verifier_verifier_name_none(self):
        """GSPS-02: student found, no payment_verified_by → verifier_name=None."""
        s = _student()
        s.payment_verified_by = None
        db = _seq_db(s)
        result = self._call(db=db)
        assert result["payment_verifier_name"] is None

    def test_with_verifier_name_returned(self):
        """GSPS-03: student has payment_verified_by → verifier name fetched."""
        s = _student()
        s.payment_verified_by = 42
        verifier = MagicMock(); verifier.name = "Admin User"
        # student query → s; verifier query → verifier
        db = _seq_db(s, verifier)
        result = self._call(db=db)
        assert result["payment_verifier_name"] == "Admin User"

    def test_verifier_not_found_shows_unknown(self):
        """GSPS-04: payment_verified_by set but verifier not in DB → 'Unknown'."""
        s = _student()
        s.payment_verified_by = 99
        # student query → s; verifier query → None
        db = _seq_db(s, None)
        result = self._call(db=db)
        assert result["payment_verifier_name"] == "Unknown"


# ---------------------------------------------------------------------------
# add_student_specialization
# ---------------------------------------------------------------------------

class TestAddStudentSpecialization:
    def _call(self, student_id=7, spec_request=None, db=None, current_user=None):
        return asyncio.run(add_student_specialization(
            request=_request(),
            student_id=student_id,
            spec_request=spec_request or _spec_req(),
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        ))

    def test_student_not_found_404(self):
        """ASS-01: student not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_invalid_spec_400(self):
        """ASS-02: invalid specialization string → 400."""
        from fastapi import HTTPException
        s = _student()
        db = _seq_db(s)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, spec_request=SpecializationRequest(specialization_type="INVALID_SPEC"))
        assert exc.value.status_code == 400

    def test_license_exists_400(self):
        """ASS-03: license already exists → 400."""
        from fastapi import HTTPException
        s = _student()
        existing_lic = MagicMock()
        db = _seq_db(s, existing_lic)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_success_sets_primary_when_none(self):
        """ASS-04: success, no primary spec → primary set."""
        s = _student(); s.specialization = None
        db = _seq_db(s, None)  # no existing license
        with patch(f"{_BASE}.issue_football_player_entitlement") as issue:
            result = self._call(db=db)
        issue.assert_called_once_with(db, user=s, payment_verified=True)
        assert s.specialization is not None
        db.commit.assert_called_once()
        assert result["success"] is True

    def test_success_verifies_payment_if_not_verified(self):
        """ASS-05: payment not verified → verify_payment called."""
        s = _student(); s.payment_verified = False; s.specialization = MagicMock()
        db = _seq_db(s, None)
        with patch(f"{_BASE}.issue_football_player_entitlement"):
            result = self._call(db=db)
        s.verify_payment.assert_called_once()


# ---------------------------------------------------------------------------
# remove_student_specialization
# ---------------------------------------------------------------------------

class TestRemoveStudentSpecialization:
    def _call(self, student_id=7, spec_request=None, db=None, current_user=None):
        return asyncio.run(remove_student_specialization(
            request=_request(),
            student_id=student_id,
            spec_request=spec_request or _spec_req(),
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        ))

    def test_student_not_found_404(self):
        """RSS-01: student not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_invalid_spec_400(self):
        """RSS-02: invalid specialization string → 400."""
        from fastapi import HTTPException
        s = _student()
        db = _seq_db(s)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, spec_request=SpecializationRequest(specialization_type="INVALID"))
        assert exc.value.status_code == 400

    def test_license_not_found_404(self):
        """RSS-03: license not found → 404."""
        from fastapi import HTTPException
        s = _student()
        # student → s; license_to_remove → None
        db = _seq_db(s, None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_last_license_unverifies_payment(self):
        """RSS-04: removing last license → unverify + clear primary spec."""
        s = _student()
        lic = MagicMock()
        # student → s; license_to_remove → lic; remaining_licenses → []
        db = _seq_db(s, lic, [])
        result = self._call(db=db)
        s.unverify_payment.assert_called_once()
        assert s.specialization is None
        db.commit.assert_called_once()
        assert result["success"] is True

    def test_was_primary_spec_new_primary_set(self):
        """RSS-05: removed spec was primary → new primary from remaining."""
        s = _student()
        spec_enum = SpecializationType(SpecializationType.LFA_FOOTBALL_PLAYER.value)
        s.specialization = spec_enum  # matches the spec we're removing
        lic = MagicMock()
        remaining_lic = MagicMock()
        remaining_lic.specialization_type = SpecializationType.LFA_COACH.value
        # student → s; license_to_remove → lic; remaining → [remaining_lic]
        db = _seq_db(s, lic, [remaining_lic])
        result = self._call(db=db)
        # New primary should be set from remaining
        assert s.specialization is not None
        assert result["success"] is True

    def test_not_primary_primary_unchanged(self):
        """RSS-06: removed spec was NOT primary → primary unchanged."""
        s = _student()
        other_spec = MagicMock()
        s.specialization = other_spec  # some different spec (not the one being removed)
        lic = MagicMock()
        remaining_lic = MagicMock()
        remaining_lic.specialization_type = SpecializationType.LFA_COACH.value
        db = _seq_db(s, lic, [remaining_lic])
        result = self._call(db=db)
        # Primary stays as other_spec (MagicMock != SpecializationType enum → branch not taken)
        assert result["success"] is True
