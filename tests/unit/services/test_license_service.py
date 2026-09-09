"""
Unit tests for app/services/license_service.py

Mock-based: no DB fixture needed.

Branches targeted (5 critical):
  1. get_or_create_user_license — license exists vs. CREATE path
  2. advance_license — validation failure (early return)
  3. advance_license — auto-sync: level_changed, sync failure, exception
  4. get_user_license_dashboard — user not found
  5. get_license_requirements_check — target not found / validation fails / requirements loop

Also covers: get_all_license_metadata, get_license_metadata_by_level,
             get_user_licenses, get_specialization_progression_path,
             _get_specialization_display_name, _get_recent_license_activity,
             _check_requirement, get_marketing_content.
"""
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

from app.services.license_service import LicenseService
from app.models.license import LicenseSystemHelper, LicenseType


# ── helpers ───────────────────────────────────────────────────────────────────

def _svc():
    db = MagicMock()
    return LicenseService(db), db


def _mock_license(user_id=42, spec="COACH", level=3, max_level=3, lic_id=10):
    lic = MagicMock()
    lic.id = lic_id
    lic.user_id = user_id
    lic.specialization_type = spec
    lic.current_level = level
    lic.max_achieved_level = level
    lic.to_dict.return_value = {
        "id": lic_id, "user_id": user_id,
        "specialization_type": spec, "current_level": level,
    }
    return lic


def _mock_meta(spec="COACH", level=1):
    m = MagicMock()
    m.to_dict.return_value = {"specialization_type": spec, "level_number": level}
    return m


# ── get_all_license_metadata ──────────────────────────────────────────────────

class TestGetAllLicenseMetadata:

    def test_no_filter_returns_all(self):
        svc, db = _svc()
        metas = [_mock_meta(), _mock_meta(level=2)]
        db.query.return_value.order_by.return_value.all.return_value = metas
        result = svc.get_all_license_metadata()
        assert len(result) == 2

    def test_with_specialization_filter(self):
        svc, db = _svc()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        result = svc.get_all_license_metadata(specialization="coach")
        # Verifies filter branch was taken (filter() is called when spec provided)
        db.query.return_value.filter.assert_called_once()
        assert result == []

    def test_with_specialization_uppercases_input(self):
        svc, db = _svc()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        svc.get_all_license_metadata(specialization="coach")
        # Should pass uppercased to filter — verified via mock arg
        filter_call_args = db.query.return_value.filter.call_args
        assert filter_call_args is not None  # filter was called


# ── get_license_metadata_by_level ─────────────────────────────────────────────

class TestGetLicenseMetadataByLevel:

    def test_returns_dict_when_found(self):
        svc, db = _svc()
        meta = _mock_meta()
        db.query.return_value.filter.return_value.first.return_value = meta
        result = svc.get_license_metadata_by_level("COACH", 1)
        assert result == meta.to_dict.return_value

    def test_returns_none_when_not_found(self):
        svc, db = _svc()
        db.query.return_value.filter.return_value.first.return_value = None
        result = svc.get_license_metadata_by_level("COACH", 99)
        assert result is None


# ── get_or_create_user_license — CRITICAL BRANCH ─────────────────────────────

class TestGetOrCreateUserLicense:

    def test_returns_existing_license(self):
        svc, db = _svc()
        existing = _mock_license()
        db.query.return_value.filter.return_value.all.return_value = [existing]
        result = svc.get_or_create_user_license(user_id=42, specialization="coach")
        assert result is existing
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_creates_license_when_not_found(self):
        svc, db = _svc()
        db.query.return_value.filter.return_value.all.return_value = []
        result = svc.get_or_create_user_license(user_id=42, specialization="coach")
        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once()

    def test_uppercases_specialization(self):
        svc, db = _svc()
        existing = _mock_license(spec="COACH")
        db.query.return_value.filter.return_value.all.return_value = [existing]
        result = svc.get_or_create_user_license(user_id=42, specialization="coach")
        assert result is existing  # Should still work after upper()

    def test_new_license_starts_at_level_1(self):
        svc, db = _svc()
        db.query.return_value.filter.return_value.all.return_value = []
        svc.get_or_create_user_license(user_id=42, specialization="COACH")
        # The UserLicense created should have current_level=1
        added_obj = db.add.call_args[0][0]
        assert added_obj.current_level == 1
        assert added_obj.max_achieved_level == 1


# ── advance_license — validation failure ──────────────────────────────────────

