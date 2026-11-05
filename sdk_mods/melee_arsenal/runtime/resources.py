"""Every Unreal asset the mod looks up, cached in one place.

`ensure_combat_resources_cached()` loads the packages and fills the caches. The definition
builders read the damage types and swing sounds; combat, throw and guard read the rest.
"""

from __future__ import annotations

from typing import Any

from .sdk import unrealsdk


from ..core.enums import Element
from ..core.parts import ELEMENTS
from .log import log_debug, log_debug_once, log_info


ATTR_MELEE_MODIFIER = "D_Attributes.DamageSourceModifiers.InstigatedMeleeDamageModifier"


ATTR_ROID_BUFF = "D_Attributes.DamageSourceModifiers.InstigatedMeleeDamageAddedBuff"


ATTR_STATUS_DMG_MOD = "D_Attributes.DamageSourceModifiers.InstigatedStatusEffectDamageModifier"


ATTR_STATUS_CHANCE_MOD = "D_Attributes.DamageSourceModifiers.InstigatedStatusEffectChanceModifier"


ELEM_DMG_TYPE_PROC_PATHS: dict[Element, str] = {
    spec.element: spec.damage_type_path for spec in ELEMENTS.values()
}


ELEM_DMG_TYPE_NODOT_PATHS: dict[Element, str] = {
    Element.NONE: "GD_Impact.DamageType.DmgType_Normal",
    Element.FIRE: "GD_Incendiary.DamageType.DmgType_Incendiary_Impact_NoDoT",
    Element.SHOCK: "GD_Shock.DamageType.DmgType_Shock_Impact_NoDoT",
    Element.CORROSIVE: "GD_Corrosive.DamageType.DmgType_Corrosive_Impact_NoDoT",
    Element.SLAG: "GD_Impact.DamageType.DmgType_Normal",
    Element.EXPLOSIVE: "GD_Explosive.DamageType.DmgType_Explosive_Impact_ForceFlinch",
}


MELEE_IMPACT_DEF_PATH = "GD_Impacts.Melee.Impact_MeleeGeneric"


CANIM_LIGHT_SLASH = "Anim_CameraAnimations.Melee.Canim_RepeaterPistol_Melee"


CANIM_MEDIUM_CLEAVE = "Anim_CameraAnimations.Melee.Canim_CombatShotgun_Melee"


CANIM_HEAVY_SLAM = "Anim_CameraAnimations.Melee.Canim_RocketLauncher_Melee"


CANIM_EXPLOSION_MEDIUM = "Anim_CameraAnimations.Explosions.Canim_Explosion_Medium"


AK_SWING_WHOOSH = "Ake_Wep_Melee.Ak_Play_Wep_Melee_Generic_Attack"


AK_SWING_HEAVY_SWIPE = "Ake_Mon_Thresher.v2.Ak_Play_Mon_Thresher_Melee_Swipe"


AK_BLADE_HIT_FLESH = "Ake_Wep_Melee.Ak_Play_wep_melee_BladedWeap_hit"


AK_HEAVY_IMPACT = "Ake_Wep_Melee.Ak_Play_wep_melee_Merc_impact"


_CACHED_ATTRS: dict[str, Any] = {}


_CACHED_CANIMS: dict[str, Any] = {}


_CACHED_AKEVENTS: dict[str, Any] = {}


_CACHED_DMG_TYPES_PROC: dict[Element, Any] = {}


_CACHED_DMG_TYPES_NODOT: dict[Element, Any] = {}


_CACHED_MELEE_IMPACT: Any = None


_CACHED_BULLET_DMG_CLS: Any = None


_CACHED_GRENADE_DMG_CLS: Any = None


_CACHED_EXPLOSION_PS: Any = None


_CACHED_EXPLOSION_AK: Any = None


_EXPLOSION_ASSETS_SCANNED: bool = False


# Definition-tier assets: read while the weapon/part/firing-mode definitions are built.
_CACHED_DEF_DMG_TYPES: dict[Element, Any] = {}
_CACHED_SWING_SOUNDS: dict[str, Any] = {}


def _safe_find(cls_name: str, path: str) -> Any:
    try:
        return unrealsdk.find_object(cls_name, path)
    except Exception as exc:
        log_debug_once(f"combat._safe_find:{cls_name}:{path}", f"_safe_find {cls_name} {path} failed: {exc!r}")
        return None


def _cache_definition_assets() -> None:
    """The three assets the definition builders need: damage types and the two swing sounds."""
    for elem_spec in ELEMENTS.values():
        if _CACHED_DEF_DMG_TYPES.get(elem_spec.element) is None:
            dt = _safe_find("WillowDamageTypeDefinition", elem_spec.damage_type_path) or _safe_find(
                "DamageTypeDefinition", elem_spec.damage_type_path
            )
            if dt is not None:
                _CACHED_DEF_DMG_TYPES[elem_spec.element] = dt

    for key, path in (
        ("light", AK_SWING_WHOOSH),
        ("heavy", "Ake_Mon_Thresher.v2.Ak_Play_Mon_Thresher_Melee_Swipe"),
    ):
        if _CACHED_SWING_SOUNDS.get(key) is None:
            _CACHED_SWING_SOUNDS[key] = _safe_find("AkEvent", path)


