import json
import os
import sys
import secrets
from pydantic_settings import BaseSettings, EnvSettingsSource
from pydantic import ConfigDict


def is_testing() -> bool:
    """Detect if we're running in test environment"""
    return (
        "pytest" in sys.modules or
        os.getenv("TESTING", "").lower() in ("1", "true", "yes") or
        "test" in sys.argv[0].lower()
    )


def get_secret_key() -> str:
    """Get SECRET_KEY from environment or generate for testing"""
    if is_testing():
        # Use deterministic key for tests (so tokens are reproducible)
        return "test-secret-key-for-testing-only-do-not-use-in-production"

    # Try loading .env if SECRET_KEY not yet in environment
    # (needed when config is imported before pydantic-settings resolves env_file)
    secret = os.getenv("SECRET_KEY")
    if not secret:
        try:
            from dotenv import load_dotenv as _load_dotenv
            _load_dotenv()
            secret = os.getenv("SECRET_KEY")
        except ImportError:
            pass

    if not secret:
        raise ValueError(
            "SECRET_KEY environment variable must be set in production! "
            "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    # Prevent accidental use of default/weak keys
    if secret in ["super-secret-jwt-key-change-this", "changeme", "secret", "admin123"]:
        raise ValueError(
            "SECRET_KEY appears to be a default/weak value. "
            "Generate a strong key with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    return secret


def get_cors_origins() -> list[str]:
    """Get CORS origins - localhost for testing/development, explicit allowlist for production"""
    # Check ENVIRONMENT variable directly (not is_testing() to avoid import issues)
    env = os.getenv("ENVIRONMENT", "development")

    # Development or test mode: allow localhost
    if env in ("development", "test", "testing"):
        return [
            "http://localhost:8501",
            "http://localhost:8000",
            "http://127.0.0.1:8501",
            "http://127.0.0.1:8000",
        ]

    # Production: explicit allowlist from environment
    origins_str = os.getenv("CORS_ALLOWED_ORIGINS", "")
    if not origins_str:
        raise ValueError(
            "CORS_ALLOWED_ORIGINS environment variable must be set in production! "
            "Example: CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com"
        )

    origins = [origin.strip() for origin in origins_str.split(",")]

    # Prevent localhost in production
    for origin in origins:
        if "localhost" in origin or "127.0.0.1" in origin:
            raise ValueError(
                f"Localhost origin '{origin}' not allowed in production CORS! "
                "Use production domain names only."
            )

    return origins


# IMPORTANT: pydantic-settings / CORS env-var interception
#
# pydantic-settings parses env vars *before* pydantic validators run, so a
# @field_validator(mode="before") on CORS_ALLOWED_ORIGINS is never reached.
#
# Execution order when CORS_ALLOWED_ORIGINS is set:
#   EnvSettingsSource.__call__()
#     → prepare_field_value()
#       → decode_complex_value()   ← json.loads(value) raises JSONDecodeError
#     ← except ValueError → re-raises as SettingsError("error parsing value…")
#   ← SettingsError propagates to BaseSettings.__init__() → Settings.__init__()
#
# Strategy: subclass EnvSettingsSource, catch ValueError *inside*
# decode_complex_value before __call__ can re-wrap it, and raise a custom
# _CORSFormatError (not a ValueError) that escapes the except-ValueError guard.
# Settings.__init__() then catches _CORSFormatError specifically and re-raises
# as a plain ValueError with a human-readable hint.
#
# Regression note: previously failed in CI with
#   SettingsError: error parsing value for field "CORS_ALLOWED_ORIGINS"
#   Caused by: JSONDecodeError: Expecting value
# when CORS_ALLOWED_ORIGINS was set to a plain URL string in the CI .env file.
# Fixed 2026-03-23. See tests/unit/test_cors_config.py for regression lock.


class _CORSFormatError(Exception):
    """Raised by _CORSSafeEnvSource when CORS_ALLOWED_ORIGINS cannot be JSON-decoded.

    Must NOT subclass ValueError: pydantic-settings' __call__() catches ValueError
    and re-wraps it as SettingsError, losing the friendly message.  A plain Exception
    subclass bypasses that catch and surfaces directly to Settings.__init__().
    """


class _CORSSafeEnvSource(EnvSettingsSource):
    """EnvSettingsSource that raises _CORSFormatError (not opaque SettingsError)
    when CORS_ALLOWED_ORIGINS value is not valid JSON.

    Why a custom source (not @field_validator): pydantic-settings calls
    decode_complex_value() during env collection, before pydantic validators run.
    A @field_validator(mode="before") would never be reached when the source raises.
    """

    def decode_complex_value(self, field_name: str, field: object, value: object) -> object:
        # super().decode_complex_value() does json.loads(value) → JSONDecodeError (ValueError)
        # on bad input.  We intercept here, before __call__()'s `except ValueError` wrapper
        # converts it to an opaque SettingsError.  _CORSFormatError is NOT a ValueError, so
        # it escapes __call__() and surfaces directly to Settings.__init__().
        try:
            return super().decode_complex_value(field_name, field, value)
        except ValueError:
            if field_name == "CORS_ALLOWED_ORIGINS":
                raise _CORSFormatError(
                    f"CORS_ALLOWED_ORIGINS must be a JSON array, got: {value!r}\n"
                    "  Correct  : CORS_ALLOWED_ORIGINS=[\"https://app.lfa.com\",\"https://admin.lfa.com\"]\n"
                    "  Dev/test : omit this variable — localhost list is configured automatically."
                ) from None
            raise


class Settings(BaseSettings):
    # Environment
    ENVIRONMENT: str = "test" if is_testing() else "development"
    TESTING: bool = is_testing()

    # Database
    DATABASE_URL: str = "postgresql://lovas.zoltan@localhost:5432/gancuju_education_center_prod"

    # Task queue (Celery + Redis)
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # JWT - SECURE: Uses environment variable in production
    SECRET_KEY: str = get_secret_key()
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # App
    APP_NAME: str = "GānCuju™© Education Center"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"
    # Internal loopback port used by the card export service when constructing
    # the render URL for headless Playwright screenshots. Must match the port
    # uvicorn is started on. Override via APP_INTERNAL_PORT env var.
    APP_INTERNAL_PORT: int = 8000

    # Initial Admin - SECURE: Must use environment variables in production
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "admin@company.com" if is_testing() else "")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin123" if is_testing() else "")
    ADMIN_NAME: str = "System Administrator"
    
    # Booking Rules
    MAX_BOOKINGS_PER_SEMESTER: int = 10
    BOOKING_DEADLINE_HOURS: int = 24
    
    # Production Security Settings
    ENABLE_RATE_LIMITING: bool = not is_testing()
    ENABLE_SECURITY_HEADERS: bool = True
    ENABLE_REQUEST_SIZE_LIMIT: bool = True
    ENABLE_STRUCTURED_LOGGING: bool = True

    # Player Progression Feature Flags
    # Propagate tournament EMA skill deltas into FootballSkillAssessment rows.
    # Set to False to disable instantly without a code deploy.
    ENABLE_TOURNAMENT_SKILL_PROPAGATION: bool = True

    # Skill Tier Notifications
    # Send in-app notification when a player's skill crosses a tier boundary.
    # Requires ENABLE_TOURNAMENT_SKILL_PROPAGATION=True.
    ENABLE_SKILL_TIER_NOTIFICATIONS: bool = False

    # ── Card export ownership enforcement ─────────────────────────────────────
    # Controls whether export routes block users who lack a CardDesignOwnership row.
    # Player Card premium guard is ALWAYS active regardless of these flags
    # (legacy unlocked_card_variants JSON shim covers existing users).
    #
    # Set to True only AFTER running scripts/backfill_card_design_ownerships.py
    # for existing users, or after a product decision to require new purchase.
    #
    # False (default): export proceeds without ownership, warning is logged.
    # True:            export blocked with HTTP 403 if ownership is missing.
    ENFORCE_WELCOME_CARD_OWNERSHIP:   bool = False
    ENFORCE_CHALLENGE_CARD_OWNERSHIP: bool = False

    SKILL_TIER_THRESHOLDS: dict = {
        60: "Intermediate",
        75: "Advanced",
        90: "Expert",
    }

    # ── Alert thresholds for in-process metrics ────────────────────────────────
    # These control GET /metrics/alerts. Adjust in production .env as traffic
    # patterns become known.  All ratios are 0–1 (e.g. 0.05 = 5 %).
    #
    # ALERT_REWARD_FAILURE_RATE    — rewards_failed / total_rewards
    # ALERT_BOOKING_WAITLIST_RATE  — bookings_waitlisted / total_bookings
    # ALERT_ENROLLMENT_GATE_BLOCK_RATE — enrollment_gate_blocked / enrollment_attempts
    # ALERT_SLOW_QUERY_TOTAL       — absolute count of slow queries (>200 ms) since start
    ALERT_REWARD_FAILURE_RATE: float = 0.05        # >5 % reward failures → warning
    ALERT_BOOKING_WAITLIST_RATE: float = 0.30      # >30 % bookings waitlisted → warning
    ALERT_ENROLLMENT_GATE_BLOCK_RATE: float = 0.20  # >20 % enrollments gate-blocked → warning
    ALERT_SLOW_QUERY_TOTAL: int = 10               # >10 slow queries since start → warning

    # ── Logging configuration ──────────────────────────────────────────────────
    # All settings are read from environment variables; override in .env or
    # the container environment for deployment-specific paths and retention needs.
    LOG_DIR: str = "logs"                    # directory for rotating log files
    LOG_MAX_BYTES: int = 10 * 1024 * 1024   # max size per log file (10 MB default)
    LOG_BACKUP_COUNT: int = 5               # rotated backups to keep (app.log.1–5)

    # ── Database connection pool tuning ───────────────────────────────────────
    # Sizing guide (concurrent users → recommended values):
    #   small  (≤ 20  users): pool_size=5,  max_overflow=10  → 15 total
    #   medium (21-100 users): pool_size=10, max_overflow=20  → 30 total
    #   large  (101-500 users): pool_size=20, max_overflow=30  → 50 total (default)
    #   extra-large (500+ users): pool_size=40, max_overflow=20 → 60 total
    #
    # Each uvicorn/gunicorn worker has its OWN pool.  With 4 workers and
    # pool_size=20 you consume up to 4 × 50 = 200 PostgreSQL connections.
    # Keep total_connections < max_connections in postgresql.conf (default 100).
    DB_POOL_SIZE: int = 20               # persistent connections per worker
    DB_MAX_OVERFLOW: int = 30            # burst connections beyond pool_size
    DB_POOL_RECYCLE: int = 3600          # seconds before connection is recycled

    # ── Database connection resilience ─────────────────────────────────────────
    # Controls how long the driver waits when opening a new database connection
    # and how many retries the startup health-check makes before aborting.
    #
    # DB_CONNECT_TIMEOUT       — seconds the psycopg2 driver waits per attempt
    # DB_STATEMENT_TIMEOUT_MS  — per-statement wall-clock limit (0 = disabled).
    #                            Prevents runaway queries from holding connections.
    # DB_STARTUP_RETRIES       — how many times wait_for_db() retries before abort
    # DB_STARTUP_RETRY_DELAY   — initial backoff (seconds); multiplied per attempt
    DB_CONNECT_TIMEOUT: int = 10           # seconds per connection attempt
    DB_STATEMENT_TIMEOUT_MS: int = 0       # 0 = disabled; e.g. 30000 = 30 s limit
    DB_STARTUP_RETRIES: int = 5            # attempts before giving up at startup
    DB_STARTUP_RETRY_DELAY: float = 2.0    # initial backoff in seconds

    # ── Graceful shutdown ──────────────────────────────────────────────────────
    # Maximum seconds to wait for in-flight background (APScheduler) jobs to
    # finish when the process receives SIGTERM.  If a job is still running after
    # this timeout the scheduler is forcibly stopped (daemon threads exit with
    # the process).  Keep in sync with uvicorn's --timeout-graceful-shutdown.
    GRACEFUL_SHUTDOWN_TIMEOUT: int = 30    # seconds

    # ── Celery broker resilience ───────────────────────────────────────────────
    # How many times the Celery worker retries the Redis broker connection before
    # giving up.  0 = unlimited (not recommended for long broker outages).
    CELERY_BROKER_CONNECTION_MAX_RETRIES: int = 10

    # ── Background removal pipeline ───────────────────────────────────────────
    # BG_REMOVAL_PROCESSOR controls which processor is active:
    #   "null"  — NullProcessor (passthrough; Remove Background button hidden)
    #   "rembg" — RembgProcessor (U2Net; requires rembg + onnxruntime-cpu)
    # Seconds after which a stuck 'processing' record is flagged timed-out in
    # the status endpoint so the user can reset it back to 'uploaded'.
    BG_REMOVAL_PROCESSOR: str = "null"
    PROCESSING_TIMEOUT_SECONDS: int = 300

    # ── Academy ID Phase 2A ───────────────────────────────────────────────────
    # Base URL used to build the QR verify link: {VERIFY_BASE_URL}/verify/{token}
    # Dev (simulator):       http://localhost:8000
    # Dev (physical iPhone): http://<MAC_LAN_IP>:8000
    # Production:            https://lfa.hu
    VERIFY_BASE_URL: str = "http://localhost:8000"

    # ── Biometric Face Matching ───────────────────────────────────────────────
    # BIOMETRIC_FACE_MATCHING_ENABLED controls the entire biometric pipeline.
    #   false (default) — all biometric endpoints return HTTP 503.
    #   true            — requires DPIA completion, consent v1.0 legal approval,
    #                     and written sign-off before setting in production.
    #
    # BIOMETRIC_EMBEDDING_KEY — 32-byte AES-256-GCM key for embedding encryption.
    #   Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    #   Must be set before BIOMETRIC_FACE_MATCHING_ENABLED=true can be used in
    #   production. Empty string disables encryption validation in development
    #   and test environments (no real embeddings stored in those environments).
    BIOMETRIC_FACE_MATCHING_ENABLED: bool = False
    BIOMETRIC_EMBEDDING_KEY: str = ""
    # Provider: "fake" (PR-4, default) | "onnx" (PR-5, R&D only — see guards below)
    BIOMETRIC_EMBEDDING_PROVIDER: str = "fake"
    # True only in test/dev — allows empty BIOMETRIC_EMBEDDING_KEY without raising
    BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY: bool = False

    # ── PR-7A Biometric Disclosure (user tájékoztató modal) ──────────────────
    # BIOMETRIC_DISCLOSURE_ENABLED controls the disclosure/consent-modal endpoints.
    #   Separate from BIOMETRIC_FACE_MATCHING_ENABLED — allows the disclosure UI
    #   to be rolled out before full face matching is activated.
    #   false (default) — disclosure endpoints return HTTP 503.
    #   true            — disclosure endpoints active; face matching still requires
    #                     BIOMETRIC_FACE_MATCHING_ENABLED=true separately.
    BIOMETRIC_DISCLOSURE_ENABLED: bool = False

    # CURRENT_BIOMETRIC_DISCLOSURE_VERSION — the canonical disclosure text version
    #   that users must accept. Bump this string when the disclosure text changes;
    #   existing acceptances of older versions will be treated as stale and users
    #   will be prompted to re-accept before liveness/verify is allowed.
    CURRENT_BIOMETRIC_DISCLOSURE_VERSION: str = "v1.0"

    # ── PR-5 ONNX R&D guards ─────────────────────────────────────────────────
    # BIOMETRIC_ONNX_RND_ENABLED — separate guard for the ONNX provider.
    #   False (default) — ONNX provider is disabled even if provider=onnx is set.
    #   True            — R&D/prototype dev/test use ONLY. NEVER set True in production.
    #                     BIOMETRIC_FACE_MATCHING_ENABLED=true alone is NOT sufficient
    #                     to activate the ONNX provider.
    BIOMETRIC_ONNX_RND_ENABLED: bool = False

    # BIOMETRIC_ONNX_MODEL_PATH — filesystem path to the ONNX model file.
    #   Must be an absolute path on disk (not a URL, not a CDN reference).
    #   Empty string → graceful disabled (503), no crash.
    #   The model file must NOT be committed to the repository (.gitignore enforced).
    BIOMETRIC_ONNX_MODEL_PATH: str = ""

    # BIOMETRIC_ONNX_MODEL_SHA256 — expected SHA-256 hex digest of the model file.
    #   Empty string → checksum validation skipped (dev only; required in staging/prod R&D).
    BIOMETRIC_ONNX_MODEL_SHA256: str = ""

    # BIOMETRIC_RETAIN_PHOTOS — privacy-by-design override.
    #   Default: False — reference photos are deleted after embedding generation
    #   (privacy-by-design: raw biometric image not retained beyond processing need).
    #   Set to True only if DPIA / legal counsel explicitly requires longer retention.
    #   See biometric_generate_embedding_task step 10 for deletion logic.
    BIOMETRIC_RETAIN_PHOTOS: bool = False

    # ── Juggling POC — Video Intake + Quality Pipeline ───────────────────────
    # JUGGLING_POC_ENABLED — master switch; false = all juggling endpoints return 503.
    JUGGLING_POC_ENABLED: bool = False

    # JUGGLING_VIDEO_MAX_SIZE_MB — upload hard limit.
    #   100 MB ≈ 80–130 s of 720p/60fps H.265 at ~6–10 Mbps; sufficient for POC.
    JUGGLING_VIDEO_MAX_SIZE_MB: int = 100

    # JUGGLING_VIDEO_MAX_DURATION_SECONDS — ffprobe duration gate.
    JUGGLING_VIDEO_MAX_DURATION_SECONDS: int = 120

    # JUGGLING_UPLOAD_DIR — filesystem path for stored video files.
    #   Intentionally outside app/static/ — NOT served by StaticFiles mount.
    JUGGLING_UPLOAD_DIR: str = "app/uploads/juggling"

    # JUGGLING_FFPROBE_TIMEOUT_SECONDS — subprocess timeout for ffprobe.
    JUGGLING_FFPROBE_TIMEOUT_SECONDS: int = 30

    # ── Juggling P2 — transcode settings ─────────────────────────────────────
    # JUGGLING_FFMPEG_TARGET_FPS — output FPS target (input above this triggers transcode).
    JUGGLING_FFMPEG_TARGET_FPS: int = 30

    # JUGGLING_FFMPEG_TARGET_HEIGHT — output height target in pixels (e.g. 720 = 720p).
    #   Videos taller than this are scaled down (scale=-2:720). Width adjusted automatically.
    JUGGLING_FFMPEG_TARGET_HEIGHT: int = 720

    # JUGGLING_FFMPEG_TIMEOUT_SECONDS — subprocess timeout for the ffmpeg transcode command.
    #   Should be larger than JUGGLING_FFPROBE_TIMEOUT_SECONDS to allow actual encoding.
    JUGGLING_FFMPEG_TIMEOUT_SECONDS: int = 120

    # ── Juggling P3 — Retention + Audit ──────────────────────────────────────
    # JUGGLING_RETENTION_CLEANUP_ENABLED — master switch for destructive retention cleanup.
    #   False (default) = no file or DB deletion runs automatically.
    #   True = retention expiry + orphan cleanup active (subject to DRY_RUN flag).
    JUGGLING_RETENTION_CLEANUP_ENABLED: bool = False

    # JUGGLING_RETENTION_DRY_RUN — if True, all cleanup operations only log; no files
    #   deleted, no DB mutations. Default True = safe baseline.
    JUGGLING_RETENTION_DRY_RUN: bool = True

    # JUGGLING_TEMP_CLEANUP_ENABLED — master switch for *.tmp.* file cleanup.
    #   Separate flag for consistency; temp files are not personal data but default False
    #   keeps all destructive ops explicit in POC.
    JUGGLING_TEMP_CLEANUP_ENABLED: bool = False

    # JUGGLING_AUDIT_HASH_SECRET — HMAC-SHA256 key for audit log path hashing
    #   and user pseudonymisation.
    #   Empty string → ValueError raised lazily (at first HMAC call) if retention
    #   is active. Does NOT block startup when retention is disabled (POC default).
    #   Test environment: deterministic value provided automatically via is_testing().
    #   Production: set via JUGGLING_AUDIT_HASH_SECRET env var.
    JUGGLING_AUDIT_HASH_SECRET: str = (
        "test-audit-hash-secret-for-testing-only-do-not-use-in-production"
        if is_testing()
        else os.getenv("JUGGLING_AUDIT_HASH_SECRET", "")
    )

    # POSE_SNAPSHOT_ENABLED — Phase 2A feature flag.
    #   OFF by default. When OFF, pose snapshot endpoints return HTTP 503.
    #   Turn ON per-deployment in .env: POSE_SNAPSHOT_ENABLED=true
    #   Dev: set True in .env.local to test pose capture locally.
    POSE_SNAPSHOT_ENABLED: bool = False

    # BALL_DETECTION_ENABLED — Phase 2B feature flag.
    #   OFF by default. When OFF, ball detection endpoints return HTTP 503.
    #   Turn ON per-deployment in .env: BALL_DETECTION_ENABLED=true
    BALL_DETECTION_ENABLED: bool = False
    BALL_DETECTION_MODEL_PATH: str = "app/ml_models/ssd_mobilenet_v1_12.onnx"

    # BALL_TRAJECTORY_ENABLED — AN-3B2D-1 dense ball tracking.
    #   OFF by default. When OFF, trajectory endpoints return HTTP 503.
    #   Turn ON per-deployment in .env: BALL_TRAJECTORY_ENABLED=true
    BALL_TRAJECTORY_ENABLED: bool = False

    # BALL_FEEDBACK_ENABLED — AN-3B2D-B0 user-assisted ball annotation feedback.
    #   OFF by default. When OFF, feedback endpoints return HTTP 503.
    #   Turn ON per-deployment in .env: BALL_FEEDBACK_ENABLED=true
    BALL_FEEDBACK_ENABLED: bool = False

    # ── AN-3B2F: Global Ball Training Hub (PR-1A) ─────────────────────────────
    # BALL_TRAINING_ALLOWED_USER_IDS — comma-separated integer user IDs that may
    #   access the global training hub in addition to ADMIN users.
    #   Empty string (default) = hub accessible to ADMIN only.
    #   Example: BALL_TRAINING_ALLOWED_USER_IDS=42,107,251
    #   No DB migration required — parsed at request time from this config value.
    BALL_TRAINING_ALLOWED_USER_IDS: str = ""

    # BALL_TRAINING_ASSIGNMENT_TTL_SECONDS — how long an assignment stays valid
    #   before the client must request a fresh queue.
    #   Default: 3600 (1 hour). Lower values reduce orphaned-assignment accumulation.
    BALL_TRAINING_ASSIGNMENT_TTL_SECONDS: int = 3600

    # BALL_TRAINING_FRAME_ENABLED — AN-3B2F PR-1B privacy-safe frame serving.
    #   OFF by default. When OFF, the frame endpoint returns HTTP 503.
    #   Turn ON per-deployment in .env: BALL_TRAINING_FRAME_ENABLED=true
    BALL_TRAINING_FRAME_ENABLED: bool = False

    # BALL_TRAINING_FRAME_MARGIN_RATIO — half-side of the canonical context crop
    #   expressed as margin_ratio * min(img_w, img_h) / 2 pixels from the ball
    #   centre, clamped to image bounds.  0.70 → square side = 70 % of the
    #   shorter dimension.
    BALL_TRAINING_FRAME_MARGIN_RATIO: float = 0.70

    # BALL_TRAINING_FULL_FRAME_CONFIDENCE_THRESHOLD — serve the full frame (not
    #   a context crop) when trajectory confidence is below this value, or when
    #   tracking_state == 'lost'.  Default 0.30.
    BALL_TRAINING_FULL_FRAME_CONFIDENCE_THRESHOLD: float = 0.30

    # ── Ball annotation reward (AN-3B2E) ──────────────────────────────────────
    BALL_ANNOTATION_XP_BASE: int = 5            # confirm / no_ball upfront
    BALL_ANNOTATION_XP_CORRECTED: int = 10      # corrected upfront
    BALL_ANNOTATION_XP_ACCURACY_BONUS: int = 5  # posterior: approved feedback
    BALL_ANNOTATION_XP_GOLD_BONUS: int = 10     # posterior: gold-standard addíció
    BALL_ANNOTATION_MAX_XP_PER_DAY: int = 100   # upfront + posterior együtt
    BALL_ANNOTATION_MAX_TASKS_PER_DAY: int = 30
    BALL_ANNOTATION_MAX_CORRECTED_CREDIT_PER_DAY: int = 10
    BALL_ANNOTATION_MIN_RELIABILITY_FOR_CREDIT: float = 0.4
    BALL_ANNOTATION_RAPID_SUBMIT_WINDOW_S: int = 60
    BALL_ANNOTATION_RAPID_SUBMIT_THRESHOLD: int = 5
    BALL_ANNOTATION_SPAM_FLAG_BLOCK_THRESHOLD: int = 10

    # ── Slow-query monitoring ──────────────────────────────────────────────────
    # Queries slower than SLOW_QUERY_THRESHOLD_MS are logged to app.slow_query
    # and counted in the slow_queries_total metric.  Raise this value if normal
    # reporting queries regularly exceed the default (e.g. large dashboards).
    SLOW_QUERY_THRESHOLD_MS: float = 200.0  # milliseconds

    # Payment configuration (override via environment variables in production)
    PAYMENT_AMOUNT_HUF: int = 50000
    PAYMENT_BANK_ACCOUNT_HOLDER: str = "LFA Education Center Kft."
    PAYMENT_BANK_ACCOUNT_NUMBER: str = "12345678-12345678-12345678"
    PAYMENT_BANK_NAME: str = "OTP Bank"
    PAYMENT_BANK_SWIFT: str = "OTPVHUHB"
    PAYMENT_BANK_IBAN: str = "HU42 1177 3016 1111 1118 0000 0000"
    
    # Rate Limiting Configuration
    RATE_LIMIT_CALLS: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    LOGIN_RATE_LIMIT_CALLS: int = 10  # More permissive for testing
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 60

    # CORS Configuration - SECURE: Explicit allowlist (localhost only in tests)
    CORS_ALLOWED_ORIGINS: list[str] = get_cors_origins()

    # Cookie Security Configuration - SECURE: HTTPS enforced in production
    COOKIE_SECURE: bool = not is_testing()  # True in production (requires HTTPS)
    COOKIE_SAMESITE: str = "strict"  # Options: "strict", "lax", "none"
    COOKIE_HTTPONLY: bool = True
    COOKIE_MAX_AGE: int = 3600  # 1 hour (matches ACCESS_TOKEN_EXPIRE_MINUTES)

    model_config = ConfigDict(env_file=".env")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        return (
            init_settings,
            _CORSSafeEnvSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    def __init__(self, **kwargs):
        try:
            super().__init__(**kwargs)
        except _CORSFormatError as e:
            raise ValueError(str(e)) from None

        # Production-only security validation (skipped in development and testing)
        _is_production = not is_testing() and self.ENVIRONMENT == "production"
        if _is_production:
            # Validate admin credentials are set
            if not self.ADMIN_EMAIL or not self.ADMIN_PASSWORD:
                raise ValueError(
                    "ADMIN_EMAIL and ADMIN_PASSWORD must be set via environment variables in production!"
                )

            # Prevent weak admin passwords
            if self.ADMIN_PASSWORD in ["admin123", "password", "changeme", "admin", "123456"]:
                raise ValueError(
                    "Admin password appears to be weak or default. "
                    "Set a strong password via ADMIN_PASSWORD environment variable."
                )

            # Validate HTTPS is configured
            if not self.COOKIE_SECURE:
                raise ValueError(
                    "COOKIE_SECURE must be True in production (HTTPS required)"
                )

            # DEBUG must be False in production — prevents stack traces leaking to clients
            if self.DEBUG:
                raise ValueError(
                    "DEBUG must be False in production. "
                    "Set DEBUG=false in your environment or .env file."
                )


settings = Settings()


def get_settings() -> Settings:
    """Get settings instance (for dependency injection)"""
    return settings