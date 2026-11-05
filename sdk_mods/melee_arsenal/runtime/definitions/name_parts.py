"""Title / Prefix `WeaponNamePartDefinition` construction and inventory preview tuning.

Titles come from the Blade+Grip matrix, prefixes from Accessory and Element parts;
every distinct string in the part catalog gets exactly one UE3 name part object.
"""
from __future__ import annotations
from ..sdk import unrealsdk

import copy
from typing import Any

from ...core.parts import ACCESSORIES, BLADES, ELEMENTS, MANUFACTURERS
from ..custom_assets import apply_gestalt_bounds_framing
from ..log import log_debug_once, obj_path
from ..state import KEEP_ALIVE_FLAGS, STATE
from .context import BuildContext
from .helpers import _clear_array_prop, _deg_to_rot_units, _ensure_subpackage, _find, _sanitize_token

def apply_ui_preview_settings_to_all_wts() -> None:
    """Orient Melee Weapons horizontally & zoom framing closer in Inventory / Inspect previews.

    Every attribute below is declared on `WeaponTypeDefinition` (measured on the live weapon type),
    so this writes them straight out instead of probing each one for existence.
    """
    pitch_ru = _deg_to_rot_units(STATE.preview_pitch_deg)
    yaw_ru = _deg_to_rot_units(STATE.preview_yaw_deg)
    roll_ru = _deg_to_rot_units(STATE.preview_roll_deg)
    focus_rad = float(STATE.preview_focus_radius)

    for wt in STATE.maker_to_wt.values():
        if wt is None:
            continue
        try:
            wt.bUIMeshRotationNeedsTransformFixup = False
            rotation = copy.copy(wt.UIMeshRotation)
            rotation.Pitch = pitch_ru
            rotation.Yaw = yaw_ru
            rotation.Roll = roll_ru
            wt.UIMeshRotation = rotation
            wt.FocusRadius = focus_rad
            wt.MinimumVerticalPercentage = 0.0
            wt.MinimumHorizontalPercentage = 0.85
            wt.ThirdPersonMeshScale = 1.05
            if STATE.ui_presentation is not None:
                wt.Presentation = STATE.ui_presentation
        except Exception as exc:  # pragma: no cover - engine call shape varies
            log_debug_once("preview_wt", f"inventory preview tuning failed: {exc!r}")

    apply_gestalt_bounds_framing()


def _build_name_part(pkg: Any, obj_name: str, text: str, template: Any, is_title: bool = True) -> Any:
    existing = _find("WeaponNamePartDefinition", f"{obj_path(pkg)}.{obj_name}")
    if existing is not None:
        return existing
    np = unrealsdk.construct_object(
        "WeaponNamePartDefinition",
        pkg,
        obj_name,
        KEEP_ALIVE_FLAGS,
        template,
    )
    STATE.keep_alive(np)
    for clear_field in (
        "AttributeSlotEffects",
        "ExternalAttributeEffects",
        "WeaponAttributeEffects",
        "CustomPresentations",
    ):
        _clear_array_prop(np, clear_field)

    np.PartName = text
    np.bNameIsUnique = bool(is_title and text)  # declared on `WeaponNamePartDefinition`
    return np


def _clear_part_stat_bonuses(part: Any) -> None:
    """Strip inherited gun accuracy/recoil/mag/bayonet/rocket-firing-mode overrides from cloned parts."""
    for field_name in (
        "WeaponAttributeEffects",
        "ExternalAttributeEffects",
        "AttributeSlotEffects",
        "AttributeSlotUpgrades",
        "ZoomFirstPersonHandsIdleAnimations",
    ):
        _clear_array_prop(part, field_name)
    part.CustomFiringModeDefinition = None  # declared on `WeaponPartDefinition`


def _apply_crit_bonus_if_supported(part: Any, crit_bonus: float, crit_template: tuple[str, Any] | None) -> None:
    """Attach native weapon weakspot critical hit bonus (`+75%`, `+100%`, etc.) onto `part`."""
    if crit_bonus <= 0.0 or crit_template is None:
        return
    try:
        field_name, donor_row = crit_template
        row = copy.copy(donor_row)
        bmv = copy.copy(row.BaseModifierValue)
        bmv.BaseValueConstant = float(crit_bonus)
        bmv.BaseValueAttribute = None
        bmv.InitializationDefinition = None
        bmv.BaseValueScaleConstant = 1.0
        row.BaseModifierValue = bmv
        setattr(part, field_name, [row])
    except Exception as exc:  # pragma: no cover - engine call shape varies
        log_debug_once("_apply_crit_bonus_if_supported", f"_apply_crit_bonus_if_supported failed: {exc!r}")


def get_or_create_title_part(title_text: str) -> Any:
    if title_text in STATE.title_parts:
        return STATE.title_parts[title_text]
    if STATE.root_package is None:
        return None
    names_pkg = _ensure_subpackage(STATE.root_package, "Names")
    template = next(iter(STATE.title_parts.values()), None)
    np = _build_name_part(
        names_pkg,
        f"Title_{_sanitize_token(title_text)}",
        title_text,
        template,
        is_title=True,
    )
    STATE.title_parts[title_text] = np
    return np


def get_or_create_prefix_part(prefix_text: str) -> Any:
    if not prefix_text:
        return STATE.empty_name_part
    if prefix_text in STATE.prefix_parts:
        return STATE.prefix_parts[prefix_text]
    if STATE.root_package is None:
        return None
    names_pkg = _ensure_subpackage(STATE.root_package, "Names")
    template = next(iter(STATE.prefix_parts.values()), None)
    np = _build_name_part(
        names_pkg,
        f"Prefix_{_sanitize_token(prefix_text)}",
        prefix_text,
        template,
        is_title=False,
    )
    STATE.prefix_parts[prefix_text] = np
    return np


def build_name_parts(ctx: BuildContext) -> None:
    """Build the Title and Prefix name parts used by every Blade / Grip / Element combination."""
    donors = ctx.donors
    names_pkg = ctx.names_pkg
    # Build Title & Prefix WeaponNamePartDefinitions
    title_tpl = donors["title_template"]
    prefix_tpl = donors["prefix_template"] or title_tpl
    STATE.empty_name_part = _build_name_part(names_pkg, "Prefix_Empty", "", prefix_tpl, is_title=False)

    all_titles: set[str] = set()
    for bspec in BLADES.values():
        all_titles.add(bspec.base_title)
        all_titles.update(bspec.grip_titles.values())
    for mspec in MANUFACTURERS.values():
        all_titles.add(mspec.archetype_name)

    for ttext in sorted(all_titles):
        STATE.title_parts[ttext] = _build_name_part(
            names_pkg,
            f"Title_{_sanitize_token(ttext)}",
            ttext,
            title_tpl,
            is_title=True,
        )

    all_prefixes: set[str] = set()
    for aspec in ACCESSORIES.values():
        if aspec.default_prefix:
            all_prefixes.add(aspec.default_prefix)
        all_prefixes.update(v for v in aspec.prefix_by_manufacturer.values() if v)
    for espec in ELEMENTS.values():
        if espec.default_prefix:
            all_prefixes.add(espec.default_prefix)
        all_prefixes.update(v for v in espec.prefix_by_manufacturer.values() if v)

    for ptext in sorted(all_prefixes):
        STATE.prefix_parts[ptext] = _build_name_part(
            names_pkg,
            f"Prefix_{_sanitize_token(ptext)}",
            ptext,
            prefix_tpl,
            is_title=False,
        )
