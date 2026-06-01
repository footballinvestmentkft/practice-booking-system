"""
IMG-FIX-1 — from-mood photo priority: processed_png_url > original_url

IMG-01  _mood_photo_asset_url returns processed_png_url when set
IMG-02  _mood_photo_asset_url falls back to original_url when processed is None
IMG-03  _mood_photo_asset_url falls back to original_url when processed is ''
IMG-04  wc-photo/from-mood uses processed_png_url (priority) when available
IMG-05  wc-photo-portrait/from-mood uses processed_png_url when available
IMG-06  wc-photo-landscape/from-mood uses processed_png_url when available
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_DIR = Path(__file__).resolve().parents[4] / "app"

# A valid mood slot (must be in MOOD_PHOTO_SLOTS frozenset)
_VALID_SLOT = "mood_happy_smile"


# ── IMG-01..03: Helper unit tests ─────────────────────────────────────────────

class TestIMG01to03MoodPhotoAssetUrl:

    def _import_helper(self):
        from app.api.web_routes.dashboard import _mood_photo_asset_url
        return _mood_photo_asset_url

    def test_img_01_returns_processed_when_set(self):
        """IMG-01: Returns processed_png_url when it has a value."""
        helper = self._import_helper()
        mood_photo = MagicMock()
        mood_photo.processed_png_url = "https://cdn.example.com/processed.png"
        mood_photo.original_url      = "https://cdn.example.com/original.jpg"
        assert helper(mood_photo) == "https://cdn.example.com/processed.png"

    def test_img_02_falls_back_to_original_when_processed_is_none(self):
        """IMG-02: Falls back to original_url when processed_png_url is None."""
        helper = self._import_helper()
        mood_photo = MagicMock()
        mood_photo.processed_png_url = None
        mood_photo.original_url      = "https://cdn.example.com/original.jpg"
        assert helper(mood_photo) == "https://cdn.example.com/original.jpg"

    def test_img_03_falls_back_to_original_when_processed_is_empty_string(self):
        """IMG-03: Falls back to original_url when processed_png_url is ''."""
        helper = self._import_helper()
        mood_photo = MagicMock()
        mood_photo.processed_png_url = ""
        mood_photo.original_url      = "https://cdn.example.com/original.jpg"
        assert helper(mood_photo) == "https://cdn.example.com/original.jpg"


# ── IMG-04..06: Endpoint integration — processed URL propagates to license ───

class TestIMG04to06EndpointPriority:

    def _run(self, coro):
        return asyncio.run(coro)

    def _make_mood_photo(self, processed_url, original_url):
        mp = MagicMock()
        mp.processed_png_url = processed_url
        mp.original_url      = original_url
        return mp

    def _make_setup(self, processed_url, original_url):
        """Return (db, user, lic, payload) with db and lic mocked."""
        from app.api.web_routes.dashboard import _WcFromMoodRequest
        mp  = self._make_mood_photo(processed_url, original_url)
        lic = MagicMock()
        lic.wc_photo_url           = None
        lic.wc_photo_portrait_url  = None
        lic.wc_photo_landscape_url = None
        db  = MagicMock()
        # filter_by chain returns the mood photo (license is patched separately)
        db.query.return_value.filter_by.return_value.first.return_value = mp
        user = MagicMock(); user.id = 42
        payload = _WcFromMoodRequest(mood_slot=_VALID_SLOT)
        return db, user, lic, payload

    def test_img_04_square_from_mood_uses_processed_url(self):
        """IMG-04: /wc-photo/from-mood writes processed_png_url when available."""
        from app.api.web_routes.dashboard import student_assign_wc_photo_from_mood
        processed = "https://cdn.example.com/processed.png"
        original  = "https://cdn.example.com/original.jpg"
        db, user, lic, payload = self._make_setup(processed, original)

        with patch("app.api.web_routes.dashboard._get_lfa_license", return_value=lic):
            self._run(student_assign_wc_photo_from_mood(payload=payload, db=db, user=user))

        assert lic.wc_photo_url == processed

    def test_img_05_portrait_from_mood_uses_processed_url(self):
        """IMG-05: /wc-photo-portrait/from-mood writes processed_png_url when available."""
        from app.api.web_routes.dashboard import student_assign_wc_portrait_photo_from_mood
        processed = "https://cdn.example.com/proc_portrait.png"
        original  = "https://cdn.example.com/orig_portrait.jpg"
        db, user, lic, payload = self._make_setup(processed, original)

        with patch("app.api.web_routes.dashboard._get_lfa_license", return_value=lic):
            self._run(student_assign_wc_portrait_photo_from_mood(payload=payload, db=db, user=user))

        assert lic.wc_photo_portrait_url == processed

    def test_img_06_landscape_from_mood_uses_processed_url(self):
        """IMG-06: /wc-photo-landscape/from-mood writes processed_png_url when available."""
        from app.api.web_routes.dashboard import student_assign_wc_landscape_photo_from_mood
        processed = "https://cdn.example.com/proc_landscape.png"
        original  = "https://cdn.example.com/orig_landscape.jpg"
        db, user, lic, payload = self._make_setup(processed, original)

        with patch("app.api.web_routes.dashboard._get_lfa_license", return_value=lic):
            self._run(student_assign_wc_landscape_photo_from_mood(payload=payload, db=db, user=user))

        assert lic.wc_photo_landscape_url == processed
