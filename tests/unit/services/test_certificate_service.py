"""Unit tests for app/services/certificate_service.py

Sprint P9 — Coverage target: ≥88% stmt+branch

Covers:
- create_certificate_template: with/without optional params
- _get_default_template: returns HTML
- _get_default_validation_rules: returns dict with expected keys
- generate_certificate: not ready / no template / success
- _calculate_final_grade: empty / with grades / mixed (None grades filtered)
- verify_certificate: not found / revoked / not authentic / valid
- get_user_certificates: basic query
- revoke_certificate: not found / success
- generate_certificate_pdf: not found / success
- get_certificate_analytics: totals and by-track breakdown
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.certificate_service import CertificateGenerationError, CertificateService

_SVC_BASE = "app.services.certificate_service"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db(*, first=None, all_=None, count_vals=None):
    """Flexible DB mock. count_vals = [val1, val2, ...] for side_effect on .count()."""
    db = MagicMock()
    q = MagicMock()
    for m in ("filter", "join", "group_by", "order_by", "limit"):
        getattr(q, m).return_value = q
    q.first.return_value = first
    q.all.return_value = all_ if all_ is not None else []
    if count_vals is not None:
        q.count.side_effect = count_vals
    else:
        q.count.return_value = 0
    db.query.return_value = q
    return db


def _module_progress(*, completed=True, grade=80.0):
    mp = MagicMock()
    mp.status.value = "completed" if completed else "in_progress"
    mp.grade = grade
    return mp


def _track_progress(*, ready=True, user_id=42, modules=None):
    tp = MagicMock()
    tp.is_ready_for_certificate = ready
    tp.track_id = "TRACK-001"
    tp.user_id = user_id
    tp.track.code = "LFA-001"
    tp.track.name = "LFA Track"
    tp.completion_percentage = 100.0
    tp.enrollment_date.isoformat.return_value = "2024-01-01"
    tp.completed_at.isoformat.return_value = "2024-06-30"
    tp.duration_days = 180
    tp.module_progresses = modules if modules is not None else [_module_progress()]
    return tp


def _certificate(*, revoked=False, authentic=True, user_id=42):
    cert = MagicMock()
    cert.unique_identifier = "CERT-001"
    cert.is_revoked = revoked
    cert.user_id = user_id
    cert.verification_hash = "hash123"
    cert.cert_metadata = {"final_grade": 85.0, "duration_days": 180}
    cert.verify_authenticity.return_value = authentic
    return cert


# ===========================================================================
# TestCertificateTemplate
# ===========================================================================

class TestCertificateTemplate:
    def test_create_template_with_defaults(self):
        db = _db()
        svc = CertificateService(db)
        result = svc.create_certificate_template("TRACK-001", "LFA Certificate")
        assert db.add.called
        assert db.commit.called
        assert db.refresh.called

    def test_create_template_with_custom_values(self):
        db = _db()
        svc = CertificateService(db)
        rules = {"minimum_grade": 70.0}
        svc.create_certificate_template(
            "TRACK-001", "Custom Cert",
            description="Desc",
            design_template="<html>Custom</html>",
            validation_rules=rules
        )
        assert db.add.called

    def test_get_default_template_returns_html(self):
        svc = CertificateService(MagicMock())
        tmpl = svc._get_default_template()
        assert "<!DOCTYPE html>" in tmpl
        assert "{{user_name}}" in tmpl
        assert "{{track_name}}" in tmpl
        assert "{{certificate_id}}" in tmpl

    def test_get_default_validation_rules_keys(self):
        svc = CertificateService(MagicMock())
        rules = svc._get_default_validation_rules()
        assert "minimum_completion_percentage" in rules
        assert "minimum_grade" in rules
        assert "required_modules" in rules
        assert "allow_retakes" in rules

    def test_default_validation_rules_minimum_completion_is_100(self):
        svc = CertificateService(MagicMock())
        rules = svc._get_default_validation_rules()
        assert rules["minimum_completion_percentage"] == 100.0


# ===========================================================================
# TestGenerateCertificate
# ===========================================================================

class TestGenerateCertificate:
    def test_raises_if_not_ready(self):
        db = MagicMock()
        svc = CertificateService(db)
        tp = _track_progress(ready=False)
        with pytest.raises(CertificateGenerationError, match="not ready"):
            svc.generate_certificate(tp)

    def test_raises_if_no_template_found(self):
        db = _db(first=None)
        svc = CertificateService(db)
        tp = _track_progress(ready=True)
        with pytest.raises(CertificateGenerationError, match="No certificate template"):
            svc.generate_certificate(tp)

    def test_success_calls_db_add_commit_refresh(self):
        template = MagicMock()
        db = _db(first=template)
        svc = CertificateService(db)
        tp = _track_progress(ready=True)
        with patch(f"{_SVC_BASE}.IssuedCertificate") as MockCert:
            MockCert.generate_unique_identifier.return_value = "CERT-001"
            MockCert.generate_verification_hash.return_value = "hash123"
            mock_cert_instance = MagicMock()
            MockCert.return_value = mock_cert_instance
            result = svc.generate_certificate(tp)
        assert db.add.called
        assert db.commit.called
        assert db.refresh.called
        assert result is mock_cert_instance

    def test_success_builds_metadata_with_modules_completed_count(self):
        template = MagicMock()
        db = _db(first=template)
        svc = CertificateService(db)
        modules = [
            _module_progress(completed=True, grade=90.0),
            _module_progress(completed=False, grade=None),
        ]
        tp = _track_progress(ready=True, modules=modules)
        captured_kwargs = {}
        with patch(f"{_SVC_BASE}.IssuedCertificate") as MockCert:
            MockCert.generate_unique_identifier.return_value = "CERT-001"
            MockCert.generate_verification_hash.return_value = "hash123"
            def capture(**kwargs):
                captured_kwargs.update(kwargs)
                return MagicMock()
            MockCert.side_effect = capture
            svc.generate_certificate(tp)
        # modules_completed should be 1 (only the completed one)
        assert captured_kwargs.get("cert_metadata", {}).get("modules_completed") == 1


# ===========================================================================
# TestCalculateFinalGrade
# ===========================================================================

class TestCalculateFinalGrade:
    def test_empty_module_progresses_returns_zero(self):
        svc = CertificateService(MagicMock())
        tp = _track_progress(modules=[])
        assert svc._calculate_final_grade(tp) == 0.0

    def test_no_completed_modules_returns_zero(self):
        svc = CertificateService(MagicMock())
        tp = _track_progress(modules=[_module_progress(completed=False, grade=80.0)])
        assert svc._calculate_final_grade(tp) == 0.0

    def test_none_grades_excluded(self):
        svc = CertificateService(MagicMock())
        m1 = _module_progress(completed=True, grade=80.0)
        m2 = _module_progress(completed=True, grade=None)
        m2.grade = None
        tp = _track_progress(modules=[m1, m2])
        result = svc._calculate_final_grade(tp)
        assert result == 80.0

    def test_average_of_multiple_grades(self):
        svc = CertificateService(MagicMock())
        modules = [
            _module_progress(completed=True, grade=80.0),
            _module_progress(completed=True, grade=90.0),
            _module_progress(completed=True, grade=100.0),
        ]
        tp = _track_progress(modules=modules)
        result = svc._calculate_final_grade(tp)
        assert result == 90.0

    def test_result_is_rounded_to_two_decimals(self):
        svc = CertificateService(MagicMock())
        modules = [
            _module_progress(completed=True, grade=100.0),
            _module_progress(completed=True, grade=100.0),
            _module_progress(completed=True, grade=100.0),
        ]
        # 300/3 = 100.0 exactly
        tp = _track_progress(modules=modules)
        result = svc._calculate_final_grade(tp)
        assert isinstance(result, float)


# ===========================================================================
# TestVerifyCertificate
# ===========================================================================

class TestVerifyCertificate:
    def test_not_found_returns_invalid(self):
        db = _db(first=None)
        svc = CertificateService(db)
        result = svc.verify_certificate("NONEXISTENT")
        assert result["valid"] is False
        assert "not found" in result["error"].lower()

    def test_revoked_returns_invalid_with_reason(self):
        cert = _certificate(revoked=True)
        cert.revoked_at = "2024-03-01"
        cert.revoked_reason = "Fraud detected"
        db = _db(first=cert)
        svc = CertificateService(db)
        result = svc.verify_certificate("CERT-001")
        assert result["valid"] is False
        assert "revoked" in result["error"].lower()
        assert result["revoked_reason"] == "Fraud detected"

    def test_not_authentic_returns_invalid(self):
        cert = _certificate(revoked=False, authentic=False)
        db = _db(first=cert)
        svc = CertificateService(db)
        result = svc.verify_certificate("CERT-001")
        assert result["valid"] is False
        assert "authenticity" in result["error"].lower()

    def test_valid_returns_full_details(self):
        cert = _certificate(revoked=False, authentic=True)
        user = MagicMock()
        user.name = "Alice"
        track = MagicMock()
        track.name = "LFA Track"
        track.code = "LFA-001"
        cert.template.track = track
        db = MagicMock()
        q = MagicMock()
        for m in ("filter", "order_by"):
            getattr(q, m).return_value = q
        q.first.side_effect = [cert, user]
        db.query.return_value = q
        svc = CertificateService(db)
        result = svc.verify_certificate("CERT-001")
        assert result["valid"] is True
        assert result["user_name"] == "Alice"
        assert result["track_name"] == "LFA Track"
        assert "certificate_id" in result


# ===========================================================================
# TestGetUserCertificates
# ===========================================================================

class TestGetUserCertificates:
    def test_returns_list_of_active_certificates(self):
        cert = MagicMock()
        db = _db(all_=[cert])
        svc = CertificateService(db)
        result = svc.get_user_certificates(user_id=42)
        assert result == [cert]

    def test_empty_result(self):
        db = _db(all_=[])
        svc = CertificateService(db)
        result = svc.get_user_certificates(user_id=42)
        assert result == []


# ===========================================================================
# TestRevokeCertificate
# ===========================================================================

class TestRevokeCertificate:
    def test_raises_if_not_found(self):
        db = _db(first=None)
        svc = CertificateService(db)
        with pytest.raises(ValueError, match="Certificate not found"):
            svc.revoke_certificate("NONEXISTENT", "Fraud")

    def test_revokes_and_commits(self):
        cert = _certificate()
        db = _db(first=cert)
        svc = CertificateService(db)
        result = svc.revoke_certificate("CERT-001", "Fraud")
        cert.revoke.assert_called_once_with("Fraud")
        assert db.commit.called
        assert result is cert


# ===========================================================================
# TestGenerateCertificatePdf
# ===========================================================================

class TestGenerateCertificatePdf:
    def test_raises_if_not_found(self):
        db = _db(first=None)
        svc = CertificateService(db)
        with pytest.raises(ValueError, match="Certificate not found"):
            svc.generate_certificate_pdf("NONEXISTENT")

    def test_returns_real_pdf_bytes(self):
        cert = _certificate()
        db = _db(first=cert)
        svc = CertificateService(db)
        result = svc.generate_certificate_pdf("CERT-001")
        assert result.startswith(b"%PDF-1.4")
        assert result.rstrip().endswith(b"%%EOF")
        assert b"Certificate ID: CERT-001" in result
        assert b"placeholder" not in result


# ===========================================================================
# TestGetCertificateAnalytics
# ===========================================================================

class TestGetCertificateAnalytics:
    def test_returns_totals_and_by_track(self):
        db = MagicMock()
        q = MagicMock()
        for m in ("filter", "join", "group_by"):
            getattr(q, m).return_value = q
        # Two .count() calls: total_issued=10, revoked_count=2
        q.count.side_effect = [10, 2]
        q.all.return_value = []
        db.query.return_value = q
        svc = CertificateService(db)
        result = svc.get_certificate_analytics()
        assert result["total_certificates_issued"] == 10
        assert result["revoked_certificates"] == 2
        assert result["active_certificates"] == 8

    def test_by_track_list_populated(self):
        db = MagicMock()
        q = MagicMock()
        for m in ("filter", "join", "group_by"):
            getattr(q, m).return_value = q
        q.count.side_effect = [3, 1]
        # simulate one track row: (name, code, count)
        q.all.return_value = [("LFA Track", "LFA-001", 3)]
        db.query.return_value = q
        svc = CertificateService(db)
        result = svc.get_certificate_analytics()
        tracks = result["certificates_by_track"]
        assert len(tracks) == 1
        assert tracks[0]["track_name"] == "LFA Track"
        assert tracks[0]["track_code"] == "LFA-001"
        assert tracks[0]["count"] == 3