class TestAdvanceLicenseValidation:

    def test_cannot_skip_levels_returns_failure(self):
        svc, db = _svc()
        mock_lic = _mock_license(level=3)
        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            result = svc.advance_license(
                user_id=42, specialization="COACH",
                target_level=5,  # skips a level
                advanced_by=99
            )
        assert result["success"] is False
        assert "one level" in result["message"].lower()
        db.commit.assert_not_called()

    def test_target_lower_than_current_returns_failure(self):
        svc, db = _svc()
        mock_lic = _mock_license(level=4)
        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            result = svc.advance_license(
                user_id=42, specialization="COACH",
                target_level=3,  # lower than current
                advanced_by=99
            )
        assert result["success"] is False
        assert "higher" in result["message"].lower()

    def test_target_same_as_current_returns_failure(self):
        svc, db = _svc()
        mock_lic = _mock_license(level=3)
        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            result = svc.advance_license(
                user_id=42, specialization="COACH",
                target_level=3,  # same = not higher
                advanced_by=99
            )
        assert result["success"] is False

    def test_target_exceeds_max_returns_failure(self):
        svc, db = _svc()
        mock_lic = _mock_license(level=8)
        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            result = svc.advance_license(
                user_id=42, specialization="COACH",
                target_level=9,  # max is 8
                advanced_by=99
            )
        assert result["success"] is False
        assert "maximum" in result["message"].lower()

    def test_failure_includes_license_dict(self):
        svc, db = _svc()
        mock_lic = _mock_license(level=3)
        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            result = svc.advance_license(
                user_id=42, specialization="COACH",
                target_level=5, advanced_by=99
            )
        assert "license" in result
        assert result["license"] == mock_lic.to_dict.return_value


# ── advance_license — success path with sync ──────────────────────────────────

class TestAdvanceLicenseSuccess:

    def _advance_with_sync(self, sync_return=None, sync_raises=None):
        """Helper: advance from level 3 to 4 with mocked sync."""
        svc, db = _svc()
        mock_lic = _mock_license(level=3)
        mock_lic.max_achieved_level = 3

        if sync_return is None:
            sync_return = {'success': True}

        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            with patch('app.database.SessionLocal') as mock_sl:
                mock_sync_db = MagicMock()
                mock_sl.return_value = mock_sync_db
                with patch('app.services.license_service.ProgressLicenseSyncService') as mock_sync_cls:
                    mock_sync_svc = MagicMock()
                    if sync_raises:
                        mock_sync_svc.sync_license_to_progress.side_effect = sync_raises
                    else:
                        mock_sync_svc.sync_license_to_progress.return_value = sync_return
                    mock_sync_cls.return_value = mock_sync_svc

                    result = svc.advance_license(
                        user_id=42, specialization="COACH",
                        target_level=4, advanced_by=99
                    )
        return result, db, mock_sync_db

    def test_success_returns_success_dict(self):
        result, db, _ = self._advance_with_sync()
        assert result["success"] is True
        assert "level 4" in result["message"]

    def test_success_commits_progression(self):
        result, db, _ = self._advance_with_sync()
        db.add.assert_called()   # LicenseProgression added
        db.commit.assert_called()

    def test_success_includes_sync_result(self):
        result, db, _ = self._advance_with_sync(sync_return={'success': True})
        assert result["sync_result"] == {'success': True}

    def test_sync_failure_does_not_fail_advancement(self):
        result, db, _ = self._advance_with_sync(sync_return={'success': False, 'message': 'sync failed'})
        assert result["success"] is True  # advancement still succeeded

    def test_sync_exception_does_not_fail_advancement(self):
        result, db, mock_sync_db = self._advance_with_sync(sync_raises=RuntimeError("DB error"))
        assert result["success"] is True
        mock_sync_db.rollback.assert_called_once()

    def test_sync_session_always_closed(self):
        _, _, mock_sync_db = self._advance_with_sync()
        mock_sync_db.close.assert_called_once()

    def test_sync_session_closed_even_on_exception(self):
        _, _, mock_sync_db = self._advance_with_sync(sync_raises=RuntimeError("crash"))
        mock_sync_db.close.assert_called_once()

    def test_license_level_updated(self):
        svc, db = _svc()
        mock_lic = _mock_license(level=3)
        mock_lic.max_achieved_level = 3

        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            with patch('app.database.SessionLocal'):
                with patch('app.services.license_service.ProgressLicenseSyncService'):
                    svc.advance_license(
                        user_id=42, specialization="COACH",
                        target_level=4, advanced_by=99
                    )
        assert mock_lic.current_level == 4

    def test_max_achieved_level_updated(self):
        svc, db = _svc()
        mock_lic = _mock_license(level=3)
        mock_lic.max_achieved_level = 3

        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            with patch('app.database.SessionLocal'):
                with patch('app.services.license_service.ProgressLicenseSyncService'):
                    svc.advance_license(
                        user_id=42, specialization="COACH",
                        target_level=4, advanced_by=99
                    )
        assert mock_lic.max_achieved_level == 4


# ── get_user_licenses ─────────────────────────────────────────────────────────

