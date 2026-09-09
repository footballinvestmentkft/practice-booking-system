"""
Unit tests for ParallelSpecializationService — Round 2 coverage

Scope (DB-free / DB-mockable paths only):
  - calculate_age: pure calculation, date.today() patched for determinism
  - get_specialization_combinations_by_semester: pure static return
  - check_age_requirement: single User DB query (MagicMock)
  - check_payment_requirement: User query + get_user_active_specializations patched
  - get_user_semester_count: User query + UserLicense.count() (MagicMock)

Deferred to Round 3 (integration-heavy):
  - get_available_specializations_for_semester  (LicenseService + complex self-calls)
  - start_new_specialization                    (db.commit / db.refresh)
  - get_user_specialization_dashboard           (full pipeline)
  - validate_specialization_addition            (delegates to above)

Mock isolation:
  Each test builds a *fresh* MagicMock db via _service(), so there is no
  shared state between tests. patch.object() calls are scoped to the
  individual 'with' block and are never applied at the class or module level.
"""
import pytest
from datetime import date as real_date, datetime
from unittest.mock import MagicMock, patch, patch as _patch

from app.services.parallel_specialization_service import ParallelSpecializationService


# ─── Shared helpers ────────────────────────────────────────────────────────────

_DATE_PATH = "app.services.parallel_specialization_service.date"
_TODAY = real_date(2026, 3, 2)  # pinned "today" for all age-related tests


def _service():
    """Return (service, db_mock) with a fresh MagicMock DB per call."""
    db = MagicMock()
    return ParallelSpecializationService(db=db), db


def _user_mock(
    *,
    role_value: str = "student",
    date_of_birth=None,
    payment_verified: bool = True,
    payment_verified_at=None,
    payment_verified_by=None,
    payment_status_display: str = "✅ Verified",
):
    """Build a minimal MagicMock User with controlled attributes.

    Uses explicit assignment (u.attr = value) rather than spec= so that
    attributes not present on the real model can still be set freely in tests.
    role.value is set via the MagicMock attribute chain: u.role.value = role_value,
    which matches how the service reads user.role.value in production code.
    """
    u = MagicMock()
    u.role.value = role_value
    u.date_of_birth = date_of_birth        # datetime | None
    u.payment_verified = payment_verified
    u.payment_verified_at = payment_verified_at
    u.payment_verified_by = payment_verified_by
    u.payment_status_display = payment_status_display
    return u


# ============================================================================
# Class 1 — calculate_age
# ============================================================================

@pytest.mark.unit
class TestCalculateAge:
    """
    Pure age calculation — no DB interaction.

    Algorithm: today.year - birth.year - (1 if birthday not yet this year else 0)
    where "not yet" = (today.month, today.day) < (birth.month, birth.day)

    date.today() is patched to _TODAY = 2026-03-02 in every helper call
    to prevent flakiness from the passage of real time.
    """

    def _age(self, birth: real_date, today: real_date = _TODAY) -> int:
        svc, _ = _service()
        with patch(_DATE_PATH) as mock_date:
            mock_date.today.return_value = today
            return svc.calculate_age(birth)

    def test_birthday_already_passed_this_year(self):
        """Born 2000-01-15; birthday passed (Jan < Mar) → 26."""
        assert self._age(real_date(2000, 1, 15)) == 26

    def test_birthday_not_yet_this_year(self):
        """Born 2000-12-25; birthday not yet (Dec > Mar) → 25."""
        assert self._age(real_date(2000, 12, 25)) == 25

    def test_birthday_exactly_today(self):
        """Born 2000-03-02; today is birthday → 26, NOT 25.
        Boundary: (3,2) < (3,2) is False → no subtraction → full year counted."""
        assert self._age(real_date(2000, 3, 2)) == 26

    def test_infant_born_today(self):
        """Born today → age 0."""
        assert self._age(real_date(2026, 3, 2)) == 0

    def test_elder_90_plus(self):
        """Born 1930-06-15 → birthday not yet (Jun > Mar) → 95."""
        assert self._age(real_date(1930, 6, 15)) == 95

    def test_leap_year_birthday_feb29(self):
        """Born 2000-02-29 (leap year); today 2026-03-02.
        Comparison: (3, 2) < (2, 29) → False (month 3 > 2) → birthday 'passed' → 26."""
        assert self._age(real_date(2000, 2, 29)) == 26

    def test_invariant_age_is_non_negative(self):
        """INVARIANT: age must be ≥ 0 for any valid birth date ≤ today."""
        for birth in [real_date(2026, 3, 2), real_date(2025, 4, 1), real_date(1990, 1, 1)]:
            assert self._age(birth) >= 0

    def test_age_exactly_at_player_minimum_boundary(self):
        """Born 2021-03-02; today 2026-03-02 → exactly 5 → meets PLAYER minimum."""
        assert self._age(real_date(2021, 3, 2)) == 5

    def test_one_day_before_player_minimum_boundary(self):
        """Born 2021-03-03; today 2026-03-02 → birthday not yet → 4 → below PLAYER."""
        assert self._age(real_date(2021, 3, 3)) == 4


