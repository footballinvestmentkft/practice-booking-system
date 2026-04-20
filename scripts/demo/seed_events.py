#!/usr/bin/env python3
"""
Events module demo seed — creates realistic event data for frontend validation.

Usage:
    python scripts/seed_events_demo.py

⚠️  DESTRUCTIVE: truncates all operational tables (semesters, sessions, campuses,
    locations, enrollments, tournament data, etc.) while preserving user accounts.

The seed is IDEMPOTENT — running it multiple times always produces the same
clean state (truncate → recreate).

Result dataset:
  2 Locations  :  Budapest (CENTER)  ·  Debrecen (PARTNER)
  4 Campuses   :  3 × Budapest  ·  1 × Debrecen
  10 Semesters :  3 Academy  ·  4 Tournament  ·  3 Camp
  33 Sessions  :  16 MATCH  ·  17 TRAINING
"""
import sys
from pathlib import Path
from datetime import date, datetime, timedelta

# Make sure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.database import SessionLocal
from app.models.location import Location, LocationType
from app.models.campus import Campus
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.session import Session as SessionModel, SessionType, EventCategory
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_type import TournamentType
from app.models.game_configuration import GameConfiguration
from app.models.tournament_reward_config import TournamentRewardConfig
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.tournament_ranking import TournamentRanking
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.core.security import get_password_hash
from app.services.tournament.session_generation.session_generator import TournamentSessionGenerator


# ── helpers ───────────────────────────────────────────────────────────────────

def _dt(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, 0)


TRUNCATE_SQL = """
    TRUNCATE TABLE
        bookings,
        event_reward_logs,
        semester_enrollments,
        semester_instructors,
        tournament_participations,
        tournament_configurations,
        tournament_status_history,
        tournament_stats,
        tournament_rankings,
        tournament_team_enrollments,
        tournament_badges,
        tournament_reward_configs,
        match_results,
        match_structures,
        sessions,
        semesters,
        campus_schedule_configs,
        campuses,
        location_master_instructors,
        locations,
        system_events,
        audit_logs
    CASCADE
"""


# ── main seed ─────────────────────────────────────────────────────────────────

