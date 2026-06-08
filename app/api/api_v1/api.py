"""
API v1 router registry — 66 sub-routers grouped into 6 domain sections.

Sections (in registration order):
  CORE          auth, users, sessions, bookings, attendance, feedback,
                reports, analytics, gamification, projects, notifications,
                messages, debug, adaptive_learning
  EDUCATION     semesters, groups, quiz, curriculum, progression, tracks,
                certificates, students, competency
  SPECIALIZATIONS  specializations, licenses, parallel_specializations,
                   lfa_player, gancuju, internship, coach, spec_info,
                   motivation, public_profile, license_renewal
  TOURNAMENTS   tournaments, tournament_types, game_presets, teams, pitches,
                session_results, session_groups
  FINANCE       invoices, coupons, invitation_codes, semester_enrollments,
                payment_verification, conflict_check
  ADMINISTRATION admin, audit, system_events, health, semester_generator,
                 locations, campuses, instructor_availability,
                 instructor_assignments, instructor_management,
                 lfa_player_generators, admin_players, sandbox, sandbox_data
"""
from fastapi import APIRouter

# ── CORE ──────────────────────────────────────────────────────────────────────
from .endpoints import (
    auth,
    users,
    sessions,
    bookings,
    attendance,
    feedback,
    reports,
    analytics,
    gamification,
    projects,
    notifications,
    messages,
    debug,
    adaptive_learning,
)

# ── EDUCATION ─────────────────────────────────────────────────────────────────
from .endpoints import (
    semesters,
    groups,
    quiz,
    curriculum,
    curriculum_adaptive,
    progression,
    tracks,
    certificates,
    students,
    competency,
)
from .endpoints.semesters import academy_generator, schedule_generator
from .endpoints.enrollments import conflict_check

# ── SPECIALIZATIONS ───────────────────────────────────────────────────────────
from .endpoints import (
    specializations,
    parallel_specializations,
    licenses,
    lfa_player,
    gancuju,
    internship,
    coach,
    spec_info,
    motivation,
    public_profile,
    license_renewal,
)
from .endpoints.lfa_player import self_assessment as lfa_player_self_assessment
from .endpoints.periods import lfa_player_generators

# ── TOURNAMENTS ───────────────────────────────────────────────────────────────
from .endpoints import (
    tournaments,
    tournament_types,
    game_presets,
    teams,
    pitches,
)
from .endpoints.sessions import results as session_results
from .endpoints import session_groups
from .endpoints.tournaments import generate_sessions, admin_enroll

# ── FINANCE ───────────────────────────────────────────────────────────────────
from .endpoints import (
    invoices,
    coupons,
    invitation_codes,
    semester_enrollments,
    payment_verification,
)

# ── ADMINISTRATION ────────────────────────────────────────────────────────────
from .endpoints import (
    admin,
    audit,
    health,
)
from .endpoints import system_events
from .endpoints import admin_players
from .endpoints.sandbox import run_test as sandbox
from .endpoints.sandbox import data as sandbox_data
from .endpoints import (
    semester_generator,
    locations,
    campuses,
    instructor_availability,
    instructor_assignments,
    instructor_management,
)

# ──────────────────────────────────────────────────────────────────────────────

api_router = APIRouter()

