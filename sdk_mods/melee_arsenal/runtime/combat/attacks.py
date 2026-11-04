"""Primary melee target acquisition, damage delivery, and hit feedback."""

from __future__ import annotations
from ..sdk import prevent_hooking_direct_calls

import copy
import math
from typing import Any

from mods_base import get_pc

from ...core.enums import Element, Manufacturer, TargetingMode
from ...core.parts.weapon import MeleeWeaponInstance
from ...core.stats import ComputedCardStats, compute_attack_damage
from ...core.targeting import TargetCandidate, Vec3
from ..log import log_debug_once, log_error, log_info
from ..object_identity import cache_key, is_dead
from ..state import STATE
from .. import resources
from . import common
from .damage import (
    clear_pending_crit_feedback,
    mark_pending_crit_feedback,
    play_camera_anim,
    read_player_melee_context,
    select_attack_damage_type,
    set_weapon_status_chance,
    sync_weapon_status_damage,
)
from .pose import CAMERA_SHAKE_SLIDER_GAIN, _trigger_first_person_blade_swing


SINGLE_TARGET_ASSIST_HALF_DEG = 22.5


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


_FIRST_SWING_LOGGED: bool = False


def is_target_dummy(actor: Any) -> bool:
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


def is_valid_melee_victim(shooter: Any, actor: Any) -> bool:
    """Return True for hostile `WillowAIPawn`s, Sanctuary Target Dummies, and destructible props."""
    if actor is None or actor == shooter or is_dead(actor):
        return False
    cls_name = getattr(getattr(actor, "Class", None), "Name", "")
    if cls_name in ("WorldInfo", "StaticMeshActor", "InterpActor", "Terrain", "WillowPlayerPawn"):
        return False
    if "InteractiveObject" in cls_name or "Destructible" in cls_name or "KActor" in cls_name:
        return True
    if is_target_dummy(actor):
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


def is_weakspot_bone(bone_name: str) -> bool:
    if not bone_name:
        return False
    low = bone_name.lower()
    if low in ("none", ""):
        return False
    return any(kw in low for kw in WEAKSPOT_BONE_KEYWORDS)


def snapshot_cleave_candidates(shooter: Any) -> tuple[list[TargetCandidate], dict[str, Any]]:
    """Collect living hostile pawns and target dummies with cycle detection and a scan cap."""
    candidates: list[TargetCandidate] = []
    pawn_map: dict[str, Any] = {}
    if shooter is None or is_dead(shooter):
        return candidates, pawn_map
    world_info = getattr(shooter, "WorldInfo", None)
    if world_info is None:
        return candidates, pawn_map

    seen: set[str] = set()
    cur = getattr(world_info, "PawnList", None)
    steps = 0
    while cur is not None and steps < 800:
        steps += 1
        cid = cache_key(cur)
        if cid in seen:
            break
        seen.add(cid)
        nxt = getattr(cur, "NextPawn", None)

        if cur != shooter and not is_dead(cur):
            is_dummy = is_target_dummy(cur)
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
                    except Exception as exc:  # pragma: no cover - engine call shape varies
                        log_debug_once("snapshot_cleave_candidates", f"snapshot_cleave_candidates failed: {exc!r}")
        cur = nxt

    return candidates, pawn_map


def run_weapon_trace(
    wpn: Any,
    vec_tpl: Any,
    start_xyz: tuple[float, float, float],
    end_xyz: tuple[float, float, float],
) -> tuple[Any, tuple[float, float, float] | None, str]:
    """Run native `WillowWeapon.CalcWeaponFire` and unpack `(HitActor, HitLocationXYZ, BoneName)`."""
    start_v = common.make_vec_from(vec_tpl, *start_xyz)
    end_v = common.make_vec_from(vec_tpl, *end_xyz)
    zero_v = common.make_vec_from(vec_tpl, 0.0, 0.0, 0.0)
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

    A zero-width ray may hit the candidate's skeletal mesh or pass between limbs and return no
    actor. A closer hit on world geometry is treated as an occluding wall.
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
    hit_actor, hit_xyz, bone = run_weapon_trace(wpn, vec_tpl, eye_xyz, end_xyz)

    if hit_actor is None:
        return True, chest, ""
    if hit_actor == cand_pawn:
        return True, (hit_xyz or chest), bone

    # Hit something else (another enemy in front, or a wall). Check hit distance!
    if hit_xyz is not None:
        hdist = math.sqrt(
            (hit_xyz[0] - eye_xyz[0]) ** 2 + (hit_xyz[1] - eye_xyz[1]) ** 2 + (hit_xyz[2] - eye_xyz[2]) ** 2
        )
        if hdist < max(8.0, dist - cand.collision_radius - 12.0):
            # Allow cleaving past another enemy in the pack, but NEVER through world walls!
            hcls = getattr(getattr(hit_actor, "Class", None), "Name", "")
            if "Pawn" not in hcls:
                return False, chest, ""
    return True, chest, ""


