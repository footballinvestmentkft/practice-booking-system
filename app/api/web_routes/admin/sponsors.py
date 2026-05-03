"""Admin sponsor management routes."""
import base64
import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.campus import Campus
from ....models.club import CsvImportLog
from ....models.semester import Semester, SemesterCategory, SemesterStatus
from ....models.sponsor import Sponsor, SponsorAudienceEntry, SponsorCampaign, SponsorContact
from ....models.tournament_configuration import TournamentConfiguration
from ....models.tournament_type import TournamentType as TournamentTypeModel
from ....models.license import UserLicense
from ....models.user import User
from ....services.sponsor_csv_import_service import (
    MAX_CSV_BYTES,
    apply_import,
    preview_rows,
)
from ....services.sponsor_cleanup_service import (
    CleanupResult,
    rollback_import,
    soft_delete_entry,
    suppress_entry,
    unlink_entry,
)
from ....services.sponsor_promote_service import promote_entries
from . import _admin_guard, templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/sponsors", response_class=HTMLResponse)
async def admin_sponsors_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: list all sponsors."""
    _admin_guard(user)
    sponsors = db.query(Sponsor).order_by(Sponsor.name).all()
    return templates.TemplateResponse(
        "admin/sponsors.html",
        {"request": request, "user": user, "sponsors": sponsors},
    )


@router.get("/admin/sponsors/new", response_class=HTMLResponse)
async def admin_sponsors_new_form(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    """Admin: render new-sponsor form."""
    _admin_guard(user)
    return templates.TemplateResponse(
        "admin/sponsor_new.html",
        {"request": request, "user": user, "error": None},
    )


@router.post("/admin/sponsors/new")
async def admin_sponsors_create(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    brand_category: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
    country: Optional[str] = Form(None),
    contact_email: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    # primary contact (optional)
    contact_name: Optional[str] = Form(None),
    contact_role: Optional[str] = Form(None),
    contact_email_primary: Optional[str] = Form(None),
    contact_phone: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: create a new sponsor."""
    _admin_guard(user)

    code = code.strip().upper()

    # Uniqueness check (user-friendly error before DB constraint fires)
    existing = db.query(Sponsor).filter(Sponsor.code == code).first()
    if existing:
        return templates.TemplateResponse(
            "admin/sponsor_new.html",
            {
                "request": request,
                "user": user,
                "error": f"Partner code '{code}' is already in use.",
                "form": {
                    "name": name, "code": code, "brand_category": brand_category,
                    "city": city, "country": country, "contact_email": contact_email,
                    "website": website, "notes": notes,
                    "contact_name": contact_name, "contact_role": contact_role,
                    "contact_email_primary": contact_email_primary,
                    "contact_phone": contact_phone,
                },
            },
            status_code=400,
        )

    sponsor = Sponsor(
        name=name,
        code=code,
        brand_category=brand_category or None,
        city=city or None,
        country=country or None,
        contact_email=contact_email or None,
        website=website or None,
        notes=notes or None,
        is_active=True,
        created_by=user.id,
    )
    db.add(sponsor)
    db.flush()  # get sponsor.id before adding contact

    if contact_name and contact_name.strip():
        primary_contact = SponsorContact(
            sponsor_id=sponsor.id,
            name=contact_name.strip(),
            role=contact_role or None,
            email=contact_email_primary or None,
            phone=contact_phone or None,
            is_primary=True,
        )
        db.add(primary_contact)

    db.commit()
    logger.info("Sponsor created: id=%s code=%s by user=%s", sponsor.id, sponsor.code, user.id)
    return RedirectResponse(
        f"/admin/sponsors/{sponsor.id}?flash=Partner+created",
        status_code=303,
    )


@router.get("/admin/sponsors/{sponsor_id}", response_class=HTMLResponse)
async def admin_sponsors_detail(
    sponsor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: sponsor detail + contacts + linked events."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")

    return templates.TemplateResponse(
        "admin/sponsor_detail.html",
        {"request": request, "user": user, "sponsor": sponsor},
    )


