"""UE3 `WeaponPartDefinition` builders — one function per part type.

Order matters: Body -> Blade -> Grip -> Accessory -> Element -> Materials. Each
builder iterates its `core/parts/` registry, so adding a part needs no SDK edits.
`ctx` carries the packages and the donor objects shared between builders.
"""
from __future__ import annotations
from ..sdk import unrealsdk

from typing import Any

from ...core.enums import Element, Manufacturer, Rarity
from ...core.parts import ACCESSORIES, BLADES, ELEMENTS, GRIPS, MANUFACTURERS
from ..custom_assets import setup_custom_inkblade_assets
from ..log import obj_path
from .. import resources
from ..state import KEEP_ALIVE_FLAGS, STATE
from .context import BuildContext
from .helpers import _clear_array_prop, _find, _sum_crit_bonus
from .name_parts import _apply_crit_bonus_if_supported, _clear_part_stat_bonuses


def build_body_parts(ctx: BuildContext) -> None:
    """Body/Hilt parts (one per Manufacturer, invisible structural glue)."""
    donors = ctx.donors
    body_pkg = ctx.body_pkg
    # 3. Build Body/Hilt parts (1 per Manufacturer)
    for maker, donor_body in donors["bodies"].items():
        bname = f"Part_Body_{maker.value}"
        bpart = _find("WeaponPartDefinition", f"{obj_path(body_pkg)}.{bname}")
        if bpart is None:
            bpart = unrealsdk.construct_object(
                "WeaponPartDefinition",
                body_pkg,
                bname,
                KEEP_ALIVE_FLAGS,
                donor_body,
            )
            STATE.keep_alive(bpart)
        _clear_part_stat_bonuses(bpart)
        mfr_obj = STATE.maker_to_donor_manufacturer.get(maker)
        if mfr_obj is not None and hasattr(bpart, "ManufacturerDefinitionForScopeMovie"):
            bpart.ManufacturerDefinitionForScopeMovie = mfr_obj
        _clear_array_prop(bpart, "TitleList")
        _clear_array_prop(bpart, "PrefixList")
        STATE.body_parts[maker] = bpart



def build_blade_parts(ctx: BuildContext) -> None:
    """Blade parts (Barrel slot) — define manufacturer, title and Gestalt fragment."""
    donors = ctx.donors
    blade_pkg = ctx.blade_pkg
    # 4. Build Blade parts (5 Manufacturer-defining Blades in Barrel slot)
    crit_tpl = donors["crit_effect_template"]
    for blade_id, bspec in BLADES.items():
        maker = bspec.manufacturer
        donor_barrel = donors["barrels"].get(maker) or next(iter(donors["barrels"].values()))
        part_name = f"Part_{blade_id}"
        part = _find("WeaponPartDefinition", f"{obj_path(blade_pkg)}.{part_name}")
        if part is None:
            part = unrealsdk.construct_object(
                "WeaponPartDefinition",
                blade_pkg,
                part_name,
                KEEP_ALIVE_FLAGS,
                donor_barrel,
            )
            STATE.keep_alive(part)
        _clear_part_stat_bonuses(part)
        _apply_crit_bonus_if_supported(
            part,
            MANUFACTURERS[maker].cleave_profile.base_crit_bonus + _sum_crit_bonus(bspec.modifiers),
            crit_tpl,
        )

        bayonet_frag = (
            "Acc_Barrel_Blade2"
            if maker in (Manufacturer.BANDIT, Manufacturer.TORGUE)
            else "Acc_Barrel_Blade1"
        )
        part.GestaltModeSkeletalMeshName = bspec.gestalt.donor_fragment
        try:
            part.AdditionalGestaltModeSkeletalMeshNames = (bayonet_frag, "None")
        except Exception:
            part.AdditionalGestaltModeSkeletalMeshNames = (bayonet_frag,)

        title_np = STATE.title_parts.get(bspec.base_title)
        part.TitleList = [title_np] if title_np is not None else []
        _clear_array_prop(part, "PrefixList")

        STATE.blade_parts[blade_id] = part
        STATE.part_path_to_blade_id[obj_path(part)] = blade_id

    ctx.crit_tpl = crit_tpl