def resolve_shooter_and_pc(wpn: Any) -> tuple[Any, Any]:
    pc = None
    try:
        pc = get_pc()
    except Exception:
        pc = None

    shooter = getattr(wpn, "Instigator", None) or getattr(wpn, "Owner", None)
    if (shooter is None or is_dead(shooter)) and pc is not None:
        shooter = getattr(pc, "Pawn", None)
    if pc is None and shooter is not None:
        pc = getattr(shooter, "Controller", None)
    return shooter, pc


def combat_breadcrumb(stage: str) -> None:
    """Persist opt-in checkpoints before native calls that may not return on a crash."""
    if STATE.debug_attack_log:
        log_info(f"Combat checkpoint: {stage}")


def execute_melee_attack(wpn: Any, inst: MeleeWeaponInstance, card: ComputedCardStats) -> int:
    """Perform full blade swing animation + synchronous wall-occluded single-target/cleave damage."""
    global _FIRST_SWING_LOGGED
    combat_breadcrumb(f"swing start: {card.manufacturer.value} {card.archetype_name}")
    shooter, pc = resolve_shooter_and_pc(wpn)
    if shooter is None or is_dead(shooter):
        return 0

    resources.ensure_combat_resources_cached()
    ctx = read_player_melee_context(shooter, pc)

    # Trigger 1st-person `Bladed_Melee` slash + CameraAnim + melee whoosh audio
    combat_breadcrumb("before swing animation")
    _trigger_first_person_blade_swing(pc, shooter, card, ctx.swing_speed_scale)
    combat_breadcrumb("after swing animation")

    # Compute camera/eye ray origin and aim vector
    vec_tpl = getattr(shooter, "Location", None)
    if vec_tpl is not None:
        eye_x = float(vec_tpl.X)
        eye_y = float(vec_tpl.Y)
        eye_z = float(vec_tpl.Z) + float(getattr(shooter, "EyeHeight", getattr(shooter, "BaseEyeHeight", 64.0)))
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
    aim_dir = common.rot_to_forward(rot_src) if rot_src is not None else (1.0, 0.0, 0.0)

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

    # Compute normal & weakspot-crit damage breakdowns + sync weapon DoT DPS
    dmg_normal = compute_attack_damage(card, ctx, is_crit=False)
    dmg_crit = compute_attack_damage(card, ctx, is_crit=True)

    sync_weapon_status_damage(wpn, dmg_normal)

    # Primary 0-width crosshair trace (detects headshots, Target Dummies, barrels, or ground/walls)
    combat_breadcrumb("before center trace")
    center_actor, center_hit_xyz, center_bone = run_weapon_trace(wpn, vec_tpl, start_xyz, center_end_xyz)

    combat_breadcrumb("after center trace")

    # List of `(actor, hit_xyz, bone_name)` to strike
    targets_to_strike: list[tuple[Any, tuple[float, float, float], str]] = []
    seen_ids: set[str] = set()

    if is_valid_melee_victim(shooter, center_actor):
        seen_ids.add(cache_key(center_actor))
        targets_to_strike.append((center_actor, center_hit_xyz or center_end_xyz, center_bone))

    # Cone acquisition (`SINGLE_TARGET` 22.5° assist vs `FRONTAL_CLEAVE` full arc)
    need_cone = card.targeting_mode == TargetingMode.FRONTAL_CLEAVE or len(targets_to_strike) == 0
    if need_cone:
        combat_breadcrumb("before candidate snapshot")
        candidates, pawn_map = snapshot_cleave_candidates(shooter)
        combat_breadcrumb(f"after candidate snapshot: {len(candidates)} candidates")
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
            if cpawn is None or cache_key(cpawn) in seen_ids:
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
            combat_breadcrumb("before candidate LOS trace")
            clear, hit_pt, bone = _candidate_has_clear_los(wpn, vec_tpl, start_xyz, cand, cpawn)
            combat_breadcrumb("after candidate LOS trace")
            if clear:
                seen_ids.add(cache_key(cpawn))
                targets_to_strike.append((cpawn, hit_pt, bone))

    # Apply `Behavior_CauseDamage` to each struck target + hit FX & audio
    dmg_cls = resources._CACHED_BULLET_DMG_CLS
    imp_def = None
    try:
        with prevent_hooking_direct_calls():
            imp_def = wpn.GetTraceImpact()
    except Exception:
        imp_def = None
    if imp_def is None:
        imp_def = resources._CACHED_MELEE_IMPACT

    hits_dealt = 0
    dots_procced = 0
    any_crit = False
    primary_blast_xyz: tuple[float, float, float] | None = None

    for victim, hit_xyz, bone_name in targets_to_strike:
        is_crit = bool(card.can_weakspot_crit and is_weakspot_bone(bone_name))
        if is_crit:
            any_crit = True
        hit_amount = float(dmg_crit.final_hit_damage if is_crit else dmg_normal.final_hit_damage)

        # Shared primary/throw proc policy: player-scaled chance + native modifier forcing.
        dt_def, proc = select_attack_damage_type(wpn, card, dmg_normal)
        if proc:
            dots_procced += 1

        # Tag the victim BEFORE the call: `Behavior_CauseDamage` can synchronously feed BL2's recent
        # damage tracker, which is what produces the floating number (and needs the crit bit already set).
        if is_crit:
            mark_pending_crit_feedback(victim)

        combat_breadcrumb(f"before direct damage: {getattr(victim, 'Name', 'unknown')}")
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
            combat_breadcrumb("after direct damage")
            hits_dealt += 1
            if primary_blast_xyz is None:
                primary_blast_xyz = hit_xyz
        except Exception as exc:
            if is_crit:
                clear_pending_crit_feedback(victim)
            log_error(f"Behavior_CauseDamage failed on {victim}: {exc!r}")

        # Spawn surface/blood/spark impact FX at the strike point
        if vec_tpl is not None and hit_xyz is not None:
            try:
                combat_breadcrumb("before impact effects")
                with prevent_hooking_direct_calls():
                    wpn.PlayImpactEffects(
                        common.make_vec_from(vec_tpl, *start_xyz),
                        common.make_vec_from(vec_tpl, *hit_xyz),
                    )
                combat_breadcrumb("after impact effects")
            except Exception:
                pass

    # Restore clean rest status chance modifiers on weapon after hit loop
    set_weapon_status_chance(wpn, 1.0)

    # Torgue Blast-Cleaver Explosive Area Slam (`Behavior_CauseRadiusDamage` + Explosion VFX & Boom SFX)
    splash_triggered = False
    if card.splash_radius_uu > 0.0 and dmg_normal.splash_hit_damage > 0:
        if primary_blast_xyz is None and center_actor is not None and center_hit_xyz is not None:
            # Slamming the ground or a wall within blade reach still detonates the Blast-Cleaver shockwave!
            primary_blast_xyz = center_hit_xyz

        if primary_blast_xyz is not None and vec_tpl is not None:
            sx, sy, sz = float(vec_tpl.X), float(vec_tpl.Y), float(vec_tpl.Z)
            offset_vec = common.make_vec_from(
                vec_tpl,
                primary_blast_xyz[0] - sx,
                primary_blast_xyz[1] - sy,
                primary_blast_xyz[2] - sz,
            )
            exp_dt = resources._CACHED_DMG_TYPES_PROC.get(Element.EXPLOSIVE)
            combat_breadcrumb("before radius damage")
            try:
                with prevent_hooking_direct_calls():
                    shooter.Behavior_CauseRadiusDamage(
                        Radius=float(card.splash_radius_uu),
                        DamageAmount=float(dmg_normal.splash_hit_damage),
                        bFullDamage=True,
                        Momentum=14000.0,
                        DamageType=resources._CACHED_GRENADE_DMG_CLS or dmg_cls,
                        DamageTypeDefinition=exp_dt,
                        ImpactDefinition=imp_def,
                        bCanDamageFriendlies=False,
                        Offset=offset_vec,
                        DamageCauser=wpn,
                        bSkipTraceTest=False,
                    )
                combat_breadcrumb("after radius damage")
                splash_triggered = True
            except Exception as exc:
                log_error(f"Behavior_CauseRadiusDamage failed: {exc!r}")

            if splash_triggered:
                combat_breadcrumb("before explosion effects")
                spawn_torgue_explosion_vfx_sfx(
                    shooter=shooter,
                    pc=pc,
                    vec_tpl=vec_tpl,
                    eye_xyz=(eye_x, eye_y, eye_z),
                    blast_xyz=primary_blast_xyz,
                )
                combat_breadcrumb("after explosion effects")

    # Meaty blade-hit audio when connecting with at least one enemy / prop
    if hits_dealt > 0 or splash_triggered:
        ak_hit = resources._CACHED_AKEVENTS.get(
            resources.AK_HEAVY_IMPACT if card.manufacturer == Manufacturer.TORGUE else resources.AK_BLADE_HIT_FLESH
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


def spawn_torgue_explosion_vfx_sfx(
    shooter: Any,
    pc: Any,
    vec_tpl: Any,
    eye_xyz: tuple[float, float, float],
    blast_xyz: tuple[float, float, float],
) -> None:
    """Spawn a real fiery explosion fireball (`MyEmitterPool.SpawnEmitter`) + boom SFX + shake at `blast_xyz`."""
    resources._cache_torgue_explosion_assets()

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

    if resources._CACHED_EXPLOSION_PS is not None and vec_tpl is not None:
        world_info = getattr(shooter, "WorldInfo", None)
        emitter_pool = getattr(world_info, "MyEmitterPool", None) if world_info is not None else None
        if emitter_pool is not None and not is_dead(emitter_pool):
            loc_v = common.make_vec_from(vec_tpl, *fx_xyz)
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
                        emitter_pool.SpawnEmitter(resources._CACHED_EXPLOSION_PS, loc_v, zero_rot)
                    else:
                        emitter_pool.SpawnEmitter(resources._CACHED_EXPLOSION_PS, loc_v)
            except Exception:
                try:
                    with prevent_hooking_direct_calls():
                        emitter_pool.SpawnEmitter(resources._CACHED_EXPLOSION_PS, loc_v)
                except Exception:
                    pass

    if resources._CACHED_EXPLOSION_AK is not None and shooter is not None:
        try:
            shooter.PlayAkEvent(resources._CACHED_EXPLOSION_AK)
        except Exception:
            pass

    user_shake = float(getattr(STATE, "camera_shake_scale", 0.5) or 0.0)
    exp_canim = resources._CACHED_CANIMS.get(resources.CANIM_EXPLOSION_MEDIUM)
    play_camera_anim(pc, exp_canim, 0.72 * user_shake / 0.5, 1.0, 0.03, 0.18)


def play_thrown_impact_feedback(pc: Any, pawn: Any, shake: bool = True) -> None:
    """A thrown blade landing: sharp metallic impact + a short thump (Torgue adds its own blast)."""
    resources.ensure_combat_resources_cached()
    if pawn is not None:
        for path in (resources.AK_BLADE_HIT_FLESH, resources.AK_HEAVY_IMPACT):
            ak = resources._CACHED_AKEVENTS.get(path)
            if ak is None:
                continue
            try:
                pawn.PlayAkEvent(ak)
            except Exception:
                pass
    if not shake or pc is None:
        return
    user_shake = float(getattr(STATE, "camera_shake_scale", 0.5) or 0.0)
    canim = resources._CACHED_CANIMS.get(resources.CANIM_LIGHT_SLASH)
    if canim is None or user_shake <= 0.001:
        return
    scale = 0.55 * user_shake * CAMERA_SHAKE_SLIDER_GAIN
    play_camera_anim(pc, canim, scale, 0.9, 0.04, 0.20)


def play_parry_feedback(pc: Any, pawn: Any, attacker: Any = None) -> None:
    """The unmistakable parry moment: camera jolt, hard metal clang and a HUD callout.

    A parry blocks the original `TakeDamage`, so without this the only evidence of a perfect
    block is the reflected number. The clang + jolt + callout give the input immediate feedback.
    """
    resources.ensure_combat_resources_cached()

    shake = float(getattr(STATE, "camera_shake_scale", 0.5) or 0.0)
    if pc is not None and shake > 0.001:
        canim = resources._CACHED_CANIMS.get(resources.CANIM_HEAVY_SLAM)
        if canim is not None:
            scale = 1.20 * shake * CAMERA_SHAKE_SLIDER_GAIN
            play_camera_anim(pc, canim, scale, 0.85, 0.06, 0.26)

    if pawn is not None:
        # Metallic blade-on-blade clang first, then the body impact for weight.
        for path in (resources.AK_HEAVY_IMPACT, resources.AK_BLADE_HIT_FLESH):
            ak = resources._CACHED_AKEVENTS.get(path)
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
