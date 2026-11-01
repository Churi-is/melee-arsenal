"""Custom Scaleform GFx Item Card hooks for Melee Arsenal weapons.

Rewrites `WillowGame.ItemCardGFxObject` for Melee Arsenal weapons (on ground pickups, in backpack,
equipped slots, comparison view, HUD swap mini-card, and vending machines) so cards display:
  - Damage        (flat gun-scaled integer, comparable against guns & other melee weapons)
  - Range         (reach in meters, e.g. '3.40m')
  - Swing Speed   (attacks per second, e.g. '1.85 /s')
  - Optional Elemental Damage/s & Effect Chance (or Torgue Splash Dmg & Blast Radius)
  - Archetype FunStats (Single-Target vs Cleave Arc, Blade Crit, Melee/Roid scaling note)
while completely hiding irrelevant gun stats (Accuracy, Reload Speed, Magazine Size).

IMPORTANT: NEVER call `gfx_obj.AdjustHeight()`! In BL2, `AdjustHeight()` moves & shrinks an
`ItemCardGFxObject` to the bottom-right HUD equipped-weapon mini-card transform, which permanently
shrinks the Inventory Menu's shared item card MovieClip!
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    import unrealsdk  # type: ignore[import-not-found]
    from unrealsdk.hooks import Block, prevent_hooking_direct_calls  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    unrealsdk = None  # type: ignore[assignment]
    Block = object()  # type: ignore[assignment]
    prevent_hooking_direct_calls = None  # type: ignore[assignment]

from ..core.enums import Element
from ..core.item_card import CompareArrow, ItemCardPayload, build_item_card_payload
from ..core.stats import ComputedCardStats
from .log import log_error
from .resolver import resolve_melee_weapon, unwrap_weapon_object


@dataclass
class ActiveCardOverride:
    card_stats: ComputedCardStats
    payload: ItemCardPayload
    gun_compare_arrow: CompareArrow | None = None
    observed_icons: dict[int, str] = field(default_factory=dict)
    observed_elem_icon: str = ""
    depth: int = 1


_ACTIVE_CARDS: dict[int, ActiveCardOverride] = {}
_COMPARE_ENUM_MAP: dict[CompareArrow, int] | None = None

_ELEMENT_ICON_FALLBACK: dict[Element, str] = {
    Element.NONE: "",
    Element.FIRE: "incendiary",
    Element.SHOCK: "shock",
    Element.CORROSIVE: "corrosive",
    Element.SLAG: "slag",
    Element.EXPLOSIVE: "explosive",
}


def _discover_compare_enum() -> dict[CompareArrow, int]:
    """Discover exact `ECompareStat` enum byte values from UE3 reflection (with safe defaults)."""
    global _COMPARE_ENUM_MAP
    if _COMPARE_ENUM_MAP is not None:
        return _COMPARE_ENUM_MAP

    mapping = {
        CompareArrow.NONE: 0,
        CompareArrow.BETTER: 1,
        CompareArrow.EQUAL: 2,
        CompareArrow.WORSE: 3,
    }
    if unrealsdk is None:
        _COMPARE_ENUM_MAP = mapping
        return mapping

    try:
        fn = unrealsdk.find_object("Function", "WillowGame.ItemCardGFxObject:SetTopStat")
        if fn is not None:
            for prop in fn._properties():
                if str(prop.Name) == "CompareArrow" and hasattr(prop, "Enum") and prop.Enum is not None:
                    enum_obj = prop.Enum
                    if hasattr(enum_obj, "_names"):
                        names = [str(n).lower() for n in enum_obj._names()]
                        for idx, nm in enumerate(names):
                            if any(k in nm for k in ("none", "blank", "no_compare", "empty")):
                                mapping[CompareArrow.NONE] = idx
                            elif any(k in nm for k in ("better", "higher", "up", "greater")):
                                mapping[CompareArrow.BETTER] = idx
                            elif any(k in nm for k in ("same", "equal", "even")):
                                mapping[CompareArrow.EQUAL] = idx
                            elif any(k in nm for k in ("worse", "lower", "down", "less")):
                                mapping[CompareArrow.WORSE] = idx
    except Exception:
        pass

    _COMPARE_ENUM_MAP = mapping
    return mapping


def _arrow_byte(arrow: CompareArrow) -> int:
    return _discover_compare_enum().get(arrow, 0)


def _compare_against_vanilla_gun_damage(card: ComputedCardStats, compare_item: Any) -> CompareArrow | None:
    """If comparing a Melee Weapon against an equipped Vanilla Gun, compare Row 0 Damage!"""
    wpn = unwrap_weapon_object(compare_item)
    if wpn is None:
        return None
    try:
        gun_dmg = float(getattr(wpn, "InstantHitDamage", 0.0) or 0.0)
        proj = int(getattr(wpn, "ProjectilesPerShot", 1) or 1)
        total_gun = gun_dmg * max(1, proj)
        if total_gun <= 0.0:
            return None
        diff = float(card.card_damage) - total_gun
        if abs(diff) <= 0.5:
            return CompareArrow.EQUAL
        return CompareArrow.BETTER if diff > 0 else CompareArrow.WORSE
    except Exception:
        return None


def _call_set_top_stat(
    gfx_obj: Any,
    slot_idx: int,
    label: str,
    val: str,
    arrow: CompareArrow,
    aux: str,
    icon: str,
) -> None:
    arrow_val = _arrow_byte(arrow) if label else _arrow_byte(CompareArrow.NONE)
    if prevent_hooking_direct_calls is not None:
        with prevent_hooking_direct_calls():
            gfx_obj.SetTopStat(slot_idx, label, val, arrow_val, aux, icon)
    else:
        gfx_obj.SetTopStat(slot_idx, label, val, arrow_val, aux, icon)


def _apply_melee_card_override(gfx_obj: Any, ov: ActiveCardOverride) -> None:
    payload = ov.payload
    rows = payload.top_stats

    dmg_icon = ov.observed_icons.get(0, "damage")
    range_icon = ov.observed_icons.get(1, "accuracy")
    speed_icon = ov.observed_icons.get(2, "firerate")

    row0_arrow = ov.gun_compare_arrow if ov.gun_compare_arrow is not None else rows[0].arrow

    _call_set_top_stat(gfx_obj, 0, rows[0].label, rows[0].formatted_value, row0_arrow, "", dmg_icon)
    _call_set_top_stat(gfx_obj, 1, rows[1].label, rows[1].formatted_value, rows[1].arrow, "", range_icon)
    _call_set_top_stat(gfx_obj, 2, rows[2].label, rows[2].formatted_value, rows[2].arrow, "", speed_icon)

    elem_sec = payload.elemental_section
    if elem_sec is not None:
        e_icon = ov.observed_elem_icon or _ELEMENT_ICON_FALLBACK.get(elem_sec.element, "")
        _call_set_top_stat(
            gfx_obj,
            3,
            elem_sec.dps_label,
            elem_sec.dps_formatted,
            elem_sec.dps_arrow,
            "",
            e_icon,
        )
        _call_set_top_stat(
            gfx_obj,
            4,
            elem_sec.chance_label,
            elem_sec.chance_formatted,
            elem_sec.chance_arrow,
            "",
            e_icon,
        )
    else:
        _call_set_top_stat(gfx_obj, 3, "", "", CompareArrow.NONE, "", "")
        _call_set_top_stat(gfx_obj, 4, "", "", CompareArrow.NONE, "", "")

    try:
        _call_set_top_stat(gfx_obj, 5, "", "", CompareArrow.NONE, "", "")
    except Exception:
        pass

    # Write bottom modifier lines WITHOUT calling `AdjustHeight()` (which shrinks inventory cards!)
    try:
        if prevent_hooking_direct_calls is not None:
            with prevent_hooking_direct_calls():
                gfx_obj.SetFunStats(payload.fun_stats_html)
        else:
            gfx_obj.SetFunStats(payload.fun_stats_html)
    except Exception as exc:
        log_error(f"SetFunStats error on melee card: {exc!r}")


# ---------------------------------------------------------------------------
# Engine Hook Callbacks
# ---------------------------------------------------------------------------

def on_set_item_card_ex_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Begin Melee Item Card override (`SetItemCard` or `SetItemCardEx`)."""
    existing = _ACTIVE_CARDS.get(id(obj))
    if existing is not None:
        existing.depth += 1
        return None

    try:
        inv_item = getattr(args, "InventoryItem", None)
        resolved = resolve_melee_weapon(inv_item, sync_weapon_props=True)
        if resolved is None:
            return None

        _, card = resolved
        cmp_item = getattr(args, "CompareAgainstInventoryItem", None)
        cmp_resolved = resolve_melee_weapon(cmp_item, sync_weapon_props=False) if cmp_item is not None else None
        cmp_card = cmp_resolved[1] if cmp_resolved is not None else None

        payload = build_item_card_payload(card, compared_against=cmp_card)
        gun_arrow = None
        if cmp_item is not None and cmp_card is None:
            gun_arrow = _compare_against_vanilla_gun_damage(card, cmp_item)

        _ACTIVE_CARDS[id(obj)] = ActiveCardOverride(
            card_stats=card,
            payload=payload,
            gun_compare_arrow=gun_arrow,
            depth=1,
        )
    except Exception as exc:
        log_error(f"on_set_item_card_ex_pre failed: {exc!r}")
    return None


