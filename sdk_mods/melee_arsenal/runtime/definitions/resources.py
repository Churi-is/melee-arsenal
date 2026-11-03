"""Cache the live combat resources (damage types, impacts, AkEvents, attributes).

Called once before any definition is constructed; every cached object is read from
`STATE` by the combat/secondary engines later on.
"""
from __future__ import annotations

from ...core.parts import ELEMENTS
from ..state import STATE
from ._sdk import unrealsdk
from .helpers import _find, _find_any


def ensure_combat_resources_cached() -> None:
    """Load packages and cache DamageTypeDefinitions, AkEvents, CameraAnims, and Attributes."""
    if unrealsdk is None:
        return
    for pkg_name in (
        "D_Attributes",
        "GD_Impact",
        "GD_Incendiary",
        "GD_Shock",
        "GD_Corrosive",
        "GD_Amp",
        "GD_Explosive",
        "GD_Impacts",
        "GD_Weap_Shared_Effects",
        "Ake_Wep_Melee",
        "Ake_Mon_Thresher",
        "Anim_CameraAnimations",
    ):
        try:
            unrealsdk.load_package(pkg_name)
        except Exception:
            pass

    for elem_spec in ELEMENTS.values():
        if elem_spec.element not in STATE.damage_types:
            dt = _find_any(
                ("WillowDamageTypeDefinition", "DamageTypeDefinition"),
                elem_spec.damage_type_path,
            )
            if dt is not None:
                STATE.damage_types[elem_spec.element] = dt

    if STATE.dmg_source_bullet_cls is None:
        try:
            STATE.dmg_source_bullet_cls = unrealsdk.find_class("WillowDmgSource_Bullet")
        except Exception:
            STATE.dmg_source_bullet_cls = None

    if STATE.impact_bullet_normal is None:
        STATE.impact_bullet_normal = (
            _find("ImpactDefinition", "GD_Impacts.Bullets.Bullet_LargeCaliber")
            or _find("ImpactDefinition", "GD_Impacts.Bullet.Bullet_Normal")
        )
    if STATE.impact_explosive is None:
        STATE.impact_explosive = (
            _find("ImpactDefinition", "GD_Elemental_Impacts.Explosions.Default_Elemental_Explosions")
            or STATE.impact_bullet_normal
        )

    if STATE.ak_swing_light is None:
        STATE.ak_swing_light = _find("AkEvent", "Ake_Wep_Melee.Ak_Play_Wep_Melee_Generic_Attack")
    if STATE.ak_swing_heavy is None:
        STATE.ak_swing_heavy = (
            _find("AkEvent", "Ake_Mon_Thresher.v2.Ak_Play_Mon_Thresher_Melee_Swipe")
            or STATE.ak_swing_light
        )
    if STATE.ak_hit_blade is None:
        STATE.ak_hit_blade = _find("AkEvent", "Ake_Wep_Melee.Ak_Play_wep_melee_BladedWeap_hit")
    if STATE.ak_hit_heavy is None:
        STATE.ak_hit_heavy = (
            _find("AkEvent", "Ake_Wep_Melee.Ak_Play_wep_melee_Merc_impact")
            or STATE.ak_hit_blade
        )

    if STATE.canim_light is None:
        STATE.canim_light = _find("CameraAnim", "Anim_CameraAnimations.Melee.Canim_RepeaterPistol_Melee")
    if STATE.canim_cleave is None:
        STATE.canim_cleave = (
            _find("CameraAnim", "Anim_CameraAnimations.Melee.Canim_CombatShotgun_Melee")
            or STATE.canim_light
        )
    if STATE.canim_heavy is None:
        STATE.canim_heavy = (
            _find("CameraAnim", "Anim_CameraAnimations.Melee.Canim_RocketLauncher_Melee")
            or STATE.canim_cleave
        )

    if STATE.attr_melee_mult is None:
        STATE.attr_melee_mult = _find(
            "AttributeDefinition",
            "D_Attributes.DamageSourceModifiers.InstigatedMeleeDamageModifier",
        )
    if STATE.attr_melee_added_buff is None:
        STATE.attr_melee_added_buff = _find(
            "AttributeDefinition",
            "D_Attributes.DamageSourceModifiers.InstigatedMeleeDamageAddedBuff",
        )
    if STATE.attr_roid_damage is None:
        STATE.attr_roid_damage = _find(
            "AttributeDefinition",
            "D_Attributes.Shield.RoidMeleeDamage",
        )
    if STATE.attr_player_crit is None:
        STATE.attr_player_crit = _find(
            "AttributeDefinition",
            "D_Attributes.GameplayAttributes.PlayerCriticalHitBonus",
        )
    if STATE.attr_status_dmg_mult is None:
        STATE.attr_status_dmg_mult = _find(
            "AttributeDefinition",
            "D_Attributes.DamageSourceModifiers.InstigatedStatusEffectDamageModifier",
        )
