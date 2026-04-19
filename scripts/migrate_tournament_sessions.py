"""
Data Migration Script: Set is_tournament_game=True for existing tournament sessions

This script finds all tournament semesters (code starts with "TOURN-") and updates
their sessions to have is_tournament_game=True.

Usage:
    DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lfa_intern_system" python scripts/migrate_tournament_sessions.py
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.database import SessionLocal
from app.models.session import Session, EventCategory
from app.models.semester import Semester


def migrate_tournament_sessions():
    """Set is_tournament_game=True for existing tournament sessions"""
    db = SessionLocal()

    try:
        print("=" * 60)
        print("TOURNAMENT SESSION MIGRATION")
        print("=" * 60)
        print()

        # Find all tournament semesters
        tournaments = db.query(Semester).filter(
            Semester.code.like("TOURN-%")
        ).all()

        print(f"Found {len(tournaments)} tournament semesters")
        print()

        if len(tournaments) == 0:
            print("✅ No tournaments found - nothing to migrate")
            return

        total_sessions = 0
        for tournament in tournaments:
            # Get sessions for this tournament
            sessions = db.query(Session).filter(
                Session.semester_id == tournament.id
            ).all()

            # Update each session
            updated_count = 0
            for session in sessions:
                if session.event_category != EventCategory.MATCH:
                    session.event_category = EventCategory.MATCH
                    updated_count += 1
                # game_type and game_results remain NULL (can be filled later by instructor)

            total_sessions += len(sessions)
            print(f"  [{tournament.code}] {tournament.name}")
            print(f"    - Total sessions: {len(sessions)}")
            print(f"    - Updated: {updated_count}")
            print()

        # Commit all changes
        db.commit()

        print("=" * 60)
        print(f"✅ MIGRATION COMPLETE!")
        print(f"   Tournaments processed: {len(tournaments)}")
        print(f"   Total sessions updated: {total_sessions}")
        print("=" * 60)

    except Exception as e:
        db.rollback()
        print(f"❌ ERROR: {str(e)}")
        raise

    finally:
        db.close()


if __name__ == "__main__":
    migrate_tournament_sessions()