# ── CORE ──────────────────────────────────────────────────────────────────────
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(semesters.router, prefix="/semesters", tags=["semesters"])
api_router.include_router(academy_generator.router, prefix="/semesters", tags=["semesters", "academy-season"])
api_router.include_router(schedule_generator.router, prefix="/semesters", tags=["semesters", "scheduling"])
api_router.include_router(conflict_check.router, prefix="/enrollments", tags=["enrollments", "conflict-check"])
api_router.include_router(groups.router, prefix="/groups", tags=["groups"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(session_results.router, prefix="/sessions", tags=["sessions", "game-results"])
api_router.include_router(bookings.router, prefix="/bookings", tags=["bookings"])
api_router.include_router(attendance.router, prefix="/attendance", tags=["attendance"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(gamification.router, prefix="/gamification", tags=["gamification"])
api_router.include_router(quiz.router, prefix="/quizzes", tags=["quizzes"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(messages.router, prefix="/messages", tags=["messages"])
api_router.include_router(debug.router, prefix="/debug", tags=["debug"])
api_router.include_router(adaptive_learning.router, prefix="/adaptive-learning", tags=["adaptive-learning"])

# ── SPECIALIZATIONS ───────────────────────────────────────────────────────────
api_router.include_router(specializations.router, prefix="/specializations", tags=["specializations"])
api_router.include_router(payment_verification.router, prefix="/payment-verification", tags=["payment-verification"])
api_router.include_router(licenses.router, prefix="/licenses", tags=["licenses"])
api_router.include_router(parallel_specializations.router, prefix="/parallel-specializations", tags=["parallel-specializations"])
api_router.include_router(progression.router, prefix="/progression", tags=["progression"])
api_router.include_router(tracks.router, prefix="/tracks", tags=["tracks"])
api_router.include_router(certificates.router, prefix="/certificates", tags=["certificates"])
api_router.include_router(students.router, prefix="/students", tags=["students"])
api_router.include_router(curriculum.router, prefix="/curriculum", tags=["curriculum"])
api_router.include_router(curriculum_adaptive.router, prefix="/curriculum-adaptive", tags=["curriculum-adaptive-learning"])
api_router.include_router(competency.router, prefix="/competency", tags=["competency"])

# ── ADMINISTRATION ────────────────────────────────────────────────────────────
api_router.include_router(health.router, prefix="/health", tags=["health-monitoring"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin-dashboard"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit-logs"])
api_router.include_router(system_events.router, prefix="/system-events", tags=["system-events"])
api_router.include_router(semester_enrollments.router, prefix="/semester-enrollments", tags=["semester-enrollments"])
api_router.include_router(invoices.router, prefix="/invoices", tags=["invoices"])
api_router.include_router(coupons.router, prefix="", tags=["coupons"])
api_router.include_router(invitation_codes.router, prefix="", tags=["invitation-codes"])
api_router.include_router(lfa_player.router, prefix="/lfa-player", tags=["lfa-player"])
api_router.include_router(lfa_player_self_assessment.router, prefix="/lfa-player", tags=["lfa-player"])
api_router.include_router(gancuju.router, prefix="/gancuju", tags=["gancuju"])
api_router.include_router(internship.router, prefix="/internship", tags=["internship"])
api_router.include_router(coach.router, prefix="/coach", tags=["coach"])
api_router.include_router(motivation.router, prefix="/licenses", tags=["motivation-assessment"])
api_router.include_router(public_profile.router, prefix="/public", tags=["public-profile"])
api_router.include_router(semester_generator.router, prefix="/admin/semesters", tags=["semester-generator"])
api_router.include_router(lfa_player_generators.router, prefix="/admin/periods", tags=["period-generators", "lfa-player"])
api_router.include_router(locations.router, prefix="/admin/locations", tags=["locations"])
api_router.include_router(instructor_availability.router, prefix="/instructor-availability", tags=["instructor-availability"])
api_router.include_router(instructor_assignments.router, prefix="/instructor-assignments", tags=["instructor-assignments"])
api_router.include_router(license_renewal.router, prefix="/license-renewal", tags=["license-renewal"])
api_router.include_router(campuses.router, prefix="/admin", tags=["campuses"])
api_router.include_router(spec_info.router, prefix="/spec-info", tags=["spec-info"])
api_router.include_router(instructor_management.router, prefix="/instructor-management", tags=["instructor-management"])
api_router.include_router(session_groups.router, prefix="/session-groups", tags=["session-groups"])

# ── TOURNAMENTS ───────────────────────────────────────────────────────────────
api_router.include_router(tournaments.router, prefix="/tournaments", tags=["tournaments"])
api_router.include_router(tournament_types.router, prefix="/tournament-types", tags=["tournament-types"])
api_router.include_router(game_presets.router, prefix="/game-presets", tags=["game-presets"])
api_router.include_router(teams.router, prefix="", tags=["teams"])
api_router.include_router(pitches.router, prefix="", tags=["pitches", "pitch-instructor-assignments"])
api_router.include_router(generate_sessions.router, prefix="/tournaments", tags=["tournaments", "session-generation"])
api_router.include_router(admin_enroll.router, prefix="/tournaments", tags=["tournaments", "admin-enrollment"])
api_router.include_router(admin_players.router, prefix="/admin", tags=["admin", "player-provisioning"])
api_router.include_router(sandbox.router, prefix="/sandbox", tags=["sandbox-testing"])
api_router.include_router(sandbox_data.router, prefix="/sandbox", tags=["sandbox-data"])
