"""
DB Seed State Validator
=======================
Quick health check — exits 0 if all required reference data is present,
exits 1 with a clear explanation of what is missing.

Usage:
    DATABASE_URL="postgresql://..." PYTHONPATH=. python scripts/validate_seed_state.py

Run this after bootstrap_clean.py to verify the system is ready.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lfa_intern_system")

from app.database import SessionLocal  # noqa: E402
from app.models.campus import Campus  # noqa: E402
from app.models.club import Club  # noqa: E402
from app.models.game_preset import GamePreset  # noqa: E402
from app.models.team import Team, TeamMember  # noqa: E402
from app.models.tournament_type import TournamentType  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.models.virtual_training import VirtualTrainingGame  # noqa: E402


def run():
    db = SessionLocal()
    try:
        print("\n🔍 Seed state validation")
        print("-" * 50)

        failures = []

        # 1. TournamentType
        tt_count = db.query(TournamentType).count()
        if tt_count >= 4:
            codes = [r.code for r in db.query(TournamentType).all()]
            print(f"  ✅ TournamentType: {tt_count} rows ({', '.join(codes)})")
        else:
            print(f"  ❌ TournamentType: {tt_count} rows (need ≥4)")
            failures.append(
                f"TournamentType: only {tt_count} rows. Run: "
                "DATABASE_URL=... python scripts/seed_tournament_types.py"
            )

        # 2. GamePreset
        gp_count = db.query(GamePreset).count()
        if gp_count >= 3:
            codes = [r.code for r in db.query(GamePreset).all()]
            print(f"  ✅ GamePreset: {gp_count} rows ({', '.join(codes)})")
        else:
            print(f"  ❌ GamePreset: {gp_count} rows (need ≥3)")
            failures.append(
                f"GamePreset: only {gp_count} rows. Run: "
                "DATABASE_URL=... python scripts/seed_game_presets.py"
            )

        # 3. Campus
        campus_rows = db.query(Campus).filter(Campus.is_active == True).all()  # noqa: E712
        if campus_rows:
            names = [c.name for c in campus_rows]
            print(f"  ✅ Campus: {len(campus_rows)} rows ({', '.join(names)})")
        else:
            print("  ❌ Campus: 0 active rows (need ≥1)")
            failures.append(
                "Campus: no active rows. Create a Location + Campus in /admin/locations "
                "or run: python scripts/bootstrap_clean.py"
            )

        # 4. Admin user
        admin = db.query(User).filter(
            User.role == UserRole.ADMIN,
            User.is_active == True,  # noqa: E712
        ).first()
        if admin:
            print(f"  ✅ Admin user: {admin.email} (id={admin.id})")
        else:
            print("  ❌ Admin user: none found")
            failures.append("No active ADMIN user. Run: python scripts/bootstrap_clean.py")

        # 5. Instructor user
        instr = db.query(User).filter(
            User.role == UserRole.INSTRUCTOR,
            User.is_active == True,  # noqa: E712
        ).first()
        if instr:
            print(f"  ✅ Instructor user: {instr.email} (id={instr.id})")
        else:
            print("  ❌ Instructor user: none found")
            failures.append(
                "No active INSTRUCTOR user. Required for CHECK_IN_OPEN → IN_PROGRESS. "
                "Run: python scripts/bootstrap_clean.py"
            )

        # 6. Club with teams + players
        clubs_with_teams = (
            db.query(Club)
            .filter(Club.is_active == True)  # noqa: E712
            .all()
        )
        found_club = None
        for c in clubs_with_teams:
            teams = db.query(Team).filter(Team.club_id == c.id, Team.is_active == True).all()  # noqa: E712
            for t in teams:
                member_count = db.query(TeamMember).filter(
                    TeamMember.team_id == t.id,
                ).count()
                if member_count > 0:
                    found_club = c
                    break
            if found_club:
                break

        if found_club:
            team_rows = db.query(Team).filter(Team.club_id == found_club.id, Team.is_active == True).all()  # noqa: E712
            age_labels = [t.age_group_label for t in team_rows if t.age_group_label]
            total_members = sum(
                db.query(TeamMember).filter(TeamMember.team_id == t.id).count()
                for t in team_rows
            )
            print(
                f"  ✅ Club with teams+players: '{found_club.name}' "
                f"({len(team_rows)} teams, {total_members} players"
                + (f" — {', '.join(sorted(set(age_labels)))}" if age_labels else "")
                + ")"
            )
        else:
            print("  ❌ Club: no club with active teams+players found")
            failures.append(
                "No Club with active teams and players. The promotion wizard will create empty tournaments. "
                "Run: python scripts/bootstrap_clean.py"
            )

        # 7. VirtualTrainingGame reference data
        _CHALLENGE_COMPAT = {"memory_sequence", "target_tracking"}
        vt_active_games = (
            db.query(VirtualTrainingGame)
            .filter(VirtualTrainingGame.is_active == True)  # noqa: E712
            .all()
        )
        vt_compat_count = sum(1 for g in vt_active_games if g.code in _CHALLENGE_COMPAT)
        if len(vt_active_games) >= 1 and vt_compat_count >= 2:
            vt_codes = [g.code for g in vt_active_games]
            print(f"  ✅ VirtualTrainingGame: {len(vt_active_games)} active ({', '.join(vt_codes)})")
        else:
            print(
                f"  ❌ VirtualTrainingGame: {len(vt_active_games)} active, "
                f"{vt_compat_count}/2 challenge-compatible (need memory_sequence + target_tracking)"
            )
            failures.append(
                f"VirtualTrainingGame: {len(vt_active_games)} active games, "
                f"{vt_compat_count}/2 challenge-compatible. "
                "Run: PYTHONPATH=. python scripts/seed_virtual_training_games.py"
            )

        print("-" * 50)

        if failures:
            print(f"\n  ❌ {len(failures)} check(s) failed:\n")
            for i, f in enumerate(failures, 1):
                print(f"  {i}. {f}\n")
            print("  Fix: DATABASE_URL=... PYTHONPATH=. python scripts/bootstrap_clean.py\n")
            sys.exit(1)
        else:
            print("\n  ✅ All checks passed — system is ready from clean DB.\n")
            sys.exit(0)

    finally:
        db.close()


if __name__ == "__main__":
    run()
