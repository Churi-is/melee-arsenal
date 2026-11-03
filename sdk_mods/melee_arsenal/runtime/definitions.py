"""Constructs and roots all UE3 Melee Arsenal definitions at runtime.

Every property name and struct layout in this module is verified directly against
`melee_probe_report.json`, `ribs_arsenal`, and `pyunrealsdk` 1.10 (`unrealsdk` 3.2.0):
- Never assign Python `float` or `dict` to UE3 `WrappedStruct` properties (`Acceleration`, `FireSounds`, etc.);
  always mutate struct sub-fields in place or `copy.copy(...)` donor structs first.
- Clear `CustomFiringModeDefinition = None` on cloned barrel/element parts so Torgue rocket barrels never
  override our `WillowLightProjectile` melee `FiringModeDefinition`s.
- Build element-specific `FiringModeDefinition`s (`STATE.element_to_firing_mode`) including native
  `Behavior_Explode` splash on `Element.EXPLOSIVE` (Torgue Blast-Cleaver).
"""
from __future__ import annotations

import copy
import re
import traceback
from typing import Any

try:
    import unrealsdk  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    unrealsdk = None  # type: ignore[assignment]

from ..core.catalog import (
    ACCESSORIES,
    BLADES,
    BLADE_BY_MANUFACTURER,
    ELEMENTS,
    GRIPS,
    MANUFACTURERS,
)
from ..core.enums import Element, ElementPolicy, Manufacturer, Rarity, StatId, TargetingMode
from ..core.loot_roller import ACCESSORY_ROLL_CHANCE
from ..core.stats import compute_swing_speed_damage_scale
from .asset_library import register_assets_in_library
from .custom_assets import (
    apply_gestalt_bounds_framing,
    assign_struct_array,
    resolve_fitted_accessory_part,
    resolve_fitted_element_part,
    resolve_fitted_grip_part,
    resolve_fitted_material_part,
    setup_custom_inkblade_assets,
)
from .log import log_debug, log_debug_once, log_error, log_info, log_warn, obj_path
from .state import KEEP_ALIVE_FLAGS, STATE


def _sum_crit_bonus(modifiers: Any) -> float:
    total = 0.0
    for m in modifiers or ():
        if getattr(m, "stat", None) == StatId.CRIT_BONUS:
            total += float(getattr(m, "value", 0.0))
    return total


# 9,000 UU/s covers 300 UU in ~33ms (~2 frames at 60 FPS): fast enough to feel instantaneous while
# surviving `WillowLightProjectileManager`'s 1-frame `bPendingAdd` birth delay before collision tracing!
MELEE_RAY_SPEED: float = 9000.0
MELEE_CLIP_SIZE: int = 100

_RARITY_DONOR_SUFFIX: dict[Rarity, tuple[str, ...]] = {
    Rarity.COMMON: ("", "1_Common", "2_Uncommon"),
    Rarity.UNCOMMON: ("2_Uncommon", ""),
    Rarity.RARE: ("3_Rare", "2_Uncommon", ""),
    Rarity.VERY_RARE: ("4_VeryRare", "3_Rare", ""),
}


def _inherited_manufacturers(bal: Any) -> list[Any]:
    """Walk `BaseDefinition` chain — higher-rarity vanilla balances leave `Manufacturers` empty."""
    cur = bal
    steps = 0
    while cur is not None and steps < 12:
        steps += 1
        mfrs = list(getattr(cur, "Manufacturers", []) or [])
        if mfrs:
            return mfrs
        cur = getattr(cur, "BaseDefinition", None)
    return []


def _inherited_plc(bal: Any) -> Any:
    """Walk `BaseDefinition` chain to find the first non-null `PartListCollection`."""
    cur = bal
    steps = 0
    while cur is not None and steps < 12:
        steps += 1
        plc = getattr(cur, "RuntimePartListCollection", None) or getattr(
            cur, "WeaponPartListCollection", None
        )
        if plc is not None:
            return plc
        cur = getattr(cur, "BaseDefinition", None)
    return None


def _find(cls_name: str, path: str) -> Any:
    if unrealsdk is None or not path:
        return None
    try:
        return unrealsdk.find_object(cls_name, path)
    except Exception as exc:
        log_debug_once(f"definitions._find:{cls_name}:{path}", f"_find {cls_name} {path} failed: {exc!r}")
        return None


