"""
Deterministic DB Bootstrap
==========================
Single idempotent script — safe to run multiple times.

After any DB reset, run this once to reach a fully working state:

    DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lfa_intern_system" \\
        PYTHONPATH=. python scripts/bootstrap_clean.py

What it creates (skips existing rows):
  1. Alembic migrations  (alembic upgrade head)
  2. TournamentType      (4 rows: league, knockout, group_knockout, swiss)
  3. GamePreset          (3 rows: outfield_default, passing_focus, shooting_focus)
  4. Location + Campus   (Budapest / Főváros Campus)
  5. Admin user          admin@lfa.com / admin123
  6. Instructor user     instructor@lfa.com / instructor123
  7. Bootstrap Club      LFA_BOOTSTRAP_CLUB — 3 teams × 12 UK-named players, all LFA-licensed
                         LFA U15 (63.0), LFA U18 (68.0), LFA Adult (72.0)
"""
import json
import os
import subprocess
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lfa_intern_system")

# ── DB setup ──────────────────────────────────────────────────────────────────
from app.database import SessionLocal  # noqa: E402

# ── Models ────────────────────────────────────────────────────────────────────
from app.models.campus import Campus  # noqa: E402
from app.models.club import Club  # noqa: E402
from app.models.game_preset import GamePreset  # noqa: E402
from app.models.license import UserLicense  # noqa: E402
from app.models.location import Location, LocationType  # noqa: E402
from app.models.team import Team, TeamMember  # noqa: E402
from app.models.tournament_type import TournamentType  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.skills_config import get_all_skill_keys  # noqa: E402

# ── Tournament type JSON files ────────────────────────────────────────────────
_JSON_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "tournament_types")

# ── Game preset definitions (mirrors seed_game_presets.py) ───────────────────
_PRESETS = [
    {
        "code": "outfield_default",
        "name": "Outfield Football (Default)",
        "description": (
            "Baseline template for standard outfield football tournaments and training sessions. "
            "Covers core technical, mental, and physical skills for any field position."
        ),
        "is_recommended": True,
        "is_locked": False,
        "game_config": {
            "version": "1.0",
            "metadata": {
                "game_category": "FOOTBALL",
                "difficulty_level": "intermediate",
                "min_players": 4,
                "recommended_player_count": {"min": 8, "max": 32},
            },
            "skill_config": {
                "skills_tested": [
                    "ball_control", "dribbling", "finishing", "passing",
                    "vision", "positioning_off", "sprint_speed", "agility", "stamina",
                ],
                "skill_weights": {
                    "ball_control": 1.2, "dribbling": 1.5, "finishing": 1.4,
                    "passing": 1.3, "vision": 1.1, "positioning_off": 1.1,
                    "sprint_speed": 1.0, "agility": 1.0, "stamina": 0.9,
                },
            },
            "format_config": {},
            "simulation_config": {},
        },
    },
    {
        "code": "passing_focus",
        "name": "Passing & Vision Focus",
        "description": "Specialised preset for passing-intensive game formats.",
        "is_recommended": False,
        "is_locked": False,
        "game_config": {
            "version": "1.0",
            "metadata": {
                "game_category": "FOOTBALL",
                "difficulty_level": "intermediate",
                "min_players": 4,
                "recommended_player_count": {"min": 6, "max": 24},
            },
            "skill_config": {
                "skills_tested": ["passing", "vision", "ball_control", "positioning_off", "agility", "stamina"],
                "skill_weights": {
                    "passing": 1.8, "vision": 1.6, "ball_control": 1.4,
                    "positioning_off": 1.3, "agility": 1.0, "stamina": 0.8,
                },
            },
            "format_config": {},
            "simulation_config": {},
        },
    },
    {
        "code": "shooting_focus",
        "name": "Finishing & Shooting Focus",
        "description": "Goal-conversion focused preset for shooting drills and 1v1 formats.",
        "is_recommended": False,
        "is_locked": False,
        "game_config": {
            "version": "1.0",
            "metadata": {
                "game_category": "FOOTBALL",
                "difficulty_level": "intermediate",
                "min_players": 4,
                "recommended_player_count": {"min": 6, "max": 20},
            },
            "skill_config": {
                "skills_tested": ["finishing", "sprint_speed", "dribbling", "ball_control", "agility", "positioning_off"],
                "skill_weights": {
                    "finishing": 2.0, "sprint_speed": 1.6, "dribbling": 1.5,
                    "ball_control": 1.2, "agility": 1.1, "positioning_off": 0.9,
                },
            },
            "format_config": {},
            "simulation_config": {},
        },
    },
    {
        "code": "sprint_relay",
        "name": "Sprint & Relay Race",
        "description": (
            "For relay race, 100m sprint, and cross-country team events. "
            "Focuses on speed, endurance, and explosive physical output."
        ),
        "is_recommended": False,
        "is_locked": False,
        "game_config": {
            "version": "1.0",
            "metadata": {
                "game_category": "ATHLETICS",
                "difficulty_level": "intermediate",
                "min_players": 3,
                "recommended_player_count": {"min": 10, "max": 40},
            },
            "skill_config": {
                "skills_tested": ["sprint_speed", "stamina", "agility", "strength", "reactions", "acceleration"],
                "skill_weights": {
                    "sprint_speed": 2.0, "stamina": 1.5, "agility": 1.2,
                    "acceleration": 1.3, "strength": 1.0, "reactions": 1.0,
                },
            },
            "format_config": {},
            "simulation_config": {},
        },
    },
    {
        "code": "strength_challenge",
        "name": "Strength & Endurance Challenge",
        "description": (
            "For push-up, pull-up, and bodyweight endurance challenges. "
            "Focuses on upper body strength, stamina, and mental composure."
        ),
        "is_recommended": False,
        "is_locked": False,
        "game_config": {
            "version": "1.0",
            "metadata": {
                "game_category": "ATHLETICS",
                "difficulty_level": "intermediate",
                "min_players": 3,
                "recommended_player_count": {"min": 10, "max": 40},
            },
            "skill_config": {
                "skills_tested": ["strength", "stamina", "composure", "reactions", "balance"],
                "skill_weights": {
                    "strength": 2.0, "stamina": 1.5, "composure": 1.0,
                    "reactions": 0.8, "balance": 0.7,
                },
            },
            "format_config": {},
            "simulation_config": {},
        },
    },
]

