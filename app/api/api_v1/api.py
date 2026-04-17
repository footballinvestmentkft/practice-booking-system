from fastapi import APIRouter

from .endpoints import (
    auth,
    users,
    semesters,
    groups,
    sessions,
    bookings,
    attendance,
    feedback,
    reports,
    analytics,
    gamification,
    quiz,
    projects,
    notifications,
    messages,
    debug,
    adaptive_learning,
    specializations,  # 🎓 NEW: Add specializations import
    payment_verification,  # 💰 NEW: Add payment verification import
    licenses,  # 🏮 NEW: Add GānCuju™️©️ license system
    parallel_specializations,  # 🎓🔀 NEW: Add parallel specialization system
    progression,  # 📈 NEW: Add progression tracking system
    tracks,  # 🎯 NEW: Add track-based education system
    certificates,  # 🏆 NEW: Add certificate management system
    students,  # 🎓 NEW: Add student dashboard endpoints
    curriculum,  # 📚 NEW: Add curriculum system endpoints
    curriculum_adaptive,  # 🧠 NEW: Add curriculum-based adaptive learning
    competency,  # 🎯 NEW: Add competency tracking system
    health,  # 🏥 P2: Add health monitoring endpoints
    admin,  # 👑 NEW: Add admin dashboard endpoints
    audit,  # 🔍 P0: Add audit log system
    semester_enrollments,  # 🎓 NEW: Add semester enrollment management
    invoices,  # 💳 NEW: Add invoice management system
    coupons,  # 🎟️ NEW: Add coupon management system
    invitation_codes,  # 🎁 NEW: Add partner invitation code system
    lfa_player,  # ⚽ NEW: Add LFA Player license API
    gancuju,  # 🥋 NEW: Add GānCuju belt/level system API
    internship,  # 📚 NEW: Add Internship XP system API
    coach,  # 👨‍🏫 NEW: Add Coach certification system API
    motivation,  # 🎯 NEW: Add motivation assessment system
    public_profile,  # 👤 NEW: Add FIFA-style public profile system
    semester_generator,  # 📅 NEW: Add semester generator system
    locations,  # 📍 NEW: Add location management system
    instructor_availability,  # 👨‍🏫 NEW: Add instructor availability management
    instructor_assignments,  # 📋 NEW: Add instructor assignment request system
    license_renewal,  # 💰 NEW: Add license renewal system (Fase 2)
    campuses,  # 🏫 NEW: Add campus management system
    spec_info,  # 🎯 NEW: Add spec services information API
    instructor_management,  # 👨‍🏫 NEW: Add two-tier instructor management system
    session_groups,  # 👥 NEW: Add dynamic session group assignment system
    tournaments,  # 🏆 NEW: Add one-day tournament generator system
    tournament_types,  # 🎯 NEW: Add tournament type system
    game_presets,  # 🎮 P3: Add game preset system
    teams,  # 👥 NEW: Team management + invite flow
    pitches  # 🏟️ NEW: Pitch instructor assignment system
)

from .endpoints.sandbox import run_test as sandbox  # 🧪 NEW: Add sandbox test system
from .endpoints.sandbox import data as sandbox_data  # 🧪 NEW: Add sandbox data endpoints

from .endpoints.sessions import results as session_results  # 🏆 NEW: Game results management

from .endpoints.semesters import academy_generator  # 🏫 NEW: Add Academy Season generator
from .endpoints.semesters import schedule_generator  # 📅 NEW: MINI_SEASON / ACADEMY_SEASON session generation
from .endpoints.enrollments import conflict_check  # ⚠️ NEW: Add enrollment conflict detection

from .endpoints.periods import lfa_player_generators  # 🚀 NEW: Add modular LFA_PLAYER period generators

