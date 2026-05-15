"""CardDraftService — get/create/update/publish card drafts.

Phase 4D-1: service layer only.  Routes still use UserLicense legacy columns.
Phase 4D-2 will wire routes to this service.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.card_draft import CardDraft
from app.models.license import UserLicense


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
        """
        draft.published_theme    = draft.draft_theme
        draft.published_variant  = draft.draft_variant
        draft.published_platform = draft.draft_platform
        draft.published_at       = datetime.now(timezone.utc)
        draft.updated_at         = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(draft)
        return draft

    @staticmethod
    def is_published(draft: CardDraft) -> bool:
        """True if current draft state exactly matches the published snapshot.

        A draft that was never published (published_theme is None) is always
        considered unpublished regardless of draft field values.
        """
        if draft.published_theme is None:
            return False
        return (
            draft.draft_theme    == draft.published_theme
            and draft.draft_variant  == draft.published_variant
            and draft.draft_platform == draft.published_platform
        )
