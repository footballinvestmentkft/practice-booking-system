"""Admin card theme management — JSON manifest upload, preview, apply, toggle."""
import json
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.card_theme import CardTheme
from ....models.user import User
from ....services.card_theme_admin_service import (
    MAX_MANIFEST_BYTES,
    ThemePreviewRow,
    apply_manifest,
    get_theme_usage_summary,
    validate_manifest,
)
from ....services.card_theme_service import _invalidate_cache

from . import _admin_guard, templates

logger = logging.getLogger(__name__)

router = APIRouter()

_PROTECTED_ID = "default"


# ── AT-01  Theme list ─────────────────────────────────────────────────────────

@router.get("/admin/card-themes", response_class=HTMLResponse)
async def admin_card_themes_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    _admin_guard(user)
    themes = (
        db.query(CardTheme)
        .order_by(CardTheme.sort_order.asc(), CardTheme.id.asc())
        .all()
    )
    error   = request.query_params.get("error", "")
    success = request.query_params.get("success", "")
    return templates.TemplateResponse("admin/card_themes.html", {
        "request": request,
        "themes":  themes,
        "error":   error,
        "success": success,
    })


# ── AT-02  Upload form ────────────────────────────────────────────────────────

@router.get("/admin/card-themes/upload", response_class=HTMLResponse)
async def admin_card_themes_upload_form(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)
    return templates.TemplateResponse("admin/card_themes_upload.html", {
        "request":      request,
        "preview_rows": None,
        "preview_json": None,
        "error":        request.query_params.get("error", ""),
    })


# ── AT-03  Upload → preview (no DB write) ─────────────────────────────────────

@router.post("/admin/card-themes/upload", response_class=HTMLResponse)
async def admin_card_themes_upload_preview(
    request: Request,
    file: UploadFile = File(...),
    db:   Session    = Depends(get_db),
    user: User       = Depends(get_current_user_web),
):
    _admin_guard(user)

    content = await file.read()
    if not content:
        return RedirectResponse(
            "/admin/card-themes/upload?error=Empty+file+uploaded",
            status_code=303,
        )
    if len(content) > MAX_MANIFEST_BYTES:
        return RedirectResponse(
            "/admin/card-themes/upload?error=File+too+large+%28max+32+KB%29",
            status_code=303,
        )

    result = validate_manifest(content, db)

    if not result.ok:
        # Pass first error via query param (truncated to 200 chars for URL safety)
        first_err = result.errors[0][:200].replace(" ", "+").replace(":", "%3A")
        return RedirectResponse(
            f"/admin/card-themes/upload?error={first_err}",
            status_code=303,
        )

    # Serialize preview rows to JSON for hidden field (B-variant state passing)
    preview_payload = [
        {
            "item":   row.item.model_dump(),
            "action": row.action,
            "diff":   {k: list(v) for k, v in row.diff.items()},
        }
        for row in result.preview_rows
    ]

    return templates.TemplateResponse("admin/card_themes_upload.html", {
        "request":      request,
        "preview_rows": result.preview_rows,
        "preview_json": json.dumps(preview_payload),
        "all_errors":   result.errors,
        "error":        "",
    })


# ── AT-04  Apply confirmed preview ────────────────────────────────────────────