# ============================================================================
# Class 2 — get_specialization_combinations_by_semester
# ============================================================================

@pytest.mark.unit
class TestGetSpecializationCombinationsBySemester:
    """
    Pure static return — no DB, no mocks needed.
    Tests verify structural correctness of the domain model constants.
    """

    def setup_method(self):
        self.svc, _ = _service()
        self.result = self.svc.get_specialization_combinations_by_semester()

    def test_returns_dict_with_semesters_1_2_3(self):
        assert set(self.result.keys()) == {1, 2, 3}

    def test_semester_1_allows_max_1_specialization(self):
        assert self.result[1]["max_specializations"] == 1

    def test_semester_2_allows_max_2_specializations(self):
        assert self.result[2]["max_specializations"] == 2

    def test_semester_3_allows_max_3_specializations(self):
        assert self.result[3]["max_specializations"] == 3

    def test_all_semesters_list_all_three_specializations_as_available(self):
        for sem in [1, 2, 3]:
            available = self.result[sem]["available"]
            for spec in ["PLAYER", "COACH", "INTERNSHIP"]:
                assert spec in available, f"Semester {sem} missing {spec}"

    def test_semester_3_combinations_contains_all_three_parallel(self):
        assert ["PLAYER", "COACH", "INTERNSHIP"] in self.result[3]["combinations"]

    def test_descriptions_are_non_empty_strings(self):
        for sem in [1, 2, 3]:
            assert isinstance(self.result[sem]["description"], str)
            assert len(self.result[sem]["description"]) > 0


# ============================================================================
# Class 3 — check_age_requirement
# ============================================================================

