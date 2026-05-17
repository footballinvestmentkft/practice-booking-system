"""Admin card design management — JSON manifest upload, preview, apply, toggle, reorder."""
import json
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.card_design import CardDesign
from ....models.user import User
from ....services.card_design_admin_service import (
    MAX_MANIFEST_BYTES,
    DesignPreviewRow,
    apply_manifest,
    get_design_usage_summary,
    validate_manifest,
)
from ....services.card_design_service import _invalidate_cache

from . import _admin_guard, templates

logger = logging.getLogger(__name__)

router = APIRouter()

_PROTECTED_ID = "fifa"


# ── AD-01  Design list ────────────────────────────────────────────────────────

@router.get("/admin/card-designs", response_class=HTMLResponse)
async def admin_card_designs_list(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)
    designs = (
        db.query(CardDesign)
        .order_by(CardDesign.sort_order.asc(), CardDesign.id.asc())
        .all()
    )
    return templates.TemplateResponse("admin/card_designs.html", {
        "request": request,
        "designs": designs,
        "error":   request.query_params.get("error", ""),
        "success": request.query_params.get("success", ""),
        "warning": request.query_params.get("warning", ""),
        "pending_toggle": request.query_params.get("pending_toggle", ""),
    })


# ── AD-02  Upload form ────────────────────────────────────────────────────────

@router.get("/admin/card-designs/upload", response_class=HTMLResponse)
async def admin_card_designs_upload_form(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)
    return templates.TemplateResponse("admin/card_designs_upload.html", {
        "request":      request,
        "preview_rows": None,
        "preview_json": None,
        "error":        request.query_params.get("error", ""),
    })


# ── AD-03  Upload → preview (no DB write) ─────────────────────────────────────

@router.post("/admin/card-designs/upload", response_class=HTMLResponse)
async def admin_card_designs_upload_preview(
    request: Request,
    file: UploadFile = File(...),
    db:   Session    = Depends(get_db),
    user: User       = Depends(get_current_user_web),
):
    _admin_guard(user)

    content = await file.read()
    if not content:
        return RedirectResponse(
            "/admin/card-designs/upload?error=Empty+file+uploaded",
            status_code=303,
        )
    if len(content) > MAX_MANIFEST_BYTES:
        return RedirectResponse(
            "/admin/card-designs/upload?error=File+too+large+%28max+32+KB%29",
            status_code=303,
        )

    result = validate_manifest(content, db)

    if not result.ok:
        first_err = result.errors[0][:200].replace(" ", "+").replace(":", "%3A")
        return RedirectResponse(
            f"/admin/card-designs/upload?error={first_err}",
            status_code=303,
        )

    # Serialize preview rows to JSON for hidden field (B-variant state passing)
    preview_payload = [
        {
            "item":   row.item.model_dump(),
            "action": row.action,
            "diff":   {
                k: [
                    v[0] if not isinstance(v[0], (list, dict)) else v[0],
                    v[1] if not isinstance(v[1], (list, dict)) else v[1],
                ]
                for k, v in row.diff.items()
            },
        }
        for row in result.preview_rows
    ]

    return templates.TemplateResponse("admin/card_designs_upload.html", {
        "request":      request,
        "preview_rows": result.preview_rows,
        "preview_json": json.dumps(preview_payload),
        "all_errors":   result.errors,
        "error":        "",
    })


# ── AD-04  Apply confirmed preview ────────────────────────────────────────────

