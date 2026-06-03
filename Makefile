# =============================================================================
# Makefile — LFA Practice Booking System
#
# Common dev tasks. Run from project root.
# Prerequisites: Python venv activated, .env configured, Redis running.
#
# Quick start (two terminals):
#   Terminal 1:  make web
#   Terminal 2:  make worker-mood
# =============================================================================

.DEFAULT_GOAL := help
.PHONY: help web worker-mood worker-all recover-mood recover-mood-execute \
        migrate test-unit test-cc docker-up docker-down

# ── Help ──────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  LFA Practice Booking System — dev commands"
	@echo ""
	@echo "  Dev servers:"
	@echo "    make web              Start FastAPI dev server (port 8000)"
	@echo "    make worker-mood      Start mood photo background removal worker"
	@echo "    make worker-all       Start worker for ALL queues (mood + tournaments)"
	@echo ""
	@echo "  Recovery:"
	@echo "    make recover-mood         Dry-run: show stuck processing mood photos"
	@echo "    make recover-mood-execute Execute: reset stuck processing mood photos"
	@echo ""
	@echo "  Database:"
	@echo "    make migrate          Run Alembic migrations"
	@echo ""
	@echo "  Tests:"
	@echo "    make test-unit        Run unit test suite"
	@echo "    make test-cc          Run Challenge Card design tests only"
	@echo ""
	@echo "  Docker:"
	@echo "    make docker-up        Start full dev stack (web + worker + redis + db)"
	@echo "    make docker-down      Stop and remove docker-compose dev containers"
	@echo ""

# ── Dev servers ───────────────────────────────────────────────────────────────
web:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker-mood:
	@echo "Starting mood_photos Celery worker..."
	@echo "BG_REMOVAL_PROCESSOR=$${BG_REMOVAL_PROCESSOR:-null} (set to 'rembg' for real processing)"
	celery -A app.celery_app worker \
		-Q mood_photos \
		--pool=solo \
		--concurrency=1 \
		--loglevel=info

worker-all:
	@echo "Starting Celery worker for ALL queues (mood_photos + tournaments + default)..."
	celery -A app.celery_app worker \
		-Q mood_photos,tournaments,default \
		--pool=prefork \
		--concurrency=2 \
		--loglevel=info

# ── Recovery ─────────────────────────────────────────────────────────────────
recover-mood:
	@echo "[DRY-RUN] Checking for stuck processing mood photos..."
	python scripts/recover_stuck_mood_photos.py

recover-mood-execute:
	@echo "[EXECUTE] Resetting stuck processing mood photos..."
	python scripts/recover_stuck_mood_photos.py --execute

# ── Database ─────────────────────────────────────────────────────────────────
migrate:
	alembic upgrade head

# ── Tests ─────────────────────────────────────────────────────────────────────
test-unit:
	python -m pytest tests/unit/ -q --tb=short

test-cc:
	python -m pytest tests/unit/api/web_routes/test_cc_design_1.py -q --tb=short

# ── Docker ───────────────────────────────────────────────────────────────────
docker-up:
	docker compose up --build

docker-down:
	docker compose down -v
