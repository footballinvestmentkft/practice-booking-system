"""
User endpoints module
Aggregates all user-related routers into a single router
"""
from fastapi import APIRouter

from . import crud, profile, search, credits, instructor_analytics, biometric_consent, biometric_liveness, biometric_verify

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

# CRUD endpoints (should be last due to /{user_id} catch-all)
router.include_router(crud.router, tags=["users"])

# Export router
__all__ = ["router"]