from .endpoints.tournaments import generate_sessions  # 🎯 NEW: Add tournament session generation system
from .endpoints import system_events  # 🔔 NEW: Add system events (Rendszerüzenetek) panel
from .endpoints.tournaments import admin_enroll  # 🔧 NEW: Add admin batch enrollment for tournaments
from .endpoints import admin_players  # 🏭 NEW: Admin bulk player provisioning (production-flow testing)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(semesters.router, prefix="/semesters", tags=["semesters"])
api_router.include_router(academy_generator.router, prefix="/semesters", tags=["semesters", "academy-season"])  # 🏫 Academy Season generator
api_router.include_router(schedule_generator.router, prefix="/semesters", tags=["semesters", "scheduling"])  # 📅 MINI_SEASON / ACADEMY_SEASON session generation
api_router.include_router(conflict_check.router, prefix="/enrollments", tags=["enrollments", "conflict-check"])  # ⚠️ Enrollment conflict detection
api_router.include_router(groups.router, prefix="/groups", tags=["groups"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(session_results.router, prefix="/sessions", tags=["sessions", "game-results"])  # 🏆 Game results endpoints
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

# 🎓 NEW: Add specialization routes
api_router.include_router(
    specializations.router, 
    prefix="/specializations", 
    tags=["specializations"]
)

# 💰 NEW: Add payment verification routes
api_router.include_router(
    payment_verification.router, 
    prefix="/payment-verification", 
    tags=["payment-verification"]
)

# 🏮 NEW: Add GānCuju™️©️ license system routes
api_router.include_router(
    licenses.router, 
    prefix="/licenses", 
    tags=["licenses"]
)

# 🎓🔀 NEW: Add parallel specialization system routes
api_router.include_router(
    parallel_specializations.router, 
    prefix="/parallel-specializations", 
    tags=["parallel-specializations"]
)

# 📈 NEW: Add progression tracking system routes
api_router.include_router(
    progression.router, 
    prefix="/progression", 
    tags=["progression"]
)

# 🎯 NEW: Add track-based education system routes
api_router.include_router(
    tracks.router, 
    prefix="/tracks", 
    tags=["tracks"]
)

# 🏆 NEW: Add certificate management system routes
api_router.include_router(
    certificates.router, 
    prefix="/certificates", 
    tags=["certificates"]
)

# 🎓 NEW: Add student dashboard routes
api_router.include_router(
    students.router,
    prefix="/students",
    tags=["students"]
)

# 📚 NEW: Add curriculum system routes
api_router.include_router(
    curriculum.router,
    prefix="/curriculum",
    tags=["curriculum"]
)

# 🧠 NEW: Add curriculum-based adaptive learning routes
api_router.include_router(
    curriculum_adaptive.router,
    prefix="/curriculum-adaptive",
    tags=["curriculum-adaptive-learning"]
)

# 🎯 NEW: Add competency tracking system routes
api_router.include_router(
    competency.router,
    prefix="/competency",
    tags=["competency"]
)

# 🏥 P2: Add health monitoring routes (admin only)
api_router.include_router(
    health.router,
    prefix="/health",
    tags=["health-monitoring"]
)

# 👑 NEW: Add admin dashboard routes
api_router.include_router(
    admin.router,
    prefix="/admin",
    tags=["admin-dashboard"]
)

# 🔍 P0: Add audit log system routes
api_router.include_router(
    audit.router,
    prefix="/audit",
    tags=["audit-logs"]
)

# 🔔 NEW: System events — Rendszerüzenetek panel (admin-only)
api_router.include_router(
    system_events.router,
    prefix="/system-events",
    tags=["system-events"]
)

# 🎓 NEW: Add semester enrollment management routes
api_router.include_router(
    semester_enrollments.router,
    prefix="/semester-enrollments",
    tags=["semester-enrollments"]
)

# 💳 NEW: Add invoice management routes
api_router.include_router(
    invoices.router,
    prefix="/invoices",
    tags=["invoices"]
)

# 🎟️ NEW: Add coupon management routes
api_router.include_router(
    coupons.router,
    prefix="",  # No prefix - routes define their own (admin/coupons, coupons/active)
    tags=["coupons"]
)

# 🎁 NEW: Add partner invitation code routes
api_router.include_router(
    invitation_codes.router,
    prefix="",  # No prefix - routes define their own (admin/invitation-codes, invitation-codes/redeem)
    tags=["invitation-codes"]
)

# ⚽ NEW: Add LFA Player license API routes (spec-specific system)
api_router.include_router(
    lfa_player.router,
    prefix="/lfa-player",
    tags=["lfa-player"]
)

# 🥋 NEW: Add GānCuju belt/level system API routes (spec-specific system)
api_router.include_router(
    gancuju.router,
    prefix="/gancuju",
    tags=["gancuju"]
)

# 📚 NEW: Add Internship XP system API routes (spec-specific system)
api_router.include_router(
    internship.router,
    prefix="/internship",
    tags=["internship"]
)

# 👨‍🏫 NEW: Add Coach certification system API routes (spec-specific system)
api_router.include_router(
    coach.router,
    prefix="/coach",
    tags=["coach"]
)

# 🎯 NEW: Add motivation assessment system routes
api_router.include_router(
    motivation.router,
    prefix="/licenses",
    tags=["motivation-assessment"]
)

# 👤 NEW: Add FIFA-style public profile system routes
api_router.include_router(
    public_profile.router,
    prefix="/public",
    tags=["public-profile"]
)

# 📅 NEW: Add semester generator system routes (admin only)
api_router.include_router(
    semester_generator.router,
    prefix="/admin/semesters",
    tags=["semester-generator"]
)

# 🚀 NEW: Add modular LFA_PLAYER period generators (admin only)
api_router.include_router(
    lfa_player_generators.router,
    prefix="/admin/periods",
    tags=["period-generators", "lfa-player"]
)

# 📍 NEW: Add location management system routes (admin only)
api_router.include_router(
    locations.router,
    prefix="/admin/locations",
    tags=["locations"]
)

# 👨‍🏫 NEW: Add instructor availability management routes
api_router.include_router(
    instructor_availability.router,
    prefix="/instructor-availability",
    tags=["instructor-availability"]
)

# 📋 NEW: Add instructor assignment request system routes
api_router.include_router(
    instructor_assignments.router,
    prefix="/instructor-assignments",
    tags=["instructor-assignments"]
)

# 💰 NEW: Add license renewal system routes (Fase 2)
api_router.include_router(
    license_renewal.router,
    prefix="/license-renewal",
    tags=["license-renewal"]
)

# 🏫 NEW: Add campus management system routes (admin only)
api_router.include_router(
    campuses.router,
    prefix="/admin",
    tags=["campuses"]
)

# 🎯 NEW: Add spec services information API routes
api_router.include_router(
    spec_info.router,
    prefix="/spec-info",
    tags=["spec-info"]
)

# 👨‍🏫 NEW: Add two-tier instructor management system routes
api_router.include_router(
    instructor_management.router,
    prefix="/instructor-management",
    tags=["instructor-management"]
)

# 👥 NEW: Add dynamic session group assignment system routes
api_router.include_router(
    session_groups.router,
    prefix="/session-groups",
    tags=["session-groups"]
)

# 🏆 NEW: Add one-day tournament generator system routes
api_router.include_router(
    tournaments.router,
    prefix="/tournaments",
    tags=["tournaments"]
)

# 🎯 NEW: Add tournament type system routes (admin only)
api_router.include_router(
    tournament_types.router,
    prefix="/tournament-types",
    tags=["tournament-types"]
)

# 🎮 P3: Add game preset system routes
api_router.include_router(
    game_presets.router,
    prefix="/game-presets",
    tags=["game-presets"]
)

# 👥 NEW: Team management + invite flow
api_router.include_router(
    teams.router,
    prefix="",
    tags=["teams"]
)

# 🏟️ NEW: Pitch instructor assignment system
api_router.include_router(
    pitches.router,
    prefix="",
    tags=["pitches", "pitch-instructor-assignments"]
)

# 🎯 NEW: Add tournament session generation system routes (admin only)
api_router.include_router(
    generate_sessions.router,
    prefix="/tournaments",
    tags=["tournaments", "session-generation"]
)

# 🔧 NEW: Add admin batch enrollment for tournaments (admin only)
api_router.include_router(
    admin_enroll.router,
    prefix="/tournaments",
    tags=["tournaments", "admin-enrollment"]
)

# 🏭 NEW: Admin bulk player provisioning (production-flow testing & large-event setup)
api_router.include_router(
    admin_players.router,
    prefix="/admin",
    tags=["admin", "player-provisioning"]
)

# 🧪 NEW: Add sandbox test system routes (admin only)
api_router.include_router(
    sandbox.router,
    prefix="/sandbox",
    tags=["sandbox-testing"]
)

# 🧪 NEW: Add sandbox data endpoints (admin only)
api_router.include_router(
    sandbox_data.router,
    prefix="/sandbox",
    tags=["sandbox-data"]
)