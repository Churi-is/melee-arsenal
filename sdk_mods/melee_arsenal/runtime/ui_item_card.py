"""Scaleform item-card hooks for Melee Arsenal weapons.

Cards show melee damage, range, swing speed, elemental or splash stats, and the equipped weapon's
Damage Reduction. Standard gun-only stats are hidden.

Do not call `gfx_obj.AdjustHeight()`: in BL2 it moves and shrinks the shared item-card movie clip.
"""
from __future__ import annotations
from .sdk import Block, prevent_hooking_direct_calls

from dataclasses import dataclass
from typing import Any


from ..core.enums import Element
from ..core.item_card import CompareArrow, ItemCardPayload, build_item_card_payload
from ..core.stats import ComputedCardStats
from .log import log_error
from .object_identity import cache_key
from .resolver import resolve_melee_weapon, unwrap_weapon_object


@dataclass
class ActiveCardOverride:
    card_stats: ComputedCardStats
    payload: ItemCardPayload
    gun_compare_arrow: CompareArrow | None = None
    depth: int = 1


_ACTIVE_CARDS: dict[str, ActiveCardOverride] = {}

# Gearbox's Flash stat icons, measured off live `ItemCardGFxObject` calls (docs/ENGINE_FACTS.md):
# slot 0 `weaponDamage`, 1 `weaponAccuracy`, 2 `weaponFireRate`, 3 `weaponsReloadSpeed`,
# 4 `weaponClipSize`, 5/6 `element<Element>` — `elementCorrosive` and `elementFire` both observed.
_ICON_DAMAGE = "weaponDamage"
_ICON_ACCURACY = "weaponAccuracy"
_ICON_FIRE_RATE = "weaponFireRate"
_ELEMENT_ICON: dict[Element, str] = {
    Element.NONE: "",
    Element.FIRE: "elementFire",
    Element.SHOCK: "elementShock",
    Element.CORROSIVE: "elementCorrosive",
    Element.SLAG: "elementSlag",
    Element.EXPLOSIVE: "elementExplosive",
}


# `SetTopStat`'s `CompareArrow` parameter is typed `ETopStatArrow`. That enum's value array is not
# exposed by the SDK (`_names()` is absent on the enum object), so it cannot be read at run time;
# the byte values below are what the card has always used and what the game accepts.
_ARROW_BYTES: dict[CompareArrow, int] = {
    CompareArrow.NONE: 0,
    CompareArrow.BETTER: 1,
    CompareArrow.EQUAL: 2,
    CompareArrow.WORSE: 3,
}


def _arrow_byte(arrow: CompareArrow) -> int:
    return _ARROW_BYTES.get(arrow, 0)


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

    dmg_icon = _ICON_DAMAGE
    range_icon = _ICON_ACCURACY
    speed_icon = _ICON_FIRE_RATE

    row0_arrow = ov.gun_compare_arrow if ov.gun_compare_arrow is not None else rows[0].arrow

    _call_set_top_stat(gfx_obj, 0, rows[0].label, rows[0].formatted_value, row0_arrow, "", dmg_icon)
    _call_set_top_stat(gfx_obj, 1, rows[1].label, rows[1].formatted_value, rows[1].arrow, "", range_icon)
    _call_set_top_stat(gfx_obj, 2, rows[2].label, rows[2].formatted_value, rows[2].arrow, "", speed_icon)

    elem_sec = payload.elemental_section
    if elem_sec is not None:
        e_icon = _ELEMENT_ICON.get(elem_sec.element, "")
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


# Engine Hook Callbacks

def on_set_item_card_ex_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Begin Melee Item Card override (`SetItemCard` or `SetItemCardEx`)."""
    existing = _ACTIVE_CARDS.get(cache_key(obj))
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

        _ACTIVE_CARDS[cache_key(obj)] = ActiveCardOverride(
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
    ov = _ACTIVE_CARDS.get(cache_key(obj))
    if ov is None:
        return None
    try:
        mfr_str = str(getattr(args, "Manufacturer", "") or ov.payload.manufacturer_name)
        # `SetTitle`'s rarity argument is a `Color` struct (measured from the UFunction), not a
        # 1-based level with a `__int__`, so the engine's colour is always what we render.
        rarity_color = getattr(args, "Rarity", None)
        type_icon = str(getattr(args, "TypeIcon", "") or "Pistol")
        elem_icon = str(getattr(args, "ElementalIcon", "") or "")
        if not elem_icon and ov.card_stats.element != Element.NONE:
            elem_icon = _ELEMENT_ICON.get(ov.card_stats.element, "")
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
    """Suppress the donor gun's Accuracy / Reload / Mag Size rows while a melee card renders."""
    if cache_key(obj) in _ACTIVE_CARDS:
        return Block
    return None


def on_set_fun_stats_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Suppress donor gun fun stats during SetItemCardEx; we write melee fun stats in POST."""
    if cache_key(obj) in _ACTIVE_CARDS:
        return Block
    return None


def on_set_item_card_ex_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Finalize Melee Weapon top stats, optional elemental stats, and fun stats on the card."""
    ov = _ACTIVE_CARDS.get(cache_key(obj))
    if ov is None:
        return None
    ov.depth -= 1
    if ov.depth > 0:
        return None
    _ACTIVE_CARDS.pop(cache_key(obj), None)
    _apply_melee_card_override(obj, ov)
    return None


def clear_active_card_overrides() -> None:
    """Drop nested card-override state on mod disable / map teardown."""
    _ACTIVE_CARDS.clear()