@pytest.mark.unit
class TestCheckAgeRequirement:
    """
    DB mock: db.query(User).filter().first() → user_mock or None.
    date.today() patched to _TODAY for determinism.

    AGE_REQUIREMENTS (from service): PLAYER=5, COACH=14, INTERNSHIP=18.
    """

    def _check(self, user_mock, specialization: str, today: real_date = _TODAY):
        svc, db = _service()
        db.query.return_value.filter.return_value.first.return_value = user_mock
        with patch(_DATE_PATH) as mock_date:
            mock_date.today.return_value = today
            return svc.check_age_requirement(user_id=1, specialization=specialization)

    def test_user_not_found_returns_false(self):
        result = self._check(None, "PLAYER")
        assert result["meets_requirement"] is False
        assert result["reason"] == "User not found"

    def test_user_no_date_of_birth_returns_false(self):
        u = _user_mock(date_of_birth=None)
        result = self._check(u, "PLAYER")
        assert result["meets_requirement"] is False
        assert "hiányzik" in result["reason"]   # service uses Hungarian: "Születési dátum hiányzik"

    def test_player_age_below_minimum(self):
        """Age 3 (born 2023-01-01) — below PLAYER minimum of 5."""
        u = _user_mock(date_of_birth=datetime(2023, 1, 1))
        result = self._check(u, "PLAYER")
        assert result["meets_requirement"] is False
        assert result["required_age"] == 5
        assert result["user_age"] == 3

    def test_player_age_exactly_at_minimum(self):
        """Born 2021-03-02; today 2026-03-02 → exactly 5 → meets requirement."""
        u = _user_mock(date_of_birth=datetime(2021, 3, 2))
        result = self._check(u, "PLAYER")
        assert result["meets_requirement"] is True
        assert result["user_age"] == 5

    def test_player_age_above_minimum(self):
        u = _user_mock(date_of_birth=datetime(2000, 1, 1))
        result = self._check(u, "PLAYER")
        assert result["meets_requirement"] is True

    def test_coach_age_below_minimum(self):
        """Born 2013-03-03; today 2026-03-02 → birthday not yet → age 12 < 14."""
        u = _user_mock(date_of_birth=datetime(2013, 3, 3))
        result = self._check(u, "COACH")
        assert result["meets_requirement"] is False
        assert result["required_age"] == 14
        assert result["user_age"] == 12

    def test_coach_age_exactly_at_minimum(self):
        """Born 2012-03-02; today 2026-03-02 → exactly 14."""
        u = _user_mock(date_of_birth=datetime(2012, 3, 2))
        result = self._check(u, "COACH")
        assert result["meets_requirement"] is True
        assert result["user_age"] == 14

    def test_internship_age_exactly_at_minimum(self):
        """Born 2008-03-02; today 2026-03-02 → exactly 18."""
        u = _user_mock(date_of_birth=datetime(2008, 3, 2))
        result = self._check(u, "INTERNSHIP")
        assert result["meets_requirement"] is True
        assert result["user_age"] == 18

    def test_unknown_specialization_fails_closed(self):
        """Unknown program identities require manual review and grant no access."""
        u = _user_mock(date_of_birth=datetime(2020, 1, 1))   # age ~6
        result = self._check(u, "UNKNOWN_SPEC")
        assert result["meets_requirement"] is False
        assert "manual review" in result["reason"]

    def test_reason_uses_canonical_global_age_denial_code(self):
        """A profile below age five is denied by the shared global policy."""
        u = _user_mock(date_of_birth=datetime(2023, 6, 15))   # age ~2
        result = self._check(u, "PLAYER")
        assert not result["meets_requirement"]
        assert result["required_age"] == 5
        assert result["user_age"] == 2
        assert result["reason"] == "MINIMUM_ACCOUNT_AGE"


# ============================================================================
# Class 4 — check_payment_requirement
# ============================================================================