@router.post("/admin/sponsors/{sponsor_id}/edit")
async def admin_sponsors_edit(
    sponsor_id: int,
    request: Request,
    name: str = Form(...),
    brand_category: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
    country: Optional[str] = Form(None),
    contact_email: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: update sponsor fields."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")

    sponsor.name           = name
    sponsor.brand_category = brand_category or None
    sponsor.city           = city or None
    sponsor.country        = country or None
    sponsor.contact_email  = contact_email or None
    sponsor.website        = website or None
    sponsor.notes          = notes or None
    sponsor.is_active      = is_active == "on"
    db.commit()
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}?flash=Partner+updated",
        status_code=303,
    )


@router.post("/admin/sponsors/{sponsor_id}/contacts/add")
async def admin_sponsors_add_contact(
    sponsor_id: int,
    request: Request,
    contact_name: str = Form(...),
    contact_role: Optional[str] = Form(None),
    contact_email: Optional[str] = Form(None),
    contact_phone: Optional[str] = Form(None),
    is_primary: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: add a contact to a sponsor."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")

    want_primary = is_primary == "on"

    if want_primary:
        existing_primary = (
            db.query(SponsorContact)
            .filter(SponsorContact.sponsor_id == sponsor_id, SponsorContact.is_primary == True)  # noqa: E712
            .first()
        )
        if existing_primary:
            return RedirectResponse(
                f"/admin/sponsors/{sponsor_id}?error=This+partner+already+has+a+primary+contact.+Unset+it+first.",
                status_code=303,
            )

    contact = SponsorContact(
        sponsor_id=sponsor_id,
        name=contact_name.strip(),
        role=contact_role or None,
        email=contact_email or None,
        phone=contact_phone or None,
        is_primary=want_primary,
    )
    db.add(contact)
    db.commit()
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}?flash=Contact+added",
        status_code=303,
    )


@router.get("/admin/sponsors/{sponsor_id}/promotion", response_class=HTMLResponse)
async def admin_sponsor_promotion_form(
    sponsor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: render the sponsor promotion event wizard."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")

    active_campaigns = (
        db.query(SponsorCampaign)
        .filter(SponsorCampaign.sponsor_id == sponsor_id, SponsorCampaign.status == "ACTIVE")
        .order_by(SponsorCampaign.created_at.desc())
        .all()
    )
    if not active_campaigns:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}?error=No+active+campaigns+found.+Create+a+campaign+first.",
            status_code=303,
        )

    campuses = db.query(Campus).filter(Campus.is_active == True).order_by(Campus.name).all()  # noqa: E712
    tournament_types = db.query(TournamentTypeModel).order_by(TournamentTypeModel.display_name).all()

    return templates.TemplateResponse(
        "admin/sponsor_promotion_wizard.html",
        {
            "request": request,
            "user": user,
            "sponsor": sponsor,
            "active_campaigns": active_campaigns,
            "campuses": campuses,
            "tournament_types": tournament_types,
        },
    )