@router.post("/admin/card-designs/apply", response_class=HTMLResponse)
async def admin_card_designs_apply(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    form = await request.form()
    raw_json = form.get("preview_json", "")
    if not raw_json:
        return RedirectResponse(
            "/admin/card-designs/upload?error=No+preview+data+found.+Please+upload+the+manifest+again.",
            status_code=303,
        )

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return RedirectResponse(
            "/admin/card-designs/upload?error=Corrupted+preview+data.+Please+upload+the+manifest+again.",
            status_code=303,
        )

    # Re-validate payload (protects against hidden-field tampering)
    from ....services.card_design_admin_service import DesignManifestItem
    try:
        preview_rows: list[DesignPreviewRow] = [
            DesignPreviewRow(
                item=DesignManifestItem.model_validate(entry["item"]),
                action=entry["action"],
                diff={k: tuple(v) for k, v in entry.get("diff", {}).items()},
            )
            for entry in payload
        ]
    except Exception as exc:
        return RedirectResponse(
            f"/admin/card-designs/upload?error=Preview+data+invalid%3A+{str(exc)[:100]}",
            status_code=303,
        )

    try:
        applied = apply_manifest(db, preview_rows)
    except Exception as exc:
        logger.exception("card_designs apply_manifest failed")
        return RedirectResponse(
            f"/admin/card-designs?error=Apply+failed%3A+{str(exc)[:120]}",
            status_code=303,
        )

    count = len(applied)
    return RedirectResponse(
        f"/admin/card-designs?success={count}+design(s)+applied+successfully",
        status_code=303,
    )


# ── AD-05  Toggle is_active ───────────────────────────────────────────────────

@router.post("/admin/card-designs/{design_id}/toggle", response_class=HTMLResponse)
async def admin_card_designs_toggle(
    design_id: str,
    request:   Request,
    db:        Session = Depends(get_db),
    user:      User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    if design_id == _PROTECTED_ID:
        return RedirectResponse(
            "/admin/card-designs?error=The+FIFA+design+is+protected+and+cannot+be+deactivated.",
            status_code=303,
        )

    design = db.query(CardDesign).filter(CardDesign.id == design_id).first()
    if not design:
        return RedirectResponse(
            "/admin/card-designs?error=Design+not+found.",
            status_code=303,
        )

    if design.is_active:
        usage = get_design_usage_summary(db, design_id)
        if usage["total_affected"] > 0:
            parts = []
            if usage["draft_count"]:
                parts.append(f"{usage['draft_count']} draft(s)")
            if usage["published_count"]:
                parts.append(f"{usage['published_count']} published card(s)")
            if usage["active_license_count"]:
                parts.append(f"{usage['active_license_count']} active license(s)")
            if usage["unlocked_count"]:
                parts.append(f"{usage['unlocked_count']} user purchase(s)")
            summary = ", ".join(parts)
            confirm = request.query_params.get("confirm", "")
            if confirm != "1":
                warn_msg = (
                    f"Design+'{design.label}'+is+used+by+{summary}.+"
                    f"Deactivating+will+cause+visual+fallback+to+FIFA+for+affected+users.+"
                    f"To+confirm%2C+click+Deactivate+again."
                )
                return RedirectResponse(
                    f"/admin/card-designs?warning={warn_msg}&pending_toggle={design_id}",
                    status_code=303,
                )

    design.is_active = not design.is_active
    from datetime import datetime, timezone
    design.updated_at = datetime.now(timezone.utc)
    db.commit()
    _invalidate_cache()

    action = "deactivated" if not design.is_active else "activated"
    return RedirectResponse(
        f"/admin/card-designs?success=Design+'{design.label}'+{action}+successfully.",
        status_code=303,
    )


# ── AD-06  Reorder sort_order ─────────────────────────────────────────────────

@router.post("/admin/card-designs/reorder", response_class=HTMLResponse)
async def admin_card_designs_reorder(
    request: Request,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    form = await request.form()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    updated = 0
    for key, val in form.items():
        if not key.startswith("sort_"):
            continue
        did = key[5:]
        try:
            new_order = int(val)
        except (ValueError, TypeError):
            continue
        design = db.query(CardDesign).filter(CardDesign.id == did).first()
        if design and design.sort_order != new_order:
            design.sort_order = new_order
            design.updated_at = now
            updated += 1

    if updated:
        db.commit()
        _invalidate_cache()

    return RedirectResponse(
        f"/admin/card-designs?success=Sort+order+updated+({updated}+design(s))",
        status_code=303,
    )
