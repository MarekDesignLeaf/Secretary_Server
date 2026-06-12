"""Tools Hub tiles — the Utilities screen content.

Serves the nature-recognition tiles exactly as the original plugin's
hub_tiles.json (commit 3966a60^) so the Android Tools tab shows them again.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from secretary_clean.api.deps import current_user
from secretary_clean.core.models import UserAccount

router = APIRouter(prefix="/tools", tags=["tools"])

# Original plugin tiles (tool_packages/nature_recognition/hub_tiles.json).
_TILES = [
    {
        "tile_key": "identify",
        "tile_title_en": "Plant identification",
        "tile_title_cs": "Rozpoznání rostlin",
        "tile_title_pl": "Rozpoznawanie roślin",
        "tile_hint_en": "Take a photo to identify a plant species, get care tips and description",
        "tile_hint_cs": "Vyfotografuj rostlinu a zjisti druh, péči a popis",
        "tile_hint_pl": "Zrób zdjęcie, aby zidentyfikować gatunek rośliny",
        "icon": "Eco",
        "sort_order": 10,
    },
    {
        "tile_key": "health",
        "tile_title_en": "Plant health check",
        "tile_title_cs": "Zdraví rostliny",
        "tile_title_pl": "Zdrowie rośliny",
        "tile_hint_en": "Diagnose diseases, pests and deficiencies from a photo",
        "tile_hint_cs": "Diagnostikuj nemoci, škůdce a deficience z fotografie",
        "tile_hint_pl": "Diagnozuj choroby, szkodniki i niedobory ze zdjęcia",
        "icon": "HealthAndSafety",
        "sort_order": 20,
    },
    {
        "tile_key": "mushroom",
        "tile_title_en": "Mushroom identification",
        "tile_title_cs": "Rozpoznání hub",
        "tile_title_pl": "Rozpoznawanie grzybów",
        "tile_hint_en": "Identify mushroom species and edibility from a photo — always verify with an expert before eating",
        "tile_hint_cs": "Identifikuj druh houby a jedlost z fotografie — vždy ověř u odborníka před konzumací",
        "tile_hint_pl": "Zidentyfikuj gatunek grzyba ze zdjęcia — zawsze weryfikuj u eksperta przed spożyciem",
        "icon": "Forest",
        "sort_order": 30,
    },
]


@router.get("/hub-tiles")
def hub_tiles(
    tenant_id: int = 1,  # legacy query param from Android; tenant comes from JWT
    user: UserAccount = Depends(current_user),
):
    return {"tenant_id": tenant_id, "tiles": _TILES, "count": len(_TILES)}