@router.post("/admin/sponsors/{sponsor_id}/promotion")
async def admin_sponsor_promotion_create(
    sponsor_id: int,
    request: Request,
    tournament_name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    campaign_id: str = Form(""),
    campus_id: str = Form(""),
    tournament_type_id: str = Form(""),
    age_groups: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: create one INDIVIDUAL promotion event per selected age category for a sponsor.

    Sponsor-only flow — no Club, no Team, no TournamentTeamEnrollment.
    Each age group produces one Semester record with:
      organizer_sponsor_id    = sponsor.id
      organizer_campaign_id   = campaign.id  (required — campaign audience feeds the event)
      organizer_club_id       = NULL  (explicit)
      participant_type        = INDIVIDUAL
    """
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")

    if not campaign_id.strip():
        return RedirectResponse(
            url=f"/admin/sponsors/{sponsor_id}/promotion?error=Select+a+campaign",
            status_code=303,
        )
    campaign = (
        db.query(SponsorCampaign)
        .filter(
            SponsorCampaign.id == int(campaign_id),
            SponsorCampaign.sponsor_id == sponsor_id,
            SponsorCampaign.status == "ACTIVE",
        )
        .first()
    )
    if not campaign:
        return RedirectResponse(
            url=f"/admin/sponsors/{sponsor_id}/promotion?error=Invalid+or+inactive+campaign",
            status_code=303,
        )

    if not age_groups:
        return RedirectResponse(
            url=f"/admin/sponsors/{sponsor_id}/promotion?error=Select+at+least+one+age+category",
            status_code=303,
        )

    for ag in age_groups:
        suffix = datetime.now().strftime("%H%M%S%f")[:9]
        code = f"PROMO-{date.fromisoformat(start_date).strftime('%Y%m%d')}-{ag.upper()[:6]}-{suffix}"

        t = Semester(
            code=code,
            name=f"{tournament_name} ({ag})",
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            status=SemesterStatus.DRAFT,
            tournament_status="DRAFT",
            semester_category=SemesterCategory.PROMOTION_EVENT,
            age_group=ag,                        # PRE / YOUTH / AMATEUR / PRO — already canonical
            enrollment_cost=0,
            campus_id=int(campus_id) if campus_id.strip() else None,
            organizer_sponsor_id=sponsor.id,     # sponsor organizer
            organizer_campaign_id=campaign.id,   # campaign whose audience feeds this event
            organizer_club_id=None,              # explicit NULL — never a club
        )
        db.add(t)
        db.flush()

        # Derive location from campus
        if t.campus_id:
            campus_obj = db.query(Campus).filter(Campus.id == t.campus_id).first()
            if campus_obj and campus_obj.location_id:
                t.location_id = campus_obj.location_id

        db.add(TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=int(tournament_type_id) if tournament_type_id.strip() else None,
            participant_type="INDIVIDUAL",       # sponsor events are always INDIVIDUAL
            number_of_rounds=1,
        ))
        # No TournamentTeamEnrollment — sponsor events have no teams

    db.commit()
    logger.info(
        "sponsor_promotion_created sponsor=%s campaign=%s age_groups=%s by admin=%s",
        sponsor.name, campaign.id, age_groups, user.email,
    )
    return RedirectResponse(
        url=f"/admin/sponsors/{sponsor_id}?flash=Promotion+event+created",
        status_code=303,
    )


@router.post("/admin/sponsors/{sponsor_id}/toggle")
async def admin_sponsors_toggle(
    sponsor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: activate or deactivate a sponsor."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    sponsor.is_active = not sponsor.is_active
    db.commit()
    status_word = "activated" if sponsor.is_active else "deactivated"
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}?flash=Partner+{status_word}",
        status_code=303,
    )


# ── P3: Campaign management ───────────────────────────────────────────────────

@router.post("/admin/sponsors/{sponsor_id}/campaigns")
async def admin_sponsor_campaigns_create(
    sponsor_id: int,
    request: Request,
    name: str = Form(...),
    campaign_type: str = Form("IMPORT"),
    event_date: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Create a new campaign for a sponsor."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    if not sponsor.is_active:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}?error=Inactive+partner+cannot+create+campaign",
            status_code=303,
        )
    parsed_date: Optional[date] = None
    if event_date and event_date.strip():
        try:
            parsed_date = date.fromisoformat(event_date.strip())
        except ValueError:
            pass
    campaign = SponsorCampaign(
        sponsor_id=sponsor_id,
        name=name.strip(),
        campaign_type=campaign_type.strip() or "IMPORT",
        event_date=parsed_date,
        status="ACTIVE",
        notes=notes or None,
        created_by=user.id,
    )
    db.add(campaign)
    db.commit()
    logger.info("sponsor_campaign_created sponsor=%s campaign=%s by=%s",
                sponsor_id, campaign.id, user.id)
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}/campaigns/{campaign.id}?flash=Campaign+created",
        status_code=303,
    )


@router.get("/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}", response_class=HTMLResponse)
async def admin_sponsor_campaign_detail(
    sponsor_id: int,
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Campaign detail: entry stats, import history, audience link."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    campaign = (
        db.query(SponsorCampaign)
        .filter(SponsorCampaign.id == campaign_id, SponsorCampaign.sponsor_id == sponsor_id)
        .first()
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    imports = (
        db.query(CsvImportLog)
        .filter(CsvImportLog.campaign_id == campaign_id)
        .order_by(CsvImportLog.uploaded_at.desc())
        .all()
    )
    flash = request.query_params.get("flash", "")
    err   = request.query_params.get("error", "")
    return templates.TemplateResponse(
        "admin/sponsor_campaign_detail.html",
        {
            "request": request,
            "user": user,
            "sponsor": sponsor,
            "campaign": campaign,
            "imports": imports,
            "flash": flash,
            "error": err,
        },
    )


# ── P3: Campaign-scoped audience list ────────────────────────────────────────

def _build_audience_context(
    sponsor_id: int,
    entries: list,
    db: Session,
) -> dict:
    """Build promoters + readiness dicts for audience list template."""
    promoter_ids = {e.promoted_by for e in entries if e.promoted_by}
    promoters: dict[int, str] = {}
    if promoter_ids:
        for u in db.query(User).filter(User.id.in_(promoter_ids)).all():
            promoters[u.id] = u.name

    _VALID_POS = frozenset({"STRIKER", "MIDFIELDER", "DEFENDER", "GOALKEEPER"})
    promoted_user_ids = {e.user_id for e in entries if e.user_id}
    license_map: dict[int, UserLicense] = {}
    if promoted_user_ids:
        for lic in (
            db.query(UserLicense)
            .filter(
                UserLicense.user_id.in_(promoted_user_ids),
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                UserLicense.is_active == True,  # noqa: E712
            )
            .all()
        ):
            license_map[lic.user_id] = lic

    readiness: dict[int, str] = {}
    for e in entries:
        if not e.user_id:
            readiness[e.id] = "no_user"
        elif not e.date_of_birth:
            readiness[e.id] = "dob_missing"
        elif e.position not in _VALID_POS:
            readiness[e.id] = "position_missing"
        else:
            lic = license_map.get(e.user_id)
            has_onboarding = lic and (lic.onboarding_completed or lic.football_skills is not None)
            readiness[e.id] = "ready" if has_onboarding else "onboarding_missing"

    return {"promoters": promoters, "readiness": readiness}


@router.get("/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience",
            response_class=HTMLResponse)
async def admin_sponsor_campaign_audience_list(
    sponsor_id: int,
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Campaign-scoped audience/prospect list."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    campaign = (
        db.query(SponsorCampaign)
        .filter(SponsorCampaign.id == campaign_id, SponsorCampaign.sponsor_id == sponsor_id)
        .first()
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    entries = (
        db.query(SponsorAudienceEntry)
        .filter(SponsorAudienceEntry.campaign_id == campaign_id)
        .order_by(SponsorAudienceEntry.imported_at.desc())
        .all()
    )
    ctx = _build_audience_context(sponsor_id, entries, db)
    flash = request.query_params.get("flash", "")
    return templates.TemplateResponse(
        "admin/sponsor_audience_list.html",
        {
            "request": request,
            "user": user,
            "sponsor": sponsor,
            "campaign": campaign,
            "entries": entries,
            "flash": flash,
            **ctx,
        },
    )


@router.post("/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience/promote")
async def admin_sponsor_campaign_audience_promote(
    sponsor_id: int,
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Promote selected ACTIVE entries from a campaign to User accounts."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    if not sponsor.is_active:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}?error=Inactive+partner",
            status_code=303,
        )

    form = await request.form()
    raw_ids = form.getlist("entry_ids")
    try:
        entry_ids = [int(i) for i in raw_ids if i]
    except ValueError:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience"
            "?error=Invalid+entry+IDs",
            status_code=303,
        )

    if not entry_ids:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience"
            "?flash=No+entries+selected",
            status_code=303,
        )

    result = promote_entries(entry_ids, sponsor_id, db, user, campaign_id=campaign_id)
    parts = []
    if result.promoted:
        parts.append(f"{result.promoted}+promoted")
    if result.already_linked:
        parts.append(f"{result.already_linked}+already+linked")
    if result.skipped:
        parts.append(f"{result.skipped}+skipped+%28not+ACTIVE+or+no+consent%29")
    flash = "%2C+".join(parts) if parts else "No+changes"
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience?flash={flash}",
        status_code=303,
    )


# ── P3: Campaign-scoped cleanup routes ───────────────────────────────────────

@router.post("/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience/{entry_id}/suppress")
async def admin_campaign_audience_suppress(
    sponsor_id: int,
    campaign_id: int,
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)
    result = suppress_entry(entry_id, sponsor_id, db, user)
    base = f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience"
    if result.errors:
        flash = "error=" + "+".join(result.errors[0].replace(" ", "+").split())
        return RedirectResponse(f"{base}?{flash}", status_code=303)
    return RedirectResponse(f"{base}?flash=1+suppressed", status_code=303)


@router.post("/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience/{entry_id}/delete")
async def admin_campaign_audience_delete(
    sponsor_id: int,
    campaign_id: int,
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)
    result = soft_delete_entry(entry_id, sponsor_id, db, user)
    base = f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience"
    if result.errors:
        flash = "error=" + "+".join(result.errors[0].replace(" ", "+").split())
        return RedirectResponse(f"{base}?{flash}", status_code=303)
    return RedirectResponse(f"{base}?flash=1+deleted", status_code=303)


@router.post("/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience/{entry_id}/unlink")
async def admin_campaign_audience_unlink(
    sponsor_id: int,
    campaign_id: int,
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)
    result = unlink_entry(entry_id, sponsor_id, db, user)
    base = f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/audience"
    if result.errors:
        flash = "error=" + "+".join(result.errors[0].replace(" ", "+").split())
        return RedirectResponse(f"{base}?{flash}", status_code=303)
    flash = f"1+unlinked+%28User+%23{result.unlinked_user_id}+preserved%29"
    return RedirectResponse(f"{base}?flash={flash}", status_code=303)


# ── P3: Campaign-scoped import ────────────────────────────────────────────────

@router.get("/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/import",
            response_class=HTMLResponse)
async def admin_sponsor_campaign_import_form(
    sponsor_id: int,
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Render CSV upload form scoped to a campaign."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    campaign = (
        db.query(SponsorCampaign)
        .filter(SponsorCampaign.id == campaign_id, SponsorCampaign.sponsor_id == sponsor_id)
        .first()
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if not sponsor.is_active:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}"
            "?error=Inactive+partner+cannot+import+audience",
            status_code=303,
        )
    return templates.TemplateResponse(
        "admin/sponsor_csv_upload.html",
        {"request": request, "user": user, "sponsor": sponsor, "campaign": campaign},
    )


@router.post("/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/import/preview",
             response_class=HTMLResponse)
async def admin_sponsor_campaign_import_preview(
    sponsor_id: int,
    campaign_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Parse and validate CSV for a campaign.  Writes nothing to DB."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    campaign = (
        db.query(SponsorCampaign)
        .filter(SponsorCampaign.id == campaign_id, SponsorCampaign.sponsor_id == sponsor_id)
        .first()
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if not sponsor.is_active:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}"
            "?error=Inactive+partner+cannot+import+audience",
            status_code=303,
        )

    content = await file.read()
    base_url = f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/import"
    if len(content) > MAX_CSV_BYTES:
        return RedirectResponse(
            f"{base_url}?error=CSV+file+too+large+%28max+1+MB%29",
            status_code=303,
        )
    if not content:
        return RedirectResponse(f"{base_url}?error=Empty+file", status_code=303)

    result = preview_rows(content, campaign_id, db, filename=file.filename or "upload.csv")
    return templates.TemplateResponse(
        "admin/sponsor_csv_preview.html",
        {
            "request": request,
            "user": user,
            "sponsor": sponsor,
            "campaign": campaign,
            "preview": result,
        },
    )


@router.post("/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/import/apply")
async def admin_sponsor_campaign_import_apply(
    sponsor_id: int,
    campaign_id: int,
    request: Request,
    csv_data: str = Form(...),
    filename: str = Form("upload.csv"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Apply the previewed CSV import into a campaign (single atomic transaction)."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    campaign = (
        db.query(SponsorCampaign)
        .filter(SponsorCampaign.id == campaign_id, SponsorCampaign.sponsor_id == sponsor_id)
        .first()
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if not sponsor.is_active:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}"
            "?error=Inactive+partner+cannot+import+audience",
            status_code=303,
        )

    try:
        content = base64.b64decode(csv_data)
    except Exception:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}?error=Invalid+import+data",
            status_code=303,
        )

    log = apply_import(content, sponsor, db, user, campaign_id=campaign_id, filename=filename)
    flash = (
        f"Audience+imported+%E2%80%94+"
        f"{log.rows_created}+created%2C+{log.rows_updated}+updated"
        + (f"%2C+{log.rows_failed}+failed" if log.rows_failed else "")
    )
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}?flash={flash}",
        status_code=303,
    )


@router.post("/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}/import/{log_id}/rollback")
async def admin_sponsor_campaign_import_rollback(
    sponsor_id: int,
    campaign_id: int,
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Rollback a CSV import: soft-delete all unpromoted entries from this import log."""
    _admin_guard(user)
    result = rollback_import(log_id, sponsor_id, db, user)
    base = f"/admin/sponsors/{sponsor_id}/campaigns/{campaign_id}"
    if result.errors:
        flash = "error=" + "+".join(result.errors[0].replace(" ", "+").split())
        return RedirectResponse(f"{base}?{flash}", status_code=303)
    parts = []
    if result.deleted:
        parts.append(f"{result.deleted}+deleted")
    if result.already_deleted:
        parts.append(f"{result.already_deleted}+already+deleted+%E2%80%94+no+action+needed")
    if result.skipped:
        parts.append(f"{result.skipped}+skipped+%28already+promoted%29")
    flash = "%2C+".join(parts) if parts else "nothing+to+rollback"
    return RedirectResponse(f"{base}?flash={flash}", status_code=303)


# ── Legacy redirects (P2 URLs → P3 campaign-based URLs) ──────────────────────

@router.get("/admin/sponsors/{sponsor_id}/audience", response_class=HTMLResponse)
async def _redirect_audience_to_campaigns(
    sponsor_id: int,
    user: User = Depends(get_current_user_web),
):
    """Legacy URL — redirects to campaign list on sponsor detail."""
    _admin_guard(user)
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}",
        status_code=303,
    )