def ensure_combat_resources_cached() -> None:
    """Load and cache attributes, damage types, melee impacts, CameraAnims, and AkEvents."""
    global _CACHED_MELEE_IMPACT, _CACHED_BULLET_DMG_CLS, _CACHED_GRENADE_DMG_CLS
    if not _CACHED_AKEVENTS:
        for pkg in (
            "Ake_Wep_Melee",
            "Ake_Mon_Thresher",
            "Anim_CameraAnimations",
            "GD_Impacts",
            "GD_Impact",
            "GD_Incendiary",
            "GD_Shock",
            "GD_Corrosive",
            "GD_Amp",
            "GD_Explosive",
        ):
            try:
                unrealsdk.load_package(pkg)
            except Exception as exc:
                log_debug_once(f"combat.ensure_cached.load:{pkg}", f"load_package {pkg} failed: {exc!r}")

    for path in (ATTR_MELEE_MODIFIER, ATTR_ROID_BUFF, ATTR_STATUS_DMG_MOD, ATTR_STATUS_CHANCE_MOD):
        if path not in _CACHED_ATTRS or _CACHED_ATTRS[path] is None:
            _CACHED_ATTRS[path] = _safe_find("AttributeDefinition", path)

    for path in (CANIM_LIGHT_SLASH, CANIM_MEDIUM_CLEAVE, CANIM_HEAVY_SLAM, CANIM_EXPLOSION_MEDIUM):
        if path not in _CACHED_CANIMS or _CACHED_CANIMS[path] is None:
            _CACHED_CANIMS[path] = _safe_find("CameraAnim", path)

    for path in (AK_SWING_WHOOSH, AK_SWING_HEAVY_SWIPE, AK_BLADE_HIT_FLESH, AK_HEAVY_IMPACT):
        if path not in _CACHED_AKEVENTS or _CACHED_AKEVENTS[path] is None:
            _CACHED_AKEVENTS[path] = _safe_find("AkEvent", path)

    for elem, dpath in ELEM_DMG_TYPE_PROC_PATHS.items():
        if elem not in _CACHED_DMG_TYPES_PROC or _CACHED_DMG_TYPES_PROC[elem] is None:
            _CACHED_DMG_TYPES_PROC[elem] = _safe_find("WillowDamageTypeDefinition", dpath)

    for elem, dpath in ELEM_DMG_TYPE_NODOT_PATHS.items():
        if elem not in _CACHED_DMG_TYPES_NODOT or _CACHED_DMG_TYPES_NODOT[elem] is None:
            _CACHED_DMG_TYPES_NODOT[elem] = _safe_find(
                "WillowDamageTypeDefinition", dpath
            ) or _CACHED_DMG_TYPES_PROC.get(elem)

    if _CACHED_MELEE_IMPACT is None:
        _CACHED_MELEE_IMPACT = _safe_find("ImpactDefinition", MELEE_IMPACT_DEF_PATH)

    if _CACHED_BULLET_DMG_CLS is None:
        try:
            _CACHED_BULLET_DMG_CLS = unrealsdk.find_class("WillowDmgSource_Bullet")
        except Exception as exc:
            log_debug(f"find_class WillowDmgSource_Bullet failed: {exc!r}")
            _CACHED_BULLET_DMG_CLS = None

    if _CACHED_GRENADE_DMG_CLS is None:
        try:
            _CACHED_GRENADE_DMG_CLS = unrealsdk.find_class("WillowDmgSource_Grenade")
        except Exception as exc:
            log_debug(f"find_class WillowDmgSource_Grenade failed: {exc!r}")
            _CACHED_GRENADE_DMG_CLS = _CACHED_BULLET_DMG_CLS

    _cache_definition_assets()
    _cache_torgue_explosion_assets()


def _cache_torgue_explosion_assets() -> None:
    """Cache the explosive `ParticleSystem` + `AkEvent` the Torgue splash uses.

    These four paths were measured as loaded in a normal session (`section_catalogue.json`), so the
    ordered fallback chain is the whole lookup: no whole-object scan, no name scoring.
    """
    global _CACHED_EXPLOSION_PS, _CACHED_EXPLOSION_AK, _EXPLOSION_ASSETS_SCANNED
    if _EXPLOSION_ASSETS_SCANNED:
        return
    _EXPLOSION_ASSETS_SCANNED = True

    best_ps = _safe_find(
        "ParticleSystem", "FX_WEP_Explosions.Particles.Default.Part_ExplosiveExplosion_Medium"
    ) or _safe_find("ParticleSystem", "FX_WEP_Explosions.Particles.Default.Part_ExplosiveExplosion_Small")
    best_ak = (
        _safe_find("AkEvent", "Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_MED")
        or _safe_find("AkEvent", "Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_SM")
        or _safe_find("AkEvent", "Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_LG")
    )

    _CACHED_EXPLOSION_PS = best_ps
    _CACHED_EXPLOSION_AK = best_ak
    if best_ps is not None or best_ak is not None:
        ps_name = str(best_ps._path_name()) if best_ps is not None else "None"
        ak_name = str(best_ak._path_name()) if best_ak is not None else "None"
        log_info(f"Cached Torgue Blast-Cleaver explosion FX={ps_name} Audio={ak_name}")
    else:
        log_debug_once("combat.explosion_fx", "no explosive ParticleSystem/AkEvent loaded; splash is silent")
