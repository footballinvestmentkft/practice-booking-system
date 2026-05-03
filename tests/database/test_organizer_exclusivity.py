"""
Organizer Exclusivity Tests — EXCL-01 through EXCL-08

Tests the dual-layer guard (ORM @validates + DB CHECK constraint)
that ensures at most one organizer (club OR sponsor) is set on a Semester.

  EXCL-01  Semester with no organizer → valid
  EXCL-02  Semester with organizer_club_id only → valid
  EXCL-03  Semester with organizer_sponsor_id only → valid
  EXCL-04  ORM @validates: set organizer_club_id when sponsor already set → ValueError
  EXCL-05  ORM @validates: set organizer_sponsor_id when club already set → ValueError
  EXCL-06  DB CHECK: INSERT both FKs simultaneously → IntegrityError
  EXCL-07  Partial unique index: two primary contacts per sponsor → IntegrityError
  EXCL-08  Partial unique index: one primary per sponsor + non-primary → valid

DONE = pytest tests/database/test_organizer_exclusivity.py -v
"""
import os
import uuid
import pytest
from datetime import date

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import engine as app_engine, SessionLocal
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.club import Club
from app.models.sponsor import Sponsor, SponsorContact
from app.models.user import User, UserRole
from app.core.security import get_password_hash


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """SAVEPOINT-isolated session per test."""
    connection = app_engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection)
    session.begin_nested()  # SAVEPOINT
    yield session
    session.close()
    transaction.rollback()
    connection.close()


def _seed_admin(db) -> User:
    u = User(
        email=f"excl-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Excl Admin",
        password_hash=get_password_hash("X"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _seed_club(db, admin: User) -> Club:
    c = Club(
        name=f"Excl Club {uuid.uuid4().hex[:6]}",
        code=f"EC-{uuid.uuid4().hex[:6].upper()}",
        is_active=True,
        created_by=admin.id,
    )
    db.add(c)
    db.flush()
    return c


def _seed_sponsor(db, admin: User) -> Sponsor:
    s = Sponsor(
        name=f"Excl Sponsor {uuid.uuid4().hex[:6]}",
        code=f"ES-{uuid.uuid4().hex[:6].upper()}",
        is_active=True,
        created_by=admin.id,
    )
    db.add(s)
    db.flush()
    return s


def _base_semester_kwargs(suffix: str = "") -> dict:
    uid = uuid.uuid4().hex[:8]
    return dict(
        code=f"EXCL-{uid}{suffix}",
        name=f"Exclusivity Test {uid}",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 3),
        status=SemesterStatus.DRAFT,
        tournament_status="DRAFT",
        semester_category=SemesterCategory.PROMOTION_EVENT,
        enrollment_cost=0,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOrganizerFieldValidity:
    """EXCL-01, EXCL-02, EXCL-03"""

    def test_excl_01_no_organizer_valid(self, db):
        admin = _seed_admin(db)
        t = Semester(**_base_semester_kwargs("01"))
        db.add(t)
        db.flush()  # must not raise

    def test_excl_02_club_only_valid(self, db):
        admin = _seed_admin(db)
        club = _seed_club(db, admin)
        t = Semester(**_base_semester_kwargs("02"), organizer_club_id=club.id)
        db.add(t)
        db.flush()  # must not raise
        assert t.organizer_club_id == club.id
        assert t.organizer_sponsor_id is None

    def test_excl_03_sponsor_only_valid(self, db):
        admin = _seed_admin(db)
        sponsor = _seed_sponsor(db, admin)
        t = Semester(**_base_semester_kwargs("03"), organizer_sponsor_id=sponsor.id)
        db.add(t)
        db.flush()  # must not raise
        assert t.organizer_sponsor_id == sponsor.id
        assert t.organizer_club_id is None


class TestOrmValidatesGuard:
    """EXCL-04, EXCL-05"""

    def test_excl_04_set_club_when_sponsor_set_raises(self, db):
        admin = _seed_admin(db)
        club = _seed_club(db, admin)
        sponsor = _seed_sponsor(db, admin)

        t = Semester(**_base_semester_kwargs("04"), organizer_sponsor_id=sponsor.id)
        db.add(t)
        db.flush()

        with pytest.raises(ValueError, match="organizer_sponsor_id is already set"):
            t.organizer_club_id = club.id

    def test_excl_05_set_sponsor_when_club_set_raises(self, db):
        admin = _seed_admin(db)
        club = _seed_club(db, admin)
        sponsor = _seed_sponsor(db, admin)

        t = Semester(**_base_semester_kwargs("05"), organizer_club_id=club.id)
        db.add(t)
        db.flush()

        with pytest.raises(ValueError, match="organizer_club_id is already set"):
            t.organizer_sponsor_id = sponsor.id


class TestDbCheckConstraint:
    """EXCL-06"""

    def test_excl_06_db_check_both_fks_raises_integrity_error(self, db):
        admin = _seed_admin(db)
        club = _seed_club(db, admin)
        sponsor = _seed_sponsor(db, admin)

        uid = uuid.uuid4().hex[:8]
        with pytest.raises((IntegrityError, ValueError)):
            db.execute(
                text("""
                    INSERT INTO semesters
                        (code, name, start_date, end_date, status, enrollment_cost,
                         organizer_club_id, organizer_sponsor_id)
                    VALUES
                        (:code, :name, :sd, :ed, 'DRAFT', 0, :cid, :sid)
                """),
                {
                    "code": f"EXCL-BOTH-{uid}",
                    "name": f"Both Organizers {uid}",
                    "sd": "2026-07-01",
                    "ed": "2026-07-03",
                    "cid": club.id,
                    "sid": sponsor.id,
                },
            )
            db.flush()


class TestPrimaryContactIndex:
    """EXCL-07, EXCL-08"""

    def test_excl_07_two_primary_contacts_raises_integrity_error(self, db):
        admin = _seed_admin(db)
        sponsor = _seed_sponsor(db, admin)

        c1 = SponsorContact(sponsor_id=sponsor.id, name="First", is_primary=True)
        db.add(c1)
        db.flush()

        c2 = SponsorContact(sponsor_id=sponsor.id, name="Second", is_primary=True)
        db.add(c2)
        with pytest.raises(IntegrityError):
            db.flush()

    def test_excl_08_one_primary_plus_non_primary_valid(self, db):
        admin = _seed_admin(db)
        sponsor = _seed_sponsor(db, admin)

        c1 = SponsorContact(sponsor_id=sponsor.id, name="Primary", is_primary=True)
        c2 = SponsorContact(sponsor_id=sponsor.id, name="Secondary", is_primary=False)
        db.add_all([c1, c2])
        db.flush()  # must not raise

        count = (
            db.query(SponsorContact)
            .filter(SponsorContact.sponsor_id == sponsor.id)
            .count()
        )
        assert count == 2
