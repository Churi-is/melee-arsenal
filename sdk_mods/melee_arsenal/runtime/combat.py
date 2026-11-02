"""Live BL2 Melee Weapon Combat, Wall-Occluded Cleave & First-Person `Bladed_Melee` Engine (v0.2.6).

Built 100% on empirical results from `melee_combat_probe_report.json` (Sanctuary live capture):

1. **Synchronous Wall-Occluded Hit Detection & Weakspot Headshot Crits (`CalcWeaponFire`)**:
   - `WillowWeapon.CalcWeaponFire(StartTrace, EndTrace, ImpactList=[], Extent=(0,0,0), bTestTrace=False)`
     returns `(ImpactInfo, ImpactList)` synchronously on frame 0 with:
       * `ImpactInfo.HitActor` (enemy `WillowAIPawn`, `Pawn_TargetDummy`, destructible barrel/pile, or world wall)
       * `ImpactInfo.HitLocation` (exact 3D impact point)
       * `ImpactInfo.HitInfo.BoneName` (`Head`, `Eye`, `Crit`, `Hips`, `Torso`, `Neck`, ...)
   - Zero reliance on `WillowLightProjectileManager` (which BL2 only spawns lazily after a vanilla gun shot).
   - `SINGLE_TARGET` (Hyperion Katana, Jakobs Sabre): checks exact crosshair ray first (with `+75%` / `+100%`
     weakspot headshot crit multiplier when hitting a crit bone!), plus a `22.5°` half-angle (`45°` total,
     matching Gearbox's `BehaviorData_PlayerMeleeAttackWithBladedWeapon`) wall-checked blade-slash assist so
     close-quarters slashes never whiff between an enemy's limbs.
   - `FRONTAL_CLEAVE` (Bandit Machete `95°`, Maliwan Phaseblade `110°`, Torgue Blast-Cleaver `85°`):
     strikes every living hostile + Target Dummy inside `card.range_uu` and `card.cleave_arc_degrees`
     whose `CalcWeaponFire` check confirms no wall/cover blocks line-of-sight!

2. **Instant Native Damage, Elemental Multipliers, Status DoTs & Torgue Splash**:
   - `WillowPawn.Behavior_CauseDamage(...)` with `WillowDmgSource_Bullet` (never `WillowDmgSource_Melee`,
     avoiding BL2 Gotcha #3 freeze) applies immediate shield/health damage, floating numbers, native
     flesh/armor/shield multipliers (`1.5x` Fire on Flesh verified to 4 decimal places), kill skills, and XP.
   - Rolls `card.elemental_chance_pct` per target between `DmgType_<Elem>_Impact` (procs Burn / Shock /
     Corrode / Slag DoT) and `DmgType_<Elem>_Impact_NoDoT` (full elemental hit damage without guaranteed DoT).
   - `WillowPawn.Behavior_CauseRadiusDamage(...)` with `WillowDmgSource_Grenade` +
     `DmgType_Explosive_Impact_ForceFlinch` triggers Torgue Blast-Cleaver's `1.8m` (`180 UU`) 50% splash!

3. **Authentic First-Person Blade Swing Animation (`Bladed_Melee` on `Pawn.Arms` `WeaponAction` Slot)**:
   - Plays Gearbox's shared `Anim_1st_Person.1st_Person_Pistol: Bladed_Melee` (`0.6333s`, present on ALL 6
     Vault Hunters; measured `65.2 UU` 3D hand-and-blade sweep in `melee_combat_probe_report.json`) directly
     on `Pawn.Arms`'s `WeaponAction` `AnimNodeSlot` scaled to your weapon's exact `Swing Speed`!
   - Suppresses gun recoil (`WeaponRecoil.StopCustomAnim`, `PlayFireEffects` `Block`, `WeaponFired` `Block`),
     layers manufacturer-tuned `CameraAnim` shakes + blade whoosh/impact `AkEvent`s, and falls back to
     `Arms.SkeletalMesh.RotOrigin` + `Arms.ForceUpdate(False)` if a modded rig ever lacks `Bladed_Melee`.
   - Regular melee (`[V]`) is completely untouched so Maya Scorn, Zer0 Execute, Krieg Buzz Axe, etc. work natively.

4. **Manufacturer Secondary Attacks (RMB / Left Trigger, `runtime/secondary.py`)**:
   - Bandit Machetes and Torgue Blast-Cleavers are **thrown** along a ballistic arc whose speed,
     loft, flight time and reach come from the weapon's real part mass (blade + grip manufacturer
     hardware + accessory). A thrown blade strikes for exactly `200%` of its normal LMB hit, then
     rebounds to your hand after an impact — or after its arc expires — and Primary Fire is locked
     out for the whole flight.
   - Hyperion Katanas and Jakobs Sabres **guard** while RMB is held: the next incoming hit has the
     blade's rolled card Damage Reduction applied twice, and any hit inside the first `0.33 s`
     deals zero damage and is reflected back at a melee attacker as a forced critical hit.
   - Maliwan Phaseblades have no secondary attack; RMB stays inert.

5. **Defensive Blade Guard: Level- & Part-Scaled Damage Reduction While Equipped**:
   - Every Melee Weapon rolls an incoming-damage reduction (printed on its item card) that protects you
     while it is the held `Pawn.Weapon`. The floor climbs from `30%` at Lv 1 to `61%` at Lv 80 + OP10,
     with parts deciding the rest up to a `70%` cap (`76%` for unique rarities).
   - `WillowPlayerPawn:TakeDamage` PRE blocks the incoming hit and re-calls the same function with
     `Damage * (1 - reduction)` inside `prevent_hooking_direct_calls()`, so BL2 still handles the
     shield/health split, flinch, hit reactions, and FFYL exactly as normal. Only the local player's own
     pawn is ever modified, and any unexpected state falls through to untouched vanilla damage.
"""
from __future__ import annotations

import copy
import math
import random
import time
from typing import Any

import unrealsdk
from mods_base import get_pc
from unrealsdk.hooks import Block, prevent_hooking_direct_calls

from ..core.enums import Element, Manufacturer, SecondaryAttack, TargetingMode
from ..core.schemas import MeleeWeaponInstance, PlayerMeleeContext
from ..core.stats import (
    ComputedCardStats,
    apply_incoming_damage_reduction,
    compute_attack_damage,
)
from ..core.targeting import TargetCandidate, Vec3
from .log import log_error, log_info
from .resolver import get_local_player_controller, resolve_melee_weapon
from .secondary import (
    begin_throw,
    handle_guard_incoming_damage,
    is_weapon_thrown,
    try_begin_guard,
)
from .state import STATE

DEAD_OBJECT_FLAGS = 0x0000200000000000 | 0x0000000200000000 | 0x8000 | 0x10000

ATTR_MELEE_MODIFIER = "D_Attributes.DamageSourceModifiers.InstigatedMeleeDamageModifier"
ATTR_ROID_BUFF = "D_Attributes.DamageSourceModifiers.InstigatedMeleeDamageAddedBuff"
ATTR_STATUS_DMG_MOD = "D_Attributes.DamageSourceModifiers.InstigatedStatusEffectDamageModifier"
ATTR_STATUS_CHANCE_MOD = "D_Attributes.DamageSourceModifiers.InstigatedStatusEffectChanceModifier"