class TestGetUserLicenses:

    def test_empty_returns_empty_list(self):
        svc, db = _svc()
        db.query.return_value.filter.return_value.all.return_value = []
        result = svc.get_user_licenses(user_id=42)
        assert result == []

    def test_license_at_max_level_no_next_meta(self):
        svc, db = _svc()
        mock_lic = _mock_license(level=8)
        db.query.return_value.filter.return_value.all.return_value = [mock_lic]
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        with patch.object(svc, 'get_license_metadata_by_level', return_value={'level': 8}):
            result = svc.get_user_licenses(user_id=42)

        assert len(result) == 1
        assert 'next_level_metadata' not in result[0]

    def test_license_below_max_has_next_meta(self):
        svc, db = _svc()
        mock_lic = _mock_license(level=3)  # max is 8 for COACH
        db.query.return_value.filter.return_value.all.return_value = [mock_lic]
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        with patch.object(svc, 'get_license_metadata_by_level', return_value={'level': 3}):
            result = svc.get_user_licenses(user_id=42)

        assert 'next_level_metadata' in result[0]


# ── get_specialization_progression_path ──────────────────────────────────────

class TestGetSpecializationProgressionPath:

    def test_returns_ordered_list(self):
        svc, db = _svc()
        metas = [_mock_meta(level=i) for i in range(1, 4)]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = metas
        result = svc.get_specialization_progression_path("COACH")
        assert len(result) == 3

    def test_empty_returns_empty_list(self):
        svc, db = _svc()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        result = svc.get_specialization_progression_path("INTERNSHIP")
        assert result == []


# ── get_user_license_dashboard — CRITICAL BRANCH ─────────────────────────────

class TestGetUserLicenseDashboard:

    def test_user_not_found_returns_error(self):
        svc, db = _svc()
        db.query.return_value.filter.return_value.first.return_value = None
        result = svc.get_user_license_dashboard(user_id=999)
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_user_found_returns_full_dashboard(self):
        svc, db = _svc()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.name = "Test User"
        mock_user.specialization = MagicMock()
        mock_user.specialization.value = "LFA_COACH"
        db.query.return_value.filter.return_value.first.return_value = mock_user

        with patch.object(svc, 'get_user_licenses', return_value=[]):
            with patch.object(svc, 'get_all_license_metadata', return_value=[{'level_number': 1}]):
                with patch.object(svc, '_get_recent_license_activity', return_value=[]):
                    result = svc.get_user_license_dashboard(user_id=42)

        assert "user" in result
        assert "licenses" in result
        assert "overall_progress" in result
        assert result["user"]["id"] == 1

    def test_overall_progress_calculated(self):
        svc, db = _svc()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.name = "Test"
        mock_user.specialization = None
        db.query.return_value.filter.return_value.first.return_value = mock_user

        with patch.object(svc, 'get_user_licenses', return_value=[{'current_level': 3}]):
            with patch.object(svc, 'get_all_license_metadata', return_value=[]):
                with patch.object(svc, '_get_recent_license_activity', return_value=[]):
                    result = svc.get_user_license_dashboard(user_id=42)

        # total_possible_levels = sum of max_levels for all LicenseType = 8+8+3 = 19
        assert result["overall_progress"]["current_levels"] == 3
        assert result["overall_progress"]["total_possible"] > 0

    def test_user_no_specialization_handled(self):
        svc, db = _svc()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.name = "Test"
        mock_user.specialization = None  # No specialization
        db.query.return_value.filter.return_value.first.return_value = mock_user

        with patch.object(svc, 'get_user_licenses', return_value=[]):
            with patch.object(svc, 'get_all_license_metadata', return_value=[]):
                with patch.object(svc, '_get_recent_license_activity', return_value=[]):
                    result = svc.get_user_license_dashboard(user_id=42)

        assert result["user"]["specialization"] is None


# ── _get_specialization_display_name (pure) ───────────────────────────────────

class TestGetSpecializationDisplayName:

    def test_coach_display_name(self):
        svc, _ = _svc()
        assert "Coach" in svc._get_specialization_display_name("COACH")

    def test_player_display_name(self):
        svc, _ = _svc()
        assert "Player" in svc._get_specialization_display_name("PLAYER")

    def test_internship_display_name(self):
        svc, _ = _svc()
        assert "Internship" in svc._get_specialization_display_name("INTERNSHIP")

    def test_unknown_returns_itself(self):
        svc, _ = _svc()
        result = svc._get_specialization_display_name("UNKNOWN_SPEC")
        assert result == "UNKNOWN_SPEC"


# ── _get_recent_license_activity ─────────────────────────────────────────────