def seed():
    db = SessionLocal()
    try:
        # ── 0. Reset ──────────────────────────────────────────────────────────
        print("🗑  Truncating operational tables (preserving users)…")
        db.execute(text(TRUNCATE_SQL))
        db.commit()
        print("   ✓ Done\n")

        today = date.today()

        # ── 1. Locations ──────────────────────────────────────────────────────
        print("📍 Locations…")
        budapest = Location(
            name="LFA Budapest Education Center",
            city="Budapest",
            country="Hungary",
            country_code="HU",
            location_code="BDPST",
            postal_code="1146",
            address="Istvánmezei út 1-3, Budapest",
            location_type=LocationType.CENTER,
            is_active=True,
        )
        debrecen = Location(
            name="LFA Debrecen Partner",
            city="Debrecen",
            country="Hungary",
            country_code="HU",
            location_code="DEBR",
            postal_code="4031",
            address="Oláh Gábor u. 5, Debrecen",
            location_type=LocationType.PARTNER,
            is_active=True,
        )
        db.add_all([budapest, debrecen])
        db.flush()
        print(f"   ✓ Budapest  (CENTER)  id={budapest.id}")
        print(f"   ✓ Debrecen  (PARTNER) id={debrecen.id}\n")

        # ── 1b. Demo players (idempotent — keyed by email) ────────────────────
        print("👤 Players…")
        _pw = get_password_hash("Player123!")
        _player_emails = [f"demo.youth.player{i}@lfa-seed.hu" for i in range(1, 9)]
        players = []
        for i, email in enumerate(_player_emails, 1):
            u = db.query(User).filter(User.email == email).first()
            if not u:
                u = User(
                    name=f"Demo Youth Player {i}",
                    email=email,
                    password_hash=_pw,
                    role=UserRole.STUDENT,
                    is_active=True,
                )
                db.add(u)
            players.append(u)
        db.flush()
        print(f"   ✓ {len(players)} demo youth players (emails: demo.youth.player1-8@lfa-seed.hu)\n")

        # ── 2. Campuses ───────────────────────────────────────────────────────
        print("🏫 Campuses…")
        buda_main = Campus(
            location_id=budapest.id,
            name="Buda Training Complex",
            venue="Outdoor fields + gym",
            address="Vérmező út 1, 1012 Budapest",
            is_active=True,
        )
        buda_indoor = Campus(
            location_id=budapest.id,
            name="Indoor Arena — Pest",
            venue="3 indoor courts, capacity 120",
            address="Stefánia út 3-5, 1143 Budapest",
            is_active=True,
        )
        buda_youth = Campus(
            location_id=budapest.id,
            name="Youth Development Hub",
            venue="Small-sided pitches + classroom",
            address="Hungária krt. 44, 1087 Budapest",
            is_active=True,
        )
        debr_main = Campus(
            location_id=debrecen.id,
            name="Főnix Sportközpont",
            venue="Community sport centre — 2 pitches",
            address="Oláh Gábor u. 5, 4031 Debrecen",
            is_active=True,
        )
        db.add_all([buda_main, buda_indoor, buda_youth, debr_main])
        db.flush()
        print(f"   ✓ Budapest: {buda_main.name} · {buda_indoor.name} · {buda_youth.name}")
        print(f"   ✓ Debrecen: {debr_main.name}\n")

        # ── 3. Academy seasons (training-session parents) ─────────────────────
        print("🎓 Academy semesters…")
        acad_youth = Semester(
            code="ACAD-YOUTH-BUD-2026-S1",
            name="Youth Academy Season — Budapest Spring 2026",
            semester_category=SemesterCategory.ACADEMY_SEASON,
            status=SemesterStatus.ONGOING,
            age_group="YOUTH",
            location_id=budapest.id,
            campus_id=buda_main.id,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=60),
            enrollment_cost=2500,
            specialization_type="LFA_PLAYER_YOUTH",
        )
        acad_pre = Semester(
            code="ACAD-PRE-BUD-2026-S1",
            name="Pre-Academy Season — Budapest Spring 2026",
            semester_category=SemesterCategory.ACADEMY_SEASON,
            status=SemesterStatus.ONGOING,
            age_group="PRE",
            location_id=budapest.id,
            campus_id=buda_youth.id,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=60),
            enrollment_cost=2000,
            specialization_type="LFA_PLAYER_PRE",
        )
        acad_debr = Semester(
            code="MINI-YOUTH-DEB-2026-S1",
            name="Youth Mini Season — Debrecen Spring 2026",
            semester_category=SemesterCategory.MINI_SEASON,
            status=SemesterStatus.READY_FOR_ENROLLMENT,
            age_group="YOUTH",
            location_id=debrecen.id,
            campus_id=debr_main.id,
            start_date=today + timedelta(days=14),
            end_date=today + timedelta(days=74),
            enrollment_cost=1800,
            specialization_type="LFA_PLAYER_YOUTH",
        )
        db.add_all([acad_youth, acad_pre, acad_debr])

        # ── 4. Tournaments ────────────────────────────────────────────────────
        print("🏆 Tournaments…")

        # T1: H2H YOUTH/LEAGUE — ONGOING (Budapest CENTER) — 3/8 checked-in
        t1 = Semester(
            code="TOURN-YOUTH-H2H-2026-Q1",
            name="LFA Youth Cup 2026 — Q1 Budapest",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.ONGOING,
            tournament_status="IN_PROGRESS",
            age_group="YOUTH",
            location_id=budapest.id,
            campus_id=buda_main.id,
            start_date=today - timedelta(days=7),
            end_date=today + timedelta(days=14),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        # T2: INDIVIDUAL RANKING PRO — ONGOING (Budapest Indoor)
        t2 = Semester(
            code="TOURN-PRO-IR-2026-Q1",
            name="LFA Pro Ranking Series — Spring 2026",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.ONGOING,
            tournament_status="IN_PROGRESS",
            age_group="PRO",
            location_id=budapest.id,
            campus_id=buda_indoor.id,
            start_date=today - timedelta(days=3),
            end_date=today + timedelta(days=14),
            enrollment_cost=500,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        # T3: H2H/KNOCKOUT — ONGOING (Budapest Youth Hub) — 8/8 checked-in
        t3 = Semester(
            code="TOURN-YOUTH-KO-2026-Q1",
            name="LFA Youth Knockout Cup 2026 — Budapest",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.ONGOING,
            tournament_status="IN_PROGRESS",
            age_group="YOUTH",
            location_id=budapest.id,
            campus_id=buda_youth.id,
            start_date=today - timedelta(days=5),
            end_date=today + timedelta(days=10),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        # T4: H2H/SWISS — ONGOING (Budapest Indoor) — 8/8 checked-in
        t4 = Semester(
            code="TOURN-AMT-SWISS-2026-Q1",
            name="LFA Amateur Swiss Open 2026 — Budapest",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.ONGOING,
            tournament_status="IN_PROGRESS",
            age_group="AMATEUR",
            location_id=budapest.id,
            campus_id=buda_indoor.id,
            start_date=today - timedelta(days=4),
            end_date=today + timedelta(days=12),
            enrollment_cost=250,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        # T5: H2H/GROUP_KNOCKOUT — ONGOING (Buda Main) — 8/8 checked-in
        t5 = Semester(
            code="TOURN-AMT-GK-2026-Q1",
            name="LFA Groups & Knockout Championship 2026 — Budapest",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.ONGOING,
            tournament_status="IN_PROGRESS",
            age_group="AMATEUR",
            location_id=budapest.id,
            campus_id=buda_main.id,
            start_date=today - timedelta(days=6),
            end_date=today + timedelta(days=15),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        # T6: H2H YOUTH/LEAGUE — COMPLETED (Budapest, historical)
        t6 = Semester(
            code="TOURN-YOUTH-H2H-2025-Q4",
            name="LFA Youth Cup 2025 — Q4 Budapest",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.COMPLETED,
            tournament_status="COMPLETED",
            age_group="YOUTH",
            location_id=budapest.id,
            campus_id=buda_main.id,
            start_date=today - timedelta(days=90),
            end_date=today - timedelta(days=60),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        db.add_all([t1, t2, t3, t4, t5, t6])
        db.flush()  # get IDs for TournamentConfiguration FK

        # ── 4b. TournamentConfigurations ──────────────────────────────────────
        print("⚙️  Tournament configurations…")
        tt_league        = db.query(TournamentType).filter(TournamentType.code == "league").first()
        tt_knockout      = db.query(TournamentType).filter(TournamentType.code == "knockout").first()
        tt_swiss         = db.query(TournamentType).filter(TournamentType.code == "swiss").first()
        tt_group_knockout= db.query(TournamentType).filter(TournamentType.code == "group_knockout").first()
        tt_score_based   = db.query(TournamentType).filter(TournamentType.code == "score_based").first()

        # T1: ONGOING H2H/LEAGUE — 3/8 checked-in (split-brain demo)
        if tt_league:
            db.add(TournamentConfiguration(
                semester_id=t1.id,
                tournament_type_id=tt_league.id,
                participant_type="INDIVIDUAL",
                is_multi_day=True,
                max_players=16,
                match_duration_minutes=90,
                break_duration_minutes=15,
                parallel_fields=2,
                sessions_generated=False,
            ))
        # T2: ONGOING INDIVIDUAL_RANKING / SCORE_BASED — tournament_type_id is now set (Phase 1)
        # score_based = LFA football player skill challenges (goals, points, repetitions)
        db.add(TournamentConfiguration(
            semester_id=t2.id,
            tournament_type_id=tt_score_based.id if tt_score_based else None,
            scoring_type="SCORE_BASED",
            measurement_unit="goals",
            ranking_direction="DESC",
            participant_type="INDIVIDUAL",
            is_multi_day=False,
            max_players=32,
            parallel_fields=1,
        ))
        # T3: ONGOING H2H/KNOCKOUT — 8/8 checked-in
        if tt_knockout:
            db.add(TournamentConfiguration(
                semester_id=t3.id,
                tournament_type_id=tt_knockout.id,
                participant_type="INDIVIDUAL",
                is_multi_day=True,
                max_players=8,
                match_duration_minutes=60,
                break_duration_minutes=10,
                parallel_fields=2,
                sessions_generated=False,
            ))
        # T4: ONGOING H2H/SWISS — 8/8 checked-in
        if tt_swiss:
            db.add(TournamentConfiguration(
                semester_id=t4.id,
                tournament_type_id=tt_swiss.id,
                participant_type="INDIVIDUAL",
                is_multi_day=False,
                max_players=16,
                match_duration_minutes=75,
                break_duration_minutes=10,
                parallel_fields=2,
                sessions_generated=False,
            ))
        # T5: ONGOING H2H/GROUP_KNOCKOUT — 8/8 checked-in
        if tt_group_knockout:
            db.add(TournamentConfiguration(
                semester_id=t5.id,
                tournament_type_id=tt_group_knockout.id,
                participant_type="INDIVIDUAL",
                is_multi_day=True,
                max_players=16,
                match_duration_minutes=60,
                break_duration_minutes=10,
                parallel_fields=2,
                sessions_generated=False,
            ))
        # T6: COMPLETED H2H/LEAGUE — historical final standings
        if tt_league:
            db.add(TournamentConfiguration(
                semester_id=t6.id,
                tournament_type_id=tt_league.id,
                participant_type="INDIVIDUAL",
                is_multi_day=True,
                max_players=16,
                match_duration_minutes=90,
                break_duration_minutes=15,
                parallel_fields=2,
                sessions_generated=False,
            ))

        # ── 4c. GameConfigurations ─────────────────────────────────────────────
        print("🎮 Game configurations…")
        _football_game_config = {
            "metadata": {"min_players": 4, "game_type": "football"},
            "match_rules": {"scoring": "goals", "overtime": False},
            "skill_weights": {"dribbling": 1.5, "shooting": 1.3, "passing": 1.0, "defending": 0.8},
        }
        # All H2H tournaments + T6 (COMPLETED) get game configs
        for _t in [t1, t2, t3, t4, t5, t6]:
            db.add(GameConfiguration(semester_id=_t.id, game_preset_id=None, game_config=_football_game_config))

        # ── 4d. TournamentRewardConfigs ────────────────────────────────────────
        print("🎁 Reward configs…")
        _standard_reward = {
            "template_name": "Standard Football",
            "custom_config": False,
            "skill_mappings": [
                {"skill": "Dribbling", "weight": 1.5, "category": "TECHNICAL", "enabled": True},
                {"skill": "Shooting",  "weight": 1.3, "category": "TECHNICAL", "enabled": True},
                {"skill": "Passing",   "weight": 1.0, "category": "TECHNICAL", "enabled": True},
                {"skill": "Defending", "weight": 0.8, "category": "PHYSICAL",  "enabled": True},
            ],
            "first_place":    {"credits": 500, "xp_multiplier": 2.0, "badges": []},
            "second_place":   {"credits": 250, "xp_multiplier": 1.5, "badges": []},
            "third_place":    {"credits": 100, "xp_multiplier": 1.2, "badges": []},
            "participation":  {"credits":  50, "xp_multiplier": 1.0, "badges": []},
        }
        for _t in [t1, t2, t3, t4, t5, t6]:
            db.add(TournamentRewardConfig(semester_id=_t.id, reward_policy_name="Standard Football", reward_config=_standard_reward))

        # ── 4e. Licenses + Enrollments for all tournaments ────────────────────
        print("📋 Licenses + Enrollments (all tournaments)…")
        from datetime import timezone as _tz
        _now = datetime.now(_tz.utc)
        for i, player in enumerate(players):
            # Create LFA_FOOTBALL_PLAYER license if not exists
            lic = db.query(UserLicense).filter(
                UserLicense.user_id == player.id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                UserLicense.is_active == True,
            ).first()
            if not lic:
                lic = UserLicense(
                    user_id=player.id,
                    specialization_type="LFA_FOOTBALL_PLAYER",
                    started_at=_now,
                    is_active=True,
                )
                db.add(lic)
            db.flush()
            # T1 league: 3/8 checked-in (split-brain demo)
            db.add(SemesterEnrollment(
                semester_id=t1.id, user_id=player.id, user_license_id=lic.id,
                request_status=EnrollmentStatus.APPROVED, is_active=True,
                tournament_checked_in_at=_now if i < 3 else None,
            ))
            # T2 IR, T3 knockout, T4 swiss, T5 group_knockout: all 8 checked-in
            for _tourn in [t2, t3, t4, t5]:
                db.add(SemesterEnrollment(
                    semester_id=_tourn.id, user_id=player.id, user_license_id=lic.id,
                    request_status=EnrollmentStatus.APPROVED, is_active=True,
                    tournament_checked_in_at=_now,
                ))
            # T6 completed: all 8 checked-in
            db.add(SemesterEnrollment(
                semester_id=t6.id, user_id=player.id, user_license_id=lic.id,
                request_status=EnrollmentStatus.APPROVED, is_active=True,
                tournament_checked_in_at=_now,
            ))
        db.flush()

        # ── 4f. Generate bracket sessions for all ONGOING + T6 ────────────────
        # Must commit first so the generator can see the enrollments in a fresh query
        print("⚡ Generating bracket sessions (T1–T5 ONGOING + T6 COMPLETED)…")
        db.commit()
        gen = TournamentSessionGenerator(db)
        _gen_targets = [
            (t1.id, "T1 league    (3/8 checked-in)"),
            (t2.id, "T2 ind.rank  (8/8 checked-in)"),
            (t3.id, "T3 knockout  (8/8 checked-in)"),
            (t4.id, "T4 swiss     (8/8 checked-in)"),
            (t5.id, "T5 grp+ko    (8/8 checked-in)"),
            (t6.id, "T6 league    (COMPLETED)     "),
        ]
        for tourn_id, label in _gen_targets:
            ok, msg, _ = gen.generate_sessions(tourn_id)
            if ok:
                db.commit()
                sc = db.query(SessionModel).filter(SessionModel.semester_id==tourn_id).count()
                print(f"   ✓ {label}: {sc} bracket sessions")
            else:
                print(f"   ⚠ {label}: {msg}")

        # ── 4g. T6 Final Standings (8-player league, COMPLETED) ───────────────
        print("🏅 T6 final standings…")
        # 8-player round-robin: 7 rounds × 4 matches = 28 total matches
        # Points: W=3, D=1, L=0 · All sums balance (wins=losses, draws=draws×2)
        _t4_standings = [
            # (rank, player_idx, wins, draws, losses, goals_for, goals_against, points)
            (1, 0, 6, 0, 1, 18,  6, 18),
            (2, 1, 5, 2, 0, 14,  5, 17),
            (3, 2, 5, 0, 2, 13,  8, 15),
            (4, 3, 4, 0, 3, 11, 10, 12),
            (5, 4, 2, 2, 3,  8, 10,  8),
            (6, 5, 1, 2, 4,  6, 12,  5),
            (7, 6, 1, 0, 6,  4, 15,  3),
            (8, 7, 0, 2, 5,  5, 13,  2),
        ]
        for rank, pidx, w, d, l, gf, ga, pts in _t4_standings:
            db.add(TournamentRanking(
                tournament_id=t6.id,
                user_id=players[pidx].id,
                participant_type="INDIVIDUAL",
                rank=rank,
                points=pts,
                wins=w,
                draws=d,
                losses=l,
                goals_for=gf,
                goals_against=ga,
            ))
        db.commit()
        print(f"   ✓ {len(_t4_standings)} final standings seeded for T4\n")

        # ── 5. Camps ──────────────────────────────────────────────────────────
        print("⛺ Camps…")

        # C1: Summer YOUTH — READY_FOR_ENROLLMENT (Budapest CENTER)
        c1 = Semester(
            code="CAMP-SUMMER26-BUDA-YOUTH",
            name="Summer Football Academy 2026 — Budapest YOUTH",
            semester_category=SemesterCategory.CAMP,
            status=SemesterStatus.READY_FOR_ENROLLMENT,
            age_group="YOUTH",
            location_id=budapest.id,
            campus_id=buda_main.id,
            start_date=today + timedelta(days=60),
            end_date=today + timedelta(days=67),
            enrollment_cost=800,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        # C2: Spring PRE — ONGOING (Debrecen PARTNER, in progress now)
        c2 = Semester(
            code="CAMP-SPRING26-DEB-PRE",
            name="Spring Pre-Academy Camp — Debrecen 2026",
            semester_category=SemesterCategory.CAMP,
            status=SemesterStatus.ONGOING,
            age_group="PRE",
            location_id=debrecen.id,
            campus_id=debr_main.id,
            start_date=today - timedelta(days=2),
            end_date=today + timedelta(days=5),
            enrollment_cost=600,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        # C3: Winter AMATEUR — COMPLETED (Budapest CENTER, historical)
        c3 = Semester(
            code="CAMP-WINTER25-BUDA-AMT",
            name="Winter Conditioning Camp 2025 — Budapest AMATEUR",
            semester_category=SemesterCategory.CAMP,
            status=SemesterStatus.COMPLETED,
            age_group="AMATEUR",
            location_id=budapest.id,
            campus_id=buda_indoor.id,
            start_date=today - timedelta(days=55),
            end_date=today - timedelta(days=48),
            enrollment_cost=700,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        db.add_all([c1, c2, c3])
        db.flush()  # get IDs before building sessions

        # ── 6. Sessions (Academy + Camp — T1/T4 sessions already generated above) ──
        print("📅 Sessions (Academy + Camp)…")
        sessions = []

        # ACAD YOUTH — 8 upcoming TRAINING sessions (Buda Main, every 3 days)
        for i in range(8):
            d = today + timedelta(days=i * 3)
            sessions.append(SessionModel(
                title=f"Youth Academy — Week {i + 1} Training",
                date_start=_dt(d, 9),
                date_end=_dt(d, 11),
                session_type=SessionType.on_site,
                event_category=EventCategory.TRAINING,
                session_status="scheduled",
                semester_id=acad_youth.id,
                campus_id=buda_main.id,
                location="Buda Training Complex",
                capacity=20,
                base_xp=75,
                sport_type="Football",
                level="YOUTH",
            ))

        # ACAD PRE — 6 upcoming TRAINING sessions (Youth Hub, every 4 days)
        for i in range(6):
            d = today + timedelta(days=i * 4 + 1)
            sessions.append(SessionModel(
                title=f"Pre-Academy — Week {i + 1} Skills",
                date_start=_dt(d, 15),
                date_end=_dt(d, 17),
                session_type=SessionType.on_site,
                event_category=EventCategory.TRAINING,
                session_status="scheduled",
                semester_id=acad_pre.id,
                campus_id=buda_youth.id,
                location="Youth Development Hub",
                capacity=15,
                base_xp=50,
                sport_type="Football",
                level="PRE",
            ))

        # CAMP C2 (ONGOING Debrecen) — 5 daily sessions (2 past + 3 future)
        for i in range(5):
            d = today - timedelta(days=2) + timedelta(days=i)
            status = "completed" if i < 2 else "scheduled"
            sessions.append(SessionModel(
                title=f"Spring Pre-Academy Camp — Day {i + 1}",
                date_start=_dt(d, 9),
                date_end=_dt(d, 13),
                session_type=SessionType.on_site,
                event_category=EventCategory.TRAINING,
                session_status=status,
                semester_id=c2.id,
                campus_id=debr_main.id,
                location="Főnix Sportközpont",
                capacity=18,
                base_xp=100,
                sport_type="Football",
                level="PRE",
            ))

        db.add_all(sessions)
        db.commit()

        # ── Summary ───────────────────────────────────────────────────────────
        n_train = sum(1 for s in sessions if s.event_category == EventCategory.TRAINING)
        _bracket_counts = {
            label: db.query(SessionModel).filter(SessionModel.semester_id==tid).count()
            for tid, label in [
                (t1.id, "T1-league"), (t2.id, "T2-ir"), (t3.id, "T3-ko"),
                (t4.id, "T4-swiss"), (t5.id, "T5-gk"), (t6.id, "T6-completed"),
            ]
        }
        total_bracket = sum(_bracket_counts.values())
        total_sessions = len(sessions) + total_bracket

        print(f"\n✅ Seed complete!\n")
        print(f"   Locations     : 2  (Budapest CENTER · Debrecen PARTNER)")
        print(f"   Campuses      : 4  (3 × Budapest · 1 × Debrecen)")
        print(f"   Semesters     : 12 (3 Academy/Mini · 6 Tournament · 3 Camp)")
        print(f"   Sessions      : {total_sessions}  ({total_bracket} bracket MATCH · {n_train} TRAINING)")
        for lbl, cnt in _bracket_counts.items():
            print(f"     {lbl}: {cnt} sessions")
        print(f"   Game configs  : 6  (all tournaments)")
        print(f"   Reward cfgs   : 6  (all tournaments)")
        print(f"   Players       : {len(players)}  (demo.youth.player1-8@lfa-seed.hu)")
        print(f"   Enrollments   : {len(players)*6}  (T1: 3/8 checked-in · T2–T6: 8/8 checked-in)")
        print(f"   Standings     : 8  (T6 COMPLETED final rankings)\n")
        print(f"   → http://localhost:8000/admin/events")
        print(f"   → http://localhost:8000/admin/camps")
        print(f"   → http://localhost:8000/admin/events/tournaments")
        print(f"   → http://localhost:8000/admin/sessions?event_category=TRAINING")
        print(f"   → http://localhost:8000/admin/sessions?event_category=MATCH")

    except Exception as exc:
        db.rollback()
        import traceback
        print(f"\n❌ Seed failed: {exc}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    seed()
