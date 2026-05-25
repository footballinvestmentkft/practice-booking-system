"""CardDraftService — get/create/update/publish card drafts.

Phase 4D-1: service layer only.  Routes still use UserLicense legacy columns.
Phase 4D-2 will wire routes to this service.
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.card_draft import CardDraft
from app.models.license import UserLicense
from app.services.highlight_video_service import (
    extract_any_video,
    build_youtube_embed_url,
)
from app.services.profile_grid_service import (
    build_module as _build_module,
    build_video_module as _build_video_module,
    grid_fingerprint as _grid_fingerprint,
    move_slot as _move_slot,
    remove_slot as _remove_slot,
    reorder_zone as _reorder_zone,
    set_slot as _set_slot,
    validate_slot_id as _validate_slot_id,
    VALID_WIDGET_TYPES as _VALID_WIDGET_TYPES,
)


class CardDraftService:

    @staticmethod
    def get_or_create_singleton(
        db: Session, user_id: int, card_type_id: str
    ) -> CardDraft:
        """Return the singleton draft for (user_id, card_type_id), creating it if absent.

        After the Phase 4D-1 migration backfill, every LFA_FOOTBALL_PLAYER user
        already has a row.  This create-path is the safety net for users created
        after migration who bypass the backfill INSERT.  For player_card it seeds
        defaults from UserLicense if available.
        """
        draft = (
            db.query(CardDraft)
            .filter(
                CardDraft.user_id      == user_id,
                CardDraft.card_type_id == card_type_id,
                CardDraft.instance_name == "default",
            )
            .first()
        )
        if draft:
            return draft

        # Seed sensible defaults, seeding from UserLicense when possible.
        draft_theme = "default"
        draft_variant = "fifa"
        draft_platform = None
        published_theme = None
        published_variant = None
        published_platform = None
        published_at: datetime | None = None

        if card_type_id == "player_card":
            lic: UserLicense | None = (
                db.query(UserLicense)
                .filter(
                    UserLicense.user_id == user_id,
                    UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                )
                .first()
            )
            if lic:
                draft_theme    = lic.card_theme    or "default"
                draft_variant  = lic.card_variant  or "fifa"
                draft_platform = lic.public_card_platform
                if lic.published_card_theme:
                    published_theme    = lic.published_card_theme
                    published_variant  = lic.published_card_variant or "fifa"
                    published_platform = lic.published_card_platform
                    published_at       = datetime.now(timezone.utc)

        new_draft = CardDraft(
            user_id       = user_id,
            card_type_id  = card_type_id,
            instance_name = "default",
            draft_theme   = draft_theme,
            draft_variant = draft_variant,
            draft_platform = draft_platform,
            published_theme    = published_theme,
            published_variant  = published_variant,
            published_platform = published_platform,
            published_at       = published_at,
        )
        db.add(new_draft)
        db.commit()
        db.refresh(new_draft)
        return new_draft

    @staticmethod
    def get_player_card_draft(db: Session, user_id: int) -> CardDraft:
        """Convenience wrapper: singleton draft for player_card."""
        return CardDraftService.get_or_create_singleton(db, user_id, "player_card")

    @staticmethod
    def update_draft_theme(
        db: Session, draft: CardDraft, theme_id: str, *, commit: bool = True
    ) -> CardDraft:
        """Set draft_theme and persist.

        commit=False skips db.commit()/refresh() so callers that need to bundle
        multiple writes into one outer commit (e.g. unlock + apply) can do so.
        """
        draft.draft_theme = theme_id
        draft.updated_at  = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(draft)
        return draft

    @staticmethod
    def update_draft_variant(
        db: Session, draft: CardDraft, variant_id: str, *, commit: bool = True
    ) -> CardDraft:
        """Set draft_variant and persist.  commit=False defers to outer commit."""
        draft.draft_variant = variant_id
        draft.updated_at    = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(draft)
        return draft

    @staticmethod
    def update_draft_platform(
        db: Session, draft: CardDraft, platform_id: str | None, *, commit: bool = True
    ) -> CardDraft:
        """Set draft_platform (None = platform default) and persist."""
        draft.draft_platform = platform_id
        draft.updated_at     = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(draft)
        return draft

    @staticmethod
    def publish_draft(db: Session, draft: CardDraft, *, commit: bool = True) -> CardDraft:
        """Copy current draft state to the published snapshot.

        Idempotent: calling twice with identical draft state yields same result.
        Sets published_at to now() on every call (tracks most-recent publish).
        Merges draft_data.highlight_video and draft_data.profile_grid into
        published_data; absence of a key in draft_data removes it from published_data.
        """
        draft.published_theme    = draft.draft_theme
        draft.published_variant  = draft.draft_variant
        draft.published_platform = draft.draft_platform
        draft.published_at       = datetime.now(timezone.utc)
        draft.updated_at         = datetime.now(timezone.utc)

        # Copy+reassign so SQLAlchemy detects the JSON mutation.
        published_data: dict[str, Any] = dict(draft.published_data or {})

        # Merge highlight_video
        draft_hv = (draft.draft_data or {}).get("highlight_video")
        if draft_hv:
            published_data["highlight_video"] = draft_hv
        else:
            published_data.pop("highlight_video", None)

        # Merge profile_grid
        draft_pg = (draft.draft_data or {}).get("profile_grid")
        if draft_pg:
            published_data["profile_grid"] = draft_pg
        else:
            published_data.pop("profile_grid", None)

        draft.published_data = published_data if published_data else None

        if commit:
            db.commit()
            db.refresh(draft)
        return draft

    @staticmethod
    def is_published(draft: CardDraft) -> bool:
        """True if current draft state exactly matches the published snapshot.

        A draft that was never published (published_theme is None) is always
        considered unpublished regardless of draft field values.
        Compares highlight_video (video_id + provider) and profile_grid fingerprint.
        """
        if draft.published_theme is None:
            return False
        theme_ok = (
            draft.draft_theme    == draft.published_theme
            and draft.draft_variant  == draft.published_variant
            and draft.draft_platform == draft.published_platform
        )
        if not theme_ok:
            return False
        draft_hv  = (draft.draft_data    or {}).get("highlight_video") or {}
        pub_hv    = (draft.published_data or {}).get("highlight_video") or {}
        hv_ok = (
            draft_hv.get("video_id") == pub_hv.get("video_id")
            and draft_hv.get("provider") == pub_hv.get("provider")
        )
        if not hv_ok:
            return False
        draft_fp = _grid_fingerprint((draft.draft_data    or {}).get("profile_grid"))
        pub_fp   = _grid_fingerprint((draft.published_data or {}).get("profile_grid"))
        return draft_fp == pub_fp

    @staticmethod
    def set_draft_slot(
        db: Session,
        draft: CardDraft,
        slot_id: str,
        video_url: str | None = None,
        title: str = "",
        *,
        widget_type: str | None = None,
        payload: dict[str, Any] | None = None,
        thumbnail_url: str | None = None,
        commit: bool = True,
    ) -> CardDraft:
        """Write a widget module into draft_data.profile_grid[slot_id].

        Backward-compatible: passing video_url (no widget_type) behaves as before.
        New path: pass widget_type + payload dict for text_bio / image_url / video.
        thumbnail_url: optional HTTPS URL for TikTok custom thumbnail preview.

        Raises ValueError for unknown slot_id, invalid URL, or bad widget payload.
        """
        _validate_slot_id(slot_id)
        if widget_type is None:
            # Legacy video path — video_url required.
            if video_url is None:
                raise ValueError("video_url is required when widget_type is not specified.")
            module = _build_video_module(video_url, title, thumbnail_url)
        else:
            _payload: dict[str, Any] = dict(payload or {})
            # Allow callers to pass video_url as positional even with widget_type for video types.
            if widget_type in ("video_youtube", "video_tiktok") and video_url and "video_url" not in _payload:
                _payload["video_url"] = video_url
                if title and "title" not in _payload:
                    _payload["title"] = title
            if thumbnail_url and "thumbnail_url" not in _payload:
                _payload["thumbnail_url"] = thumbnail_url
            module = _build_module(widget_type, _payload)
        draft_data: dict[str, Any] = dict(draft.draft_data or {})
        draft_data["profile_grid"] = _set_slot(draft_data.get("profile_grid"), slot_id, module)
        draft.draft_data = draft_data
        draft.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(draft)
        return draft

    @staticmethod
    def remove_draft_slot(
        db: Session, draft: CardDraft, slot_id: str, *, commit: bool = True
    ) -> CardDraft:
        """Remove a slot module from draft_data.profile_grid.

        Publish is required for removal to be reflected on the public profile.
        """
        _validate_slot_id(slot_id)
        draft_data: dict[str, Any] = dict(draft.draft_data or {})
        existing_pg = draft_data.get("profile_grid")
        new_pg = _remove_slot(existing_pg, slot_id)
        if new_pg:
            draft_data["profile_grid"] = new_pg
        else:
            draft_data.pop("profile_grid", None)
        draft.draft_data = draft_data if draft_data else None
        draft.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(draft)
        return draft

    @staticmethod
    def reorder_draft_zone(
        db: Session,
        draft: CardDraft,
        zone: str,
        slot_ids: list[str],
        *,
        commit: bool = True,
    ) -> CardDraft:
        """Reorder filled modules within a zone in draft_data.profile_grid.

        slot_ids: slot_ids of the zone's slots in desired visual order.
        No-op (no DB write) when ≤1 filled slot.
        Raises ValueError for unknown zone or mismatched slot_ids.
        """
        draft_data: dict[str, Any] = dict(draft.draft_data or {})
        existing_pg = draft_data.get("profile_grid")
        new_pg = _reorder_zone(existing_pg, zone, slot_ids)
        if new_pg is existing_pg:
            return draft  # no-op — ≤1 filled slot
        if new_pg:
            draft_data["profile_grid"] = new_pg
        else:
            draft_data.pop("profile_grid", None)
        draft.draft_data = draft_data
        draft.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(draft)
        return draft

    @staticmethod
    def move_draft_slot(
        db: Session,
        draft: CardDraft,
        source_slot_id: str,
        target_slot_id: str,
        *,
        on_conflict: str = "swap",
        commit: bool = True,
    ) -> CardDraft:
        """Move module from source to target slot in draft_data.profile_grid.

        on_conflict: "swap" (default) | "overwrite" | "reject"
        No DB write when source is empty (no-op).
        Raises ValueError for unknown slots, source == target, occupied reject, or
        invalid on_conflict value.
        """
        draft_data: dict[str, Any] = dict(draft.draft_data or {})
        existing_pg = draft_data.get("profile_grid")
        new_pg = _move_slot(existing_pg, source_slot_id, target_slot_id, on_conflict=on_conflict)
        if new_pg is existing_pg:
            return draft  # no-op — source was empty
        draft_data["profile_grid"] = new_pg
        draft.draft_data = draft_data
        draft.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(draft)
        return draft

    @staticmethod
    def update_draft_highlight_video(
        db: Session, draft: CardDraft, video_url: str, *, commit: bool = True
    ) -> CardDraft:
        """Validate YouTube or TikTok URL, extract video_id, write into draft_data.highlight_video.

        Raises ValueError for invalid / unsupported URLs, including TikTok short URLs
        (vm./vt.tiktok.com) with an informative message guiding the user to paste
        the full canonical link.
        source_url is stored for audit/prefill only — never used as iframe src.
        """
        try:
            parsed = extract_any_video(video_url)
        except ValueError:
            raise  # propagate informative short-URL message
        if parsed is None:
            raise ValueError(
                "Invalid or unsupported video URL. "
                "Paste a YouTube link (youtube.com/watch?v=...) or "
                "full TikTok link (tiktok.com/@user/video/...)."
            )
        draft_data: dict[str, Any] = dict(draft.draft_data or {})
        draft_data["highlight_video"] = {
            "provider":   parsed["provider"],
            "video_id":   parsed["video_id"],
            "source_url": video_url,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        draft.draft_data = draft_data
        draft.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(draft)
        return draft

    @staticmethod
    def remove_draft_highlight_video(
        db: Session, draft: CardDraft, *, commit: bool = True
    ) -> CardDraft:
        """Remove highlight_video from draft_data.

        Publish is required for the removal to be reflected on the public profile.
        """
        draft_data: dict[str, Any] = dict(draft.draft_data or {})
        draft_data.pop("highlight_video", None)
        draft.draft_data = draft_data if draft_data else None
        draft.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(draft)
        return draft
