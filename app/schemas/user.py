from pydantic import BaseModel, EmailStr, ConfigDict, field_serializer, computed_field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
from ..models.user import UserRole
from ..models.specialization import SpecializationType


# Simple UserLicense schema for embedding in User response
class UserLicenseSimple(BaseModel):
    """Simplified license info for User API responses"""
    id: int
    specialization_type: str
    is_active: bool
    payment_verified: bool
    onboarding_completed: bool = False  # ✅ CRITICAL FIX: Required for onboarding check
    motivation_scores: Optional[Dict[str, Any]] = None  # ✅ CRITICAL FIX: Required for dashboard data display

    model_config = ConfigDict(from_attributes=True)


class UserBase(BaseModel):
    name: str
    nickname: Optional[str] = None
    email: EmailStr
    role: UserRole = UserRole.STUDENT
    is_active: bool = True


class UserCreate(UserBase):
    password: str
    phone: Optional[str] = None
    emergency_contact: Optional[str] = None
    emergency_phone: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    medical_notes: Optional[str] = None
    position: Optional[str] = None
    specialization: Optional[str] = None
    onboarding_completed: Optional[bool] = False
    payment_verified: Optional[bool] = False
    parental_consent: Optional[bool] = False
    parental_consent_by: Optional[str] = None


class UserUpdate(BaseModel):
    name: Optional[str] = None
    nickname: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    phone: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    nationality: Optional[str] = None
    gender: Optional[str] = None


class UserUpdateSelf(BaseModel):
    name: Optional[str] = None
    nickname: Optional[str] = None
    email: Optional[EmailStr] = None
    onboarding_completed: Optional[bool] = None
    phone: Optional[str] = None
    emergency_contact: Optional[str] = None
    emergency_phone: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    medical_notes: Optional[str] = None
    interests: Optional[str] = None  # JSON string of interests array
    position: Optional[str] = None  # Football position
    specialization: Optional[str] = None  # Player/Coach/Internship
    nda_accepted: Optional[bool] = None
    nda_ip_address: Optional[str] = None
    parental_consent: Optional[bool] = None
    parental_consent_by: Optional[str] = None


class User(UserBase):
    id: int
    onboarding_completed: Optional[bool] = False
    phone: Optional[str] = None
    emergency_contact: Optional[str] = None
    emergency_phone: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    nationality: Optional[str] = None
    gender: Optional[str] = None
    medical_notes: Optional[str] = None
    interests: Optional[str] = None  # JSON string of interests array
    position: Optional[str] = None  # Football position
    specialization: Optional[str] = None  # Player/Coach/Internship track (DEPRECATED)
    payment_verified: Optional[bool] = False
    payment_verified_at: Optional[datetime] = None
    payment_verified_by: Optional[int] = None
    nda_accepted: Optional[bool] = False
    nda_accepted_at: Optional[datetime] = None
    nda_ip_address: Optional[str] = None
    parental_consent: Optional[bool] = False
    parental_consent_at: Optional[datetime] = None
    parental_consent_by: Optional[str] = None
    created_at: Optional[datetime] = None  # Make optional for legacy data
    updated_at: Optional[datetime] = None
    created_by: Optional[int] = None
    # 💳 Credit system fields
    credit_balance: Optional[int] = 0
    credit_purchased: Optional[int] = 0
    credit_payment_reference: Optional[str] = None
    # ⭐ XP system fields
    xp_balance: Optional[int] = 0
    # 🪪 Profile photo (Academy ID Phase 1)
    profile_photo_url:           Optional[str]  = None
    profile_photo_processed_url: Optional[str]  = None
    profile_photo_status:        Optional[str]  = None
    # 🪪 Academy ID (Phase 2A) — only exposed on the owner's own authenticated response
    lfa_academy_id:              Optional[str]  = None
    public_token:                Optional[UUID] = None
    # 📜 User licenses (NEW - replaces deprecated specialization field)
    licenses: List[UserLicenseSimple] = []

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def age(self) -> Optional[int]:
        """Calculate user's age in years from date_of_birth"""
        if not self.date_of_birth:
            return None
        from datetime import timezone
        today = datetime.now(timezone.utc).date()
        dob = self.date_of_birth.date() if isinstance(self.date_of_birth, datetime) else self.date_of_birth
        age_years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return age_years

    @field_serializer('specialization')
    def serialize_specialization(self, value, _info):
        """Convert SpecializationType enum to string"""
        if value is None:
            return None
        if isinstance(value, SpecializationType):
            return value.value
        return value


class UserWithStats(User):
    total_bookings: int
    completed_sessions: int
    feedback_count: int


class UserList(BaseModel):
    users: List[User]
    total: int
    page: int
    size: int