def build_grip_parts(ctx: BuildContext) -> None:
    """Grip parts (cross-manufacturer hilts)."""
    donors = ctx.donors
    grip_pkg = ctx.grip_pkg
    crit_tpl = ctx.crit_tpl
    # 5. Build Grip parts (5 cross-compatible Manufacturer Grips)
    for grip_id, gspec in GRIPS.items():
        maker = gspec.manufacturer
        donor_grip = donors["grips"].get(maker) or next(iter(donors["grips"].values()))
        part_name = f"Part_{grip_id}"
        part = _find("WeaponPartDefinition", f"{obj_path(grip_pkg)}.{part_name}")
        if part is None:
            part = unrealsdk.construct_object(
                "WeaponPartDefinition",
                grip_pkg,
                part_name,
                KEEP_ALIVE_FLAGS,
                donor_grip,
            )
            STATE.keep_alive(part)
        _clear_part_stat_bonuses(part)
        _apply_crit_bonus_if_supported(part, _sum_crit_bonus(gspec.modifiers), crit_tpl)
        _clear_array_prop(part, "TitleList")
        _clear_array_prop(part, "PrefixList")
        STATE.grip_parts[grip_id] = part
        STATE.part_path_to_grip_id[obj_path(part)] = grip_id



def build_accessory_parts(ctx: BuildContext) -> None:
    """Accessory parts (Acc_None plus the physical/tech blade mods)."""
    donors = ctx.donors
    acc_pkg = ctx.acc_pkg
    crit_tpl = ctx.crit_tpl
    # 6. Build Accessory parts (Acc_None + 7 physical/tech blade mods)
    # IMPORTANT: Never clone visible accessories from `Pistol_Accessory_None`! `Pistol_Accessory_None`
    # carries UE3's hidden-part flag and suppresses Gestalt fragment rendering even when a fragment is bound.
    visible_acc_donor = (
        _find("WeaponPartDefinition", "GD_Weap_Pistol.Accessory.Pistol_Accessory_Bayonet_1")
        or (donors["visible_accessories"][0] if donors["visible_accessories"] else None)
        or next(
            (a for a in donors["accessories"] if not obj_path(a).lower().endswith("_none")),
            None,
        )
        or next(iter(donors["barrels"].values()))
    )
    none_acc_donor = donors.get("accessory_none") or visible_acc_donor

    for acc_id, aspec in ACCESSORIES.items():
        if aspec.gestalt is None:
            acc_donor = none_acc_donor
        else:
            spec_donor = (
                _find("WeaponPartDefinition", aspec.donor_part_path)
                if aspec.donor_part_path
                else None
            )
            if spec_donor is not None and not obj_path(spec_donor).lower().endswith("_none"):
                acc_donor = spec_donor
            else:
                acc_donor = visible_acc_donor

        part_name = f"Part_{acc_id}"
        part = _find("WeaponPartDefinition", f"{obj_path(acc_pkg)}.{part_name}")
        if part is None:
            part = unrealsdk.construct_object(
                "WeaponPartDefinition",
                acc_pkg,
                part_name,
                KEEP_ALIVE_FLAGS,
                acc_donor,
            )
            STATE.keep_alive(part)
        _clear_part_stat_bonuses(part)
        _apply_crit_bonus_if_supported(part, _sum_crit_bonus(aspec.modifiers), crit_tpl)
        if aspec.gestalt is None:
            part.GestaltModeSkeletalMeshName = "None"
        else:
            part.GestaltModeSkeletalMeshName = aspec.gestalt.donor_fragment
        try:
            part.AdditionalGestaltModeSkeletalMeshNames = ("None", "None")
        except Exception:
            part.AdditionalGestaltModeSkeletalMeshNames = ()

        _clear_array_prop(part, "TitleList")
        pref_np = STATE.prefix_parts.get(aspec.default_prefix) if aspec.default_prefix else None
        part.PrefixList = [pref_np] if pref_np is not None else []
        STATE.accessory_parts[acc_id] = part
        STATE.part_path_to_accessory_id[obj_path(part)] = acc_id

    ctx.visible_acc_donor = visible_acc_donor
    ctx.none_acc_donor = none_acc_donor


