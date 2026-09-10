"""
Contract tests — User schemas

Validates that User, UserCreate, and UserLicenseSimple preserve required fields,
types, and defaults. Fails immediately on any field rename or removal in
app/schemas/user.py without requiring a DB or server.
"""

from app.models.user import UserRole
from app.schemas.user import User, UserCreate, UserLicenseSimple


class TestUserResponseContract:
    """Contract: GET /api/v1/users/{id} response body (User schema)."""

    def test_user_has_id_field(self):
        assert "id" in User.model_fields

    def test_user_has_email_field(self):
        assert "email" in User.model_fields

    def test_user_has_name_field(self):
        assert "name" in User.model_fields

    def test_user_has_role_field(self):
        assert "role" in User.model_fields

    def test_user_has_is_active_field(self):
        assert "is_active" in User.model_fields

    def test_user_id_is_required(self):
        assert User.model_fields["id"].is_required()

    def test_user_email_is_required(self):
        assert User.model_fields["email"].is_required()

    def test_user_name_is_required(self):
        assert User.model_fields["name"].is_required()

    def test_user_role_defaults_to_student(self):
        assert User.model_fields["role"].default == UserRole.STUDENT

    def test_user_is_active_defaults_to_true(self):
        assert User.model_fields["is_active"].default is True

    def test_user_has_licenses_field(self):
        assert "licenses" in User.model_fields

    def test_user_licenses_defaults_to_empty_list(self):
        assert User.model_fields["licenses"].default == []


class TestUserCreateContract:
    """Contract: POST /api/v1/users/ request body (UserCreate schema)."""

    def test_user_create_has_password_field(self):
        assert "password" in UserCreate.model_fields

    def test_user_create_password_is_required(self):
        assert UserCreate.model_fields["password"].is_required()

    def test_user_create_has_email_field(self):
        assert "email" in UserCreate.model_fields

    def test_user_create_has_name_field(self):
        assert "name" in UserCreate.model_fields

    def test_user_create_date_of_birth_is_required(self):
        assert "date_of_birth" in UserCreate.model_fields
        assert UserCreate.model_fields["date_of_birth"].is_required()


class TestUserLicenseSimpleContract:
    """Contract: UserLicense embedded in User response (UserLicenseSimple schema)."""

    def test_user_license_has_id_field(self):
        assert "id" in UserLicenseSimple.model_fields

    def test_user_license_has_specialization_type_field(self):
        assert "specialization_type" in UserLicenseSimple.model_fields

    def test_user_license_has_is_active_field(self):
        assert "is_active" in UserLicenseSimple.model_fields

    def test_user_license_has_payment_verified_field(self):
        assert "payment_verified" in UserLicenseSimple.model_fields

    def test_user_license_has_onboarding_completed_field(self):
        assert "onboarding_completed" in UserLicenseSimple.model_fields

    def test_user_license_id_is_required(self):
        assert UserLicenseSimple.model_fields["id"].is_required()

    def test_user_license_specialization_type_is_required(self):
        assert UserLicenseSimple.model_fields["specialization_type"].is_required()
