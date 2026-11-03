"""Construct and root all UE3 Melee Arsenal definitions at runtime.

The old 1,550-line monolith is now a package split by concern:

============================  ======================================================
``helpers.py``                UE3 find/subpackage/struct helpers
``donors.py``                 vanilla pistol/shotgun donor harvesting
``resources.py``              combat resource cache (damage types, audio, attrs)
``context.py``                BuildContext: root packages + donors for one build
``firing_modes.py``           element melee firing modes + Torgue splash
``name_parts.py``             title/prefix name parts + inventory preview tuning
``weapon_types.py``           per-manufacturer WeaponTypeDefinitions
``parts.py``                  one builder per part type (body/blade/grip/acc/elem/mat)
``balances.py``               part-list collections, balances and drop pools
============================  ======================================================

`ensure_definitions_built()` below is the only orchestrator; it calls the builders
in order. Historical module-level names are re-exported so existing imports keep
working (including tests that patch `register_assets_in_library`).
"""
from __future__ import annotations

import traceback

from ...core.enums import Manufacturer
from ..asset_library import register_assets_in_library
from ..log import log_error, log_info, log_warn
from ..state import STATE
from ._sdk import unrealsdk
from .balances import (
    _configure_balance_definition,
    _configure_part_list_collection,
    _find_template_manufacturer_weight,
    _find_template_weighted_row,
    build_balances,
)
from .context import BuildContext
from .donors import _RARITY_DONOR_SUFFIX, _extract_parts_from_plc, _harvest_pistol_donors
from .firing_modes import (
    _attach_torgue_explode_on_impact,
    _configure_melee_firing_mode,
    build_element_firing_modes,
)
from .helpers import (
    MELEE_CLIP_SIZE,
    MELEE_RAY_SPEED,
    _clear_array_prop,
    _deg_to_rot_units,
    _dump_uobject_fields_summary,
    _ensure_subpackage,
    _find,
    _find_any,
    _inherited_manufacturers,
    _inherited_plc,
    _sanitize_token,
    _sum_crit_bonus,
    _zero_vector_struct,
)
from .name_parts import (
    _apply_crit_bonus_if_supported,
    _build_name_part,
    _clear_part_stat_bonuses,
    apply_ui_preview_settings_to_all_wts,
    build_name_parts,
    get_or_create_prefix_part,
    get_or_create_title_part,
)
from .parts import (
    build_accessory_parts,
    build_blade_parts,
    build_body_parts,
    build_element_parts,
    build_grip_parts,
    build_material_parts,
)
from .resources import ensure_combat_resources_cached
from .weapon_types import build_weapon_types

__all__ = [
    "BuildContext",
    "MELEE_CLIP_SIZE",
    "MELEE_RAY_SPEED",
    "apply_ui_preview_settings_to_all_wts",
    "build_accessory_parts",
    "build_balances",
    "build_blade_parts",
    "build_body_parts",
    "build_element_firing_modes",
    "build_element_parts",
    "build_grip_parts",
    "build_material_parts",
    "build_name_parts",
    "build_weapon_types",
    "ensure_combat_resources_cached",
    "ensure_definitions_built",
    "get_or_create_prefix_part",
    "get_or_create_title_part",
    "register_assets_in_library",
    "_RARITY_DONOR_SUFFIX",
    "_apply_crit_bonus_if_supported",
    "_attach_torgue_explode_on_impact",
    "_build_name_part",
    "_clear_array_prop",
    "_clear_part_stat_bonuses",
    "_configure_balance_definition",
    "_configure_melee_firing_mode",
    "_configure_part_list_collection",
    "_deg_to_rot_units",
    "_dump_uobject_fields_summary",
    "_ensure_subpackage",
    "_extract_parts_from_plc",
    "_find",
    "_find_any",
    "_find_template_manufacturer_weight",
    "_find_template_weighted_row",
    "_harvest_pistol_donors",
    "_inherited_manufacturers",
    "_inherited_plc",
    "_sanitize_token",
    "_sum_crit_bonus",
    "_zero_vector_struct",
]


def ensure_definitions_built() -> bool:
    """Idempotently construct all Melee Arsenal definitions, balances, and AssLibMan entries."""
    if STATE.initialized:
        return True
    if unrealsdk is None:
        return False

    try:
        ensure_combat_resources_cached()
        donors = _harvest_pistol_donors()
        if not donors["wt"] or donors["wt"].get(Manufacturer.HYPERION) is None:
            log_warn("GD_Weap_Pistol donors not yet loaded; deferring Melee Arsenal build.")
            return False

        ctx = BuildContext.create(donors)

        # One builder per concern / part type, in the exact original order.
        build_name_parts(ctx)
        build_element_firing_modes(ctx)
        build_weapon_types(ctx)
        build_body_parts(ctx)
        build_blade_parts(ctx)
        build_grip_parts(ctx)
        build_accessory_parts(ctx)
        build_element_parts(ctx)
        build_material_parts(ctx)
        build_balances(ctx)

            # Register all custom assets in AssetLibraryManager (`AssLibMan`) for save/load.
        register_assets_in_library("WeaponTypeDefinition", list(STATE.maker_to_wt.values()))
        all_parts = (
            list(STATE.body_parts.values())
            + list(STATE.blade_parts.values())
            + list(STATE.grip_parts.values())
            + list(STATE.accessory_parts.values())
            + list(STATE.element_parts.values())
            + list(STATE.all_extra_part_objects)
        )
        register_assets_in_library("WeaponPartDefinition", all_parts)
        all_names = (
            ([STATE.empty_name_part] if STATE.empty_name_part is not None else [])
            + list(STATE.title_parts.values())
            + list(STATE.prefix_parts.values())
        )
        register_assets_in_library("WeaponNamePartDefinition", all_names)
        register_assets_in_library("WeaponBalanceDefinition", list(STATE.balances.values()))

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