def build_element_parts(ctx: BuildContext) -> None:
    """Element parts (native damage type, card icon and visible donor)."""
    donors = ctx.donors
    elem_pkg = ctx.elem_pkg
    visible_acc_donor = ctx.visible_acc_donor
    # 7. Build Element parts (preserving native elemental damage type, card icon & visible Gestalt donor!)
    # IMPORTANT: Never clone active elements (e.g. `Elem_Explosive`) from `Pistol_Elemental_None`!
    visible_elem_donor = (
        donors["elements"].get(Element.FIRE)
        or _find("WeaponPartDefinition", "GD_Weap_Pistol.elemental.Pistol_Elemental_Fire")
        or next(
            (ep for el, ep in donors["elements"].items() if el != Element.NONE and ep is not None),
            None,
        )
        or visible_acc_donor
    )
    none_elem_donor = (
        donors["elements"].get(Element.NONE)
        or _find("WeaponPartDefinition", "GD_Weap_Pistol.elemental.Pistol_Elemental_None")
        or visible_elem_donor
    )
    for elem_id, espec in ELEMENTS.items():
        if espec.element == Element.NONE:
            edonor = none_elem_donor
        else:
            spec_edonor = (
                _find("WeaponPartDefinition", espec.donor_part_path)
                if espec.donor_part_path
                else None
            )
            edonor = (
                donors["elements"].get(espec.element)
                or (
                    spec_edonor
                    if spec_edonor is not None and not obj_path(spec_edonor).lower().endswith("_none")
                    else None
                )
                or visible_elem_donor
            )
        part_name = f"Part_{elem_id}"
        part = _find("WeaponPartDefinition", f"{obj_path(elem_pkg)}.{part_name}")
        if part is None:
            part = unrealsdk.construct_object(
                "WeaponPartDefinition",
                elem_pkg,
                part_name,
                KEEP_ALIVE_FLAGS,
                edonor,
            )
            STATE.keep_alive(part)
        _clear_part_stat_bonuses(part)
        if espec.element == Element.NONE and hasattr(part, "GestaltModeSkeletalMeshName"):
            try:
                part.GestaltModeSkeletalMeshName = "None"
            except Exception:
                pass

        dt = resources._CACHED_DEF_DMG_TYPES.get(espec.element)
        if dt is not None and hasattr(part, "CustomDamageTypeDefinition"):
            try:
                part.CustomDamageTypeDefinition = dt
            except Exception:
                pass

        _clear_array_prop(part, "TitleList")
        pref_np = STATE.prefix_parts.get(espec.default_prefix) if espec.default_prefix else None
        part.PrefixList = [pref_np] if pref_np is not None else []
        STATE.element_parts[elem_id] = part
        STATE.part_path_to_element_id[obj_path(part)] = elem_id



def build_material_parts(ctx: BuildContext) -> None:
    """Material parts per (Maker, Rarity) + custom InkBlade Gestalt activation."""
    donors = ctx.donors
    root_pkg = ctx.root_pkg
    acc_pkg = ctx.acc_pkg
    mats_pkg = ctx.mats_pkg
    visible_acc_donor = ctx.visible_acc_donor
    # 8. Cache Manufacturer Material parts per (Maker, Rarity) & activate Custom Inkblade 3D Gestalt if present
    for (maker, rarity), mpart in donors["materials"].items():
        STATE.material_parts[(maker, rarity)] = mpart

    donor_mat_template = next(iter(STATE.material_parts.values()), visible_acc_donor)
    donor_mats_by_rarity: dict[Rarity, Any] = {}
    for rar in (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.VERY_RARE):
        rar_mat = (
            STATE.material_parts.get((Manufacturer.HYPERION, rar))
            or STATE.material_parts.get((Manufacturer.MALIWAN, rar))
            or next((mp for (_m, r), mp in STATE.material_parts.items() if r == rar), None)
            or donor_mat_template
        )
        donor_mats_by_rarity[rar] = rar_mat

    setup_custom_inkblade_assets(
        root_pkg=root_pkg,
        parts_pkg=acc_pkg,
        mats_pkg=mats_pkg,
        donor_mat_part=donor_mat_template,
        donor_mats_by_rarity=donor_mats_by_rarity,
    )
