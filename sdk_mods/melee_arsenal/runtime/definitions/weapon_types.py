"""Manufacturer `WeaponTypeDefinition`s plus their per-maker firing modes.

Each archetype's cadence, clip, recoil-free first-person swing, instant-hit damage
scale and melee swipe audio are stamped onto a clone of the vanilla pistol type.
"""
from __future__ import annotations

import copy

from ...core.enums import TargetingMode
from ...core.parts import BLADE_BY_MANUFACTURER, MANUFACTURERS
from ...core.stats import compute_swing_speed_damage_scale
from ..log import log_info, obj_path
from ..state import KEEP_ALIVE_FLAGS, STATE
from ._sdk import unrealsdk
from .context import BuildContext
from .firing_modes import _configure_melee_firing_mode
from .helpers import (
    MELEE_CLIP_SIZE,
    _clear_array_prop,
    _dump_uobject_fields_summary,
    _find,
    _sanitize_token,
)
from .name_parts import apply_ui_preview_settings_to_all_wts


def build_weapon_types(ctx: BuildContext) -> None:
    """Build one WeaponTypeDefinition + firing mode per Manufacturer archetype."""
    donors = ctx.donors
    wt_pkg = ctx.wt_pkg
    fm_pkg = ctx.fm_pkg
    fm_donor = ctx.fm_donor
    # 2B. Build Maker FiringModeDefinitions & WeaponTypeDefinitions
    for maker, mspec in MANUFACTURERS.items():
        donor_wt = donors["wt"][maker]
        STATE.donor_wt_by_maker[maker] = donor_wt

        fm_name = f"FM_Melee_{maker.value}_{_sanitize_token(mspec.archetype_name)}"
        fm = _find("FiringModeDefinition", f"{obj_path(fm_pkg)}.{fm_name}")
        if fm is None:
            fm = unrealsdk.construct_object(
                "FiringModeDefinition",
                fm_pkg,
                fm_name,
                KEEP_ALIVE_FLAGS,
                fm_donor,
            )
            STATE.keep_alive(fm)
        _configure_melee_firing_mode(fm, mspec.cleave_profile.base_range_uu)
        STATE.maker_to_firing_mode[maker] = fm
        STATE.fm_path_to_maker[obj_path(fm)] = maker

        wt_name = f"WT_{maker.value}_{_sanitize_token(mspec.archetype_name)}"
        wt = _find("WeaponTypeDefinition", f"{obj_path(wt_pkg)}.{wt_name}")
        if wt is None:
            wt = unrealsdk.construct_object(
                "WeaponTypeDefinition",
                wt_pkg,
                wt_name,
                KEEP_ALIVE_FLAGS,
                donor_wt,
            )
            STATE.keep_alive(wt)

        wt.DefaultFiringModeDefinition = getattr(donor_wt, "DefaultFiringModeDefinition", None) or fm
        wt.ShotCost = 0
        wt.ClipSize = MELEE_CLIP_SIZE
        wt.bDisableReloadMessages = True
        wt.ReloadTime = 0.10
        wt.FireRate = round(1.0 / mspec.base_swing_speed, 4)
        # Keep internal aim trace long so crosshair targeting has zero muzzle parallax;
        # actual melee reach is clamped by camera-eye strike endpoints in the synchronous combat path.
        wt.Range = 4096.0
        wt.Spread = 0.0
        wt.PerShotAccuracyImpulse = 0.0
        wt.MuzzleFlashPSTemplates = None
        wt.MuzzleFlashLightTemplate = None
        wt.ShellCasingPSCTemplate = None
        wt.TracerTemplate = None
        wt.Typename = mspec.archetype_name
        _clear_array_prop(wt, "WeaponAttributeEffects")
        # Strip 0.2667s pistol recoil kick & gun shake so our 1st-person blade swing plays cleanly
        _clear_array_prop(wt, "WeaponFireAnimations")
        for kick_attr in (
            "WeaponKickSpeed",
            "WeaponKickUp",
            "WeaponKickDown",
            "WeaponKickLeft",
            "WeaponKickRight",
            "ZoomedRecoilAnimScale",
        ):
            if hasattr(wt, kick_attr):
                try:
                    setattr(wt, kick_attr, 0.0)
                except Exception:
                    pass
        if hasattr(wt, "FireShake"):
            try:
                fs = copy.copy(wt.FireShake)
                fs.OffsetTime = 0.0
                fs.RotTime = 0.0
                fs.FOVTime = 0.0
                wt.FireShake = fs
            except Exception:
                pass

        ihd = copy.copy(wt.InstantHitDamage)
        ihd.BaseValueConstant = 20.0
        # Mirror `compute_card_stats` (including the fast-blade per-hit trade-off) so any engine path
        # that ever reads the inherited pistol damage agrees with the item card.
        ihd.BaseValueScaleConstant = round(
            2.65 * mspec.base_damage_mult * compute_swing_speed_damage_scale(mspec.base_swing_speed),
            6,
        )
        wt.InstantHitDamage = ihd

        # Replace gunshot audio with melee swipe AkEvent (or silence so no gun bang ever plays!)
        swing_event = (
            STATE.ak_swing_heavy
            if mspec.cleave_profile.mode == TargetingMode.FRONTAL_CLEAVE
            else STATE.ak_swing_light
        )
        donor_sounds = list(getattr(donor_wt, "FireSounds", []) or [])
        if donor_sounds and swing_event is not None:
            snd_struct = copy.copy(donor_sounds[0])
            snd_struct.Event = swing_event
            snd_struct.Cue = None
            wt.FireSounds = [snd_struct]
        else:
            _clear_array_prop(wt, "FireSounds")
        _clear_array_prop(wt, "FireTailSounds")

        bspec = BLADE_BY_MANUFACTURER[maker]
        default_title_np = STATE.title_parts.get(bspec.base_title)
        wt.TitleList = [default_title_np] if default_title_np is not None else []
        _clear_array_prop(wt, "PrefixList")

        STATE.maker_to_wt[maker] = wt
        STATE.wt_path_to_maker[obj_path(wt)] = maker

    # Clone & inspect `InventoryCardPresentationDefinition` (`GD_InventoryPresentations.Definitions.Pistol`)
    sample_donor_wt = next(iter(donors["wt"].values()), None)
    donor_pres = getattr(sample_donor_wt, "Presentation", None) if sample_donor_wt is not None else None
    if donor_pres is not None:
        pres_cls_name = str(getattr(getattr(donor_pres, "Class", None), "Name", "") or "InventoryCardPresentationDefinition")
        pres_melee = _find(pres_cls_name, f"{obj_path(wt_pkg)}.Presentation_Melee")
        if pres_melee is None:
            try:
                pres_melee = unrealsdk.construct_object(
                    pres_cls_name,
                    wt_pkg,
                    "Presentation_Melee",
                    KEEP_ALIVE_FLAGS,
                    donor_pres,
                )
            except Exception:
                pres_melee = donor_pres
        STATE.ui_presentation = STATE.keep_alive(pres_melee)
        pres_fields = _dump_uobject_fields_summary(STATE.ui_presentation)
        if pres_fields:
            log_info(f"UI Presentation ({pres_cls_name}) fields: {pres_fields}")

    apply_ui_preview_settings_to_all_wts()