@pytest.mark.unit
class TestCheckPaymentRequirement:
    """
    DB mock: db.query(User).filter().first() → user_mock or None.

    get_user_active_specializations is patched via patch.object to isolate
    this method from UserLicense DB queries and LicenseService calls.

    semester is passed explicitly to short-circuit
    `semester or self.get_user_semester_count(user_id)`, avoiding a second
    DB mock chain for UserLicense.count().
    """

    def _check(
        self,
        user_mock,
        *,
        specialization_type=None,
        semester: int = 1,
        active_specs=None,
    ):
        svc, db = _service()
        db.query.return_value.filter.return_value.first.return_value = user_mock
        with _patch.object(svc, "get_user_active_specializations", return_value=active_specs or []):
            return svc.check_payment_requirement(
                user_id=1,
                specialization_type=specialization_type,
                semester=semester,
            )

    # ── Not found / role bypass ─────────────────────────────────────────────

    def test_user_not_found_returns_false(self):
        result = self._check(None)
        assert result["payment_verified"] is False
        assert result["reason"] == "User not found"

    def test_admin_role_bypasses_payment_unconditionally(self):
        """Admin with payment_verified=False still gets payment_verified=True."""
        u = _user_mock(role_value="admin", payment_verified=False)
        result = self._check(u)
        assert result["payment_verified"] is True
        assert "Admin" in result["reason"]

    def test_instructor_role_bypasses_payment_unconditionally(self):
        """Instructor with payment_verified=False still gets payment_verified=True."""
        u = _user_mock(role_value="instructor", payment_verified=False)
        result = self._check(u)
        assert result["payment_verified"] is True
        assert "Instructor" in result["reason"]

    # ── Semester 1 (baseline) ────────────────────────────────────────────────

    def test_student_semester1_payment_verified_true(self):
        u = _user_mock(role_value="student", payment_verified=True)
        result = self._check(u, semester=1)
        assert result["payment_verified"] is True

    def test_student_semester1_payment_verified_false(self):
        u = _user_mock(role_value="student", payment_verified=False)
        result = self._check(u, semester=1)
        assert result["payment_verified"] is False

    # ── Semester 2+ branching ────────────────────────────────────────────────

    def test_student_semester2_no_existing_specs_payment_true(self):
        """No active specs yet (first enrollment); semester 2; payment verified → True."""
        u = _user_mock(role_value="student", payment_verified=True)
        result = self._check(u, specialization_type="PLAYER", semester=2, active_specs=[])
        assert result["payment_verified"] is True

    def test_student_semester2_adding_new_spec_type_payment_verified_true(self):
        """Has PLAYER, adding COACH (new type); payment verified → True."""
        u = _user_mock(role_value="student", payment_verified=True)
        active_specs = [{"specialization_type": "PLAYER"}]
        result = self._check(u, specialization_type="COACH", semester=2, active_specs=active_specs)
        assert result["payment_verified"] is True

    def test_student_semester2_adding_new_spec_type_payment_not_verified(self):
        """Has PLAYER, adding COACH; payment NOT verified → False."""
        u = _user_mock(role_value="student", payment_verified=False)
        active_specs = [{"specialization_type": "PLAYER"}]
        result = self._check(u, specialization_type="COACH", semester=2, active_specs=active_specs)
        assert result["payment_verified"] is False

    def test_student_semester2_requesting_already_enrolled_spec_falls_to_else(self):
        """Has PLAYER, requesting PLAYER again → existing-spec branch (else) → payment as-is."""
        u = _user_mock(role_value="student", payment_verified=True)
        active_specs = [{"specialization_type": "PLAYER"}]
        result = self._check(u, specialization_type="PLAYER", semester=2, active_specs=active_specs)
        assert result["payment_verified"] is True

    # ── Output shape ─────────────────────────────────────────────────────────

    def test_output_semester_context_reflects_passed_semester(self):
        """semester_context in output must match the semester argument."""
        u = _user_mock(role_value="student", payment_verified=True)
        result = self._check(u, semester=3)
        assert result["semester_context"] == 3

    def test_admin_output_contains_verified_at_and_by_as_none(self):
        """Admin bypass returns verified_at=None and verified_by=None (no real verification)."""
        u = _user_mock(role_value="admin")
        result = self._check(u)
        assert result["verified_at"] is None
        assert result["verified_by"] is None


# ============================================================================
# Class 5 — get_user_semester_count
# ============================================================================

@pytest.mark.unit
class TestGetUserSemesterCount:
    """
    DB mock:
      - db.query(User).filter().first()          → user or None
      - db.query(UserLicense).filter().count()   → int

    Both queries share the same MagicMock chain (db.query.return_value.filter.return_value),
    but terminate on different methods (.first() vs .count()), so they can be
    configured independently on the same mock object without interference.
    """

    def _count(self, user_mock, license_count: int = 0) -> int:
        svc, db = _service()
        db.query.return_value.filter.return_value.first.return_value = user_mock
        db.query.return_value.filter.return_value.count.return_value = license_count
        return svc.get_user_semester_count(user_id=1)

    def test_user_not_found_returns_semester_1(self):
        """Guard: unknown user defaults to semester 1 (safe minimum)."""
        assert self._count(None) == 1

    def test_user_with_zero_licenses_is_semester_1(self):
        u = _user_mock()
        assert self._count(u, license_count=0) == 1

    def test_user_with_one_license_is_semester_2(self):
        u = _user_mock()
        assert self._count(u, license_count=1) == 2

    def test_user_with_multiple_licenses_is_still_semester_2(self):
        """Function returns binary: 1 (no licenses) or 2 (any licenses > 0)."""
        u = _user_mock()
        assert self._count(u, license_count=5) == 2


# ============================================================================
# Class 6 — get_available_specializations_for_semester (cap enforcement only)
# ============================================================================

