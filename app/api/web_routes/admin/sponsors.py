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
from ....models.sponsor import Sponsor, SponsorAudienceEntry, SponsorContact
from ....models.tournament_configuration import TournamentConfiguration
from ....models.tournament_type import TournamentType as TournamentTypeModel
from ....models.user import User
from ....services.sponsor_csv_import_service import (
    MAX_CSV_BYTES,
    apply_import,
    preview_rows,
)
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

    campuses = db.query(Campus).filter(Campus.is_active == True).order_by(Campus.name).all()  # noqa: E712
    tournament_types = db.query(TournamentTypeModel).order_by(TournamentTypeModel.display_name).all()

    return templates.TemplateResponse(
        "admin/sponsor_promotion_wizard.html",
        {
            "request": request,
            "user": user,
            "sponsor": sponsor,
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
    campus_id: str = Form(""),
    tournament_type_id: str = Form(""),
    age_groups: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: create one INDIVIDUAL promotion event per selected age category for a sponsor.

    Sponsor-only flow — no Club, no Team, no TournamentTeamEnrollment.
    Each age group produces one Semester record with:
      organizer_sponsor_id = sponsor.id
      organizer_club_id    = NULL  (explicit)
      participant_type     = INDIVIDUAL
    """
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")

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
            age_group=ag,                      # PRE / YOUTH / AMATEUR / PRO — already canonical
            enrollment_cost=0,
            campus_id=int(campus_id) if campus_id.strip() else None,
            organizer_sponsor_id=sponsor.id,   # sponsor organizer
            organizer_club_id=None,            # explicit NULL — never a club
        )
        db.add(t)
        db.flush()

        # Derive location from campus
        if t.campus_id:
            campus = db.query(Campus).filter(Campus.id == t.campus_id).first()
            if campus and campus.location_id:
                t.location_id = campus.location_id

        db.add(TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=int(tournament_type_id) if tournament_type_id.strip() else None,
            participant_type="INDIVIDUAL",     # sponsor events are always INDIVIDUAL
            number_of_rounds=1,
        ))
        # No TournamentTeamEnrollment — sponsor events have no teams

    db.commit()
    logger.info(
        "sponsor_promotion_created sponsor=%s age_groups=%s by admin=%s",
        sponsor.name, age_groups, user.email,
    )
    return RedirectResponse(
        url=f"/admin/promotion-events?flash=Promotion+event+created+for+{sponsor.name}",
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


# ── Sponsor Audience CSV Import ───────────────────────────────────────────────

@router.get("/admin/sponsors/{sponsor_id}/csv-import", response_class=HTMLResponse)
async def admin_sponsor_csv_upload_form(
    sponsor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: render the CSV upload form for sponsor audience import."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    if not sponsor.is_active:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}?error=Inactive+partner+cannot+import+audience",
            status_code=303,
        )
    return templates.TemplateResponse(
        "admin/sponsor_csv_upload.html",
        {"request": request, "user": user, "sponsor": sponsor},
    )


@router.post("/admin/sponsors/{sponsor_id}/csv-import/preview", response_class=HTMLResponse)
async def admin_sponsor_csv_preview(
    sponsor_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: parse and validate CSV, return preview page.  Writes nothing to DB."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    if not sponsor.is_active:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}?error=Inactive+partner+cannot+import+audience",
            status_code=303,
        )

    content = await file.read()
    if len(content) > MAX_CSV_BYTES:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}/csv-import"
            "?error=CSV+file+too+large+%28max+1+MB%29",
            status_code=303,
        )
    if not content:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}/csv-import?error=Empty+file",
            status_code=303,
        )

    result = preview_rows(content, sponsor_id, db, filename=file.filename or "upload.csv")

    return templates.TemplateResponse(
        "admin/sponsor_csv_preview.html",
        {
            "request": request,
            "user": user,
            "sponsor": sponsor,
            "preview": result,
        },
    )


@router.post("/admin/sponsors/{sponsor_id}/csv-import/apply")
async def admin_sponsor_csv_apply(
    sponsor_id: int,
    request: Request,
    csv_data: str = Form(...),
    filename: str = Form("upload.csv"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: apply the previously-previewed CSV import (single atomic transaction)."""
    _admin_guard(user)
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Partner not found")
    if not sponsor.is_active:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}?error=Inactive+partner+cannot+import+audience",
            status_code=303,
        )

    try:
        content = base64.b64decode(csv_data)
    except Exception:
        return RedirectResponse(
            f"/admin/sponsors/{sponsor_id}?error=Invalid+import+data",
            status_code=303,
        )

    log = apply_import(content, sponsor, db, user, filename=filename)
    flash = (
        f"Audience+imported+%E2%80%94+"
        f"{log.rows_created}+created%2C+{log.rows_updated}+updated"
        + (f"%2C+{log.rows_failed}+failed" if log.rows_failed else "")
    )
    return RedirectResponse(
        f"/admin/sponsors/{sponsor_id}?flash={flash}",
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