# ── Bootstrap Club definition ─────────────────────────────────────────────────
_CLUB_CODE = "LFA-BOOT"
_CLUB_NAME = "LFA_BOOTSTRAP_CLUB"

# 3 age-group teams × 12 players each = 36 total
_TEAMS = [
    {"name": "LFA U15",   "age_group_label": "U15",   "skill_base": 63.0, "dob": date(2010, 6, 1)},
    {"name": "LFA U18",   "age_group_label": "U18",   "skill_base": 68.0, "dob": date(2007, 6, 1)},
    {"name": "LFA Adult", "age_group_label": "ADULT", "skill_base": 72.0, "dob": date(1995, 6, 1)},
]

# 12 unique UK English names per age group (36 total, no cross-group overlap)
_PLAYERS: dict[str, list[tuple[str, str]]] = {
    "LFA U15": [
        ("James", "Archer"), ("Oliver", "Bennett"), ("Harry", "Clarke"),
        ("Charlie", "Dixon"), ("George", "Ellis"), ("Freddie", "Ford"),
        ("Archie", "Grant"), ("Oscar", "Hughes"), ("Noah", "Irving"),
        ("Ethan", "James"), ("Lewis", "King"), ("Samuel", "Lee"),
    ],
    "LFA U18": [
        ("William", "Marsh"), ("Thomas", "Nash"), ("Daniel", "Owen"),
        ("Joseph", "Price"), ("Edward", "Quinn"), ("Arthur", "Reid"),
        ("Sebastian", "Scott"), ("Mason", "Turner"), ("Luca", "Underwood"),
        ("Elijah", "Vale"), ("Henry", "Ward"), ("Felix", "York"),
    ],
    "LFA Adult": [
        ("Robert", "Adams"), ("Michael", "Baker"), ("Christopher", "Cole"),
        ("Andrew", "Davis"), ("Jonathan", "Evans"), ("Matthew", "Fisher"),
        ("Benjamin", "Gray"), ("Nicholas", "Hall"), ("Patrick", "Ingram"),
        ("Richard", "Jenkins"), ("Stephen", "Knight"), ("Timothy", "Lane"),
    ],
}

