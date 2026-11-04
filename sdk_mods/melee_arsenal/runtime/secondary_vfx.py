"""Client-side visuals for the RMB secondary attacks: the thrown blade actor.

Tediore's reload throw is a *real actor*: the Tediore weapon type's `BehaviorProviderDefinition`
holds a `Behavior_SpawnProjectile` node (`WeaponType_Tediore_Pistol:BehaviorProviderDefinition_6._4`
is the normal throw) and the spawned `WillowProjectile` carries the gun's own mesh and spin until it
explodes or is caught.

Melee Weapons are custom clones of donor pistol types and have no such behaviour graph, so this
module reproduces the visually important half of that trick: a real `SkeletalMeshComponent` using
the held weapon's own `SkeletalMesh` + material instances is attached to the pawn and flown along
the solved ballistic arc, then back into your hand on the rebound. Damage, traces and Torgue's
splash stay in `runtime.secondary` — the Bandit blade deliberately has no explosion, only the
flying blade and the catch.

Everything is best-effort: any engine function that is missing on a given build simply leaves the
throw without the flying actor (the whoosh, the emptied first-person hands, the impact FX and the
camera still fire from `runtime.secondary`).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import unrealsdk  # type: ignore[import-not-found]

from .log import log_error, log_info, log_warn
from .state import STATE

# UE3 rotator units per degree (65536 units / 360 deg).
_ROTATOR_UNITS_PER_DEG = 65536.0 / 360.0

# A thrown blade tumbles end over end; a slow spin would read as a floating prop.
_THROWN_SPIN_DEG_PER_SEC = 780.0


def _vec(x: float, y: float, z: float) -> Any:
    try:
        return unrealsdk.make_struct("Vector", X=float(x), Y=float(y), Z=float(z))
    except Exception:
        return (float(x), float(y), float(z))


def _rot(pitch: float, yaw: float = 0.0, roll: float = 0.0) -> Any:
    def _unit(deg: float) -> int:
        return int(round((deg % 360.0) * _ROTATOR_UNITS_PER_DEG)) & 0xFFFF

    try:
        return unrealsdk.make_struct("Rotator", Pitch=_unit(pitch), Yaw=_unit(yaw), Roll=_unit(roll))
    except Exception:
        return None


def _fallback_material(wpn: Any) -> Any:
    """The blade's own MIC, for when only the cooked gestalt mesh is available."""
    dd = getattr(wpn, "DefinitionData", None)
    for slot in ("MaterialPartDefinition", "BarrelPartDefinition", "BodyPartDefinition"):
        part = getattr(dd, slot, None) if dd is not None else None
        material = getattr(part, "Material", None) if part is not None else None
        if material is not None:
            return material
    return None


def _material_of(entry: Any) -> Any:
    """`Materials` entries are either the material itself or a `{Material, ...}` struct."""
    if entry is None:
        return None
    inner = getattr(entry, "Material", None)
    return inner if inner is not None else entry


@dataclass
class ThrownBladeVisual:
    """The spinning mesh actor that stands in for the airborne blade."""

    comp: Any
    pawn: Any
    # The held weapon's own (first-person) mesh is hidden while the blade is airborne, so the
    # hand is visibly empty even if the procedural arms offset is unavailable on a build.
    held_mesh: Any = None
    spin_deg: float = 0.0
    engine_xyz: tuple[float, float, float] | None = None
    last_update: float = field(default_factory=time.monotonic)
    alive: bool = True

    def move_to(self, xyz: tuple[float, float, float]) -> None:
        if not self.alive:
            return
        now = time.monotonic()
        dt = max(0.0, min(0.25, now - self.last_update))
        self.last_update = now
        self.spin_deg = (self.spin_deg + _THROWN_SPIN_DEG_PER_SEC * dt) % 360.0

        location = _vec(*xyz)
        # `AttachComponent` can clear the absolute flags again on some builds; re-assert them so
        # the actor really moves in world space instead of staying pinned to the pawn.
        try:
            self.comp.SetAbsolute(True, True, True)
        except Exception:
            pass
        moved = False
        try:
            self.comp.SetTranslation(location)
            moved = True
        except Exception:
            pass
        if not moved:
            # Direct property write, same transform the setter applies.
            try:
                self.comp.Translation = location
            except Exception:
                pass

        rot = _rot(self.spin_deg)
        if rot is not None:
            try:
                self.comp.SetRotation(rot)
            except Exception:
                try:
                    self.comp.Rotation = rot
                except Exception:
                    pass

        # Read the transform the engine actually holds, for `melee_arsenal.log` diagnostics.
        self.engine_xyz = None
        try:
            translation = self.comp.Translation
            self.engine_xyz = (
                float(translation.X),
                float(translation.Y),
                float(translation.Z),
            )
        except Exception:
            pass

    def destroy(self) -> None:
        if not self.alive:
            return
        self.alive = False
        try:
            self.comp.SetHiddenGame(True)
        except Exception:
            pass
        try:
            self.pawn.DetachComponent(self.comp)
        except Exception:
            pass
        try:
            self.comp.SetSkeletalMesh(None)
        except Exception:
            pass
        if self.held_mesh is not None:
            try:
                self.held_mesh.SetHiddenGame(False)
            except Exception:
                pass


