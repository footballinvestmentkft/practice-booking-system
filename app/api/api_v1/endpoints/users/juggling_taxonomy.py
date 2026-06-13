"""
Juggling taxonomy endpoint.

GET /api/v1/users/me/juggling/taxonomy

Returns the full v1 contact type taxonomy with ETag cache support.
Response is derived from datasets/juggling/contact_types_v1.json (source of truth).

Cache:
  ETag: "v1-<sha256[:16]>" — stable unless JSON changes.
  If-None-Match: <etag> → 304 Not Modified when cache is current.

Gating: require_juggling_enabled (503 when flag off), get_current_user (401).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.dependencies import get_current_user
from app.models.user import User
from app.services.juggling.feature_flag import require_juggling_enabled
from app.services.juggling.taxonomy_service import build_taxonomy_response, get_etag

router = APIRouter()

_TAXONOMY_TAG = "juggling"


@router.get(
    "/me/juggling/taxonomy",
    dependencies=[Depends(require_juggling_enabled)],
    summary="Get juggling contact type taxonomy v1",
    tags=[_TAXONOMY_TAG],
)
def get_juggling_taxonomy(
    request: Request,
    _current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """
    Returns taxonomy v1 (18 contact types, 5 groups) for use by the iOS annotation UI.

    ETag caching:
      - Send If-None-Match: <etag> to receive 304 when taxonomy is unchanged.
      - Taxonomy is static until an explicit v2 upgrade; 304 is the expected response
        for all requests after the first.
    """
    etag = get_etag()
    if_none_match = request.headers.get("if-none-match", "")
    if if_none_match and if_none_match.strip('"') == etag.strip('"'):
        return JSONResponse(status_code=304, content=None, headers={"ETag": etag})

    return JSONResponse(
        content=build_taxonomy_response(),
        headers={"ETag": etag, "Cache-Control": "private, max-age=3600"},
    )