@router.get("/admin/sponsors/{sponsor_id}/csv-import", response_class=HTMLResponse)
async def _redirect_csv_import_to_campaigns(
    sponsor_id: int,
    user: User = Depends(get_current_user_web),
):
    """Legacy URL — redirects to sponsor detail (create a campaign there first)."""
    _admin_guard(user)
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}",
        status_code=303,
    )


@router.post("/admin/sponsors/{sponsor_id}/csv-import/{log_id}/rollback")
async def _legacy_rollback_redirect(
    sponsor_id: int,
    log_id: int,
    user: User = Depends(get_current_user_web),
):
    """Legacy rollback URL — redirects to sponsor detail with guidance."""
    _admin_guard(user)
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}"
        "?error=Please+use+the+campaign+rollback+button+in+the+campaign+detail+page",
        status_code=303,
    )


@router.post("/admin/sponsors/{sponsor_id}/contacts/{contact_id}/delete")
async def admin_sponsors_delete_contact(
    sponsor_id: int,
    contact_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: remove a contact from a sponsor."""
    _admin_guard(user)
    contact = (
        db.query(SponsorContact)
        .filter(SponsorContact.id == contact_id, SponsorContact.sponsor_id == sponsor_id)
        .first()
    )
    if contact:
        db.delete(contact)
        db.commit()
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}?flash=Contact+removed",
        status_code=303,
    )