# Verified WillowDamageTypeDefinition paths (with & without StatusEffect DoT proc!)
ELEM_DMG_TYPE_PROC_PATHS: dict[Element, str] = {
    Element.NONE: "GD_Impact.DamageType.DmgType_Normal",
    Element.FIRE: "GD_Incendiary.DamageType.DmgType_Incendiary_Impact",
    Element.SHOCK: "GD_Shock.DamageType.DmgType_Shock_Impact",
    Element.CORROSIVE: "GD_Corrosive.DamageType.DmgType_Corrosive_Impact",
    Element.SLAG: "GD_Amp.DamageType.DmgType_Amp_Impact",
    Element.EXPLOSIVE: "GD_Explosive.DamageType.DmgType_Explosive_Impact_ForceFlinch",
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

BLADED_MELEE_SEQ_LENGTH = 0.6333
SINGLE_TARGET_ASSIST_HALF_DEG = 22.5  # Matches Gearbox `BehaviorData_PlayerMeleeAttackWithBladedWeapon` (45° cone)

# CameraAnim tuning. The old values (scale 0.45-0.90 at rate 0.95-1.35, 40-120ms blends) made fast
# blades read as a hard camera "flick" every swing: a ~100ms ramp read as an instant jump while the
# 240ms fade-out looked smooth, i.e. it eased out but not in. Playback is now slowed well below 1.0x
# (the whole motion, including its opening spike, is stretched), the blend-in window is long enough to
# actually ease (~0.30s == a visible ramp at 60fps), and the fade-out matches it.
CAMERA_SHAKE_BLEND_IN_SEC = 0.30
CAMERA_SHAKE_BLEND_OUT_SEC = 0.32
CAMERA_SHAKE_RATE_HEAVY = 0.65
CAMERA_SHAKE_RATE_CLEAVE = 0.68
CAMERA_SHAKE_RATE_SINGLE = 0.70
CAMERA_SHAKE_CADENCE_REFERENCE = 1.45  # swings/sec at which a shake plays at full strength
CAMERA_SHAKE_CADENCE_FLOOR = 0.45      # never damp a shake below this fraction of its scale

# `MODS -> Swing Camera Shake (%)` is a feel dial, not a raw multiplier: the amplitudes authored below
# are the *loud* reference, and the slider is rescaled by this gain so the setting players preferred
# under the old curve (10%) is exactly what 50% means now — 100% is 2x that, 150% is 3x.
CAMERA_SHAKE_SLIDER_GAIN = 0.20

# `FPlayerRecentDamageEventData.DamageEventFlags` bits (see `WillowDamageTypeDefinition`):
#   bit 0 = critical hit, bit 1 = healing, bit 2 = resisted
DAMAGE_EVENT_FLAG_CRIT = 1

# BL2 only derives the floating "CRITICAL" popup from `DamageEventFlags`, which the engine's damage
# pipeline sets from a real `HitInfo`/hit-region crit. `Behavior_CauseDamage` (what this mod uses to
# apply exact, already-crit-multiplied damage) carries no HitInfo, so without help the crit text never
# appears. We tag each victim we crit and re-issue the vanilla display call with the crit bit forced.
CRIT_FEEDBACK_WINDOW_SEC = 0.75
_PENDING_CRIT_FEEDBACK: dict[int, float] = {}

# ADS zoom stays suppressed on Melee Weapons (see `on_melee_weapon_start_zoom_pre`) because RMB /
# Left Trigger now drives each manufacturer's secondary attack: Bandit & Torgue throw their blade,
# Hyperion & Jakobs guard with it, and Maliwan keeps RMB inert.
MELEE_SECONDARY_ATTACK_ENABLED = True

WEAKSPOT_BONE_KEYWORDS = (
    "head",
    "eye",
    "crit",
    "skull",
    "brain",
    "mouth",
    "jaw",
    "weak",
    "eyeball",
    "pod",
)

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

# Cached `Pawn.Arms` AnimNodeSlots keyed by `id(arms)`:
#   id(arms) -> {"action": WeakPointer|obj, "custom": WeakPointer|obj, "recoil": WeakPointer|obj}
_ARMS_SLOT_CACHE: dict[int, dict[str, Any]] = {}

# Fallback `Arms.SkeletalMesh` rest baseline if procedural fallback is ever used
_ARMS_REST_BASELINE: dict[int, dict[str, Any]] = {}

# Live first-person posture for the secondary attacks ("" = normal idle hands):
#   POSE_GUARD  — blade braced across the view while RMB is held
#   POSE_THROWN — hands emptied out of frame after the blade leaves them
# The pose shares `_ARMS_REST_BASELINE` with the fallback slash arc, so every offset is applied
# relative to the untouched rest transform and can always be restored exactly.
_POSE_KIND: str = ""
_POSE_STARTED_AT: float = 0.0

POSE_GUARD = "guard"
POSE_THROWN = "thrown"

# A pose must never outlive its own state; this is the last-resort ceiling for the breathing guard.
POSE_MAX_HOLD_SEC: float = 12.0

# Offsets in UE3 rotator units (65536/360 per degree) / UU, added to the captured rest transform.
#
# These are deliberately small: the first-person arms mesh pivots well behind the hands, so a
# large `RotOrigin` delta swings the hands straight out of frame (that is exactly what a 25 degree
# guard rotation did in playtesting). The guard therefore tilts the blade only ~8-10 degrees with
# no translation, and the thrown pose is pure downward translation (no rotation at all), which can
# always be restored exactly.
_POSE_OFFSETS: dict[str, dict[str, float]] = {
    # Positive pitch lifts the blade, negative yaw swings it inward toward the crosshair (the old
    # signs leaned the sword away from the middle of the screen). No translation: the hands stay
    # on screen.
    POSE_GUARD: {"pitch": 1500.0, "yaw": -1200.0, "roll": -1000.0, "x": 0.0, "y": 0.0, "z": 0.0},
    POSE_THROWN: {"pitch": 0.0, "yaw": 0.0, "roll": 0.0, "x": 0.0, "y": 4.0, "z": -75.0},
}

_SHOT_SEQ: int = 0
_HANDLED_SHOT_SEQ: int = 0
_LAST_SWING_AT: float = 0.0
_FIRST_SWING_LOGGED: bool = False


# ---------------------------------------------------------------------------
# Safe Object, WeakPointer & Struct Helpers
# ---------------------------------------------------------------------------

def _is_dead(obj: Any) -> bool:
    if obj is None:
        return True
    try:
        flags = getattr(obj, "ObjectFlags", 0)
        return bool(int(flags) & DEAD_OBJECT_FLAGS)
    except Exception:
        return False


def _wrap_weak(obj: Any) -> Any:
    if obj is None:
        return None
    wp_cls = getattr(unrealsdk.unreal, "WeakPointer", None) if hasattr(unrealsdk, "unreal") else None
    if wp_cls is not None:
        try:
            return wp_cls(obj)
        except Exception:
            return obj
    return obj


def _deref_weak(ref: Any) -> Any:
    if ref is None:
        return None
    if callable(ref) and not hasattr(ref, "_path_name") and not hasattr(ref, "PlayCustomAnim"):
        try:
            obj = ref()
            return None if _is_dead(obj) else obj
        except Exception:
            return None
    return None if _is_dead(ref) else ref


def _safe_find(cls_name: str, path: str) -> Any:
    try:
        return unrealsdk.find_object(cls_name, path)
    except Exception:
        return None


def _make_vec_from(template_vec: Any, x: float, y: float, z: float) -> Any:
    if template_vec is not None:
        try:
            v = copy.copy(template_vec)
            v.X = float(x)
            v.Y = float(y)
            v.Z = float(z)
            return v
        except Exception:
            pass
    try:
        return unrealsdk.make_struct("Vector", X=float(x), Y=float(y), Z=float(z))
    except Exception:
        return (float(x), float(y), float(z))


def _s16(val: int) -> int:
    v = int(val) & 0xFFFF
    return v - 0x10000 if (v & 0x8000) else v


def _rotator_add(base: int, delta: float) -> int:
    """Add a delta to a 16-bit UE3 rotator component, wrapping like the engine does."""
    return _s16(int(round(int(base) + float(delta))))


def _rot_to_forward(rot: Any) -> tuple[float, float, float]:
    pitch = _s16(getattr(rot, "Pitch", 0)) * (math.pi / 32768.0)
    yaw = _s16(getattr(rot, "Yaw", 0)) * (math.pi / 32768.0)
    cp = math.cos(pitch)
    return (cp * math.cos(yaw), cp * math.sin(yaw), math.sin(pitch))


# ---------------------------------------------------------------------------
# Combat Asset Cache
# ---------------------------------------------------------------------------

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
            except Exception:
                pass

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
            _CACHED_DMG_TYPES_NODOT[elem] = (
                _safe_find("WillowDamageTypeDefinition", dpath) or _CACHED_DMG_TYPES_PROC.get(elem)
            )

    if _CACHED_MELEE_IMPACT is None:
        _CACHED_MELEE_IMPACT = _safe_find("ImpactDefinition", MELEE_IMPACT_DEF_PATH)

    if _CACHED_BULLET_DMG_CLS is None:
        try:
            _CACHED_BULLET_DMG_CLS = unrealsdk.find_class("WillowDmgSource_Bullet")
        except Exception:
            _CACHED_BULLET_DMG_CLS = None

    if _CACHED_GRENADE_DMG_CLS is None:
        try:
            _CACHED_GRENADE_DMG_CLS = unrealsdk.find_class("WillowDmgSource_Grenade")
        except Exception:
            _CACHED_GRENADE_DMG_CLS = _CACHED_BULLET_DMG_CLS

    _cache_torgue_explosion_assets()


def _score_explosion_ps_path(p: str) -> int:
    low = p.lower()
    if "explo" not in low:
        return -1
    if any(
        bad in low
        for bad in (
            "default__",
            "muzzle",
            "trail",
            "tracer",
            "loop",
            "_lp",
            "ambient",
            "env_",
            "nuke",
            "mongol",
            "norfleet",
            "badaboom",
            "tunguska",
            "blood",
            "gore",
            "corrosive",
            "incendiary",
            "shock",
            "slag",
            "amp",
            "cryo",
            "firehawk",
        )
    ):
        return -1
    score = 10
    if "gyrojet" in low or "gyro" in low:
        score += 65
    if "torgue" in low:
        score += 55
    if "explosive" in low:
        score += 50
    if "fx_wep_explosions" in low or "fxe_" in low:
        score += 30
    if "default" in low or "small" in low or "med" in low:
        score += 25
    if "grenade" in low or "impact" in low:
        score += 18
    return score


def _score_explosion_ak_path(p: str) -> int:
    low = p.lower()
    if "ak_play" not in low or "explo" not in low:
        return -1
    if any(
        bad in low
        for bad in (
            "default__",
            "stop",
            "loop",
            "_lp",
            "tail",
            "reload",
            "equip",
            "ui_",
            "vo_",
            "dlg",
            "nuke",
        )
    ):
        return -1
    score = 10
    if "gyro" in low:
        score += 60
    if "torgue" in low:
        score += 50
    if "wep_explosion" in low or "ake_wep_explosion" in low or "explosions" in low:
        score += 40
    if "default" in low or "small" in low or "med" in low:
        score += 30
    if "grenade" in low or "impact" in low or "rocket" in low:
        score += 20
    return score


def _cache_torgue_explosion_assets() -> None:
    """Discover and cache a real loaded explosive `ParticleSystem` + `AkEvent` once."""
    global _CACHED_EXPLOSION_PS, _CACHED_EXPLOSION_AK, _EXPLOSION_ASSETS_SCANNED
    if _EXPLOSION_ASSETS_SCANNED and (_CACHED_EXPLOSION_PS is not None or _CACHED_EXPLOSION_AK is not None):
        return
    _EXPLOSION_ASSETS_SCANNED = True

    # 1. Prefer exact live-verified Borderlands 2 Startup/base explosion assets from Phase-3 probe (`p06`)
    best_ps = (
        _safe_find("ParticleSystem", "FX_WEP_Explosions.Particles.Default.Part_ExplosiveExplosion_Medium")
        or _safe_find("ParticleSystem", "FX_WEP_Explosions.Particles.Default.Part_ExplosiveExplosion_Small")
    )
    best_ak = (
        _safe_find("AkEvent", "Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_MED")
        or _safe_find("AkEvent", "Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_SM")
        or _safe_find("AkEvent", "Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_LG")
    )

    # 2. Fallback heuristic scan across already-loaded objects if needed
    if best_ps is None:
        best_ps_score = -1
        try:
            for ps in list(unrealsdk.find_all("ParticleSystem", exact=False)):
                if _is_dead(ps):
                    continue
                try:
                    p = str(ps._path_name())
                except Exception:
                    continue
                sc = _score_explosion_ps_path(p)
                if sc > best_ps_score:
                    best_ps_score = sc
                    best_ps = ps
        except Exception:
            pass

    if best_ak is None:
        best_ak_score = -1
        try:
            for ak in list(unrealsdk.find_all("AkEvent", exact=False)):
                if _is_dead(ak):
                    continue
                try:
                    p = str(ak._path_name())
                except Exception:
                    continue
                sc = _score_explosion_ak_path(p)
                if sc > best_ak_score:
                    best_ak_score = sc
                    best_ak = ak
        except Exception:
            pass

    _CACHED_EXPLOSION_PS = best_ps
    _CACHED_EXPLOSION_AK = best_ak
    if best_ps is not None or best_ak is not None:
        ps_name = str(best_ps._path_name()) if best_ps is not None else "None"
        ak_name = str(best_ak._path_name()) if best_ak is not None else "None"
        log_info(f"Cached Torgue Blast-Cleaver explosion FX={ps_name} Audio={ak_name}")


def _eval_attr(attr_path: str, pawn: Any, default: float) -> float:
    ensure_combat_resources_cached()
    attr_obj = _CACHED_ATTRS.get(attr_path)
    if attr_obj is None or pawn is None:
        return default
    try:
        res = attr_obj.GetValue(pawn)
        if isinstance(res, tuple) and len(res) >= 1:
            return float(res[0])
        return float(res)
    except Exception:
        return default


def read_player_melee_context(pawn: Any, pc: Any = None) -> PlayerMeleeContext:
    """Snapshot live player melee skill multipliers, Roid shield flat damage, and UVHM flag."""
    if not STATE.stack_player_melee_bonuses or pawn is None:
        return PlayerMeleeContext()

    melee_mult = max(0.1, _eval_attr(ATTR_MELEE_MODIFIER, pawn, 1.0))
    roid_flat = max(0.0, _eval_attr(ATTR_ROID_BUFF, pawn, 0.0)) if STATE.stack_roid_shields else 0.0
    if STATE.stack_roid_shields and roid_flat == 0.0:
        try:
            roid_flat = max(0.0, float(getattr(pawn, "InstigatedMeleeDamageAdded", 0.0)))
        except Exception:
            roid_flat = 0.0

    status_dmg_mult = max(0.1, _eval_attr(ATTR_STATUS_DMG_MOD, pawn, 1.0))
    status_chance_mult = max(0.1, _eval_attr(ATTR_STATUS_CHANCE_MOD, pawn, 1.0))

    is_uvhm = False
    if pc is not None:
        try:
            is_uvhm = int(pc.GetCurrentPlaythrough()) >= 2
        except Exception:
            is_uvhm = False

    return PlayerMeleeContext(
        melee_damage_scale=melee_mult,
        roid_flat_damage=roid_flat,
        status_damage_scale=status_dmg_mult,
        status_chance_scale=status_chance_mult,
        is_uvhm=is_uvhm,
    )


def read_live_player_melee_context(pc: Any, pawn: Any) -> PlayerMeleeContext:
    """Compatibility wrapper `(pc, pawn)` used by `commands.dump_held_melee_weapon`."""
    return read_player_melee_context(pawn, pc)


# BL2 names the incoming-damage parameter `Damage` on `WillowPawn`/`WillowPlayerPawn:TakeDamage`,
# but tolerate naming drift across game builds the same way the rest of this mod does.
_TAKE_DAMAGE_AMOUNT_FIELDS = ("Damage", "DamageAmount", "InDamage", "RawDamage", "BaseDamage")


def _read_take_damage_amount(args: Any) -> tuple[str, float] | None:
    """Locate and read the incoming-damage float field off a `TakeDamage` args struct."""
    if args is None:
        return None
    for field_name in _TAKE_DAMAGE_AMOUNT_FIELDS:
        try:
            if not hasattr(args, field_name):
                continue
            return field_name, float(getattr(args, field_name))
        except Exception:
            continue
    return None


def on_player_take_damage_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Grant the equipped Melee Weapon's rolled level- & part-scaled Damage Reduction (`TakeDamage`).

    Only the LOCAL player's pawn is touched, and only while a Melee Arsenal weapon is the active
    `Pawn.Weapon`. pyunrealsdk hook `args` are mutable but never propagate to the live call, so the
    reduction is applied by blocking the original hit and re-calling the same `TakeDamage` with the
    reduced damage inside `prevent_hooking_direct_calls()` (BL2 then splits that reduced amount
    between shields and health exactly as it normally would). Any unexpected state falls through and
    lets vanilla damage apply untouched.

    The rolled percentage starts at `30%` for a Lv 1 blade and climbs to the `70%` cap (76% for
    unique rarities) at Lv 80 + OP10 — see `core.stats.compute_damage_reduction_pct`.
    """
    try:
        pc = get_local_player_controller()
        if pc is None or obj is None or getattr(pc, "Pawn", None) is not obj:
            return None

        # RMB Guard (Hyperion / Jakobs) supersedes the passive perk: a braced hit has the card
        # Damage Reduction applied twice, and a hit inside the parry window is reflected instead.
        if handle_guard_incoming_damage(obj, args, func):
            return Block

        if not STATE.damage_reduction_enabled:
            return None

        resolved = resolve_melee_weapon(getattr(obj, "Weapon", None), sync_weapon_props=False)
        if resolved is None:
            return None
        _inst, card = resolved

        pct = int(getattr(card, "damage_reduction_pct", 0))
        if pct <= 0:
            return None

        found = _read_take_damage_amount(args)
        if found is None:
            return None
        field_name, damage = found
        if damage <= 0.0:
            return None

        reduced = apply_incoming_damage_reduction(damage, pct)
        if reduced >= damage:
            return None

        try:
            setattr(args, field_name, reduced)
            with prevent_hooking_direct_calls():
                func(args)
        except Exception as exc:
            log_error(f"Reduced-damage TakeDamage re-call failed: {exc!r}")
            return None

        if STATE.debug_attack_log:
            log_info(
                f"Melee Damage Reduction [{card.manufacturer.value} {card.archetype_name}] "
                f"{pct}%: {damage:.1f} -> {reduced:.1f} incoming"
            )
        return Block
    except Exception as exc:
        log_error(f"on_player_take_damage_pre failed: {exc!r}")
        return None


# ---------------------------------------------------------------------------
# Weakspot Crit Feedback (vanilla floating "CRITICAL" popup)
# ---------------------------------------------------------------------------

def _mark_pending_crit_feedback(victim: Any) -> None:
    """Tag a victim we just critically struck so its vanilla damage popup shows as a crit."""
    if victim is None:
        return
    now = time.monotonic()
    for stale_id, expires_at in list(_PENDING_CRIT_FEEDBACK.items()):
        if expires_at < now:
            _PENDING_CRIT_FEEDBACK.pop(stale_id, None)
    _PENDING_CRIT_FEEDBACK[id(victim)] = now + CRIT_FEEDBACK_WINDOW_SEC


def _clear_pending_crit_feedback(victim: Any) -> None:
    if victim is not None:
        _PENDING_CRIT_FEEDBACK.pop(id(victim), None)


def clear_all_pending_crit_feedback() -> None:
    """Drop every pending crit-feedback tag (map change / mod disable hygiene)."""
    _PENDING_CRIT_FEEDBACK.clear()


def on_display_recent_damage_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Force the native CRITICAL popup for our Python-side weakspot crits.

    BL2 builds its floating damage numbers (and the red "CRITICAL" text) in
    `WillowDamageTypeDefinition:DisplayRecentDamageForPlayer`, reading
    `FPlayerRecentDamageEventData.DamageEventFlags`. The engine's damage pipeline only sets the crit
    bit when it is handed a real `HitInfo` crit region — and `Behavior_CauseDamage` has no `HitInfo`
    parameter, so our crits dealt full crit damage but always drew as a normal white number.

    pyunrealsdk hook args are a *copy*, so mutating them in place never reaches the live call. We
    therefore block the vanilla display and re-call the same function with `DamageEventFlags`' crit
    bit OR-ed in, exactly the block-and-recall pattern the damage-reduction hook already uses.
    Any unexpected state falls through to the untouched vanilla call.
    """
    if not _PENDING_CRIT_FEEDBACK:
        return None

    try:
        data = getattr(args, "DamageEventData", None)
        actor = getattr(data, "DamagedActor", None) if data is not None else None
        if actor is None:
            return None

        expires_at = _PENDING_CRIT_FEEDBACK.get(id(actor))
        if expires_at is None:
            return None
        if time.monotonic() > expires_at:
            _PENDING_CRIT_FEEDBACK.pop(id(actor), None)
            return None

        # Only ever touch the local player's own client-side numbers (co-op safe).
        pc = getattr(args, "PC", None)
        local_pc = get_local_player_controller()
        if pc is not None and local_pc is not None and pc is not local_pc:
            return None

        flags = int(getattr(data, "DamageEventFlags", 0) or 0)
        if flags & DAMAGE_EVENT_FLAG_CRIT:
            _PENDING_CRIT_FEEDBACK.pop(id(actor), None)
            return None

        try:
            data.DamageEventFlags = flags | DAMAGE_EVENT_FLAG_CRIT
        except Exception:
            return None

        # Consume the tag so later DoT ticks / follow-up hits never inherit a stale crit flag.
        _PENDING_CRIT_FEEDBACK.pop(id(actor), None)
        try:
            with prevent_hooking_direct_calls():
                func(args)
        except Exception as exc:
            log_error(f"Crit damage-popup re-call failed: {exc!r}")
            return None
        if STATE.debug_attack_log:
            log_info(
                f"Forced crit damage popup on {getattr(actor, 'Name', 'unknown')} "
                f"({float(getattr(data, 'TotalDamageForDamageType', 0.0) or 0.0):.1f} dmg)"
            )
        return Block
    except Exception as exc:
        log_error(f"on_display_recent_damage_pre failed: {exc!r}")
        return None


# ---------------------------------------------------------------------------
# Secondary Fire (RMB) — ADS zoom disabled, reserved for future secondary attacks
# ---------------------------------------------------------------------------

def _try_melee_secondary_attack(wpn: Any, inst: MeleeWeaponInstance, card: ComputedCardStats) -> bool:
    """Dispatch a Melee Weapon secondary attack on RMB / Left Trigger.

    - `THROW` (Bandit, Torgue): hurl the blade. Returns False if it is already airborne.
    - `GUARD` (Hyperion, Jakobs): raise the guard; it is held until RMB is released (`StopZoom`)
      and is refused while its part-driven cooldown is still running.
    - `NONE` (Maliwan): nothing happens, RMB stays inert.

    Zoom is blocked either way, so returning False never re-enables ADS on a blade.
    """
    if not MELEE_SECONDARY_ATTACK_ENABLED:
        return False
    if card.secondary_attack == SecondaryAttack.THROW:
        started = begin_throw(wpn, inst, card)
        if started:
            # Give the throw a real arm motion + whoosh + camera bump (the blade itself leaves the
            # hand logically: state is authoritative, the 1st-person swing sells the release).
            shooter, pc = _resolve_shooter_and_pc(wpn)
            if shooter is not None and not _is_dead(shooter):
                ctx = read_player_melee_context(shooter, pc)
                _trigger_first_person_blade_swing(pc, shooter, card, ctx.swing_speed_scale)
        return started
    if card.secondary_attack == SecondaryAttack.GUARD:
        return try_begin_guard(wpn, inst, card)
    return False


def on_melee_weapon_start_zoom_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block RMB zoom while a Melee Weapon is held and offer the secondary-attack stub a turn."""
    resolved = resolve_melee_weapon(obj, sync_weapon_props=False)
    if resolved is None:
        return None
    inst, card = resolved

    if _try_melee_secondary_attack(obj, inst, card):
        return Block

    if STATE.debug_attack_log:
        log_info(
            f"RMB zoom suppressed [{card.manufacturer.value} {card.archetype_name}] "
            f"(secondary attack: {card.secondary_attack.value})"
        )
    return Block


# ---------------------------------------------------------------------------
# First-Person `Bladed_Melee` Swing Animation + Fallback `RotOrigin`
# ---------------------------------------------------------------------------

def _get_arms_anim_slots(arms: Any) -> dict[str, Any]:
    """Locate & cache `WeaponAction`, `CustomAnimSlot`, and `WeaponRecoil` on `Pawn.Arms`."""
    if arms is None or _is_dead(arms):
        return {}
    aid = id(arms)
    cached = _ARMS_SLOT_CACHE.get(aid)
    if cached is not None:
        act = _deref_weak(cached.get("action"))
        cus = _deref_weak(cached.get("custom"))
        rec = _deref_weak(cached.get("recoil"))
        if act is not None or cus is not None:
            return {"action": act, "custom": cus, "recoil": rec}

    found_act = None
    found_cus = None
    found_rec = None
    try:
        for slot in list(unrealsdk.find_all("AnimNodeSlot", exact=False)):
            if _is_dead(slot):
                continue
            if getattr(slot, "SkelComponent", None) != arms:
                continue
            nname = str(getattr(slot, "NodeName", ""))
            if nname == "WeaponAction":
                found_act = slot
            elif nname == "CustomAnimSlot":
                found_cus = slot
            elif nname == "WeaponRecoil":
                found_rec = slot
    except Exception:
        pass

    if found_act is not None or found_cus is not None or found_rec is not None:
        _ARMS_SLOT_CACHE[aid] = {
            "action": _wrap_weak(found_act),
            "custom": _wrap_weak(found_cus),
            "recoil": _wrap_weak(found_rec),
        }
    return {"action": found_act, "custom": found_cus, "recoil": found_rec}


def _capture_arms_rest_transform(arms: Any, sm: Any) -> dict[str, Any] | None:
    """Remember the pristine `RotOrigin`/`Origin` of the first-person hands exactly once."""
    if arms is None or sm is None:
        return None
    entry = _ARMS_REST_BASELINE.get(id(sm))
    if entry is not None:
        # A recycled `id()` (map change / pawn respawn) must never be mistaken for the same mesh,
        # or the pose would be restored onto a dead object and the live hands would stay offset.
        if _deref_weak(entry.get("sm_ref")) is sm:
            return entry
        _ARMS_REST_BASELINE.pop(id(sm), None)
    try:
        ro0 = sm.RotOrigin
        origin0 = getattr(sm, "Origin", None)
        entry = {
            "arms_ref": _wrap_weak(arms),
            "sm_ref": _wrap_weak(sm),
            "rot": (int(ro0.Pitch), int(ro0.Yaw), int(ro0.Roll)),
            "loc": (
                (float(origin0.X), float(origin0.Y), float(origin0.Z))
                if origin0 is not None
                else None
            ),
        }
        _ARMS_REST_BASELINE[id(sm)] = entry
        log_info(
            f"Captured first-person arms rest transform: rot={entry['rot']}, loc={entry.get('loc')}"
        )
        return entry
    except Exception:
        return None


def _pose_target(pc: Any) -> tuple[Any, Any, Any, dict[str, Any]] | None:
    """Resolve `(pawn, arms, skeletal_mesh, rest_entry)` for the local first-person hands."""
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pawn is None or _is_dead(pawn):
        return None
    arms = getattr(pawn, "Arms", None)
    if arms is None or _is_dead(arms):
        return None
    sm = getattr(arms, "SkeletalMesh", None)
    if sm is None or _is_dead(sm) or not hasattr(sm, "RotOrigin"):
        return None
    entry = _capture_arms_rest_transform(arms, sm)
    if entry is None:
        return None
    return pawn, arms, sm, entry


def apply_first_person_pose(pc: Any, kind: str) -> bool:
    """Brace the blade (`guard`) or empty the hands (`thrown`) in first person.

    The pose is procedural (`Arms.SkeletalMesh.RotOrigin` + `Origin` + `ForceUpdate`) because no
    vanilla sequence holds a guard stance: the same technique the mod already uses for its swing
    fallback, which is verified to move the hands and the attached blade. Returns True when the
    pose is live.
    """
    global _POSE_KIND, _POSE_STARTED_AT
    if kind not in _POSE_OFFSETS:
        return clear_first_person_pose(pc)
    target = _pose_target(pc)
    if target is None:
        return False
    _POSE_KIND = kind
    _POSE_STARTED_AT = time.monotonic()
    update_first_person_pose(pc)
    log_info(f"First-person pose applied: {kind}")
    return True


def active_first_person_pose() -> str:
    """The currently applied secondary-attack posture ("" when the hands are idle)."""
    return _POSE_KIND


def update_first_person_pose(pc: Any, now: float | None = None) -> None:
    """Re-apply the live pose (guard gets a slow brace breathing bob, throw stays still)."""
    global _POSE_KIND
    if not _POSE_KIND:
        return
    target = _pose_target(pc)
    if target is None:
        _write_rest_transforms()
        _POSE_KIND = ""
        return
    _pawn, arms, sm, entry = target
    offsets = _POSE_OFFSETS.get(_POSE_KIND)
    if offsets is None:
        return
    now = time.monotonic() if now is None else now
    if now - _POSE_STARTED_AT > POSE_MAX_HOLD_SEC:
        reset_first_person_pose("held past its safety limit")
        return
    bob = 0.0
    if _POSE_KIND == POSE_GUARD:
        # A slow, shallow bob keeps the brace alive instead of reading as a frozen T-pose.
        bob = math.sin((now - _POSE_STARTED_AT) * 6.2)
    try:
        bp, by, br = entry["rot"]
        ro = copy.copy(sm.RotOrigin)
        ro.Pitch = _rotator_add(bp, offsets["pitch"] + 90.0 * bob)
        ro.Yaw = _rotator_add(by, offsets["yaw"])
        ro.Roll = _rotator_add(br, offsets["roll"] + 60.0 * bob)
        sm.RotOrigin = ro
        loc = entry.get("loc")
        if loc is not None and hasattr(sm, "Origin"):
            origin = copy.copy(sm.Origin)
            origin.X = float(loc[0]) + offsets["x"]
            origin.Y = float(loc[1]) + offsets["y"]
            origin.Z = float(loc[2]) + offsets["z"]
            sm.Origin = origin
        if hasattr(arms, "ForceUpdate"):
            arms.ForceUpdate(False)
    except Exception:
        _write_rest_transforms()
        _POSE_KIND = ""


def reset_first_person_pose(reason: str = "") -> None:
    """Unconditionally restore the idle hands and drop the pose, whatever its kind.

    This is the guard-release path: a hit can consume the brace without the engine ever reporting
    an RMB release, so the restore must never be conditional on the tracked pose kind matching.
    """
    global _POSE_KIND
    had = _POSE_KIND
    _POSE_KIND = ""
    _write_rest_transforms()
    if had:
        log_info(f"First-person pose force-restored ({had}{': ' + reason if reason else ''})")


def capture_first_person_rest_transform() -> bool:
    """Capture the idle arms transform while nothing is posed, so it can never bake in an offset."""
    if _POSE_KIND or STATE.swing_motion.active:
        return False
    try:
        return _pose_target(get_local_player_controller()) is not None
    except Exception:
        return False


def pose_held_seconds() -> float:
    """How long the current pose has been applied (0.0 when idle)."""
    if not _POSE_KIND:
        return 0.0
    return max(0.0, time.monotonic() - _POSE_STARTED_AT)


def clear_first_person_pose(pc: Any = None, kind: str = "") -> bool:
    """Restore idle hands. Pass `kind` to only clear that specific pose."""
    global _POSE_KIND
    if not _POSE_KIND:
        return True
    if kind and _POSE_KIND != kind:
        return False
    cleared = _POSE_KIND
    _POSE_KIND = ""
    _write_rest_transforms()
    log_info(f"First-person pose cleared: {cleared}")
    return True


def _write_rest_transforms() -> None:
    """Write every captured rest transform back (safe to call at any time)."""
    for sm_id, entry in list(_ARMS_REST_BASELINE.items()):
        sm = _deref_weak(entry.get("sm_ref"))
        arms = _deref_weak(entry.get("arms_ref"))
        if sm is None:
            _ARMS_REST_BASELINE.pop(sm_id, None)
            continue
        try:
            bp, by, br = entry["rot"]
            ro = copy.copy(sm.RotOrigin)
            ro.Pitch, ro.Yaw, ro.Roll = bp, by, br
            sm.RotOrigin = ro
            loc = entry.get("loc")
            if loc is not None and hasattr(sm, "Origin"):
                origin = copy.copy(sm.Origin)
                origin.X, origin.Y, origin.Z = loc
                sm.Origin = origin
            if arms is not None and hasattr(arms, "ForceUpdate"):
                arms.ForceUpdate(False)
        except Exception:
            pass


def _trigger_first_person_blade_swing(
    pc: Any,
    pawn: Any,
    card: ComputedCardStats,
    swing_speed_scale: float = 1.0,
) -> None:
    """Play Gearbox's `Bladed_Melee` 1st-person blade slash + CameraAnim + melee whoosh audio."""
    global _LAST_SWING_AT
    now = time.monotonic()
    if now - _LAST_SWING_AT < 0.03:
        return
    _LAST_SWING_AT = now

    ensure_combat_resources_cached()
    eff_speed = max(0.35, card.swing_speed * max(0.25, swing_speed_scale))
    swing_interval = max(0.22, 1.0 / eff_speed)

    # 1. Play real `Bladed_Melee` sequence on `Pawn.Arms` `CustomAnimSlot` (never clobbering
    # `SpecialMove_FirstPerson` on `WeaponAction`, and skipping during mid-swap `PendingWeapon`).
    played_real_anim = False
    pending_wpn = getattr(pawn, "PendingWeapon", None) if pawn is not None else None
    arms = getattr(pawn, "Arms", None) if (pawn is not None and pending_wpn is None) else None
    if arms is not None and not _is_dead(arms):
        slots = _get_arms_anim_slots(arms)
        recoil_slot = slots.get("recoil")
        target_slot = slots.get("custom") or slots.get("action")

        with prevent_hooking_direct_calls():
            if recoil_slot is not None:
                try:
                    recoil_slot.StopCustomAnim(0.0)
                except Exception:
                    pass

            if target_slot is not None:
                play_rate = max(0.68, min(2.35, BLADED_MELEE_SEQ_LENGTH / (swing_interval * 0.92)))
                for seq_name in ("Bladed_Melee", "Melee"):
                    try:
                        target_slot.StopCustomAnim(0.02)
                    except Exception:
                        pass
                    try:
                        dur = target_slot.PlayCustomAnim(
                            seq_name,
                            float(play_rate),
                            0.035,
                            0.08,
                            False,
                            True,
                        )
                        if isinstance(dur, (int, float)) and float(dur) > 0.01:
                            played_real_anim = True
                            break
                    except Exception:
                        continue

    # 2. Procedural `RotOrigin` + `ForceUpdate(False)` fallback ONLY if no AnimNodeSlot existed
    if not played_real_anim and arms is not None and not _is_dead(arms):
        sm = getattr(arms, "SkeletalMesh", None)
        if sm is not None and hasattr(sm, "RotOrigin"):
            _capture_arms_rest_transform(arms, sm)
            sm_info = STATE.swing_motion
            sm_info.active = True
            sm_info.start_time = now
            sm_info.duration = min(0.55, swing_interval * 0.88)
            sm_info.style = card.manufacturer.value
            sm_info.swing_sign *= -1

    # 3. Manufacturer-tuned CameraAnim impact feel — soft, slow and eased on purpose.
    #    A 2.35 swings/sec Sabre re-triggers this every ~0.42s, so a snappy shake stacked into a
    #    jarring camera flick. Playback runs at 0.60-0.68x (slower opening motion), the blend-in
    #    window is long enough to read as a ramp instead of a jump, and cadence damping scales the
    #    shake down as swing speed rises. `MODS -> Swing Camera Shake (%)` (rescaled by
    #    `CAMERA_SHAKE_SLIDER_GAIN`) is the final taste dial.
    if pc is not None:
        if card.manufacturer == Manufacturer.TORGUE:
            canim = _CACHED_CANIMS.get(CANIM_HEAVY_SLAM)
            scale, rate = 0.50, CAMERA_SHAKE_RATE_HEAVY
        elif card.targeting_mode == TargetingMode.FRONTAL_CLEAVE:
            canim = _CACHED_CANIMS.get(CANIM_MEDIUM_CLEAVE)
            scale, rate = 0.30, CAMERA_SHAKE_RATE_CLEAVE
        else:
            canim = _CACHED_CANIMS.get(CANIM_LIGHT_SLASH)
            scale, rate = 0.18, CAMERA_SHAKE_RATE_SINGLE

        cadence_damping = min(
            1.0,
            max(CAMERA_SHAKE_CADENCE_FLOOR, CAMERA_SHAKE_CADENCE_REFERENCE / eff_speed),
        )
        shake_scale = float(getattr(STATE, "camera_shake_scale", 0.5) or 0.0)
        scale *= cadence_damping * shake_scale * CAMERA_SHAKE_SLIDER_GAIN

        if canim is not None and scale > 0.001:
            try:
                pc.ClientPlayCameraAnim(
                    canim,
                    scale,
                    rate,
                    CAMERA_SHAKE_BLEND_IN_SEC,
                    CAMERA_SHAKE_BLEND_OUT_SEC,
                    False,
                    False,
                )
            except Exception:
                try:
                    pc.ClientPlayCameraAnim(canim)
                except Exception:
                    pass

    # 4. Crisp melee slash whoosh audio
    if pawn is not None:
        ak_whoosh = _CACHED_AKEVENTS.get(AK_SWING_WHOOSH)
        if ak_whoosh is not None:
            try:
                pawn.PlayAkEvent(ak_whoosh)
            except Exception:
                pass
        if card.manufacturer in (Manufacturer.BANDIT, Manufacturer.TORGUE):
            ak_heavy = _CACHED_AKEVENTS.get(AK_SWING_HEAVY_SWIPE)
            if ak_heavy is not None:
                try:
                    pawn.PlayAkEvent(ak_heavy)
                except Exception:
                    pass


def restore_all_arms_rest_transforms() -> None:
    """Restore any modified fallback `SkeletalMesh` transform cleanly.

    A live secondary-attack pose (`guard` / `thrown`) is intentionally *not* torn down here: the
    next `tick_secondary_attacks()` re-applies it on top of the restored rest transform.
    """
    STATE.swing_motion.active = False
    _write_rest_transforms()


def _update_first_person_slash_motion(pc: Any) -> None:
    """Drive fallback `RotOrigin` + `ForceUpdate(False)` arc only when `AnimNodeSlot` was unavailable."""
    sm_state = STATE.swing_motion
    if not sm_state.active:
        return
    # A guard brace / emptied throwing hand owns the transform; the fallback arc never fights it.
    if _POSE_KIND:
        return

    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pawn is None or _is_dead(pawn):
        restore_all_arms_rest_transforms()
        return

    wpn = getattr(pawn, "Weapon", None)
    if wpn is None or resolve_melee_weapon(wpn) is None:
        restore_all_arms_rest_transforms()
        return

    arms = getattr(pawn, "Arms", None)
    sm = getattr(arms, "SkeletalMesh", None) if arms is not None else None
    entry = _ARMS_REST_BASELINE.get(id(sm)) if sm is not None else None
    if sm is None or entry is None:
        sm_state.active = False
        return

    elapsed = time.monotonic() - sm_state.start_time
    dur = max(0.12, sm_state.duration)
    if elapsed >= dur:
        restore_all_arms_rest_transforms()
        return

    u = elapsed / dur
    arc = math.sin(u * math.pi) * max(0.0, STATE.slash_motion_scale)
    sgn = float(sm_state.swing_sign or 1)
    bp, by, br = entry["rot"]
    try:
        ro = copy.copy(sm.RotOrigin)
        ro.Pitch = int(bp - 3800 * arc)
        ro.Yaw = int(by - 4200 * arc * sgn)
        ro.Roll = int(br + 2800 * arc * sgn)
        sm.RotOrigin = ro
        if hasattr(arms, "ForceUpdate"):
            arms.ForceUpdate(False)
    except Exception:
        sm_state.active = False


def tick_melee_projectiles() -> None:
    """Kept as a no-op compatibility hook for `runtime/__init__.py` (`v0.2.6` hits synchronously)."""
    return None


# ---------------------------------------------------------------------------
# Synchronous `CalcWeaponFire` + `Behavior_CauseDamage` Melee Hit & Cleave Engine
# ---------------------------------------------------------------------------

def _is_target_dummy(actor: Any) -> bool:
    if actor is None:
        return False
    try:
        p = str(actor._path_name()).lower()
        if "targetdummy" in p or "dummy" in p:
            return True
    except Exception:
        pass
    try:
        ai_cls = getattr(actor, "AIClass", None)
        if ai_cls is not None and "dummy" in str(ai_cls._path_name()).lower():
            return True
    except Exception:
        pass
    return False


def _is_valid_melee_victim(shooter: Any, actor: Any) -> bool:
    """Return True for hostile `WillowAIPawn`s, Sanctuary Target Dummies, and destructible props."""
    if actor is None or actor == shooter or _is_dead(actor):
        return False
    cls_name = getattr(getattr(actor, "Class", None), "Name", "")
    if cls_name in ("WorldInfo", "StaticMeshActor", "InterpActor", "Terrain", "WillowPlayerPawn"):
        return False
    if "InteractiveObject" in cls_name or "Destructible" in cls_name or "KActor" in cls_name:
        return True
    if _is_target_dummy(actor):
        return True
    if "Pawn" in cls_name:
        try:
            if hasattr(actor, "IsAliveAndWell") and not bool(actor.IsAliveAndWell()):
                return False
        except Exception:
            pass
        try:
            return bool(shooter.IsEnemy(actor))
        except Exception:
            return "AIPawn" in cls_name
    return False


def _is_weakspot_bone(bone_name: str) -> bool:
    if not bone_name:
        return False
    low = bone_name.lower()
    if low in ("none", ""):
        return False
    return any(kw in low for kw in WEAKSPOT_BONE_KEYWORDS)


def _snapshot_cleave_candidates(shooter: Any) -> tuple[list[TargetCandidate], dict[str, Any]]:
    """Snapshot living hostile pawns + Target Dummies (seen-set + 800-step cap, Gotcha #13)."""
    candidates: list[TargetCandidate] = []
    pawn_map: dict[str, Any] = {}
    if shooter is None or _is_dead(shooter):
        return candidates, pawn_map
    world_info = getattr(shooter, "WorldInfo", None)
    if world_info is None:
        return candidates, pawn_map

    seen: set[int] = set()
    cur = getattr(world_info, "PawnList", None)
    steps = 0
    while cur is not None and steps < 800:
        steps += 1
        cid = id(cur)
        if cid in seen:
            break
        seen.add(cid)
        nxt = getattr(cur, "NextPawn", None)

        if cur != shooter and not _is_dead(cur):
            is_dummy = _is_target_dummy(cur)
            hostile = False
            try:
                hostile = bool(shooter.IsEnemy(cur))
            except Exception:
                hostile = getattr(getattr(cur, "Class", None), "Name", "") == "WillowAIPawn"

            if hostile or is_dummy:
                alive = True
                try:
                    alive = bool(cur.IsAliveAndWell())
                except Exception:
                    alive = True
                if alive:
                    try:
                        loc = cur.Location
                        cyl = getattr(cur, "CylinderComponent", None)
                        rad = float(getattr(cyl, "CollisionRadius", 42.0)) if cyl else 42.0
                        hgt = float(getattr(cyl, "CollisionHeight", 65.0)) if cyl else 65.0
                        tid = str(cid)
                        candidates.append(
                            TargetCandidate(
                                target_id=tid,
                                location=Vec3(float(loc.X), float(loc.Y), float(loc.Z)),
                                collision_radius=max(18.0, rad),
                                collision_half_height=max(28.0, hgt),
                                aim_height_offset=max(14.0, hgt * 0.45),
                            )
                        )
                        pawn_map[tid] = cur
                    except Exception:
                        pass
        cur = nxt

    return candidates, pawn_map


def _run_weapon_trace(
    wpn: Any,
    vec_tpl: Any,
    start_xyz: tuple[float, float, float],
    end_xyz: tuple[float, float, float],
) -> tuple[Any, tuple[float, float, float] | None, str]:
    """Run native `WillowWeapon.CalcWeaponFire` and unpack `(HitActor, HitLocationXYZ, BoneName)`."""
    start_v = _make_vec_from(vec_tpl, *start_xyz)
    end_v = _make_vec_from(vec_tpl, *end_xyz)
    zero_v = _make_vec_from(vec_tpl, 0.0, 0.0, 0.0)
    try:
        with prevent_hooking_direct_calls():
            ret = wpn.CalcWeaponFire(
                StartTrace=start_v,
                EndTrace=end_v,
                ImpactList=[],
                Extent=zero_v,
                bTestTrace=False,
            )
        imp = ret[0] if isinstance(ret, tuple) and len(ret) >= 1 else ret
        hit_actor = getattr(imp, "HitActor", None)
        hl = getattr(imp, "HitLocation", None)
        hl_xyz = (float(hl.X), float(hl.Y), float(hl.Z)) if hl is not None else None
        hinfo = getattr(imp, "HitInfo", None)
        bone = str(getattr(hinfo, "BoneName", "")) if hinfo is not None else ""
        return hit_actor, hl_xyz, bone
    except Exception:
        return None, None, ""


def _candidate_has_clear_los(
    wpn: Any,
    vec_tpl: Any,
    eye_xyz: tuple[float, float, float],
    cand: TargetCandidate,
    cand_pawn: Any,
) -> tuple[bool, tuple[float, float, float], str]:
    """Verify no wall blocks `eye_xyz -> cand` using `CalcWeaponFire`.

    Note (verified in `melee_combat_probe_report.json` Run #0 vs Run #1):
    - If `hit_actor == cand_pawn`: direct skeletal mesh hit (returns exact bone name!).
    - If `hit_actor is None`: 0-width ray passed cleanly between two limbs WITH ZERO WALLS in the way -> CLEAR!
    - If `hit_actor` is world geometry closer than `dist - cand.collision_radius`: BLOCKED BY WALL!
    """
    cp = cand.chest_point
    chest = (cp.x, cp.y, cp.z)
    dx, dy, dz = chest[0] - eye_xyz[0], chest[1] - eye_xyz[1], chest[2] - eye_xyz[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist < 1e-3:
        return True, chest, ""

    # Extend ray 35 UU through the skeleton so Hips/Spine/Chest bones intersect cleanly
    ux, uy, uz = dx / dist, dy / dist, dz / dist
    end_xyz = (chest[0] + ux * 35.0, chest[1] + uy * 35.0, chest[2] + uz * 35.0)
    hit_actor, hit_xyz, bone = _run_weapon_trace(wpn, vec_tpl, eye_xyz, end_xyz)

    if hit_actor is None:
        return True, chest, ""
    if hit_actor == cand_pawn:
        return True, (hit_xyz or chest), bone

    # Hit something else (another enemy in front, or a wall). Check hit distance!
    if hit_xyz is not None:
        hdist = math.sqrt(
            (hit_xyz[0] - eye_xyz[0]) ** 2
            + (hit_xyz[1] - eye_xyz[1]) ** 2
            + (hit_xyz[2] - eye_xyz[2]) ** 2
        )
        if hdist < max(8.0, dist - cand.collision_radius - 12.0):
            # Allow cleaving past another enemy in the pack, but NEVER through world walls!
            hcls = getattr(getattr(hit_actor, "Class", None), "Name", "")
            if "Pawn" not in hcls:
                return False, chest, ""
    return True, chest, ""


def _resolve_shooter_and_pc(wpn: Any) -> tuple[Any, Any]:
    pc = None
    try:
        pc = get_pc()
    except Exception:
        pc = None

    shooter = getattr(wpn, "Instigator", None) or getattr(wpn, "Owner", None)
    if (shooter is None or _is_dead(shooter)) and pc is not None:
        shooter = getattr(pc, "Pawn", None)
    if pc is None and shooter is not None:
        pc = getattr(shooter, "Controller", None)
    return shooter, pc


def _combat_breadcrumb(stage: str) -> None:
    """Persist opt-in checkpoints before native calls that may not return on a crash."""
    if STATE.debug_attack_log:
        log_info(f"Combat checkpoint: {stage}")


def _execute_melee_attack(wpn: Any, inst: MeleeWeaponInstance, card: ComputedCardStats) -> int:
    """Perform full blade swing animation + synchronous wall-occluded single-target/cleave damage."""
    global _FIRST_SWING_LOGGED
    _combat_breadcrumb(f"swing start: {card.manufacturer.value} {card.archetype_name}")
    shooter, pc = _resolve_shooter_and_pc(wpn)
    if shooter is None or _is_dead(shooter):
        return 0

    ensure_combat_resources_cached()
    ctx = read_player_melee_context(shooter, pc)

    # 1. Trigger 1st-person `Bladed_Melee` slash + CameraAnim + melee whoosh audio
    _combat_breadcrumb("before swing animation")
    _trigger_first_person_blade_swing(pc, shooter, card, ctx.swing_speed_scale)
    _combat_breadcrumb("after swing animation")

    # 2. Compute camera/eye ray origin and aim vector
    vec_tpl = getattr(shooter, "Location", None)
    if vec_tpl is not None:
        eye_x = float(vec_tpl.X)
        eye_y = float(vec_tpl.Y)
        eye_z = float(vec_tpl.Z) + float(
            getattr(shooter, "EyeHeight", getattr(shooter, "BaseEyeHeight", 64.0))
        )
    else:
        eye_x, eye_y, eye_z = 0.0, 0.0, 64.0

    try:
        with prevent_hooking_direct_calls():
            st = wpn.InstantFireStartTrace()
        if st is not None:
            eye_x, eye_y, eye_z = float(st.X), float(st.Y), float(st.Z)
    except Exception:
        pass

    rot_src = getattr(pc, "Rotation", None) or getattr(shooter, "Rotation", None)
    aim_dir = _rot_to_forward(rot_src) if rot_src is not None else (1.0, 0.0, 0.0)

    # Step 4 UU back inside shooter's ignored capsule so 0.0m point-blank targets always intersect
    start_xyz = (
        eye_x - aim_dir[0] * 4.0,
        eye_y - aim_dir[1] * 4.0,
        eye_z - aim_dir[2] * 4.0,
    )
    reach_uu = float(card.range_uu) + 12.0
    center_end_xyz = (
        eye_x + aim_dir[0] * reach_uu,
        eye_y + aim_dir[1] * reach_uu,
        eye_z + aim_dir[2] * reach_uu,
    )

    # 3. Compute normal & weakspot-crit damage breakdowns + sync weapon DoT DPS
    dmg_normal = compute_attack_damage(card, ctx, is_crit=False)
    dmg_crit = compute_attack_damage(card, ctx, is_crit=True)

    if dmg_normal.effective_dot_dps > 0:
        for attr_n in ("StatusEffectDamageBaseValue", "StatusEffectDamage"):
            try:
                setattr(wpn, attr_n, float(dmg_normal.effective_dot_dps))
            except Exception:
                pass

    # 4. Primary 0-width crosshair trace (detects headshots, Target Dummies, barrels, or ground/walls)
    _combat_breadcrumb("before center trace")
    center_actor, center_hit_xyz, center_bone = _run_weapon_trace(
        wpn, vec_tpl, start_xyz, center_end_xyz
    )

    _combat_breadcrumb("after center trace")

    # List of `(actor, hit_xyz, bone_name)` to strike
    targets_to_strike: list[tuple[Any, tuple[float, float, float], str]] = []
    seen_ids: set[int] = set()

    if _is_valid_melee_victim(shooter, center_actor):
        seen_ids.add(id(center_actor))
        targets_to_strike.append((center_actor, center_hit_xyz or center_end_xyz, center_bone))

    # 5. Cone acquisition (`SINGLE_TARGET` 22.5° assist vs `FRONTAL_CLEAVE` full arc)
    need_cone = (
        card.targeting_mode == TargetingMode.FRONTAL_CLEAVE
        or len(targets_to_strike) == 0
    )
    if need_cone:
        _combat_breadcrumb("before candidate snapshot")
        candidates, pawn_map = _snapshot_cleave_candidates(shooter)
        _combat_breadcrumb(f"after candidate snapshot: {len(candidates)} candidates")
        if card.targeting_mode == TargetingMode.FRONTAL_CLEAVE:
            half_arc_deg = max(8.0, card.cleave_arc_degrees * 0.5)
            max_extra = max(1, card.max_targets)
        else:
            half_arc_deg = SINGLE_TARGET_ASSIST_HALF_DEG
            max_extra = 1

        cos_limit = math.cos(math.radians(half_arc_deg))
        scored: list[tuple[float, TargetCandidate, Any]] = []

        for cand in candidates:
            cpawn = pawn_map.get(cand.target_id)
            if cpawn is None or id(cpawn) in seen_ids:
                continue
            cp = cand.chest_point
            dx = cp.x - eye_x
            dy = cp.y - eye_y
            dz = cp.z - eye_z
            dist_3d = math.sqrt(dx * dx + dy * dy + dz * dz)
            eff_dist = max(0.0, dist_3d - cand.collision_radius)
            if eff_dist > float(card.range_uu):
                continue

            if dist_3d > 1e-3:
                dot_3d = (dx * aim_dir[0] + dy * aim_dir[1] + dz * aim_dir[2]) / dist_3d
            else:
                dot_3d = 1.0

            # Allow extra wide angle at <85 UU face-hugging range so point-blank targets never miss
            min_cos = cos_limit if eff_dist > 85.0 else min(cos_limit, 0.35)
            if dot_3d < min_cos:
                continue

            # Prioritize closest to crosshair for SINGLE_TARGET, closest distance for CLEAVE
            score = (dot_3d * 500.0 - eff_dist) if card.targeting_mode == TargetingMode.SINGLE_TARGET else (-eff_dist)
            scored.append((score, cand, cpawn))

        scored.sort(key=lambda t: t[0], reverse=True)
        for _sc, cand, cpawn in scored:
            if len(targets_to_strike) >= max_extra:
                break
            _combat_breadcrumb("before candidate LOS trace")
            clear, hit_pt, bone = _candidate_has_clear_los(wpn, vec_tpl, start_xyz, cand, cpawn)
            _combat_breadcrumb("after candidate LOS trace")
            if clear:
                seen_ids.add(id(cpawn))
                targets_to_strike.append((cpawn, hit_pt, bone))

    # 6. Apply `Behavior_CauseDamage` to each struck target + hit FX & audio
    dmg_cls = _CACHED_BULLET_DMG_CLS
    imp_def = None
    try:
        with prevent_hooking_direct_calls():
            imp_def = wpn.GetTraceImpact()
    except Exception:
        imp_def = None
    if imp_def is None:
        imp_def = _CACHED_MELEE_IMPACT

    def _set_wpn_status_chance_mods(val: float) -> None:
        for attr_n in (
            "BaseStatusEffectChanceModifierBaseValue",
            "BaseStatusEffectChanceModifier",
            "StatusEffectChanceModifierBaseValue",
            "StatusEffectChanceModifier",
        ):
            try:
                setattr(wpn, attr_n, float(val))
            except Exception:
                pass

    hits_dealt = 0
    dots_procced = 0
    any_crit = False
    primary_blast_xyz: tuple[float, float, float] | None = None

    for victim, hit_xyz, bone_name in targets_to_strike:
        is_crit = bool(card.can_weakspot_crit and _is_weakspot_bone(bone_name))
        if is_crit:
            any_crit = True
        hit_amount = float(dmg_crit.final_hit_damage if is_crit else dmg_normal.final_hit_damage)

        # Single authoritative proc roll using the exact Item Card Effect Chance % + player status chance bonuses.
        # When `proc` succeeds, force `WeaponCombinedStatusEffectChanceModifier = 2500.0` (50 * 50) so BL2's
        # internal `CauseDamage` status roll succeeds 100% instead of double-rolling ~8% on top of Python's roll!
        proc = False
        if card.element in (Element.FIRE, Element.SHOCK, Element.CORROSIVE, Element.SLAG):
            proc = random.random() < dmg_normal.effective_proc_chance
            if proc:
                dt_def = _CACHED_DMG_TYPES_PROC.get(card.element)
                _set_wpn_status_chance_mods(50.0)
                dots_procced += 1
            else:
                dt_def = _CACHED_DMG_TYPES_NODOT.get(card.element)
                _set_wpn_status_chance_mods(0.0)
        else:
            dt_def = _CACHED_DMG_TYPES_PROC.get(card.element)
            _set_wpn_status_chance_mods(0.0)

        # Tag the victim BEFORE the call: `Behavior_CauseDamage` can synchronously feed BL2's recent
        # damage tracker, which is what produces the floating number (and needs the crit bit already set).
        if is_crit:
            _mark_pending_crit_feedback(victim)

        _combat_breadcrumb(f"before direct damage: {getattr(victim, 'Name', 'unknown')}")
        try:
            with prevent_hooking_direct_calls():
                shooter.Behavior_CauseDamage(
                    Target=victim,
                    DamageAmount=hit_amount,
                    Momentum=8500.0 * max(0.5, card.momentum_scale),
                    DamageType=dmg_cls,
                    DamageTypeDefinition=dt_def,
                    ImpactDefinition=imp_def,
                    bCanDamageFriendlies=False,
                    DamageCauser=wpn,
                )
            _combat_breadcrumb("after direct damage")
            hits_dealt += 1
            if primary_blast_xyz is None:
                primary_blast_xyz = hit_xyz
        except Exception as exc:
            if is_crit:
                _clear_pending_crit_feedback(victim)
            log_error(f"Behavior_CauseDamage failed on {victim}: {exc!r}")

        # Spawn surface/blood/spark impact FX at the strike point
        if vec_tpl is not None and hit_xyz is not None:
            try:
                _combat_breadcrumb("before impact effects")
                with prevent_hooking_direct_calls():
                    wpn.PlayImpactEffects(
                        _make_vec_from(vec_tpl, *start_xyz),
                        _make_vec_from(vec_tpl, *hit_xyz),
                    )
                _combat_breadcrumb("after impact effects")
            except Exception:
                pass

    # Restore clean rest status chance modifiers on weapon after hit loop
    _set_wpn_status_chance_mods(1.0)

    # 7. Torgue Blast-Cleaver Explosive Area Slam (`Behavior_CauseRadiusDamage` + Explosion VFX & Boom SFX)
    splash_triggered = False
    if card.splash_radius_uu > 0.0 and dmg_normal.splash_hit_damage > 0:
        if primary_blast_xyz is None and center_actor is not None and center_hit_xyz is not None:
            # Slamming the ground or a wall within blade reach still detonates the Blast-Cleaver shockwave!
            primary_blast_xyz = center_hit_xyz

        if primary_blast_xyz is not None and vec_tpl is not None:
            sx, sy, sz = float(vec_tpl.X), float(vec_tpl.Y), float(vec_tpl.Z)
            offset_vec = _make_vec_from(
                vec_tpl,
                primary_blast_xyz[0] - sx,
                primary_blast_xyz[1] - sy,
                primary_blast_xyz[2] - sz,
            )
            exp_dt = _CACHED_DMG_TYPES_PROC.get(Element.EXPLOSIVE)
            _combat_breadcrumb("before radius damage")
            try:
                with prevent_hooking_direct_calls():
                    shooter.Behavior_CauseRadiusDamage(
                        Radius=float(card.splash_radius_uu),
                        DamageAmount=float(dmg_normal.splash_hit_damage),
                        bFullDamage=True,
                        Momentum=14000.0,
                        DamageType=_CACHED_GRENADE_DMG_CLS or dmg_cls,
                        DamageTypeDefinition=exp_dt,
                        ImpactDefinition=imp_def,
                        bCanDamageFriendlies=False,
                        Offset=offset_vec,
                        DamageCauser=wpn,
                        bSkipTraceTest=False,
                    )
                _combat_breadcrumb("after radius damage")
                splash_triggered = True
            except Exception as exc:
                log_error(f"Behavior_CauseRadiusDamage failed: {exc!r}")

            if splash_triggered:
                _combat_breadcrumb("before explosion effects")
                _spawn_torgue_explosion_vfx_sfx(
                    shooter=shooter,
                    pc=pc,
                    vec_tpl=vec_tpl,
                    eye_xyz=(eye_x, eye_y, eye_z),
                    blast_xyz=primary_blast_xyz,
                )
                _combat_breadcrumb("after explosion effects")

    # 8. Meaty blade-hit audio when connecting with at least one enemy / prop
    if hits_dealt > 0 or splash_triggered:
        ak_hit = _CACHED_AKEVENTS.get(
            AK_HEAVY_IMPACT if card.manufacturer == Manufacturer.TORGUE else AK_BLADE_HIT_FLESH
        )
        if ak_hit is not None:
            try:
                shooter.PlayAkEvent(ak_hit)
            except Exception:
                pass

    if not _FIRST_SWING_LOGGED or STATE.debug_attack_log:
        _FIRST_SWING_LOGGED = True
        log_info(
            f"Swing [{card.manufacturer.value} {card.archetype_name}] "
            f"mode={card.targeting_mode.name} hits={hits_dealt} "
            f"dots={dots_procced} crit={any_crit} splash={splash_triggered} "
            f"reach={card.range_meters:.2f}m card={card.card_damage} "
            f"melee_mult={ctx.melee_damage_scale:.2f}x roid=+{ctx.roid_flat_damage:.0f} "
            f"-> hit_dmg={dmg_crit.final_hit_damage if any_crit else dmg_normal.final_hit_damage}"
        )

    return hits_dealt


def _spawn_torgue_explosion_vfx_sfx(
    shooter: Any,
    pc: Any,
    vec_tpl: Any,
    eye_xyz: tuple[float, float, float],
    blast_xyz: tuple[float, float, float],
) -> None:
    """Spawn a real fiery explosion fireball (`MyEmitterPool.SpawnEmitter`) + boom SFX + shake at `blast_xyz`."""
    _cache_torgue_explosion_assets()

    # Nudge the fireball 18 UU toward the player eye so it blooms on the front surface of the enemy/wall
    # rather than buried inside an opaque torso mesh.
    vx = eye_xyz[0] - blast_xyz[0]
    vy = eye_xyz[1] - blast_xyz[1]
    vz = eye_xyz[2] - blast_xyz[2]
    vlen = math.sqrt(vx * vx + vy * vy + vz * vz)
    if vlen > 1e-3:
        fx_xyz = (
            blast_xyz[0] + (vx / vlen) * min(18.0, vlen * 0.35),
            blast_xyz[1] + (vy / vlen) * min(18.0, vlen * 0.35),
            blast_xyz[2] + (vz / vlen) * min(18.0, vlen * 0.35),
        )
    else:
        fx_xyz = blast_xyz

    if _CACHED_EXPLOSION_PS is not None and vec_tpl is not None:
        world_info = getattr(shooter, "WorldInfo", None)
        emitter_pool = getattr(world_info, "MyEmitterPool", None) if world_info is not None else None
        if emitter_pool is not None and not _is_dead(emitter_pool):
            loc_v = _make_vec_from(vec_tpl, *fx_xyz)
            rot_tpl = getattr(shooter, "Rotation", None)
            zero_rot = None
            if rot_tpl is not None:
                try:
                    zero_rot = copy.copy(rot_tpl)
                    zero_rot.Pitch = 0
                    zero_rot.Yaw = 0
                    zero_rot.Roll = 0
                except Exception:
                    zero_rot = None
            try:
                with prevent_hooking_direct_calls():
                    if zero_rot is not None:
                        emitter_pool.SpawnEmitter(_CACHED_EXPLOSION_PS, loc_v, zero_rot)
                    else:
                        emitter_pool.SpawnEmitter(_CACHED_EXPLOSION_PS, loc_v)
            except Exception:
                try:
                    with prevent_hooking_direct_calls():
                        emitter_pool.SpawnEmitter(_CACHED_EXPLOSION_PS, loc_v)
                except Exception:
                    pass

    if _CACHED_EXPLOSION_AK is not None and shooter is not None:
        try:
            shooter.PlayAkEvent(_CACHED_EXPLOSION_AK)
        except Exception:
            pass

    if pc is not None:
        exp_canim = _CACHED_CANIMS.get(CANIM_EXPLOSION_MEDIUM)
        if exp_canim is not None:
            try:
                pc.ClientPlayCameraAnim(exp_canim, 0.72, 1.0, 0.03, 0.18, False, False)
            except Exception:
                try:
                    pc.ClientPlayCameraAnim(exp_canim)
                except Exception:
                    pass


def play_thrown_impact_feedback(pc: Any, pawn: Any, shake: bool = True) -> None:
    """A thrown blade landing: sharp metallic impact + a short thump (Torgue adds its own blast)."""
    ensure_combat_resources_cached()
    if pawn is not None:
        for path in (AK_BLADE_HIT_FLESH, AK_HEAVY_IMPACT):
            ak = _CACHED_AKEVENTS.get(path)
            if ak is None:
                continue
            try:
                pawn.PlayAkEvent(ak)
            except Exception:
                pass
    if not shake or pc is None:
        return
    user_shake = float(getattr(STATE, "camera_shake_scale", 0.5) or 0.0)
    canim = _CACHED_CANIMS.get(CANIM_LIGHT_SLASH)
    if canim is None or user_shake <= 0.001:
        return
    scale = 0.55 * user_shake * CAMERA_SHAKE_SLIDER_GAIN
    try:
        pc.ClientPlayCameraAnim(canim, scale, 0.9, 0.04, 0.20, False, False)
    except Exception:
        try:
            pc.ClientPlayCameraAnim(canim)
        except Exception:
            pass


def play_parry_feedback(pc: Any, pawn: Any, attacker: Any = None) -> None:
    """The unmistakable parry moment: camera jolt, hard metal clang and a HUD callout.

    A parry blocks the original `TakeDamage`, so without this the only evidence of a perfect
    block is the reflected number. The clang + jolt + callout give the input immediate feedback.
    """
    ensure_combat_resources_cached()

    shake = float(getattr(STATE, "camera_shake_scale", 0.5) or 0.0)
    if pc is not None and shake > 0.001:
        canim = _CACHED_CANIMS.get(CANIM_HEAVY_SLAM)
        if canim is not None:
            scale = 1.20 * shake * CAMERA_SHAKE_SLIDER_GAIN
            try:
                pc.ClientPlayCameraAnim(canim, scale, 0.85, 0.06, 0.26, False, False)
            except Exception:
                try:
                    pc.ClientPlayCameraAnim(canim)
                except Exception:
                    pass

    if pawn is not None:
        # Metallic blade-on-blade clang first, then the body impact for weight.
        for path in (AK_HEAVY_IMPACT, AK_BLADE_HIT_FLESH):
            ak = _CACHED_AKEVENTS.get(path)
            if ak is None:
                continue
            try:
                pawn.PlayAkEvent(ak)
            except Exception:
                pass

    if pc is not None:
        try:
            hud = pc.GetHUDMovie()
            if hud is not None:
                hud.AddTrainingText(
                    "PERFECT PARRY!",
                    "MELEE ARSENAL",
                    2.5,
                    (),
                    "",
                    False,
                    0,
                    pc.PlayerReplicationInfo,
                    True,
                )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Engine Hook Callbacks
# ---------------------------------------------------------------------------

def on_weapon_consume_ammo_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Keep melee magazine full, enforce semi-auto on Jakobs Sabre (Gotcha #24), & arm swing."""
    global _SHOT_SEQ
    resolved = resolve_melee_weapon(obj, sync_weapon_props=True)
    if resolved is None:
        return None
    _inst, card = resolved
    _SHOT_SEQ += 1

    try:
        clip = int(getattr(obj, "ClipSize", 100) or 100)
        obj.ReloadCnt = max(10, clip)
    except Exception:
        pass

    if card.semi_auto_click:
        try:
            obj.ClearPendingFire(0)
        except Exception:
            pass
    return None


on_weapon_consume_ammo_pre = on_weapon_consume_ammo_post


def on_weapon_instant_fire_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Primary `LMB` melee attack intercept (`WillowWeapon:InstantFire` PRE -> `Block`)."""
    global _HANDLED_SHOT_SEQ
    resolved = resolve_melee_weapon(obj)
    if resolved is None:
        return None
    # The blade is in the air (Bandit / Torgue throw): Primary Fire is locked until it rebounds.
    # Consume the shot sequence too, so the `SharedFireAmmunition` backup never fires a delayed
    # swing once the blade comes back.
    if is_weapon_thrown(obj):
        _HANDLED_SHOT_SEQ = _SHOT_SEQ
        return Block
    inst, card = resolved
    _HANDLED_SHOT_SEQ = _SHOT_SEQ
    try:
        _execute_melee_attack(obj, inst, card)
    except Exception as exc:
        log_error(f"on_weapon_instant_fire_pre error: {exc!r}")
    return Block


def on_weapon_firing_mode_fire_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block native gun bullet spawning (`WillowWeapon:FiringModeDefinitionFire` PRE)."""
    if resolve_melee_weapon(obj) is not None:
        return Block
    return None


def on_weapon_play_fire_effects_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block gun muzzle flashes & gun-fire twitch animations (`WillowWeapon:PlayFireEffects` PRE)."""
    if resolve_melee_weapon(obj) is not None:
        return Block
    return None


def on_weapon_shared_fire_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Backup safety net if any firing path ever bypasses `InstantFire`."""
    global _HANDLED_SHOT_SEQ
    if _HANDLED_SHOT_SEQ == _SHOT_SEQ:
        return None
    resolved = resolve_melee_weapon(obj)
    if resolved is None:
        return None
    # Swallow any backup `SharedFireAmmunition` path while the thrown blade is still airborne,
    # consuming the shot sequence so no delayed swing fires after the blade comes back.
    if is_weapon_thrown(obj):
        _HANDLED_SHOT_SEQ = _SHOT_SEQ
        return None
    inst, card = resolved
    _HANDLED_SHOT_SEQ = _SHOT_SEQ
    try:
        _execute_melee_attack(obj, inst, card)
    except Exception as exc:
        log_error(f"on_weapon_shared_fire_post error: {exc!r}")
    return None


def on_pawn_weapon_fired_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block 1st-person gun recoil (`ADD_Fire_Recoil`) on `Pawn.Arms` for Melee Arsenal blades."""
    try:
        in_weapon = getattr(args, "InWeapon", None) or getattr(obj, "Weapon", None)
    except Exception:
        in_weapon = getattr(obj, "Weapon", None)
    if in_weapon is not None and resolve_melee_weapon(in_weapon) is not None:
        return Block
    return None


def on_light_projectile_add_proj_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block any stray `MeleeArsenalDev.*` light projectile if ever invoked."""
    try:
        fm = getattr(args, "FiringModeDefinition", None)
        if fm is not None and str(fm._path_name()).startswith("MeleeArsenalDev."):
            return Block, -1
    except Exception:
        pass
    return None


def on_weapon_reload_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Melee weapons never reload — block reload attempts and keep `ReloadCnt` full."""
    if resolve_melee_weapon(obj) is None:
        return None
    try:
        obj.ReloadCnt = max(10, int(getattr(obj, "ClipSize", 100) or 100))
    except Exception:
        pass
    return Block


def on_player_tick_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Update active Combo Hit 2 Right->Left return slash right after `PlayerTick`."""
    if STATE.swing_motion.active:
        _update_first_person_slash_motion(obj)
    return None


def on_ai_take_damage_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Optional debug telemetry on AI damage taken from Melee Arsenal blades."""
    if not STATE.debug_attack_log:
        return None
    try:
        causer = getattr(args, "DamageCauser", None)
        op = getattr(causer, "ObjectPointer", None)
        if op is not None:
            causer = op
        if causer is not None and resolve_melee_weapon(causer) is not None:
            dmg = getattr(args, "Damage", 0.0)
            log_info(f"AI TakeDamage -> {getattr(obj, 'Name', '?')} took {dmg:.1f}")
    except Exception:
        pass
    return None