_ACTIVE_VISUAL: ThrownBladeVisual | None = None
_SPAWN_COUNTER: int = 0


def _build_component(pawn: Any, wpn: Any) -> Any:
    """Create + attach a skeletal mesh component showing the held blade in the world."""
    global _SPAWN_COUNTER
    source = getattr(wpn, "Mesh", None)
    skel = getattr(source, "SkeletalMesh", None) if source is not None else None
    if skel is None:
        # Fall back to the cooked gestalt mesh (all fragments in one asset).
        skel = getattr(STATE, "custom_skeletal_mesh", None)
    if skel is None:
        log_warn(
            "Thrown blade visual unavailable: the held weapon exposes no SkeletalMesh and the "
            "custom gestalt mesh is not loaded."
        )
        return None

    _SPAWN_COUNTER += 1
    comp = unrealsdk.construct_object(
        "SkeletalMeshComponent",
        pawn,
        f"MeleeArsenalThrownBlade_{_SPAWN_COUNTER}",
        0,
        None,
    )
    comp.SetSkeletalMesh(skel)

    # The weapon's own material instances carry the atlas + rarity material, so copy them across.
    materials = list(getattr(source, "Materials", None) or []) if source is not None else []
    for index, entry in enumerate(materials):
        material = _material_of(entry)
        if material is None:
            continue
        try:
            comp.SetMaterial(index, material)
        except Exception:
            pass
    if not materials:
        material = _fallback_material(wpn)
        if material is not None:
            try:
                comp.SetMaterial(0, material)
            except Exception:
                pass

    # Match whatever scale the held weapon's mesh uses so the thrown blade is not a toy.
    if source is not None:
        scale3d = getattr(source, "Scale3D", None)
        if scale3d is not None:
            try:
                comp.SetScale3D(scale3d)
            except Exception:
                try:
                    comp.Scale3D = scale3d
                except Exception:
                    pass
        else:
            try:
                comp.SetScale(float(getattr(source, "Scale", 1.0)))
            except Exception:
                pass

    for attr, value in (("bForceRefpose", True), ("bUpdateSkelWhenNotRendered", True)):
        try:
            setattr(comp, attr, value)
        except Exception:
            pass

    try:
        pawn.AttachComponent(comp)
    except Exception:
        pass
    for call, call_args in (
        ("SetAbsolute", (True, True, True)),
        ("SetBlockRigidBody", (False,)),
        ("SetCollision", (False, False)),
        ("SetHiddenGame", (False,)),
    ):
        try:
            getattr(comp, call)(*call_args)
        except Exception:
            pass
    log_info(
        f"Thrown blade visual ready (mesh={getattr(skel, 'Name', '?')}, "
        f"materials={len(list(getattr(comp, 'Materials', None) or []))})"
    )
    return comp


def spawn_thrown_blade(pawn: Any, wpn: Any, origin_xyz: tuple[float, float, float]) -> Any:
    """Spawn the flying blade for a fresh throw. Returns the visual (or None if unsupported)."""
    global _ACTIVE_VISUAL
    destroy_thrown_blade()
    if pawn is None or wpn is None:
        return None
    try:
        comp = _build_component(pawn, wpn)
    except Exception as exc:
        log_error(f"Thrown blade visual could not be spawned: {exc!r}")
        return None
    if comp is None:
        return None
    held_mesh = getattr(wpn, "Mesh", None)
    visual = ThrownBladeVisual(comp=comp, pawn=pawn, held_mesh=held_mesh)
    visual.move_to(origin_xyz)
    _ACTIVE_VISUAL = visual
    return visual


def hide_held_blade() -> None:
    """Hide the weapon mesh still attached to the first-person hands (empty hand while it flies)."""
    visual = _ACTIVE_VISUAL
    if visual is None or visual.held_mesh is None or not visual.alive:
        return
    try:
        visual.held_mesh.SetHiddenGame(True)
    except Exception:
        pass


def update_thrown_blade(xyz: tuple[float, float, float]) -> None:
    if _ACTIVE_VISUAL is not None:
        _ACTIVE_VISUAL.move_to(xyz)


def destroy_thrown_blade() -> None:
    global _ACTIVE_VISUAL
    visual = _ACTIVE_VISUAL
    _ACTIVE_VISUAL = None
    if visual is not None:
        try:
            visual.destroy()
        except Exception as exc:
            log_error(f"Thrown blade visual cleanup failed: {exc!r}")


def has_thrown_blade_visual() -> bool:
    return _ACTIVE_VISUAL is not None


def thrown_blade_debug_info() -> dict[str, Any]:
    """Engine-side state of the live flying blade, for logging/diagnostics."""
    visual = _ACTIVE_VISUAL
    if visual is None:
        return {}
    return {
        "spin_deg": round(visual.spin_deg, 1),
        "engine_xyz": visual.engine_xyz,
    }


