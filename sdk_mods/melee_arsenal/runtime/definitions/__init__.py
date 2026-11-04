"""Construct and register the live UE3 weapon definitions."""
from __future__ import annotations

import traceback

from ...core.enums import Manufacturer
from .. import asset_library
from ..log import log_error, log_info, log_warn
from ..state import STATE
from . import balances, donors, firing_modes, name_parts, parts, resources, weapon_types
from ._sdk import unrealsdk
from .context import BuildContext

__all__ = ["ensure_definitions_built"]


def ensure_definitions_built() -> bool:
    """Construct definitions and register their assets with the game."""
    if STATE.initialized:
        return True
    if unrealsdk is None:
        return False

    try:
        resources.ensure_combat_resources_cached()
        donor_assets = donors._harvest_pistol_donors()
        if not donor_assets["wt"] or donor_assets["wt"].get(Manufacturer.HYPERION) is None:
            log_warn("GD_Weap_Pistol donors not yet loaded; deferring Melee Arsenal build.")
            return False

        ctx = BuildContext.create(donor_assets)
        name_parts.build_name_parts(ctx)
        firing_modes.build_element_firing_modes(ctx)
        weapon_types.build_weapon_types(ctx)
        parts.build_body_parts(ctx)
        parts.build_blade_parts(ctx)
        parts.build_grip_parts(ctx)
        parts.build_accessory_parts(ctx)
        parts.build_element_parts(ctx)
        parts.build_material_parts(ctx)
        balances.build_balances(ctx)

        asset_library.register_assets_in_library("WeaponTypeDefinition", list(STATE.maker_to_wt.values()))
        all_parts = (
            list(STATE.body_parts.values())
            + list(STATE.blade_parts.values())
            + list(STATE.grip_parts.values())
            + list(STATE.accessory_parts.values())
            + list(STATE.element_parts.values())
            + list(STATE.all_extra_part_objects)
        )
        asset_library.register_assets_in_library("WeaponPartDefinition", all_parts)
        all_names = (
            ([STATE.empty_name_part] if STATE.empty_name_part is not None else [])
            + list(STATE.title_parts.values())
            + list(STATE.prefix_parts.values())
        )
        asset_library.register_assets_in_library("WeaponNamePartDefinition", all_names)
        asset_library.register_assets_in_library("WeaponBalanceDefinition", list(STATE.balances.values()))

        STATE.initialized = True
        log_info(
            f"Built Melee Arsenal definitions: {len(STATE.maker_to_wt)} WeaponTypes, "
            f"{len(STATE.blade_parts)} Blades, {len(STATE.grip_parts)} Grips, "
            f"{len(STATE.accessory_parts)} Accessories, {len(STATE.element_parts)} Elements, "
            f"{len(STATE.balances)} Balances."
        )
        return True
    except Exception as exc:
        log_error(f"ensure_definitions_built failed: {exc!r}\n{traceback.format_exc()}")
        return False
