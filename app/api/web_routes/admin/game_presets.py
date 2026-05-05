"""Admin game preset management routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
import logging

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User
from ....models.game_preset import GamePreset
from ....skills_config import SKILL_CATEGORIES

from . import templates, _admin_guard

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_FOOT_CONTEXTS = frozenset({"right", "left", "neutral"})


def _build_skill_groups():
    """Build skill groups from SKILL_CATEGORIES config for template rendering."""
    groups = []
    for cat in SKILL_CATEGORIES:
        groups.append({
            "label": f"{cat['emoji']} {cat['name_en']}",
            "skills": [{"key": s["key"], "name": s["name_en"]} for s in cat["skills"]]
        })
    return groups


@router.get("/admin/game-presets", response_class=HTMLResponse)
async def admin_game_presets_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Game Presets management"""
    _admin_guard(user)
    presets = db.query(GamePreset).order_by(GamePreset.name).all()
    skill_groups = _build_skill_groups()
    return templates.TemplateResponse(
        "admin/game_presets.html",
        {"request": request, "user": user, "presets": presets, "skill_groups": skill_groups}
    )


@router.post("/admin/game-presets")
async def admin_create_game_preset(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    difficulty: str = Form(""),
    min_players: int = Form(4),
    skill_impact: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    form_data = await request.form()
    # Collect selected skills and weights from form
    skills = []
    weights = {}
    for key, val in form_data.multi_items():
        if key.startswith("skill_cb_"):
            skill_key = key[len("skill_cb_"):]
            skills.append(skill_key)
        if key.startswith("skill_w_"):
            skill_key = key[len("skill_w_"):]
            try:
                weights[skill_key] = int(val)
            except (ValueError, TypeError):
                weights[skill_key] = 1

    skill_foot_contexts: dict = {}
    for key, val in form_data.multi_items():
        if key.startswith("skill_fc_"):
            sk = key[len("skill_fc_"):]
            if sk in skills and val in _VALID_FOOT_CONTEXTS:
                skill_foot_contexts[sk] = val

    total = sum(weights.get(s, 1) for s in skills) or 1
    skill_weights = {s: round(weights.get(s, 1) / total, 4) for s in skills}

    game_config = {
        "version": "1.0",
        "format_config": {},
        "skill_config": {
            "skills_tested": skills,
            "skill_weights": skill_weights,
            "skill_impact_on_matches": bool(skill_impact),
            **({"skill_foot_contexts": skill_foot_contexts} if skill_foot_contexts else {}),
        },
        "simulation_config": {},
        "metadata": {
            "game_category": category or None,
            "difficulty_level": difficulty or None,
            "min_players": min_players,
        },
    }
    preset = GamePreset(
        code=code.strip(),
        name=name.strip(),
        description=description.strip() or None,
        game_config=game_config,
        is_active=True,
        created_by=user.id,
    )
    db.add(preset)
    db.commit()
    return RedirectResponse(url="/admin/game-presets", status_code=303)


@router.get("/admin/game-presets/{preset_id}/edit", response_class=HTMLResponse)
async def admin_edit_game_preset_page(
    preset_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    preset = db.query(GamePreset).filter(GamePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Game preset not found")
    skill_groups = _build_skill_groups()
    # Extract current skill weights as integer percentages for the form
    sc = (preset.game_config or {}).get("skill_config", {})
    raw_weights = sc.get("skill_weights", {})
    current_skills = sc.get("skills_tested", [])
    total_w = sum(raw_weights.values()) or 1.0
    weight_pcts = {k: max(1, round(v / total_w * 100)) for k, v in raw_weights.items()}
    return templates.TemplateResponse(
        "admin/game_preset_edit.html",
        {
            "request": request, "user": user, "preset": preset,
            "skill_groups": skill_groups, "current_skills": current_skills,
            "weight_pcts": weight_pcts,
            "skill_foot_contexts": sc.get("skill_foot_contexts", {}),
        }
    )


@router.post("/admin/game-presets/{preset_id}/edit")
async def admin_edit_game_preset_submit(
    preset_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    difficulty: str = Form(""),
    min_players: int = Form(4),
    skill_impact: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    preset = db.query(GamePreset).filter(GamePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Game preset not found")
    form_data = await request.form()
    skills = []
    weights = {}
    for key, val in form_data.multi_items():
        if key.startswith("skill_cb_"):
            skills.append(key[len("skill_cb_"):])
        if key.startswith("skill_w_"):
            try:
                weights[key[len("skill_w_"):]] = int(val)
            except (ValueError, TypeError):
                weights[key[len("skill_w_"):]] = 1

    skill_foot_contexts: dict = {}
    for key, val in form_data.multi_items():
        if key.startswith("skill_fc_"):
            sk = key[len("skill_fc_"):]
            if sk in skills and val in _VALID_FOOT_CONTEXTS:
                skill_foot_contexts[sk] = val

    total = sum(weights.get(s, 1) for s in skills) or 1
    skill_weights = {s: round(weights.get(s, 1) / total, 4) for s in skills}

    existing_config = preset.game_config or {}
    new_config = {
        **existing_config,
        "skill_config": {
            "skills_tested": skills,
            "skill_weights": skill_weights,
            "skill_impact_on_matches": bool(skill_impact),
            **({"skill_foot_contexts": skill_foot_contexts} if skill_foot_contexts else {}),
        },
        "metadata": {
            "game_category": category or None,
            "difficulty_level": difficulty or None,
            "min_players": min_players,
        },
    }
    preset.name = name.strip()
    preset.description = description.strip() or None
    preset.game_config = new_config
    db.commit()
    return RedirectResponse(url="/admin/game-presets", status_code=303)


@router.post("/admin/game-presets/{preset_id}/toggle")
async def admin_toggle_game_preset(
    preset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    preset = db.query(GamePreset).filter(GamePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Game preset not found")
    preset.is_active = not preset.is_active
    db.commit()
    return RedirectResponse(url="/admin/game-presets", status_code=303)


@router.post("/admin/game-presets/{preset_id}/delete")
async def admin_delete_game_preset(
    preset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    preset = db.query(GamePreset).filter(GamePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Game preset not found")
    if getattr(preset, "is_locked", False):
        raise HTTPException(status_code=400, detail="Cannot delete a locked game preset")
    db.delete(preset)
    db.commit()
    return RedirectResponse(url="/admin/game-presets", status_code=303)