def on_set_title_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Ensure the card title & elemental icon match our Melee Weapon name & element."""
    ov = _ACTIVE_CARDS.get(id(obj))
    if ov is None:
        return None
    try:
        mfr_str = str(getattr(args, "Manufacturer", "") or ov.payload.manufacturer_name)
        rarity_color = getattr(args, "Rarity", None)
        type_icon = str(getattr(args, "TypeIcon", "") or "pistol")
        elem_icon = str(getattr(args, "ElementalIcon", "") or "")
        if not elem_icon and ov.card_stats.element != Element.NONE:
            elem_icon = _ELEMENT_ICON_FALLBACK.get(ov.card_stats.element, "")
        ov.observed_elem_icon = elem_icon
        is_readied = bool(getattr(args, "bIsReadied", False))

        if prevent_hooking_direct_calls is not None:
            with prevent_hooking_direct_calls():
                obj.SetTitle(
                    mfr_str,
                    ov.card_stats.full_name,
                    rarity_color,
                    type_icon,
                    elem_icon,
                    is_readied,
                )
        else:
            obj.SetTitle(
                mfr_str,
                ov.card_stats.full_name,
                rarity_color,
                type_icon,
                elem_icon,
                is_readied,
            )
        return Block
    except Exception:
        return None


def on_set_top_stat_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Capture Gearbox's Flash icon names and suppress Accuracy/Reload/Mag Size on melee cards."""
    ov = _ACTIVE_CARDS.get(id(obj))
    if ov is None:
        return None
    try:
        idx = int(getattr(args, "StatIndex", -1))
        icon = str(getattr(args, "IconName", "") or "")
        if idx >= 0 and icon:
            ov.observed_icons[idx] = icon
            if idx >= 3 and not ov.observed_elem_icon:
                ov.observed_elem_icon = icon
    except Exception:
        pass
    return Block


def on_set_fun_stats_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Suppress donor gun fun stats during SetItemCardEx; we write melee fun stats in POST."""
    if id(obj) in _ACTIVE_CARDS:
        return Block
    return None


def on_set_item_card_ex_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Finalize Melee Weapon top stats, optional elemental stats, and fun stats on the card."""
    ov = _ACTIVE_CARDS.get(id(obj))
    if ov is None:
        return None
    ov.depth -= 1
    if ov.depth > 0:
        return None
    _ACTIVE_CARDS.pop(id(obj), None)
    _apply_melee_card_override(obj, ov)
    return None