_POSITIONS = ["STRIKER", "MIDFIELDER", "MIDFIELDER", "DEFENDER", "DEFENDER", "GOALKEEPER"]
_GOALS = [
    "improve_skills", "play_higher_level", "become_professional",
    "team_football", "fitness_health", "enjoy_game",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _step(n: int, title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  Step {n}: {title}")
    print("="*70)


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _skip(msg: str) -> None:
    print(f"  ⏭️  {msg}")


def _seed_tournament_types(db) -> int:
    """Seed 4 TournamentType rows from JSON files — skip existing codes."""
    files = ["league.json", "knockout.json", "group_knockout.json", "swiss.json"]
    created = 0
    for fname in files:
        path = os.path.join(_JSON_DIR, fname)
        try:
            with open(path) as f:
                cfg = json.load(f)
        except FileNotFoundError:
            print(f"  ❌ JSON not found: {path}")
            continue

        code = cfg["code"]
        if db.query(TournamentType).filter(TournamentType.code == code).first():
            _skip(f"TournamentType '{code}' already exists")
            continue

        tt = TournamentType(
            code=code,
            display_name=cfg["display_name"],
            description=cfg["description"],
            format=cfg.get("format", "INDIVIDUAL_RANKING"),
            min_players=cfg["min_players"],
            max_players=cfg.get("max_players"),
            requires_power_of_two=cfg["requires_power_of_two"],
            session_duration_minutes=cfg["session_duration_minutes"],
            break_between_sessions_minutes=cfg["break_between_sessions_minutes"],
            config=cfg,
        )
        db.add(tt)
        db.flush()
        _ok(f"TournamentType '{code}' created (id={tt.id})")
        created += 1

    return created


def _seed_game_presets(db) -> int:
    """Seed 3 GamePreset rows — skip existing codes."""
    created = 0
    for defn in _PRESETS:
        if db.query(GamePreset).filter(GamePreset.code == defn["code"]).first():
            _skip(f"GamePreset '{defn['code']}' already exists")
            continue
        preset = GamePreset(
            code=defn["code"],
            name=defn["name"],
            description=defn.get("description"),
            game_config=defn["game_config"],
            is_active=True,
            is_recommended=defn.get("is_recommended", False),
            is_locked=defn.get("is_locked", False),
        )
        db.add(preset)
        db.flush()
        _ok(f"GamePreset '{defn['code']}' created (id={preset.id})")
        created += 1
    return created


def _seed_location_campus(db) -> tuple:
    """Create Budapest location + Főváros Campus if not present."""
    # Check by city (unique) first, then fall back to location_code
    loc = (
        db.query(Location).filter(Location.city == "Budapest").first()
        or db.query(Location).filter(Location.location_code == "BDPST").first()
    )
    if loc:
        _skip(f"Location 'Budapest' already exists (id={loc.id})")
    else:
        loc = Location(
            name="Budapest",
            city="Budapest",
            country="Hungary",
            country_code="HU",
            location_code="BDPST",
            address="Budapest, Hungary",
            location_type=LocationType.CENTER,
            is_active=True,
        )
        db.add(loc)
        db.flush()
        _ok(f"Location 'Budapest' created (id={loc.id})")

    campus = db.query(Campus).filter(Campus.location_id == loc.id).first()
    if campus:
        _skip(f"Campus '{campus.name}' already exists (id={campus.id})")
    else:
        campus = Campus(
            location_id=loc.id,
            name="Főváros Campus",
            venue="LFA Main Venue",
            address="Budapest, Főváros u. 1.",
            is_active=True,
        )
        db.add(campus)
        db.flush()
        _ok(f"Campus 'Főváros Campus' created (id={campus.id})")

    return loc, campus


def _seed_users(db) -> tuple:
    """Create admin@lfa.com and instructor@lfa.com if not present."""
    admin = db.query(User).filter(User.email == "admin@lfa.com").first()
    if admin:
        _skip(f"Admin user 'admin@lfa.com' already exists (id={admin.id})")
    else:
        admin = User(
            name="LFA Admin",
            email="admin@lfa.com",
            password_hash=get_password_hash("admin123"),
            role=UserRole.ADMIN,
            is_active=True,
            onboarding_completed=True,
        )
        db.add(admin)
        db.flush()
        _ok(f"Admin user created: admin@lfa.com / admin123 (id={admin.id})")

    instr = db.query(User).filter(User.email == "instructor@lfa.com").first()
    if instr:
        _skip(f"Instructor user 'instructor@lfa.com' already exists (id={instr.id})")
    else:
        instr = User(
            name="LFA Instructor",
            email="instructor@lfa.com",
            password_hash=get_password_hash("instructor123"),
            role=UserRole.INSTRUCTOR,
            is_active=True,
            onboarding_completed=True,
        )
        db.add(instr)
        db.flush()
        _ok(f"Instructor user created: instructor@lfa.com / instructor123 (id={instr.id})")

    return admin, instr


def _seed_bootstrap_club(db) -> Club:
    """Create LFA_BOOTSTRAP_CLUB with 3 age-group teams × 12 UK-named LFA-licensed players."""
    all_keys = get_all_skill_keys()
    now = datetime.now()

    club = db.query(Club).filter(Club.code == _CLUB_CODE).first()
    if club:
        _skip(f"Club '{_CLUB_NAME}' already exists (id={club.id})")
    else:
        club = Club(
            name=_CLUB_NAME,
            code=_CLUB_CODE,
            city="Budapest",
            country="HU",
            contact_email="bootstrap@lfa.com",
            is_active=True,
        )
        db.add(club)
        db.flush()
        _ok(f"Club '{_CLUB_NAME}' created (id={club.id})")

    for tdef in _TEAMS:
        team = db.query(Team).filter(
            Team.club_id == club.id,
            Team.name == tdef["name"],
        ).first()
        if team:
            _skip(f"  Team '{tdef['name']}' already exists (id={team.id})")
        else:
            team = Team(
                name=tdef["name"],
                club_id=club.id,
                age_group_label=tdef["age_group_label"],
                is_active=True,
            )
            db.add(team)
            db.flush()
            _ok(f"  Team '{tdef['name']}' created (id={team.id})")

        skill_base = tdef["skill_base"]
        football_skills = {k: skill_base for k in all_keys}
        age_slug = tdef["age_group_label"].lower()  # "u15", "u18", "adult"

        for idx, (first, last) in enumerate(_PLAYERS[tdef["name"]]):
            email = f"lfa-{age_slug}-{first.lower()}.{last.lower()}@lfa.com"
            user = db.query(User).filter(User.email == email).first()
            if not user:
                position = _POSITIONS[idx % len(_POSITIONS)]
                goals = _GOALS[idx % len(_GOALS)]
                user = User(
                    name=f"{first} {last}",
                    first_name=first,
                    last_name=last,
                    nickname=first,
                    email=email,
                    password_hash=get_password_hash("Bootstrap#123"),
                    role=UserRole.STUDENT,
                    is_active=True,
                    onboarding_completed=True,
                    credit_balance=1000,
                    date_of_birth=tdef["dob"],
                    nationality="British",
                    gender="Male",
                    phone=f"+44 7700 9{idx:05d}",
                    street_address="1 Academy Way",
                    city="London",
                    postal_code="EC1A 1BB",
                    country="United Kingdom",
                )
                db.add(user)
                db.flush()

                lic = UserLicense(
                    user_id=user.id,
                    specialization_type="LFA_FOOTBALL_PLAYER",
                    current_level=1,
                    max_achieved_level=1,
                    started_at=now,
                    payment_verified=True,
                    payment_verified_at=now,
                    onboarding_completed=True,
                    onboarding_completed_at=now,
                    is_active=True,
                    football_skills=football_skills,
                    motivation_scores={
                        "position": position,
                        "goals": goals,
                        "motivation": "",
                        "average_skill_level": skill_base,
                        "onboarding_completed_at": now.isoformat(),
                    },
                    average_motivation_score=skill_base,
                )
                db.add(lic)
                db.flush()
                _ok(f"    {first} {last} ({email})")
            else:
                _skip(f"    {first} {last} already exists")

            # Team membership
            member = db.query(TeamMember).filter(
                TeamMember.team_id == team.id,
                TeamMember.user_id == user.id,
            ).first()
            if not member:
                db.add(TeamMember(team_id=team.id, user_id=user.id, role="PLAYER"))
                db.flush()

        member_count = db.query(TeamMember).filter(TeamMember.team_id == team.id).count()
        print(f"  ✅ {tdef['name']}: {member_count} players")

    return club


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print("\n" + "="*70)
    print("  LFA Practice Booking System — DB Bootstrap")
    print("="*70)

    # Step 1: Migrations
    _step(1, "Alembic migrations")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ❌ Migration failed:\n{result.stderr}")
        sys.exit(1)
    output = (result.stdout + result.stderr).strip()
    if "Running upgrade" in output or "upgrade" in output.lower():
        _ok("Migrations applied")
    else:
        _ok("Schema already up to date")

    db = SessionLocal()
    try:
        # Step 2: TournamentType
        _step(2, "TournamentType (4 rows from JSON)")
        _seed_tournament_types(db)
        db.commit()
        total = db.query(TournamentType).count()
        print(f"  → TournamentType total: {total}")

        # Step 3: GamePreset
        _step(3, "GamePreset (3 rows)")
        _seed_game_presets(db)
        db.commit()
        total = db.query(GamePreset).count()
        print(f"  → GamePreset total: {total}")

        # Step 4: Location + Campus
        _step(4, "Location + Campus")
        loc, campus = _seed_location_campus(db)
        db.commit()

        # Step 5: Admin + Instructor users
        _step(5, "Admin + Instructor users")
        admin, instr = _seed_users(db)
        db.commit()

        # Step 6: Bootstrap Club
        _step(6, "Bootstrap Club (3 teams × 12 UK-named players)")
        club = _seed_bootstrap_club(db)
        db.commit()

        # Step 7: Backfill missing fields on seed tournaments (idempotent)
        _step(7, "Backfill location_id + semester_category + game_preset on seed tournaments")
        from sqlalchemy import text as _text
        db.execute(_text("""
            UPDATE semesters s
            SET location_id = c.location_id
            FROM campuses c
            WHERE s.campus_id = c.id
              AND s.location_id IS NULL
              AND c.location_id IS NOT NULL
        """))
        # Semesters created via old seed (before semester_category was set in wizard)
        db.execute(_text("""
            UPDATE semesters s
            SET semester_category = 'TOURNAMENT'
            FROM tournament_configurations tc
            WHERE tc.semester_id = s.id
              AND s.semester_category IS NULL
        """))
        db.commit()

        # Tournaments without a game_configuration → assign default (outfield_default) preset
        from app.models.game_configuration import GameConfiguration
        default_preset = db.query(GamePreset).filter(GamePreset.code == "outfield_default").first()
        if default_preset:
            from app.models.tournament_configuration import TournamentConfiguration
            tournament_ids_with_cfg = {
                row.semester_id
                for row in db.query(GameConfiguration).all()
            }
            all_tournament_ids = {
                row.semester_id
                for row in db.query(TournamentConfiguration).all()
            }
            missing = all_tournament_ids - tournament_ids_with_cfg
            for tid in missing:
                db.add(GameConfiguration(semester_id=tid, game_preset_id=default_preset.id))
            if missing:
                db.commit()
                print(f"       → assigned default game preset to {len(missing)} tournament(s)")

        # Summary
        print("\n" + "="*70)
        print("  Bootstrap complete")
        print("="*70)
        tt_count = db.query(TournamentType).count()
        gp_count = db.query(GamePreset).count()
        campus_count = db.query(Campus).count()
        admin_u = db.query(User).filter(User.email == "admin@lfa.com").first()
        instr_u = db.query(User).filter(User.email == "instructor@lfa.com").first()
        boot_club = db.query(Club).filter(Club.code == _CLUB_CODE).first()
        team_count = db.query(Team).filter(Team.club_id == boot_club.id).count() if boot_club else 0
        print(f"  TournamentType : {tt_count} rows")
        print(f"  GamePreset     : {gp_count} rows")
        print(f"  Campus         : {campus_count} (id={campus.id}, '{campus.name}')")
        print(f"  Admin          : {admin_u.email if admin_u else '—'}  /  admin123")
        print(f"  Instructor     : {instr_u.email if instr_u else '—'}  /  instructor123")
        print(f"  Club           : {boot_club.name if boot_club else '—'} (id={boot_club.id if boot_club else '—'})")
        player_count = sum(
            db.query(TeamMember).filter(TeamMember.team_id == t.id).count()
            for t in db.query(Team).filter(Team.club_id == boot_club.id).all()
        ) if boot_club else 0
        print(f"  Teams          : {team_count} (LFA U15 / LFA U18 / LFA Adult, 12 players each)")
        print(f"  Players        : {player_count} total (UK English names)")
        print()
        print("  Run validate:  PYTHONPATH=. python scripts/validate_seed_state.py")
        print("="*70 + "\n")

    except Exception:
        db.rollback()
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    run()
