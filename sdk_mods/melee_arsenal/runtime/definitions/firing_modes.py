"""Melee `FiringModeDefinition` construction (element rays + Torgue splash).

Fire modes are invisible, zero-extent `WillowLightProjectile` rays so every swing
routes through `WillowLightProjectileManager:AddProj` even at point-blank range.
"""
from __future__ import annotations
from ..sdk import unrealsdk

import copy
from typing import Any

from ...core.enums import Element
from ..log import log_debug_once, log_warn, obj_path
from .. import resources
from ..state import KEEP_ALIVE_FLAGS, STATE
from .context import BuildContext
from .helpers import (
    MELEE_RAY_SPEED,
    _clear_array_prop,
    _find,
    _zero_vector_struct,
)

# Donor for the Torgue splash: the one loaded firing mode whose `OnAnyImpact` list carries the
# `Behavior_Explode` we clone (its `_2`/`_3` siblings do too).
FLAKKER_EXPLODE_DONOR = "GD_Weap_Shotgun.FiringModes.Bullets_Shotgun_Torgue_Flakker_Child"


def _configure_melee_firing_mode(fm: Any, range_uu: float) -> None:
    """Configure a cloned `FiringModeDefinition` as an invisible, zero-extent light-projectile melee ray.

    IMPORTANT:
    - Clone from `Bullet_Shotgun_TidalWave` (pure `WillowLightProjectile`, `FireType=0`) so shots ALWAYS
      route through `WillowLightProjectileManager:AddProj` even at 0.0m point-blank melee distance.
    - Keep `TraceExtent = (0, 0, 0)`! Non-zero box extents on launched light projectiles start penetrating
      world/actor collision near the player capsule and fail to register hits.
    """
    fm.FireType = 0
    fm.Speed = MELEE_RAY_SPEED
    fm.TraceInterval = 0.2
    fm.Lifetime = max(0.11, (range_uu / MELEE_RAY_SPEED) + 0.075)
    # Every field below is declared on `FiringModeDefinition` itself (see docs/ENGINE_FACTS.md).
    fm.AirFriction = 0.0
    fm.RicochetFriction = 0.0
    fm.PartSysTemplate = None
    fm.ConditionalPartSysTemplate = None
    fm.SoundDefinition = None
    fm.bSuppressWeaponSpread = True
    fm.bFireRandomlyFromPattern = False
    fm.bResetPatternAfterEachShot = True
    fm.bScalePatternByWeaponSpread = False
    fm.bPenetratePawn = False
    fm.NumRicochets = 0
    for arr_field in ("FiringPatternLines", "OnAnyImpact", "TimerEvents"):
        _clear_array_prop(fm, arr_field)
    for vec_field in ("Acceleration", "WaveFreq", "WaveAmp", "WavePhase", "TraceExtent"):
        _zero_vector_struct(fm, vec_field)


def _first_explode_behavior(mode: Any) -> Any:
    """First `Behavior_Explode` in a firing mode's impact behaviour list, if any."""
    for behaviour in getattr(mode, "OnAnyImpact", None) or ():
        class_name = str(getattr(getattr(behaviour, "Class", None), "Name", "") or "")
        if class_name == "Behavior_Explode":
            return behaviour
    return None


def _attach_torgue_explode_on_impact(fm_explosive: Any, pkg: Any) -> None:
    """Attach a 180 UU (50% splash) `Behavior_Explode` to `FM_Melee_Elem_EXPLOSIVE` when available.

    The donor is `Bullets_Shotgun_Torgue_Flakker_Child`, and its blast behaviour sits in its
    `OnAnyImpact` list — not in its `TimerEvents`, which is where the earlier search looked (all 38
    loaded explosive firing modes carry `Behavior_Explode` on `OnAnyImpact` and none in `TimerEvents`,
    so the search could never match and the splash was silently skipped).
    """
    try:
        unrealsdk.load_package("GD_Weap_Shotgun")
    except Exception:
        pass
    try:
        donor_mode = _find("FiringModeDefinition", FLAKKER_EXPLODE_DONOR)
        donor_behavior = _first_explode_behavior(donor_mode) if donor_mode is not None else None
        if donor_behavior is None:
            log_debug_once(
                "torgue.explode_donor",
                f"no Behavior_Explode on {FLAKKER_EXPLODE_DONOR}; explosive splash left disabled",
            )
            return

        explode_beh = _find("Behavior_Explode", f"{obj_path(pkg)}.Melee_BlastCleaver_Explode")
        if explode_beh is None:
            explode_beh = unrealsdk.construct_object(
                "Behavior_Explode",
                pkg,
                "Melee_BlastCleaver_Explode",
                KEEP_ALIVE_FLAGS,
                donor_behavior,
            )
            STATE.keep_alive(explode_beh)

        for fname, fval in (("DamageRadiusFormula", 180.0), ("MomentumFormula", 18000.0)):
            formula = copy.copy(getattr(explode_beh, fname))
            formula.BaseValueConstant = fval
            formula.BaseValueAttribute = None
            formula.InitializationDefinition = None
            formula.BaseValueScaleConstant = 1.0
            setattr(explode_beh, fname, formula)

        dmg_f = copy.copy(explode_beh.DamageFormula)
        dmg_f.BaseValueScaleConstant = 0.50
        explode_beh.DamageFormula = dmg_f
        explode_beh.OverrideName = "ExplosionOverride"
        explode_beh.bDisableRadiusDamageFalloff = False
        explode_beh.bCanDamageFriendlies = False

        overrides = _find(
            "ExplosionCollectionDefinition",
            "GD_Weap_Shared_Effects.Default_Elemental_Explosions",
        )
        if overrides is not None:
            fm_explosive.ExplosionOverrideDefinition = overrides
            fm_explosive.ExplosionOverideInstanceDataName = "ExplosionOverride"
        fm_explosive.OnAnyImpact = [explode_beh]
    except Exception as exc:
        log_warn(f"Optional Torgue Behavior_Explode setup skipped: {exc!r}")


def build_element_firing_modes(ctx: BuildContext) -> None:
    """Build one melee firing mode per Element (Torgue gets native Behavior_Explode splash)."""
    donors = ctx.donors
    fm_pkg = ctx.fm_pkg
    # 2A. Build Element-specific Melee FiringModeDefinitions (`STATE.element_to_firing_mode`)
    fm_donor = donors["firing_mode"]
    for elem in Element:
        efm_name = f"FM_Melee_Elem_{elem.name}"
        efm = _find("FiringModeDefinition", f"{obj_path(fm_pkg)}.{efm_name}")
        if efm is None:
            efm = unrealsdk.construct_object(
                "FiringModeDefinition",
                fm_pkg,
                efm_name,
                KEEP_ALIVE_FLAGS,
                fm_donor,
            )
            STATE.keep_alive(efm)
        _configure_melee_firing_mode(efm, 400.0)
        dt_def = resources._CACHED_DEF_DMG_TYPES.get(elem) or resources._CACHED_DEF_DMG_TYPES.get(
            Element.NONE
        )
        if dt_def is not None and hasattr(efm, "DamageTypeDefinition"):
            efm.DamageTypeDefinition = dt_def
        if elem == Element.EXPLOSIVE:
            _attach_torgue_explode_on_impact(efm, fm_pkg)
        STATE.element_to_firing_mode[elem] = efm

    ctx.fm_donor = fm_donor