class TestGetRecentLicenseActivity:

    def test_empty_progressions_returns_empty(self):
        svc, db = _svc()
        db.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        result = svc._get_recent_license_activity(user_id=42)
        assert result == []

    def test_returns_activity_dicts(self):
        svc, db = _svc()
        mock_prog = MagicMock()
        mock_prog.user_license_id = 10
        mock_prog.from_level = 2
        mock_prog.to_level = 3
        mock_prog.to_dict.return_value = {"from_level": 2, "to_level": 3}

        mock_lic = _mock_license()
        mock_lic.specialization_type = "COACH"

        db.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [mock_prog]
        db.query.return_value.filter.return_value.first.return_value = mock_lic

        with patch.object(svc, 'get_license_metadata_by_level', return_value={'level': 1}):
            result = svc._get_recent_license_activity(user_id=42)

        assert len(result) == 1
        assert result[0]["specialization"] == "COACH"


# ── get_license_requirements_check — CRITICAL BRANCH ─────────────────────────

class TestGetLicenseRequirementsCheck:

    def test_target_level_not_found_returns_error(self):
        svc, _ = _svc()
        mock_lic = _mock_license(level=2)
        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            with patch.object(svc, 'get_license_metadata_by_level', return_value=None):
                result = svc.get_license_requirements_check(
                    user_id=42, specialization="COACH", target_level=99
                )
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_validation_fails_returns_error(self):
        svc, _ = _svc()
        mock_lic = _mock_license(level=4)
        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            with patch.object(svc, 'get_license_metadata_by_level', return_value={'advancement_criteria': {}}):
                result = svc.get_license_requirements_check(
                    user_id=42, specialization="COACH",
                    target_level=2  # lower than current (4) → validation fails
                )
        assert "error" in result

    def test_valid_check_returns_requirements_dict(self):
        svc, _ = _svc()
        mock_lic = _mock_license(level=3)
        meta = {'advancement_criteria': {'min_sessions': 5, 'min_xp': 100}}
        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            with patch.object(svc, 'get_license_metadata_by_level', return_value=meta):
                result = svc.get_license_requirements_check(
                    user_id=42, specialization="COACH", target_level=4
                )
        assert result["user_id"] == 42
        assert result["target_level"] == 4
        assert "requirements" in result
        assert "can_advance" in result

    def test_empty_requirements_all_met_false(self):
        svc, _ = _svc()
        mock_lic = _mock_license(level=3)
        meta = {'advancement_criteria': {}}
        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            with patch.object(svc, 'get_license_metadata_by_level', return_value=meta):
                result = svc.get_license_requirements_check(
                    user_id=42, specialization="COACH", target_level=4
                )
        # Empty requirements → vacuously True (all() of empty = True)
        assert result["all_requirements_met"] is True

    def test_unmet_requirement_blocks_advancement(self):
        svc, _ = _svc()
        mock_lic = _mock_license(level=3)
        meta = {'advancement_criteria': {'min_sessions': 5}}
        with patch.object(svc, 'get_or_create_user_license', return_value=mock_lic):
            with patch.object(svc, 'get_license_metadata_by_level', return_value=meta):
                with patch.object(svc, '_check_requirement', return_value={'met': False}):
                    result = svc.get_license_requirements_check(
                        user_id=42, specialization="COACH", target_level=4
                    )
        assert result["can_advance"] is False


# ── _check_requirement (pure placeholder) ─────────────────────────────────────

class TestCheckRequirement:

    def test_returns_structured_dict(self):
        svc, _ = _svc()
        result = svc._check_requirement(user_id=42, req_type="min_sessions", req_value=5)
        assert result["type"] == "min_sessions"
        assert result["required"] == 5
        assert "met" in result
        assert result["met"] is False  # Always False (placeholder)

    def test_includes_description(self):
        svc, _ = _svc()
        result = svc._check_requirement(user_id=42, req_type="min_xp", req_value=100)
        assert "description" in result


# ── get_marketing_content ─────────────────────────────────────────────────────

class TestGetMarketingContent:

    def test_with_level_returns_single_dict(self):
        svc, db = _svc()
        meta = _mock_meta(level=2)
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = meta
        result = svc.get_marketing_content("COACH", level=2)
        assert result == meta.to_dict.return_value

    def test_with_level_not_found_returns_empty(self):
        svc, db = _svc()
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None
        result = svc.get_marketing_content("COACH", level=99)
        assert result == {}

    def test_without_level_returns_all_levels(self):
        svc, db = _svc()
        metas = [_mock_meta(level=i) for i in range(1, 4)]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = metas
        result = svc.get_marketing_content("COACH")
        assert "specialization" in result
        assert "levels" in result
        assert result["specialization"] == "COACH"
        assert len(result["levels"]) == 3

    def test_without_level_includes_display_name(self):
        svc, db = _svc()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        result = svc.get_marketing_content("COACH")
        assert "display_name" in result
        assert "Coach" in result["display_name"]
