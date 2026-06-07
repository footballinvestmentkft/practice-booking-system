"""
Web routes module aggregator
Combines all modular web route files into a single router
"""
from fastapi import APIRouter

from . import (
    verify,
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
    programs,
    events,
    adaptive_learning,
    training,
    virtual_training,
    communications,
    teams,
    tournament_live,
    public_player,
    public_tournament,
    sport_director,
    my_cards,
    card_editor,
    card_studio,
    shop,
    friends,
    vt_challenges,
    vt_card,
    ws_events,
    mood_photos,
)
from .admin import router as admin_router
from .tournaments import router as tournaments_router

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
router.include_router(tournaments_router)
router.include_router(programs.router)       # 📅 MINI_SEASON / ACADEMY_SEASON student enrollment
router.include_router(events.router)         # 📅 Events hub (/events)
router.include_router(adaptive_learning.router)  # 🧠 Adaptive Learning entry point
router.include_router(training.router)           # 🎓 Training hub (/training)
router.include_router(virtual_training.router)   # ⚡ Virtual Training mini-games
router.include_router(communications.router)
router.include_router(teams.router)
router.include_router(tournament_live.router)  # ✅ Live monitoring (WebSocket + admin page)
router.include_router(public_player.router)      # 🌐 Public player card (no auth)
router.include_router(public_tournament.router)  # 🌐 Public event detail page (no auth)
router.include_router(sport_director.router)     # 🏅 Sport Director team enrollment
router.include_router(my_cards.router)           # 🃏 My Cards hub (/my-cards)
router.include_router(card_editor.router)        # ✏️  Card Editor (/card-editor)
router.include_router(card_studio.router)        # 🎴  Card Studio shell (/card-studio)
router.include_router(shop.router)               # 🛒 Card Shop (/shop)
router.include_router(friends.router)            # 👥 Friendship system (/friends)
router.include_router(vt_challenges.router)      # 🎮 VT Challenges (/challenges)
router.include_router(vt_card.router)            # 🃏 VT Card (/virtual-training/card)
router.include_router(ws_events.router)           # 🔌 Per-user WS event stream (/ws/events)
router.include_router(mood_photos.router)         # 📸 Hangulatképek (/profile/my-mood-photos)
router.include_router(verify.router)              # 🪪 Academy ID public verify (/verify/{token})
