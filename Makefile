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
.PHONY: help web worker-mood worker-juggling worker-ball-feedback worker-all \
        recover-mood recover-mood-execute \
        recover-juggling recover-juggling-execute \
        migrate test-unit test-cc docker-up docker-down \
        ios-build ios-install ios-launch ios-run

# ── iOS config ────────────────────────────────────────────────────────────────
IOS_DEVICE_UDID  := 339B8F67-79A2-5099-A110-ABAF9E9902F5
IOS_BUNDLE_ID    := com.lovas-zoltan.lfa-education-center
IOS_PROJECT      := ios/LFAEducationCenter.xcodeproj
IOS_SCHEME       := LFAEducationCenter
IOS_DERIVED_DATA := /tmp/lfa_ios_build
IOS_APP          := $(IOS_DERIVED_DATA)/Build/Products/Debug-iphoneos/LFAEducationCenter.app

# ── Help ──────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  LFA Practice Booking System — dev commands"
	@echo ""
	@echo "  Dev servers:"
	@echo "    make web                  Start FastAPI dev server (port 8000)"
	@echo "    make worker-mood          Start mood photo background removal worker"
	@echo "    make worker-juggling      Start juggling video transcode + analyze worker"
	@echo "    make worker-ball-feedback Start ball feedback consensus worker (AN-3B2B2)"
	@echo "    make worker-all           Start worker for ALL queues (incl. ball_feedback)"
	@echo ""
	@echo "  Recovery:"
	@echo "    make recover-mood             Dry-run: show stuck processing mood photos"
	@echo "    make recover-mood-execute     Execute: reset stuck processing mood photos"
	@echo "    make recover-juggling         Dry-run: show stuck processing juggling videos"
	@echo "    make recover-juggling-execute Execute: reset stuck processing juggling videos"
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
	@echo "  iOS (no-debugger workflow):"
	@echo "    make ios-run          Build + install + launch on iPhone without LLDB"
	@echo "    make ios-build        xcodebuild Debug build only"
	@echo "    make ios-install      Install last build to connected iPhone"
	@echo "    make ios-launch       Launch installed app without debugger"
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

worker-juggling:
	@echo "Starting juggling_videos Celery worker (transcode + analyze)..."
	celery -A app.celery_app worker \
		-Q juggling_videos,juggling_retention \
		--pool=solo \
		--concurrency=1 \
		--loglevel=info

worker-ball-feedback:
	@echo "Starting ball_feedback Celery worker (AN-3B2B2 consensus + auto-approve)..."
	celery -A app.celery_app worker \
		-Q ball_feedback \
		--pool=solo \
		--concurrency=1 \
		--loglevel=info

worker-all:
	@echo "Starting Celery worker for ALL queues (incl. ball_feedback)..."
	celery -A app.celery_app worker \
		-Q mood_photos,tournaments,juggling_videos,juggling_retention,ball_feedback,default \
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

recover-juggling:
	@echo "[DRY-RUN] Checking for stuck processing juggling videos..."
	python scripts/recover_stuck_juggling.py

recover-juggling-execute:
	@echo "[EXECUTE] Resetting stuck processing juggling videos..."
	python scripts/recover_stuck_juggling.py --execute

# ── iOS — build + install + launch (no debugger attach) ──────────────────────
ios-build:
	xcodebuild build \
		-scheme $(IOS_SCHEME) \
		-project $(IOS_PROJECT) \
		-destination "id=$(IOS_DEVICE_UDID)" \
		-configuration Debug \
		-derivedDataPath $(IOS_DERIVED_DATA) \
		CODE_SIGN_STYLE=Automatic \
		DEVELOPMENT_TEAM=4D7V9ZWVHY

ios-install:
	@echo "[ios-install] Installing $(IOS_APP) → $(IOS_DEVICE_UDID)"
	xcrun devicectl device install app \
		--device $(IOS_DEVICE_UDID) \
		"$(IOS_APP)"

ios-launch:
	@echo "[ios-launch] Launching $(IOS_BUNDLE_ID) without debugger"
	xcrun devicectl device process launch \
		--device $(IOS_DEVICE_UDID) \
		$(IOS_BUNDLE_ID)

ios-run: ios-build ios-install ios-launch

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