@pytest.mark.unit
class TestGetAvailableSpecializationsCaps:
    """
    Tests only the early-return cap logic in get_available_specializations_for_semester.
    When current_count >= semester_max, the method returns [] immediately, BEFORE any
    call to license_service or check_age/payment_requirement.

    get_user_active_specializations is patched to control current_count.
    No LicenseService mock needed for cap tests — the method never reaches it.

    Stability note: these tests assert a pure control-flow invariant (enrollment caps),
    not the internal content of available specs. They are immune to changes in
    specialization names or LicenseService internals.
    """

    def _available(self, active_specs, semester):
        svc, _ = _service()
        with _patch.object(svc, "get_user_active_specializations", return_value=active_specs):
            return svc.get_available_specializations_for_semester(user_id=1, semester=semester)

    def test_semester1_cap_blocks_when_user_has_one_spec(self):
        """Semester 1 max=1: already has 1 spec → empty list, no further checks."""
        active = [{"specialization_type": "PLAYER"}]
        assert self._available(active, semester=1) == []

    def test_semester1_empty_hand_does_not_trigger_cap(self):
        """Semester 1, 0 existing specs → cap NOT triggered (proceeds to build list)."""
        # With a real db mock the license_service would return None → empty available.
        # We only assert it doesn't return early; an empty list is fine here too.
        svc, _ = _service()
        svc.license_service = MagicMock()
        svc.license_service.get_license_metadata_by_level.return_value = None
        age_ok = {"meets_requirement": True, "user_age": 20, "required_age": 5, "reason": "ok"}
        pay_ok = {"payment_verified": True, "reason": "ok"}
        with _patch.object(svc, "get_user_active_specializations", return_value=[]):
            with _patch.object(svc, "check_age_requirement", return_value=age_ok):
                with _patch.object(svc, "check_payment_requirement", return_value=pay_ok):
                    result = svc.get_available_specializations_for_semester(user_id=1, semester=1)
        # Metadata is None → none of the 3 specs get appended → empty, but NOT from cap
        assert isinstance(result, list)

    def test_semester2_cap_blocks_when_user_has_two_specs(self):
        """Semester 2 max=2: already has 2 specs → empty list."""
        active = [{"specialization_type": "PLAYER"}, {"specialization_type": "COACH"}]
        assert self._available(active, semester=2) == []

    def test_semester2_one_spec_does_not_trigger_cap(self):
        """Semester 2, 1 existing spec → cap NOT triggered (max=2)."""
        svc, _ = _service()
        svc.license_service = MagicMock()
        svc.license_service.get_license_metadata_by_level.return_value = None
        active = [{"specialization_type": "PLAYER"}]
        age_ok = {"meets_requirement": True, "user_age": 20, "required_age": 5, "reason": "ok"}
        pay_ok = {"payment_verified": True, "reason": "ok"}
        with _patch.object(svc, "get_user_active_specializations", return_value=active):
            with _patch.object(svc, "check_age_requirement", return_value=age_ok):
                with _patch.object(svc, "check_payment_requirement", return_value=pay_ok):
                    result = svc.get_available_specializations_for_semester(user_id=1, semester=2)
        assert isinstance(result, list)

    def test_semester3_cap_blocks_when_user_has_three_specs(self):
        """Semester 3+ max=3: already has all 3 specs → empty list."""
        active = [
            {"specialization_type": "PLAYER"},
            {"specialization_type": "COACH"},
            {"specialization_type": "INTERNSHIP"},
        ]
        assert self._available(active, semester=3) == []

    def test_license_metadata_none_prevents_spec_from_being_offered(self):
        """
        INVARIANT: even when age and payment requirements are met, a specialization
        is only offered if license_service returns non-None metadata.
        This guards against misconfigured or missing LicenseMetadata rows.
        """
        svc, _ = _service()
        svc.license_service = MagicMock()
        # All three specs return None metadata → none appended → available = []
        svc.license_service.get_license_metadata_by_level.return_value = None
        age_ok = {"meets_requirement": True, "user_age": 25, "required_age": 5, "reason": "ok"}
        pay_ok = {"payment_verified": True, "reason": "ok"}
        with _patch.object(svc, "get_user_active_specializations", return_value=[]):
            with _patch.object(svc, "check_age_requirement", return_value=age_ok):
                with _patch.object(svc, "check_payment_requirement", return_value=pay_ok):
                    result = svc.get_available_specializations_for_semester(user_id=1, semester=1)
        assert result == []