@router.post("/admin/card-themes/apply", response_class=HTMLResponse)
async def admin_card_themes_apply(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    form = await request.form()
    raw_json = form.get("preview_json", "")
    if not raw_json:
        return RedirectResponse(
            "/admin/card-themes/upload?error=No+preview+data+found.+Please+upload+the+manifest+again.",
            status_code=303,
        )

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return RedirectResponse(
            "/admin/card-themes/upload?error=Corrupted+preview+data.+Please+upload+the+manifest+again.",
            status_code=303,
        )

    # Re-validate the payload (protects against tampering with the hidden field)
    from ....services.card_theme_admin_service import ThemeManifestItem
    try:
        preview_rows: list[ThemePreviewRow] = [
            ThemePreviewRow(
                item=ThemeManifestItem.model_validate(entry["item"]),
                action=entry["action"],
                diff={k: tuple(v) for k, v in entry.get("diff", {}).items()},
            )
            for entry in payload
        ]
    except Exception as exc:
        return RedirectResponse(
            f"/admin/card-themes/upload?error=Preview+data+invalid%3A+{str(exc)[:100]}",
            status_code=303,
        )

    try:
        applied = apply_manifest(db, preview_rows)
    except Exception as exc:
        logger.exception("card_themes apply_manifest failed")
        return RedirectResponse(
            f"/admin/card-themes?error=Apply+failed%3A+{str(exc)[:120]}",
            status_code=303,
        )

    count = len(applied)
    return RedirectResponse(
        f"/admin/card-themes?success={count}+theme(s)+applied+successfully",
        status_code=303,
    )


# ── AT-05  Toggle is_active ───────────────────────────────────────────────────

@router.post("/admin/card-themes/{theme_id}/toggle", response_class=HTMLResponse)
async def admin_card_themes_toggle(
    theme_id: str,
    request:  Request,
    db:       Session = Depends(get_db),
    user:     User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    if theme_id == _PROTECTED_ID:
        return RedirectResponse(
            "/admin/card-themes?error=The+default+theme+is+protected+and+cannot+be+deactivated.",
            status_code=303,
        )

    theme = db.query(CardTheme).filter(CardTheme.id == theme_id).first()
    if not theme:
        return RedirectResponse(
            "/admin/card-themes?error=Theme+not+found.",
            status_code=303,
        )

    # Warn before deactivating — check if any users are affected
    if theme.is_active:
        usage = get_theme_usage_summary(db, theme_id)
        if usage["total_affected"] > 0:
            parts = []
            if usage["draft_count"]:
                parts.append(f"{usage['draft_count']} draft(s)")
            if usage["published_count"]:
                parts.append(f"{usage['published_count']} published card(s)")
            if usage["unlocked_count"]:
                parts.append(f"{usage['unlocked_count']} user purchase(s)")
            summary = ", ".join(parts)
            # Surface warning — require explicit confirm via ?confirm=1
            confirm = request.query_params.get("confirm", "")
            if confirm != "1":
                warn_msg = (
                    f"Theme+'{theme.label}'+is+used+by+{summary}.+"
                    f"Deactivating+will+cause+visual+fallback+to+default+for+affected+users.+"
                    f"To+confirm%2C+click+Deactivate+again."
                )
                # Store warning in session-like query param; client re-posts with ?confirm=1
                return RedirectResponse(
                    f"/admin/card-themes?warning={warn_msg}&pending_toggle={theme_id}",
                    status_code=303,
                )

    theme.is_active = not theme.is_active
    from datetime import datetime, timezone
    theme.updated_at = datetime.now(timezone.utc)
    db.commit()
    _invalidate_cache()

    action = "deactivated" if not theme.is_active else "activated"
    return RedirectResponse(
        f"/admin/card-themes?success=Theme+'{theme.label}'+{action}+successfully.",
        status_code=303,
    )


# ── AT-06  Reorder sort_order ─────────────────────────────────────────────────

@router.post("/admin/card-themes/reorder", response_class=HTMLResponse)
async def admin_card_themes_reorder(
    request: Request,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    form = await request.form()
    # Expect form fields: sort_<id> = integer
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    updated = 0
    for key, val in form.items():
        if not key.startswith("sort_"):
            continue
        tid = key[5:]
        try:
            new_order = int(val)
        except (ValueError, TypeError):
            continue
        theme = db.query(CardTheme).filter(CardTheme.id == tid).first()
        if theme and theme.sort_order != new_order:
            theme.sort_order = new_order
            theme.updated_at = now
            updated += 1

    if updated:
        db.commit()
        _invalidate_cache()

    return RedirectResponse(
        f"/admin/card-themes?success=Sort+order+updated+({updated}+theme(s))",
        status_code=303,
    )