def _find_any(cls_names: tuple[str, ...], path: str) -> Any:
    for cls_name in cls_names:
        found = _find(cls_name, path)
        if found is not None:
            return found
    return None


def _sanitize_token(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", text.strip())
    return cleaned.strip("_") or "Default"


def _ensure_subpackage(parent: Any, name: str) -> Any:
    full = f"{obj_path(parent)}.{name}" if parent is not None else name
    existing = _find("Package", full)
    if existing is not None:
        return STATE.keep_alive(existing)
    pkg = unrealsdk.construct_object("Package", parent, name, KEEP_ALIVE_FLAGS, None)
    return STATE.keep_alive(pkg)


def _harvest_pistol_donors() -> dict[str, Any]:
    """Ensure base packages are loaded and harvest live `GD_Weap_Pistol` & `GD_Weap_Shotgun` donor UObjects."""
    for pkg_name in ("GD_Manufacturers", "GD_Weap_Pistol", "GD_Weap_Shotgun", "GD_Itempools"):
        try:
            unrealsdk.load_package(pkg_name)
        except Exception as exc:
            log_debug(f"load_package {pkg_name} failed: {exc!r}")

    donors: dict[str, Any] = {
        "wt": {},
        "mfr": {},
        "balances": {},
        "bodies": {},
        "grips": {},
        "barrels": {},
        "materials": {},
        "elements": {},
        "accessories": [],
        "accessory_none": _find("WeaponPartDefinition", "GD_Weap_Pistol.Accessory.Pistol_Accessory_None"),
        "visible_accessories": [],
        "crit_effect_template": None,
        "firing_mode": (
            _find("FiringModeDefinition", "GD_Weap_Shotgun.FiringModes.Bullet_Shotgun_TidalWave")
            or _find("FiringModeDefinition", "GD_Weap_Pistol.FiringModes.Bullet_Pistol_Default")
        ),
        "title_template": _find("WeaponNamePartDefinition", "GD_Weap_Pistol.Name.Title_Hyperion.Title__Shooter"),
        "prefix_template": _find(
            "WeaponNamePartDefinition",
            "GD_Weap_Pistol.Name.Prefix_Hyperion.Prefix__AutoCorrecting",
        ),
    }

    for maker in Manufacturer:
        mname = maker.value
        wt = _find("WeaponTypeDefinition", f"GD_Weap_Pistol.A_Weapons.WeaponType_{mname}_Pistol")
        mfr = _find("ManufacturerDefinition", f"GD_Manufacturers.Manufacturers.{mname}")
        donors["wt"][maker] = wt
        donors["mfr"][maker] = mfr
        STATE.maker_to_donor_manufacturer[maker] = mfr

        if wt is not None:
            if donors["title_template"] is None:
                tlist = list(getattr(wt, "TitleList", []) or [])
                if tlist:
                    donors["title_template"] = tlist[0]
            if donors["prefix_template"] is None:
                plist = list(getattr(wt, "PrefixList", []) or [])
                if plist:
                    donors["prefix_template"] = plist[0]

        for rarity, suffixes in _RARITY_DONOR_SUFFIX.items():
            bal = None
            for suf in suffixes:
                bal_name = f"Pistol_{mname}" if not suf else f"Pistol_{mname}_{suf}"
                bal = _find("WeaponBalanceDefinition", f"GD_Weap_Pistol.A_Weapons.{bal_name}")
                if bal is not None:
                    break
            if bal is not None:
                donors["balances"][(maker, rarity)] = bal
                plc = _inherited_plc(bal)
                if plc is not None:
                    _extract_parts_from_plc(maker, rarity, plc, donors)

    # Harvest a WeaponExtraCriticalHitDamage effect row from Jakobs barrel/type if present
    for cand in list(donors["barrels"].values()) + list(donors["wt"].values()):
        if cand is None or donors["crit_effect_template"] is not None:
            continue
        for field_name in ("WeaponAttributeEffects", "ExternalAttributeEffects"):
            for row in getattr(cand, field_name, []) or []:
                attr_p = obj_path(getattr(row, "AttributeToModify", None)).lower()
                if "crit" in attr_p:
                    donors["crit_effect_template"] = (field_name, row)
                    break

    return donors


def _extract_parts_from_plc(maker: Manufacturer, rarity: Rarity, plc: Any, donors: dict[str, Any]) -> None:
    def _parts_in_slot(slot_name: str) -> list[Any]:
        slot = getattr(plc, slot_name, None)
        if slot is None:
            return []
        out = []
        for wp in getattr(slot, "WeightedParts", []) or []:
            part = getattr(wp, "Part", None)
            if part is not None:
                out.append(part)
        return out

    bodies = _parts_in_slot("BodyPartData")
    if bodies and maker not in donors["bodies"]:
        donors["bodies"][maker] = bodies[0]

    for gpart in _parts_in_slot("GripPartData"):
        pname = obj_path(gpart).lower()
        for gmaker in Manufacturer:
            if gmaker.value.lower() in pname and gmaker not in donors["grips"]:
                donors["grips"][gmaker] = gpart

    for bpart in _parts_in_slot("BarrelPartData"):
        pname = obj_path(bpart).lower()
        for bmaker in Manufacturer:
            if bmaker.value.lower() in pname and bmaker not in donors["barrels"]:
                donors["barrels"][bmaker] = bpart

    mats = _parts_in_slot("MaterialPartData")
    if mats and (maker, rarity) not in donors["materials"]:
        donors["materials"][(maker, rarity)] = mats[0]

    for epart in _parts_in_slot("ElementalPartData"):
        pname = obj_path(epart).lower()
        if "fire" in pname or "incendiary" in pname:
            donors["elements"].setdefault(Element.FIRE, epart)
        elif "shock" in pname:
            donors["elements"].setdefault(Element.SHOCK, epart)
        elif "corrosive" in pname:
            donors["elements"].setdefault(Element.CORROSIVE, epart)
        elif "slag" in pname or "amp" in pname:
            donors["elements"].setdefault(Element.SLAG, epart)
        elif "explosive" in pname:
            donors["elements"].setdefault(Element.EXPLOSIVE, epart)
        elif "none" in pname:
            donors["elements"].setdefault(Element.NONE, epart)

    for apart in _parts_in_slot("Accessory1PartData"):
        if apart not in donors["accessories"]:
            donors["accessories"].append(apart)
        apath_low = obj_path(apart).lower()
        if apath_low.endswith("_none") or ".pistol_accessory_none" in apath_low:
            if donors["accessory_none"] is None:
                donors["accessory_none"] = apart
        elif apart not in donors["visible_accessories"]:
            donors["visible_accessories"].append(apart)


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


def _clear_array_prop(obj: Any, prop_name: str) -> None:
    if hasattr(obj, prop_name):
        try:
            arr = getattr(obj, prop_name)
            if hasattr(arr, "clear"):
                arr.clear()
            else:
                setattr(obj, prop_name, [])
        except Exception:
            pass


def _zero_vector_struct(obj: Any, field_name: str) -> None:
    if not hasattr(obj, field_name):
        return
    try:
        vec = getattr(obj, field_name)
        if vec is None:
            return
        vcopy = copy.copy(vec)
        vcopy.X = 0.0
        vcopy.Y = 0.0
        vcopy.Z = 0.0
        setattr(obj, field_name, vcopy)
    except Exception:
        try:
            vec = getattr(obj, field_name)
            vec.X = 0.0
            vec.Y = 0.0
            vec.Z = 0.0
        except Exception:
            pass


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
    fm.AirFriction = 0.0
    if hasattr(fm, "RicochetFriction"):
        fm.RicochetFriction = 0.0
    fm.PartSysTemplate = None
    fm.ConditionalPartSysTemplate = None
    if hasattr(fm, "SoundDefinition"):
        fm.SoundDefinition = None
    fm.bSuppressWeaponSpread = True
    if hasattr(fm, "bFireRandomlyFromPattern"):
        fm.bFireRandomlyFromPattern = False
    if hasattr(fm, "bResetPatternAfterEachShot"):
        fm.bResetPatternAfterEachShot = True
    if hasattr(fm, "bScalePatternByWeaponSpread"):
        fm.bScalePatternByWeaponSpread = False
    fm.bPenetratePawn = False
    fm.NumRicochets = 0
    for arr_field in ("FiringPatternLines", "OnAnyImpact", "TimerEvents"):
        _clear_array_prop(fm, arr_field)
    for vec_field in ("Acceleration", "WaveFreq", "WaveAmp", "WavePhase", "TraceExtent"):
        _zero_vector_struct(fm, vec_field)


def _attach_torgue_explode_on_impact(fm_explosive: Any, pkg: Any) -> None:
    """Attach a 180 UU (50% splash) `Behavior_Explode` to `FM_Melee_Elem_EXPLOSIVE` when available."""
    try:
        unrealsdk.load_package("GD_Weap_Shotgun")
    except Exception:
        pass
    try:
        donor_behavior = None
        flakker = _find(
            "FiringModeDefinition",
            "GD_Weap_Shotgun.FiringModes.Bullets_Shotgun_Torgue_Flakker_Child",
        )
        candidates = [flakker] if flakker is not None else []
        if not candidates:
            candidates = list(unrealsdk.find_all("FiringModeDefinition") or [])
        for mode in candidates:
            if mode is None:
                continue
            for ev in getattr(mode, "TimerEvents", []) or []:
                resp = getattr(ev, "Response", None)
                for beh in getattr(resp, "Behaviors", []) or []:
                    if beh is not None and str(getattr(beh.Class, "Name", "")) == "Behavior_Explode":
                        donor_behavior = beh
                        break
                if donor_behavior is not None:
                    break
            if donor_behavior is not None:
                break

        if donor_behavior is None:
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


def _deg_to_rot_units(deg: float) -> int:
    return int(round(float(deg) * 65536.0 / 360.0))


def _dump_uobject_fields_summary(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if obj is None:
        return out
    try:
        cls = getattr(obj, "Class", None)
        if cls is None or not hasattr(cls, "_properties"):
            return out
        for prop in cls._properties():
            pname = str(getattr(prop, "Name", ""))
            if not pname or pname.startswith("VfTable"):
                continue
            try:
                val = getattr(obj, pname)
                if hasattr(val, "Pitch") and hasattr(val, "Yaw") and hasattr(val, "Roll"):
                    out[pname] = {"Pitch": int(val.Pitch), "Yaw": int(val.Yaw), "Roll": int(val.Roll)}
                elif hasattr(val, "X") and hasattr(val, "Y") and hasattr(val, "Z"):
                    out[pname] = {"X": round(float(val.X), 4), "Y": round(float(val.Y), 4), "Z": round(float(val.Z), 4)}
                elif isinstance(val, (int, float, bool, str)) or val is None:
                    out[pname] = val
                else:
                    out[pname] = obj_path(val) or repr(val)
            except Exception:
                continue
    except Exception:
        pass
    return out


def apply_ui_preview_settings_to_all_wts() -> None:
    """Orient Melee Weapons horizontally & zoom framing closer in Inventory / Inspect previews."""
    pitch_ru = _deg_to_rot_units(STATE.preview_pitch_deg)
    yaw_ru = _deg_to_rot_units(STATE.preview_yaw_deg)
    roll_ru = _deg_to_rot_units(STATE.preview_roll_deg)
    focus_rad = float(STATE.preview_focus_radius)

    for wt in STATE.maker_to_wt.values():
        if wt is None:
            continue
        if hasattr(wt, "bUIMeshRotationNeedsTransformFixup"):
            try:
                wt.bUIMeshRotationNeedsTransformFixup = False
            except Exception:
                pass
        if hasattr(wt, "UIMeshRotation"):
            try:
                rot = copy.copy(wt.UIMeshRotation)
                rot.Pitch = pitch_ru
                rot.Yaw = yaw_ru
                rot.Roll = roll_ru
                wt.UIMeshRotation = rot
            except Exception:
                try:
                    wt.UIMeshRotation = unrealsdk.make_struct(
                        "Rotator",
                        Pitch=pitch_ru,
                        Yaw=yaw_ru,
                        Roll=roll_ru,
                    )
                except Exception:
                    pass
        if hasattr(wt, "FocusRadius"):
            try:
                wt.FocusRadius = focus_rad
            except Exception:
                pass
        if hasattr(wt, "MinimumVerticalPercentage"):
            try:
                wt.MinimumVerticalPercentage = 0.0
            except Exception:
                pass
        if hasattr(wt, "MinimumHorizontalPercentage"):
            try:
                wt.MinimumHorizontalPercentage = 0.85
            except Exception:
                pass
        if hasattr(wt, "ThirdPersonMeshScale"):
            try:
                wt.ThirdPersonMeshScale = 1.05
            except Exception:
                pass
        if STATE.ui_presentation is not None and hasattr(wt, "Presentation"):
            try:
                wt.Presentation = STATE.ui_presentation
            except Exception:
                pass

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
    if hasattr(np, "bNameIsUnique"):
        np.bNameIsUnique = bool(is_title and text)
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
    if hasattr(part, "CustomFiringModeDefinition"):
        try:
            part.CustomFiringModeDefinition = None
        except Exception:
            pass


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
    except Exception:
        pass


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

        root_pkg = _ensure_subpackage(None, "MeleeArsenalDev")
        STATE.root_package = root_pkg

        wt_pkg = _ensure_subpackage(root_pkg, "WeaponTypes")
        fm_pkg = _ensure_subpackage(root_pkg, "FiringModes")
        blade_pkg = _ensure_subpackage(root_pkg, "Blades")
        grip_pkg = _ensure_subpackage(root_pkg, "Grips")
        body_pkg = _ensure_subpackage(root_pkg, "Bodies")
        acc_pkg = _ensure_subpackage(root_pkg, "Accessories")
        elem_pkg = _ensure_subpackage(root_pkg, "Elements")
        mats_pkg = _ensure_subpackage(root_pkg, "Materials")
        names_pkg = _ensure_subpackage(root_pkg, "Names")
        bal_pkg = _ensure_subpackage(root_pkg, "Balances")

        # 1. Build Title & Prefix WeaponNamePartDefinitions
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
            dt_def = STATE.damage_types.get(elem) or STATE.damage_types.get(Element.NONE)
            if dt_def is not None and hasattr(efm, "DamageTypeDefinition"):
                efm.DamageTypeDefinition = dt_def
            if elem == Element.EXPLOSIVE:
                _attach_torgue_explode_on_impact(efm, fm_pkg)
            STATE.element_to_firing_mode[elem] = efm

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

            dt = STATE.damage_types.get(espec.element)
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

        # 9. Build 20 WeaponBalanceDefinitions, WeaponPartListCollections & Drop ItemPools
        pools_pkg = _ensure_subpackage(root_pkg, "Pools")
        drop_pool_donor = (
            _find("ItemPoolDefinition", "GD_Itempools.Runnables.Pool_MadameVonBartlesby")
            or _find("ItemPoolDefinition", "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_01_Common")
            or _find("ItemPoolDefinition", "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_02_Uncommon")
        )
        all_donor_mfr_source = next(
            (b for b in donors["balances"].values() if _inherited_manufacturers(b)),
            None,
        )

        for maker, mspec in MANUFACTURERS.items():
            blade_id = BLADE_BY_MANUFACTURER[maker].part_id
            blade_part = STATE.blade_parts[blade_id]
            body_part = STATE.body_parts.get(maker)
            wt_obj = STATE.maker_to_wt[maker]
            mfr_obj = STATE.maker_to_donor_manufacturer.get(maker)
            maker_common_bal = donors["balances"].get((maker, Rarity.COMMON)) or all_donor_mfr_source

            allowed_elem_parts = [
                (
                    resolve_fitted_element_part(espec.part_id, blade_id=blade_id),
                    8.0 if espec.element == Element.NONE else 3.0,
                )
                for espec in ELEMENTS.values()
                if espec.element in mspec.allowed_elements
            ]
            grip_weighted = [
                (resolve_fitted_grip_part(gid, blade_id=blade_id), float(GRIPS[gid].weight))
                for gid in STATE.grip_parts
            ]

            for rarity in (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.VERY_RARE):
                real_acc_parts = [
                    (
                        resolve_fitted_accessory_part(aspec.part_id, blade_id=blade_id),
                        float(aspec.weight),
                    )
                    for aspec in ACCESSORIES.values()
                    if aspec.part_id != "Acc_None"
                    and rarity >= aspec.min_rarity
                    and not (aspec.elemental_only and mspec.element_policy == ElementPolicy.NEVER)
                    and not (
                        aspec.cleave_only
                        and mspec.cleave_profile.mode != TargetingMode.FRONTAL_CLEAVE
                    )
                    and not (
                        aspec.single_target_only
                        and mspec.cleave_profile.mode != TargetingMode.SINGLE_TARGET
                    )
                ]
                acc_chance = float(ACCESSORY_ROLL_CHANCE.get(rarity, 0.25))
                none_acc_part = STATE.accessory_parts.get("Acc_None")
                if acc_chance >= 1.0 and real_acc_parts:
                    allowed_acc_parts = real_acc_parts
                elif acc_chance <= 0.0 or not real_acc_parts:
                    allowed_acc_parts = [(none_acc_part, 1.0)] if none_acc_part is not None else real_acc_parts
                else:
                    real_w_sum = sum(w for _, w in real_acc_parts)
                    none_w = round(real_w_sum * (1.0 - acc_chance) / acc_chance, 3)
                    allowed_acc_parts = (
                        [(none_acc_part, none_w), *real_acc_parts]
                        if none_acc_part is not None
                        else real_acc_parts
                    )
                donor_bal = donors["balances"].get((maker, rarity)) or next(
                    iter(donors["balances"].values())
                )
                donor_plc = _inherited_plc(donor_bal) or _inherited_plc(maker_common_bal)
                mat_part = (
                    resolve_fitted_material_part(maker, rarity)
                    or next(iter(STATE.material_parts.values()), None)
                )

                plc_name = f"PLC_Melee_{maker.value}_{rarity.name}"
                plc = _find("WeaponPartListCollectionDefinition", f"{obj_path(bal_pkg)}.{plc_name}")
                if plc is None:
                    plc = unrealsdk.construct_object(
                        "WeaponPartListCollectionDefinition",
                        bal_pkg,
                        plc_name,
                        KEEP_ALIVE_FLAGS,
                        donor_plc,
                    )
                    STATE.keep_alive(plc)

                plc.AssociatedWeaponType = wt_obj
                _configure_part_list_collection(
                    plc,
                    donor_plc,
                    body_part=body_part,
                    blade_part=blade_part,
                    mat_part=mat_part,
                    grip_weighted=grip_weighted,
                    elem_weighted=allowed_elem_parts,
                    acc_weighted=allowed_acc_parts,
                )

                bal_name = f"Balance_Melee_{maker.value}_{rarity.name}"
                bal = _find("WeaponBalanceDefinition", f"{obj_path(bal_pkg)}.{bal_name}")
                if bal is None:
                    bal = unrealsdk.construct_object(
                        "WeaponBalanceDefinition",
                        bal_pkg,
                        bal_name,
                        KEEP_ALIVE_FLAGS,
                        donor_bal,
                    )
                    STATE.keep_alive(bal)

                _configure_balance_definition(
                    bal,
                    donor_bal,
                    wt_obj,
                    mfr_obj,
                    plc,
                    fallback_bal=maker_common_bal,
                )
                STATE.balances[(maker, rarity)] = bal
                STATE.balance_path_to_meta[obj_path(bal)] = (maker, rarity)

                # Build dedicated 100%-probability ItemPoolDefinition for ground-drop spawning
                if drop_pool_donor is not None:
                    pool_name = f"Pool_Drop_{maker.value}_{rarity.name}"
                    dpool = _find("ItemPoolDefinition", f"{obj_path(pools_pkg)}.{pool_name}")
                    if dpool is None:
                        dpool = unrealsdk.construct_object(
                            "ItemPoolDefinition",
                            pools_pkg,
                            pool_name,
                            KEEP_ALIVE_FLAGS,
                            drop_pool_donor,
                        )
                        STATE.keep_alive(dpool)
                    # Clear any inherited GameStage gates (`Gamestage_02` / `Gamestage_07`) from donor pistol pools
                    for gate_attr in ("MinGameStageRequirement", "MaxGameStageRequirement"):
                        if hasattr(dpool, gate_attr):
                            try:
                                setattr(dpool, gate_attr, None)
                            except Exception:
                                pass
                    if hasattr(dpool, "bAutoReadyItems"):
                        try:
                            dpool.bAutoReadyItems = False
                        except Exception:
                            pass
                    if hasattr(dpool, "Quantity"):
                        try:
                            q = copy.copy(dpool.Quantity)
                            q.BaseValueConstant = 1.0
                            q.BaseValueAttribute = None
                            q.InitializationDefinition = None
                            q.BaseValueScaleConstant = 1.0
                            dpool.Quantity = q
                        except Exception:
                            pass
                    donor_items = list(getattr(drop_pool_donor, "BalancedItems", []) or [])
                    if donor_items:
                        entry = copy.copy(donor_items[0])
                        entry.InvBalanceDefinition = bal
                        entry.ItmPoolDefinition = None
                        entry.bDropOnDeath = True
                        prob = copy.copy(entry.Probability)
                        prob.BaseValueConstant = 1.0
                        prob.BaseValueAttribute = None
                        prob.InitializationDefinition = None
                        prob.BaseValueScaleConstant = 1.0
                        entry.Probability = prob
                        assign_struct_array(dpool, "BalancedItems", [entry])
                    STATE.balance_drop_pools[(maker, rarity)] = dpool

        # Build reusable Behavior_SpawnLootAroundPoint for zero-XP drops in front of player
        if STATE.drop_spawner is None:
            try:
                spawner = _find("Behavior_SpawnLootAroundPoint", f"{obj_path(pools_pkg)}.MeleeDropSpawner")
                if spawner is None:
                    spawner = unrealsdk.construct_object(
                        "Behavior_SpawnLootAroundPoint",
                        pools_pkg,
                        "MeleeDropSpawner",
                        KEEP_ALIVE_FLAGS,
                        None,
                    )
                STATE.drop_spawner = STATE.keep_alive(spawner)
            except Exception as sp_exc:
                log_warn(f"Could not construct Behavior_SpawnLootAroundPoint: {sp_exc!r}")

        # 10. Register all custom assets in AssetLibraryManager (`AssLibMan`) for save/load
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


def _find_template_weighted_row(donor_plc: Any) -> Any:
    for slot_name in (
        "BarrelPartData",
        "GripPartData",
        "BodyPartData",
        "MaterialPartData",
        "ElementalPartData",
        "Accessory1PartData",
    ):
        slot = getattr(donor_plc, slot_name, None)
        if slot is not None:
            rows = list(getattr(slot, "WeightedParts", []) or [])
            if rows:
                return rows[0]
    return None


def _find_template_manufacturer_weight(donor_plc: Any) -> Any:
    for slot_name in (
        "ElementalPartData",
        "Accessory1PartData",
        "GripPartData",
        "BarrelPartData",
        "BodyPartData",
        "MaterialPartData",
        "SightPartData",
        "StockPartData",
    ):
        slot = getattr(donor_plc, slot_name, None)
        if slot is None:
            continue
        for row in getattr(slot, "WeightedParts", []) or []:
            mfrs = list(getattr(row, "Manufacturers", []) or [])
            if mfrs:
                return mfrs[0]
    return None


def _configure_part_list_collection(
    plc: Any,
    donor_plc: Any,
    body_part: Any,
    blade_part: Any,
    mat_part: Any,
    grip_weighted: list[tuple[Any, float]],
    elem_weighted: list[tuple[Any, float]],
    acc_weighted: list[tuple[Any, float]],
) -> None:
    donor_init = list(getattr(donor_plc, "ConsolidatedAttributeInitData", []) or [])
    if not donor_init:
        init_list = [
            unrealsdk.make_struct(
                "AttributeInitializationData",
                BaseValueConstant=1.0,
                BaseValueAttribute=None,
                InitializationDefinition=None,
                BaseValueScaleConstant=1.0,
            )
        ]
    else:
        init_list = [copy.copy(row) for row in donor_init]

    init_template = init_list[0]
    weight_to_idx: dict[float, int] = {}

    def _weight_index(w: float) -> int:
        key = round(float(w), 3)
        if key in weight_to_idx:
            return weight_to_idx[key]
        entry = copy.copy(init_template)
        entry.BaseValueConstant = key
        entry.BaseValueAttribute = None
        entry.InitializationDefinition = None
        entry.BaseValueScaleConstant = 1.0
        idx = len(init_list)
        init_list.append(entry)
        weight_to_idx[key] = idx
        return idx

    min_stage_idx = _weight_index(1.0)
    max_stage_idx = _weight_index(10000.0)
    tpl_row = _find_template_weighted_row(donor_plc)
    tpl_mfr_w = _find_template_manufacturer_weight(donor_plc)

    # Native `Borderlands2.exe` (`ChooseRandomParts`) ignores `row.DefaultWeightIndex` and substitutes
    # a flat weight of `100` whenever `row.Manufacturers` is empty! Supplying a single entry with
    # `Manufacturer=None` and `DefaultWeightIndex=min_stage_idx` (`1.0`) instructs native part selection
    # to multiply `row.DefaultWeightIndex * 1.0`, honoring our exact Grip, Element (`8:3`), and Accessory weights.
    mfr_weight_one: Any = None
    if tpl_mfr_w is not None:
        try:
            mw = copy.copy(tpl_mfr_w)
            mw.Manufacturer = None
            mw.DefaultWeightIndex = min_stage_idx
            if hasattr(mw, "MinGameStageIndex"):
                mw.MinGameStageIndex = min_stage_idx
            if hasattr(mw, "MaxGameStageIndex"):
                mw.MaxGameStageIndex = max_stage_idx
            mfr_weight_one = mw
        except Exception:
            mfr_weight_one = None
    if mfr_weight_one is None and unrealsdk is not None:
        try:
            mfr_weight_one = unrealsdk.make_struct(
                "ManufacturerCustomPartWeightData",
                Manufacturer=None,
                MinGameStageIndex=min_stage_idx,
                MaxGameStageIndex=max_stage_idx,
                DefaultWeightIndex=min_stage_idx,
            )
        except Exception:
            mfr_weight_one = None

    def _set_slot(slot_name: str, weighted_items: list[tuple[Any, float]]) -> None:
        slot = getattr(plc, slot_name, None)
        if slot is None:
            return
        valid = [(p, w) for (p, w) in weighted_items if p is not None and w > 0]
        if not valid or tpl_row is None:
            slot.bEnabled = False
            assign_struct_array(slot, "WeightedParts", [])
            return
        rows = []
        for part_obj, weight_val in valid:
            row = copy.copy(tpl_row)
            row.Part = part_obj
            assign_struct_array(
                row,
                "Manufacturers",
                [copy.copy(mfr_weight_one)] if mfr_weight_one is not None else [],
            )
            row.MinGameStageIndex = min_stage_idx
            row.MaxGameStageIndex = max_stage_idx
            row.DefaultWeightIndex = _weight_index(weight_val)
            rows.append(row)
        slot.bEnabled = True
        assign_struct_array(slot, "WeightedParts", rows)

    _set_slot("BodyPartData", [(body_part, 1.0)])
    _set_slot("BarrelPartData", [(blade_part, 1.0)])
    _set_slot("MaterialPartData", [(mat_part, 1.0)])
    _set_slot("GripPartData", grip_weighted)
    _set_slot("ElementalPartData", elem_weighted)
    _set_slot("Accessory1PartData", acc_weighted)
    _set_slot("Accessory2PartData", [])
    _set_slot("SightPartData", [])
    _set_slot("StockPartData", [])

    assign_struct_array(plc, "ConsolidatedAttributeInitData", init_list)


def _configure_balance_definition(
    bal: Any,
    donor_bal: Any,
    wt_obj: Any,
    mfr_obj: Any,
    plc: Any,
    fallback_bal: Any = None,
) -> None:
    bal.BaseDefinition = None
    bal.InventoryDefinition = wt_obj
    bal.RuntimePartListCollection = plc
    bal.WeaponPartListCollection = plc

    donor_mfrs = _inherited_manufacturers(donor_bal) or _inherited_manufacturers(fallback_bal)
    if donor_mfrs:
        mfr_entry = copy.copy(donor_mfrs[0])
        if mfr_obj is not None:
            mfr_entry.Manufacturer = mfr_obj
        grades = list(getattr(mfr_entry, "Grades", []) or [])
        if grades:
            g0 = copy.copy(grades[0])
            if hasattr(g0, "GameStageRequirement"):
                req = copy.copy(g0.GameStageRequirement)
                req.MinGameStage = 1
                req.MaxGameStage = 10000
                g0.GameStageRequirement = req
            assign_struct_array(mfr_entry, "Grades", [g0])
        assign_struct_array(bal, "Manufacturers", [mfr_entry])