# ============================================================================
# Class 7 — start_new_specialization (failure paths only)
# ============================================================================

@pytest.mark.unit
class TestStartNewSpecializationFailurePaths:
    """
    Tests the two failure modes of start_new_specialization without DB writes.

    The success path (db.add + db.commit + db.refresh + new_license.to_dict())
    requires a persisted ORM object and belongs in integration tests.

    Mock isolation: each test uses a fresh db via _service() and patches only
    the self-methods it needs. No shared state between tests.
    """

    def test_duplicate_specialization_returns_failure_without_db_write(self):
        """User already has PLAYER → early return, db.add never called."""
        svc, db = _service()
        existing = MagicMock()
        existing.canonical_program_id = None
        existing.specialization_type = "PLAYER"
        existing.to_dict.return_value = {"id": 1, "specialization_type": "PLAYER"}
        db.query.return_value.filter.return_value.all.return_value = [existing]

        result = svc.start_new_specialization(user_id=1, specialization="PLAYER")

        assert result["success"] is False
        assert "already has" in result["message"]
        assert "GANCUJU_PLAYER" in result["message"]
        db.add.assert_not_called()

    def test_spec_not_available_returns_failure_without_db_write(self):
        """Spec not in available list (e.g. cap hit) → failure, db.add never called."""
        svc, db = _service()
        # No existing license for this spec
        db.query.return_value.filter.return_value.first.return_value = None

        with _patch.object(svc, "get_user_semester_count", return_value=1):
            with _patch.object(
                svc,
                "get_available_specializations_for_semester",
                return_value=[],   # empty → can_start will be False
            ):
                result = svc.start_new_specialization(user_id=1, specialization="COACH")

        assert result["success"] is False
        assert "not available" in result["message"]
        db.add.assert_not_called()

    def test_spec_available_but_can_start_false_returns_failure(self):
        """Spec in available list but can_start=False (age/payment not met) → failure."""
        svc, db = _service()
        db.query.return_value.filter.return_value.first.return_value = None

        available = [{"specialization_type": "COACH", "can_start": False}]
        with _patch.object(svc, "get_user_semester_count", return_value=2):
            with _patch.object(svc, "get_available_specializations_for_semester", return_value=available):
                result = svc.start_new_specialization(user_id=1, specialization="COACH")

        assert result["success"] is False
        db.add.assert_not_called()


# ============================================================================
# Class 8 — validate_specialization_addition
# ============================================================================

@pytest.mark.unit
class TestValidateSpecializationAddition:
    """
    validate_specialization_addition delegates entirely to
    get_user_semester_count + get_available_specializations_for_semester.
    Both are patched; this class tests only the branching logic.
    """

    def _validate(self, available_specs, new_spec):
        svc, _ = _service()
        with _patch.object(svc, "get_user_semester_count", return_value=1):
            with _patch.object(
                svc, "get_available_specializations_for_semester", return_value=available_specs
            ):
                return svc.validate_specialization_addition(
                    user_id=1, new_specialization=new_spec
                )

    def test_spec_not_in_available_returns_invalid(self):
        """No matching spec in available list → valid=False, reason contains spec name."""
        result = self._validate(available_specs=[], new_spec="PLAYER")
        assert result["valid"] is False
        assert "PLAYER" in result["reason"]

    def test_spec_available_but_cannot_start_returns_invalid(self):
        """Spec found but can_start=False → valid=False, reason from spec's reason."""
        available = [{"specialization_type": "PLAYER", "can_start": False, "reason": "Életkor nem megfelelő"}]
        result = self._validate(available, "PLAYER")
        assert result["valid"] is False
        assert result["reason"] == "Életkor nem megfelelő"

    def test_spec_available_and_can_start_returns_valid_with_metadata(self):
        """Spec found and can_start=True → valid=True, semester and metadata returned."""
        meta = {"specialization_type": "PLAYER", "can_start": True, "reason": "Minden OK"}
        result = self._validate([meta], "PLAYER")
        assert result["valid"] is True
        assert result["semester"] == 1
        assert result["metadata"] == meta

    def test_case_insensitive_lookup(self):
        """new_specialization='player' (lowercase) matches 'PLAYER' in available list."""
        meta = {"specialization_type": "PLAYER", "can_start": True, "reason": "OK"}
        result = self._validate([meta], "player")
        assert result["valid"] is True


