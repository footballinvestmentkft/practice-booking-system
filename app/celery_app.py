"""
Celery Application Factory

Configures Celery with Redis as broker and result backend.

Usage:
    # Start worker (from project root):
    celery -A app.celery_app worker --loglevel=info --concurrency=4

    # Start worker with dedicated queue for tournaments:
    celery -A app.celery_app worker -Q tournaments --loglevel=info

    # Monitor tasks:
    celery -A app.celery_app flower

Environment variables (override via .env or shell):
    CELERY_BROKER_URL      default: redis://localhost:6379/0
    CELERY_RESULT_BACKEND  default: redis://localhost:6379/1
"""
from celery import Celery

from app.config import settings


def create_celery() -> Celery:
    celery = Celery(
        "lfa_intern_system",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
        include=[
            "app.tasks.tournament_tasks",
            "app.tasks.mood_photo_tasks",
            "app.tasks.biometric_tasks",
            "app.tasks.juggling_tasks",
            "app.tasks.juggling_transcode_task",
            "app.tasks.juggling_retention_task",
        ],
    )

    celery.conf.update(
        # Serialisation
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Timezone
        timezone="UTC",
        enable_utc=True,
        # Result expiry (24 hours — tasks are polled immediately after generation)
        result_expires=86_400,
        # Reliability
        task_acks_late=True,               # ACK only after successful execution
        task_reject_on_worker_lost=True,   # Re-queue if worker crashes mid-task
        worker_prefetch_multiplier=1,      # One task at a time per worker thread
        # ── Broker connection resilience ──────────────────────────────────────
        # Retry broker connection on worker startup so the worker survives a
        # brief Redis restart or a delayed pod scheduling sequence.
        broker_connection_retry_on_startup=True,
        broker_connection_retry=True,       # Retry on transient mid-run disconnects
        broker_connection_max_retries=settings.CELERY_BROKER_CONNECTION_MAX_RETRIES,
        # Socket-level timeouts prevent the worker from hanging on a silently
        # dropped Redis TCP connection (e.g. firewall rule change, network blip).
        broker_transport_options={
            "socket_timeout": 10,           # seconds for Redis socket operations
            "socket_connect_timeout": 10,   # seconds to establish Redis connection
            "retry_on_timeout": True,       # re-issue timed-out Redis commands
        },
        # Routing
        task_routes={
            "app.tasks.tournament_tasks.generate_sessions_task":                   {"queue": "tournaments"},
            "app.tasks.mood_photo_tasks.remove_background_task":                   {"queue": "mood_photos"},
            "app.tasks.biometric_tasks.biometric_generate_embedding_task":         {"queue": "biometric_embeddings"},
            "app.tasks.biometric_tasks.biometric_delete_embedding_task":           {"queue": "biometric_embeddings"},
            "app.tasks.juggling_tasks.analyze_video_task":                         {"queue": "juggling_videos"},
            "app.tasks.juggling_transcode_task.transcode_video_task":               {"queue": "juggling_videos"},
            "app.tasks.juggling_retention_task.run_retention_task":                 {"queue": "juggling_retention"},
        },
        # Queues
        task_default_queue="default",
        task_queues={
            "default":              {},
            "tournaments":          {},
            "mood_photos":          {},
            "biometric_embeddings": {},
            "juggling_videos":      {},
            "juggling_retention":   {},
        },
        # Rate limiting (protect DB under heavy load)
        task_annotations={
            "app.tasks.tournament_tasks.generate_sessions_task": {
                "rate_limit": "10/m",
            },
            "app.tasks.biometric_tasks.biometric_generate_embedding_task": {
                "rate_limit": "30/m",
            },
            "app.tasks.biometric_tasks.biometric_delete_embedding_task": {
                "rate_limit": "60/m",
            },
            "app.tasks.juggling_tasks.analyze_video_task": {
                "rate_limit": "20/m",
            },
            "app.tasks.juggling_transcode_task.transcode_video_task": {
                "rate_limit": "10/m",
            },
            "app.tasks.juggling_retention_task.run_retention_task": {
                "rate_limit": "2/h",
            },
        },
    )

    return celery


celery_app = create_celery()
