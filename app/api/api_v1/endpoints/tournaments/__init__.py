"""
Tournament endpoints
"""
from fastapi import APIRouter
from .create import router as create_router  # ✅ Clean tournament creation endpoint
from .detail import router as detail_router  # ✅ Tournament detail query (GET /{id})
from .generator import router as generator_router
from .available import router as available_router
from .enroll import router as enroll_router
from .instructor import router as instructor_router
from .lifecycle import router as lifecycle_router
from .rewards import router as rewards_router
from .rewards_v2 import router as rewards_v2_router  # 🆕 V2: Unified reward system (badges + skill/XP)
from .reward_config import router as reward_config_router  # 🎁 Reward configuration (templates, save/load)
from .results import router as results_router  # ✅ P2: Modular match results (submission, rounds, finalization)
from .instructor_assignment import router as instructor_assignment_router  # ✅ P0-1 Phase 3: Instructor assignment lifecycle
from .cancellation import router as cancellation_router  # ✅ Feature: Tournament cancellation & refund
from .calculate_rankings import router as calculate_rankings_router  # ✅ P0: HEAD_TO_HEAD ranking calculation (league/knockout)
from .campus_schedule import router as campus_schedule_router  # 🏟️ Per-campus schedule configuration
from .schedule_config import router as schedule_config_router  # ⏱️ match_duration first-class domain entity
from .generate_sessions import router as generate_sessions_router  # ✅ Session generation with async/background support
from .checkin import router as checkin_router  # ✅ Pre-tournament check-in (regression fix)
from .ops_scenario import router as ops_scenario_router  # ✅ OPS scenario endpoint (split from generator.py)
from .lifecycle_instructor import router as lifecycle_instructor_router  # ✅ Cycle 2 instructor assignment (split from lifecycle.py)
from .lifecycle_updates import router as lifecycle_updates_router        # ✅ Admin tournament update (split from lifecycle.py)
from .team_enrollment import router as team_enrollment_router            # ✅ Admin team enrollment (automation path)

# Combine all tournament routers
router = APIRouter()
router.include_router(create_router)  # ✅ Clean tournament creation (production entry point)
router.include_router(detail_router)  # ✅ Tournament detail query (GET /{id})
router.include_router(lifecycle_router)  # New lifecycle endpoints (create, status, history)
router.include_router(generator_router)
router.include_router(available_router)
router.include_router(enroll_router)
router.include_router(checkin_router)  # ✅ Pre-tournament check-in (regression fix)
router.include_router(instructor_router)  # Thin router (queries, debug)
router.include_router(instructor_assignment_router)  # ✅ P0-1 Phase 3: Assignment lifecycle (apply, approve, accept, decline)
router.include_router(results_router)  # ✅ P2: Modular match results (submission, rounds, finalization)
router.include_router(cancellation_router)  # ✅ Feature: Tournament cancellation & refund
router.include_router(calculate_rankings_router)  # ✅ P0: HEAD_TO_HEAD ranking calculation (league/knockout)
router.include_router(rewards_router)  # Rewards & ranking endpoints (legacy)
router.include_router(rewards_v2_router)  # 🆕 V2: Unified reward system (badges + skill/XP)
router.include_router(reward_config_router)  # 🎁 Reward configuration (templates, save/load)
router.include_router(campus_schedule_router)  # 🏟️ Per-campus schedule configuration
router.include_router(schedule_config_router)  # ⏱️ match_duration first-class domain entity
router.include_router(generate_sessions_router)  # ✅ Session generation with async/background support
router.include_router(ops_scenario_router)  # ✅ OPS scenario endpoint (admin-only)
router.include_router(lifecycle_instructor_router)  # ✅ Cycle 2: assign-instructor, instructor/accept, instructor/decline
router.include_router(lifecycle_updates_router)     # ✅ Admin PATCH /{id}: tournament field updates
router.include_router(team_enrollment_router)        # ✅ Admin team enrollment (automation path)

__all__ = ["router"]