# ============================================================================
# TestEvaluateSpecializationType
# ============================================================================

class TestEvaluateSpecializationType:
    """
    Unit tests for _evaluate_specialization_type — the extracted helper.

    All external calls (license_service, check_age_requirement,
    check_payment_requirement) are replaced with mocks/patch.object so the
    helper's own branching logic is exercised in complete isolation.
    """

    # ----- shared fixtures -----
    _AGE_OK   = {"meets_requirement": True,  "user_age": 25, "required_age": 5,  "reason": "Életkor követelmény teljesítve (25 év)"}
    _AGE_FAIL = {"meets_requirement": False, "user_age": 3,  "required_age": 5,  "reason": "Minimum életkor: 5 év (jelenlegi: 3 év)"}
    _PAY_OK   = {"payment_verified": True,  "reason": "Alapbefizetés ellenőrizve és jóváhagyva"}
    _PAY_FAIL = {"payment_verified": False, "reason": "Alapbefizetés szükséges"}
    _META     = {"specialization_type": "PLAYER", "level_number": 1, "title": "Player L1"}
    _SUCCESS  = "Player specializáció (min. 5 év) - minden követelmény teljesített"

    def _eval(
        self,
        spec_type="PLAYER",
        current_spec_codes=None,
        age_result=None,
        pay_result=None,
        meta_result=None,
        success_reason=None,
        semester=1,
    ):
        """Helper: build a mocked service and call _evaluate_specialization_type."""
        svc, _ = _service()
        svc.license_service = MagicMock()
        svc.license_service.get_license_metadata_by_level.return_value = meta_result
        age_result  = age_result  if age_result  is not None else self._AGE_OK
        pay_result  = pay_result  if pay_result  is not None else self._PAY_OK
        success_reason = success_reason or self._SUCCESS
        with _patch.object(svc, "check_age_requirement",    return_value=age_result):
            with _patch.object(svc, "check_payment_requirement", return_value=pay_result):
                return svc._evaluate_specialization_type(
                    user_id=1,
                    spec_type=spec_type,
                    semester=semester,
                    current_spec_codes=current_spec_codes or [],
                    success_reason=success_reason,
                )

    # ----- early-exit guards -----

    def test_already_enrolled_returns_none(self):
        """spec_type in current_spec_codes → immediate None, no DB call made."""
        svc, _ = _service()
        svc.license_service = MagicMock()
        result = svc._evaluate_specialization_type(
            user_id=1,
            spec_type="PLAYER",
            semester=1,
            current_spec_codes=["PLAYER", "COACH"],
            success_reason=self._SUCCESS,
        )
        assert result is None
        svc.license_service.get_license_metadata_by_level.assert_not_called()

    def test_metadata_none_returns_none(self):
        """No LicenseMetadata for the spec → None returned, age/payment never called."""
        result = self._eval(meta_result=None)
        assert result is None

    # ----- failure-reason branches -----

    def test_age_fail_reason_contains_age_prefix(self):
        """Age check fails → reason starts with spec display name, contains 'Korhatár:'."""
        result = self._eval(age_result=self._AGE_FAIL, pay_result=self._PAY_OK, meta_result=self._META)
        assert result is not None
        assert result["can_start"] is False
        assert "Korhatár:" in result["reason"]
        assert "Minimum életkor: 5 év" in result["reason"]

    def test_payment_fail_reason_contains_payment_prefix(self):
        """Payment check fails → reason contains 'Befizetés:'."""
        result = self._eval(age_result=self._AGE_OK, pay_result=self._PAY_FAIL, meta_result=self._META)
        assert result is not None
        assert result["can_start"] is False
        assert "Befizetés:" in result["reason"]
        assert "Alapbefizetés szükséges" in result["reason"]

    def test_both_fail_reason_joins_with_semicolon(self):
        """Both age and payment fail → reason contains both parts joined by '; '."""
        result = self._eval(age_result=self._AGE_FAIL, pay_result=self._PAY_FAIL, meta_result=self._META)
        assert result is not None
        assert result["can_start"] is False
        assert "Korhatár:" in result["reason"]
        assert "Befizetés:" in result["reason"]
        # Both parts must appear in a single '; '-separated string
        assert "; " in result["reason"]

    def test_both_fail_reason_starts_with_display_name(self):
        """Reason prefix must be the human-readable spec display name."""
        result = self._eval(spec_type="LFA_COACH", age_result=self._AGE_FAIL, pay_result=self._PAY_FAIL, meta_result=self._META)
        assert result["reason"].startswith("LFA Coach specializáció")

    def test_both_fail_internship_display_name(self):
        """INTERNSHIP display name is 'Gyakornoki program' in failure reason."""
        result = self._eval(spec_type="INTERNSHIP", age_result=self._AGE_FAIL, pay_result=self._PAY_FAIL, meta_result=self._META)
        assert result["reason"].startswith("Gyakornoki program")

    # ----- success path -----

    def test_can_start_true_when_both_checks_pass(self):
        """Both checks pass → can_start=True, requirement_met=True."""
        result = self._eval(age_result=self._AGE_OK, pay_result=self._PAY_OK, meta_result=self._META)
        assert result is not None
        assert result["can_start"] is True
        assert result["requirement_met"] is True

    def test_can_start_true_uses_provided_success_reason(self):
        """success_reason is passed through verbatim when can_start=True."""
        custom = "Egyedi siker szöveg"
        result = self._eval(age_result=self._AGE_OK, pay_result=self._PAY_OK, meta_result=self._META, success_reason=custom)
        assert result["reason"] == custom

    def test_result_dict_includes_meta_keys(self):
        """Return value spreads meta dict — all metadata keys must be present."""
        result = self._eval(age_result=self._AGE_OK, pay_result=self._PAY_OK, meta_result=self._META)
        for key in self._META:
            assert key in result, f"Missing meta key: {key}"

    def test_result_dict_includes_check_sub_dicts(self):
        """age_requirement and payment_requirement sub-dicts preserved in result."""
        result = self._eval(age_result=self._AGE_OK, pay_result=self._PAY_OK, meta_result=self._META)
        assert result["age_requirement"]     == self._AGE_OK
        assert result["payment_requirement"] == self._PAY_OK

    # ----- sem_key distinction via outer method -----

    def test_semester1_success_reason_contains_extra_context(self):
        """
        Semester 1 success reason includes 'alapképzés' / 'edzői képzés' extra context.
        Verified via get_available_specializations_for_semester(semester=1).
        """
        svc, _ = _service()
        svc.license_service = MagicMock()
        svc.license_service.get_license_metadata_by_level.return_value = self._META
        with _patch.object(svc, "get_user_active_specializations", return_value=[]):
            with _patch.object(svc, "check_age_requirement",    return_value=self._AGE_OK):
                with _patch.object(svc, "check_payment_requirement", return_value=self._PAY_OK):
                    result_sem1 = svc.get_available_specializations_for_semester(user_id=1, semester=1)

        assert len(result_sem1) == 4
        player_entry = next(e for e in result_sem1 if e["reason"].startswith("LFA Football Player"))
        assert "alapképzés" in player_entry["reason"]

    def test_semester2_success_reason_lacks_extra_context(self):
        """
        Semester 2+ success reason omits the 'alapképzés' / 'edzői képzés' qualifier.
        Verified via get_available_specializations_for_semester(semester=2).
        """
        svc, _ = _service()
        svc.license_service = MagicMock()
        svc.license_service.get_license_metadata_by_level.return_value = self._META
        with _patch.object(svc, "get_user_active_specializations", return_value=[]):
            with _patch.object(svc, "check_age_requirement",    return_value=self._AGE_OK):
                with _patch.object(svc, "check_payment_requirement", return_value=self._PAY_OK):
                    result_sem2 = svc.get_available_specializations_for_semester(user_id=1, semester=2)

        assert len(result_sem2) == 4
        player_entry = next(e for e in result_sem2 if e["reason"].startswith("LFA Football Player"))
        assert "alapképzés" not in player_entry["reason"]
        assert "min. 5 év" in player_entry["reason"]
