"""
Web routes module aggregator
Combines all modular web route files into a single router
"""
from fastapi import APIRouter

from . import (
    auth,
    onboarding,
    profile,
    student_features,
    dashboard,
    specialization,
    sessions,
    attendance,
    quiz,
    instructor,
    instructor_dashboard,
    tournaments,
    programs,
    communications,
    teams,
    tournament_live,
    public_player,
    public_tournament,
    sport_director,
)
from .admin import router as admin_router

# Create main router with tags
router = APIRouter(tags=["web"])

# Include all sub-routers
router.include_router(auth.router)
router.include_router(onboarding.router)
router.include_router(profile.router)
router.include_router(student_features.router)
router.include_router(dashboard.router)
router.include_router(specialization.router)
router.include_router(sessions.router)
router.include_router(attendance.router)
router.include_router(quiz.router)
router.include_router(instructor.router)
router.include_router(instructor_dashboard.router)
router.include_router(admin_router)
router.include_router(tournaments.router)
router.include_router(programs.router)       # 📅 MINI_SEASON / ACADEMY_SEASON student enrollment
router.include_router(communications.router)
router.include_router(teams.router)
router.include_router(tournament_live.router)  # ✅ Live monitoring (WebSocket + admin page)
router.include_router(public_player.router)      # 🌐 Public player card (no auth)
router.include_router(public_tournament.router)  # 🌐 Public event detail page (no auth)
router.include_router(sport_director.router)     # 🏅 Sport Director team enrollment
