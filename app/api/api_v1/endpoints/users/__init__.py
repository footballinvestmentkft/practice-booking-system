"""
User endpoints module
Aggregates all user-related routers into a single router
"""
from fastapi import APIRouter

from . import (
    crud,
    profile,
    search,
    credits,
    instructor_analytics,
    biometric_consent,
    biometric_liveness,
    biometric_verify,
    biometric_disclosure,
    biometric_photo,
    juggling_consent,
    juggling_videos,
    juggling_contacts,
    juggling_taxonomy,
    juggling_pose_snapshots,
    juggling_ball_detection,
)

# Create main router
router = APIRouter()

# Include all sub-routers
# Order matters: more specific routes should come before general ones

# Profile endpoints (must come before /{user_id} to avoid path conflicts)
router.include_router(profile.router, tags=["users"])

# Instructor analytics endpoints (must come before /{user_id})
router.include_router(instructor_analytics.router, tags=["users"])

# Search endpoints
router.include_router(search.router, tags=["users"])

# Credits endpoints
router.include_router(credits.router, tags=["users"])

# Biometric consent endpoints (feature-flag gated; 503 when flag off)
router.include_router(biometric_consent.router, tags=["users", "biometric"])

# Biometric liveness reference endpoint (PR-3; feature-flag gated; 503 when flag off)
router.include_router(biometric_liveness.router, tags=["users", "biometric"])

# Biometric face verify endpoint (PR-6; feature-flag gated; 503 when flag off)
router.include_router(biometric_verify.router, tags=["users", "biometric"])

# Biometric disclosure modal endpoints (PR-7A; BIOMETRIC_DISCLOSURE_ENABLED gated)
router.include_router(biometric_disclosure.router, tags=["users", "biometric"])

# Biometric reference photo upload endpoint (PR-2; feature-flag gated; 503 when flag off)
router.include_router(biometric_photo.router, tags=["users", "biometric"])

# Juggling POC — video intake + quality pipeline (JUGGLING_POC_ENABLED gated; 503 when off)
router.include_router(juggling_consent.router, tags=["users", "juggling"])
router.include_router(juggling_videos.router, tags=["users", "juggling"])
# AN-1: contact annotation CRUD + taxonomy (more specific paths before /{event_id} catch-alls)
router.include_router(juggling_taxonomy.router, tags=["users", "juggling"])
router.include_router(juggling_contacts.router, tags=["users", "juggling"])
# Phase 2A: pose snapshots (POSE_SNAPSHOT_ENABLED gated; 503 when off)
router.include_router(juggling_pose_snapshots.router, tags=["users", "juggling"])
# Phase 2B: ball detection (BALL_DETECTION_ENABLED gated; 503 when off)
router.include_router(juggling_ball_detection.router, tags=["users", "juggling"])

# CRUD endpoints (should be last due to /{user_id} catch-all)
router.include_router(crud.router, tags=["users"])

# Export router
__all__ = ["router"]