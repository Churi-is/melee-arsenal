"""Custom Inkblade 3D Gestalt mesh, bifacial socket-fitted parts, and unified element-atlas materials.

Reads `data/inkblade_compiled_manifest.json` from the installed mod. The compiler writes the
manifest under `build/`; packaging and installation place it alongside the matching UPKs,
updating part bounds, socket offsets, face-gap twins, and atlases without code changes.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any

try:
    import unrealsdk  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    unrealsdk = None  # type: ignore[assignment]

from ..core.catalog import BLADES, BLADE_BY_MANUFACTURER, ELEMENT_PART_BY_TYPE, MANUFACTURERS
from ..core.enums import Manufacturer, Rarity
from .log import log_debug_once, log_error, log_info, log_warn, obj_path
from .state import KEEP_ALIVE_FLAGS, STATE


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "inkblade_compiled_manifest.json"
MASTER_CLASSMOD_SHADER_PATH = "Item_ClassMods.Mat.Master_ClassMod"

BLADE_TO_KIT_ID: dict[str, str] = {
    "Blade_Hyperion_Katana": "blade_hyperion",
    "Blade_Bandit_Machete": "blade_bandit",
    "Blade_Jakobs_Sabre": "blade_jakobs",
    "Blade_Maliwan_Phaseblade": "blade_maliwan",
    "Blade_Torgue_BlastCleaver": "blade_torgue",
}

GRIP_TO_KIT_ID: dict[str, str] = {
    "Grip_Hyperion": "grip_hyperion",
    "Grip_Bandit": "grip_bandit",
    "Grip_Jakobs": "grip_jakobs",
    "Grip_Maliwan": "grip_maliwan",
    "Grip_Torgue": "grip_torgue",
}

ACCESSORY_TO_KIT_ID: dict[str, str | None] = {
    "Acc_None": None,
    "Acc_Serrated": "accessory_serrated_edge",
    "Acc_Counterweight": "accessory_balanced_pommel",
    "Acc_ExtendedTang": "accessory_extended_blade_fuller",
    "Acc_RazorFuller": "accessory_monofilament_honing",
    "Acc_BroadSweep": "accessory_flared_cleave_guard",
    "Acc_HeavySpine": "accessory_reinforced_heavy_spine",
    "Acc_FluxCapacitor": "accessory_overclocked_elemental_coil",
}

ELEMENT_TO_KIT_ID: dict[str, str | None] = {
    "Elem_None": None,
    "Elem_Fire": "element_fire",
    "Elem_Shock": "element_shock",
    "Elem_Corrosive": "element_corrosive",
    "Elem_Slag": "element_slag",
    "Elem_Explosive": "element_explosive",
}

ELEMENT_ID_TO_ATLAS_KEY: dict[str, str] = {
    "Elem_None": "None",
    "Elem_Fire": "Fire",
    "Elem_Shock": "Shock",
    "Elem_Corrosive": "Corrosive",
    "Elem_Slag": "Slag",
    "Elem_Explosive": "Explosive",
}


def _find(cls_name: str, path_name: str) -> Any:
    if unrealsdk is None or not path_name:
        return None
    try:
        return unrealsdk.find_object(cls_name, path_name)
    except Exception as exc:
        log_debug_once(f"custom_assets._find:{cls_name}:{path_name}", f"_find {cls_name} {path_name} failed: {exc!r}")
        return None


def _locate_cooked_pcconsole_dir() -> Path | None:
    """Resolve `Borderlands 2/WillowGame/CookedPCConsole` from `sys.executable` or mod path."""
    candidates: list[Path] = []
    try:
        exe_path = Path(sys.executable).resolve()
        # Typical BL2 exe: `<BL2>/Binaries/Win32/Borderlands2.exe`
        for parent in exe_path.parents:
            cand = parent / "WillowGame" / "CookedPCConsole"
            candidates.append(cand)
    except Exception:
        pass
    try:
        mod_file = Path(__file__).resolve()
        for parent in mod_file.parents:
            cand = parent / "WillowGame" / "CookedPCConsole"
            candidates.append(cand)
    except Exception:
        pass

    for cand in candidates:
        try:
            if cand.is_dir():
                return cand
        except Exception:
            continue
    return None


def _upk_needs_restage(src: Path, dst: Path) -> bool:
    """Return True if `dst` is missing, differs in byte size, or differs in its 129-byte UPK summary/GUID header."""
    if not dst.is_file():
        return True
    try:
        if dst.stat().st_size != src.stat().st_size:
            return True
        with src.open("rb") as fs, dst.open("rb") as fd:
            return fs.read(129) != fd.read(129)
    except Exception:
        return True


def _try_stage_bundled_upks(cooked_dir: Path | None, pkg_names: tuple[str, ...]) -> None:
    """If UPKs were placed inside `sdk_mods/melee_arsenal/CookedPCConsole/` or `upk/`, stage them into `CookedPCConsole`."""
    if cooked_dir is None or not cooked_dir.is_dir():
        return
    mod_root = Path(__file__).resolve().parents[1]
    for bundled_dir in (mod_root / "CookedPCConsole", mod_root / "upk"):
        if not bundled_dir.is_dir():
            continue
        for pkg in pkg_names:
            src = bundled_dir / f"{pkg}.upk"
            dst = cooked_dir / f"{pkg}.upk"
            if src.is_file() and _upk_needs_restage(src, dst):
                try:
                    shutil.copy2(src, dst)
                    log_info(f"Staged bundled {src.name} -> {dst}")
                except Exception as exc:
                    log_warn(f"Could not auto-stage {src} -> {dst}: {exc!r}")


def _safe_load_custom_package(pkg_name: str, cooked_dir: Path | None) -> Any:
    """Guard `unrealsdk.load_package` with an explicit `.upk` file check so missing packages never crash."""
    existing = _find("Package", pkg_name)
    if existing is not None:
        return STATE.keep_alive(existing)

    if cooked_dir is not None:
        upk_file = cooked_dir / f"{pkg_name}.upk"
        if not upk_file.is_file():
            log_warn(
                f"Custom UPK {upk_file} not found; copy {pkg_name}.upk into WillowGame/CookedPCConsole."
            )
            return None
    else:
        # No live BL2 CookedPCConsole directory (e.g. unit test environment without pre-seeded mock package)
        return None

    loaded = unrealsdk.load_package(pkg_name)
    if loaded is not None:
        STATE.keep_alive(loaded)
    return loaded


def set_part_gestalt_fragment(part: Any, fragment_name: str) -> None:
    """Assign `GestaltModeSkeletalMeshName` and clear `AdditionalGestaltModeSkeletalMeshNames`."""
    if part is None:
        return
    try:
        part.GestaltModeSkeletalMeshName = fragment_name
    except Exception:
        pass
    if hasattr(part, "AdditionalGestaltModeSkeletalMeshNames"):
        try:
            cur = list(part.AdditionalGestaltModeSkeletalMeshNames)
            n = max(2, len(cur)) if cur else 2
            part.AdditionalGestaltModeSkeletalMeshNames = tuple("None" for _ in range(n))
        except Exception:
            try:
                for idx in range(len(part.AdditionalGestaltModeSkeletalMeshNames)):
                    part.AdditionalGestaltModeSkeletalMeshNames[idx] = "None"
            except Exception:
                pass


def set_part_rarity(part: Any, rarity: Rarity | int | float) -> None:
    """Pin `WeaponPartDefinition.Rarity` to an exact BL2 rarity level (`1` White … `4` Purple).

    BL2 no longer sums rarity from every part: one specific part — the **Material** part — decides
    the rarity of the finished item. That single float drives the item-card title colour, the ground
    loot-beam colour (`WillowPickup.InventoryRarityLevel` via `GlobalsDefinition.GetRarityColorForLevel`)
    and the rarity-scaled sell value. A clone whose `Rarity` was inherited from a Common donor (or left
    at `0`) renders every Melee Weapon white no matter which tier rolled, so we stamp it explicitly.
    """
    if part is None or not hasattr(part, "Rarity"):
        return
    level = float(int(rarity))
    try:
        data = copy.copy(part.Rarity)
        data.BaseValueConstant = level
        data.BaseValueAttribute = None
        data.InitializationDefinition = None
        data.BaseValueScaleConstant = 1.0
        part.Rarity = data
    except Exception:
        pass


def get_part_rarity(part: Any) -> float:
    """Read a part's `Rarity` level (`0.0` when unset/unreadable).

    Vanilla parts frequently resolve their rarity through a `BaseValueAttribute` resolver chain
    instead of a flat `BaseValueConstant`, so both shapes are decoded here.
    """
    try:
        data = part.Rarity
    except Exception:
        return 0.0
    value = 0.0
    resolver_chain = getattr(getattr(data, "BaseValueAttribute", None), "ValueResolverChain", None)
    if resolver_chain:
        try:
            value = float(getattr(resolver_chain[0], "ConstantValue", 0.0) or 0.0)
        except Exception:
            value = 0.0
    if not value:
        try:
            value = float(getattr(data, "BaseValueConstant", 0.0) or 0.0)
        except Exception:
            value = 0.0
    try:
        value *= float(getattr(data, "BaseValueScaleConstant", 1.0) or 1.0)
    except Exception:
        pass
    return value


def is_custom_asset_part(part: Any) -> bool:
    """True when `part` is one of our runtime-constructed parts (never mutate vanilla donors!)."""
    path = obj_path(part)
    if not path:
        return False
    if "Inkblade" in path or "MeleeArsenal" in path:
        return True
    root = obj_path(STATE.root_package) if STATE.root_package is not None else ""
    return bool(root) and path.startswith(f"{root}.")


def load_compiled_manifest(manifest_path: Path | None = None) -> dict[str, Any] | None:
    p = manifest_path or MANIFEST_PATH
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        log_error(f"Failed reading {p}: {exc!r}")
        return None


# Local weapon-space pivot mapping to rotated horizontal inventory slot center `(-15.4, +3.5)`
_PREVIEW_BOUNDS_PIVOT_UU: tuple[float, float, float] = (2.67, -2.97, 15.28)


def assign_struct_array(owner: Any, attr_name: str, items: list[Any]) -> None:
    """Mutate `owner.<attr_name>` in-place via `.clear()` + `.append()` for `pyunrealsdk` `WrappedArray` safety."""
    seq = list(items)
    try:
        arr = getattr(owner, attr_name, None)
        if arr is not None and hasattr(arr, "append"):
            try:
                arr.clear()
            except Exception:
                while len(arr) > 0:
                    arr.pop()
            for it in seq:
                arr.append(it)
            if len(getattr(owner, attr_name)) == len(seq):
                return
    except Exception:
        pass
    setattr(owner, attr_name, seq)


_assign_struct_array = assign_struct_array


def _compute_tightened_fragment_box(
    lo: list[float] | tuple[float, float, float],
    hi: list[float] | tuple[float, float, float],
    xy_scale: float,
    z_scale: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    """Slim unrotated fragment AABBs so `TransformBy(Yaw=+26°, Roll=-66°)` frames horizontal swords at ~90% slot width."""
    cx0 = (float(lo[0]) + float(hi[0])) * 0.5
    cy0 = (float(lo[1]) + float(hi[1])) * 0.5
    cz0 = (float(lo[2]) + float(hi[2])) * 0.5
    ex0 = (float(hi[0]) - float(lo[0])) * 0.5
    ey0 = (float(hi[1]) - float(lo[1])) * 0.5
    ez0 = (float(hi[2]) - float(lo[2])) * 0.5

    s_xy = max(0.05, min(1.5, float(xy_scale)))
    s_z = max(0.10, min(1.5, float(z_scale)))
    px, py, pz = _PREVIEW_BOUNDS_PIVOT_UU

    ox = px + (cx0 - px) * s_xy
    oy = py + (cy0 - py) * min(1.0, s_xy * 1.12)
    oz = pz + (cz0 - pz) * s_z
    ex = max(0.5, ex0 * s_xy)
    ey = max(0.5, ey0 * s_xy)
    ez = max(1.0, ez0 * s_z)
    rad = math.sqrt(ex * ex + ey * ey + ez * ez)
    return (ox, oy, oz), (ex, ey, ez), rad


def apply_gestalt_bounds_framing(
    gestalt: Any = None,
    manifest: dict[str, Any] | None = None,
) -> bool:
    """Update `GestaltPartBounds` on `Gestalt_Inkblade` using `STATE.preview_bounds_{xy,z}_scale`."""
    g_obj = gestalt if gestalt is not None else STATE.custom_gestalt_def
    if g_obj is None:
        return False
    m_doc = manifest if manifest is not None else load_compiled_manifest()
    if not m_doc:
        return False

    cur_bounds = list(getattr(g_obj, "GestaltPartBounds", []) or [])
    if not cur_bounds:
        return False
    tpl_bound = cur_bounds[0]

    new_bounds: list[Any] = []
    for frag in m_doc.get("fragments", []):
        fname = str(frag["fragment_name"])
        bound = copy.copy(tpl_bound)
        bound.SkeletalMeshFragmentName = fname
        orig, ext, rad = _compute_tightened_fragment_box(
            frag["bounds_min"],
            frag["bounds_max"],
            STATE.preview_bounds_xy_scale,
            STATE.preview_bounds_z_scale,
        )
        box = copy.copy(bound.ReferencePoseBounds)
        for idx, axis in enumerate(("X", "Y", "Z")):
            setattr(box.Origin, axis, orig[idx])
            setattr(box.BoxExtent, axis, ext[idx])
        box.SphereRadius = rad
        bound.ReferencePoseBounds = box
        new_bounds.append(bound)

    _assign_struct_array(g_obj, "GestaltPartBounds", new_bounds)
    return True


def _configure_gestalt_definition(
    root_pkg: Any,
    mesh: Any,
    manifest: dict[str, Any],
) -> Any:
    """Clone `Weap_Pistol.GestaltDef_Pistol` -> `Gestalt_Inkblade` with all compiled Inkblade fragments."""
    stock_gestalt = _find("GestaltSkeletalMeshDefinition", "Weap_Pistol.GestaltDef_Pistol")
    if stock_gestalt is None:
        log_warn("Weap_Pistol.GestaltDef_Pistol not found; cannot build Gestalt_Inkblade.")
        return None

    gestalt_path = f"{obj_path(root_pkg)}.Gestalt_Inkblade"
    gestalt = _find("GestaltSkeletalMeshDefinition", gestalt_path)
    if gestalt is None:
        gestalt = unrealsdk.construct_object(
            "GestaltSkeletalMeshDefinition",
            root_pkg,
            "Gestalt_Inkblade",
            KEEP_ALIVE_FLAGS,
            stock_gestalt,
        )
    STATE.keep_alive(gestalt)
    gestalt.GestaltSkeletalMesh = mesh

    # Update elemental socket location on `InkbladeGestaltMesh` (`BoneName='Root'`) to Inkblade cartridge
    sockets_uu = manifest.get("sockets_uu") or {}
    elem_loc = sockets_uu.get("elemental")
    for sock in getattr(mesh, "Sockets", []) or []:
        STATE.keep_alive(sock)
        if elem_loc and str(getattr(sock, "SocketName", "")) == "Acc_Barrel_Elemental2_Elemental":
            try:
                sock.RelativeLocation = unrealsdk.make_struct(
                    "Vector",
                    X=float(elem_loc[0]),
                    Y=float(elem_loc[1]),
                    Z=float(elem_loc[2]),
                )
            except Exception:
                pass

    stock_parts = list(stock_gestalt.GestaltInfos[0].Parts)
    stock_bounds = list(stock_gestalt.GestaltPartBounds)
    if not stock_parts or not stock_bounds:
        log_warn("GestaltDef_Pistol missing template Parts/Bounds entries.")
        return None

    tpl_entry = stock_parts[0]
    tpl_bound = stock_bounds[0]

    new_entries: list[Any] = []
    new_bounds: list[Any] = []
    blade_frag_names: list[str] = []
    elem_frag_names: list[str] = []

    for frag in manifest.get("fragments", []):
        fname = str(frag["fragment_name"])
        entry = copy.copy(tpl_entry)
        entry.SkeletalMeshFragmentName = fname
        entry.FirstIndex = int(frag["start_index"])
        entry.NumPrimitives = int(frag["num_triangles"])
        new_entries.append(entry)

        bound = copy.copy(tpl_bound)
        bound.SkeletalMeshFragmentName = fname
        orig, ext, rad = _compute_tightened_fragment_box(
            frag["bounds_min"],
            frag["bounds_max"],
            STATE.preview_bounds_xy_scale,
            STATE.preview_bounds_z_scale,
        )
        box = copy.copy(bound.ReferencePoseBounds)
        for idx, axis in enumerate(("X", "Y", "Z")):
            setattr(box.Origin, axis, orig[idx])
            setattr(box.BoxExtent, axis, ext[idx])
        box.SphereRadius = rad
        bound.ReferencePoseBounds = box
        new_bounds.append(bound)

        slot_name = str(frag.get("slot", ""))
        if slot_name == "BarrelPartDefinition":
            blade_frag_names.append(fname)
        elif slot_name == "ElementalPartDefinition":
            elem_frag_names.append(fname)

    # Assign GestaltInfos[0].Parts & GestaltPartBounds
    try:
        info0 = copy.copy(gestalt.GestaltInfos[0])
        _assign_struct_array(info0, "Parts", new_entries)
        _assign_struct_array(gestalt, "GestaltInfos", [info0])
    except Exception:
        _assign_struct_array(gestalt.GestaltInfos[0], "Parts", new_entries)

    _assign_struct_array(gestalt, "GestaltPartBounds", new_bounds)

    # Map essential weapon & elemental sockets onto custom Blade & Element fragments
    blade_donor_frags = {"Pistol_Barrel_Bandit", "Pistol_Body_Dahl"}
    elem_donor_frags = {"Acc_Barrel_Elemental2"}
    blade_tpl_mappings = [
        m
        for m in getattr(stock_gestalt, "GestaltSocketMappings", []) or []
        if str(getattr(m, "SkeletalMeshFragmentName", "")) in blade_donor_frags
    ]
    elem_tpl_mappings = [
        m
        for m in getattr(stock_gestalt, "GestaltSocketMappings", []) or []
        if str(getattr(m, "SkeletalMeshFragmentName", "")) in elem_donor_frags
    ]

    new_mappings: list[Any] = []
    for bfrag in blade_frag_names:
        for tm in blade_tpl_mappings:
            mc = copy.copy(tm)
            mc.SkeletalMeshFragmentName = bfrag
            new_mappings.append(mc)
    for efrag in elem_frag_names:
        for tm in elem_tpl_mappings:
            mc = copy.copy(tm)
            mc.SkeletalMeshFragmentName = efrag
            new_mappings.append(mc)

    if new_mappings:
        _assign_struct_array(gestalt, "GestaltSocketMappings", new_mappings)

    return gestalt


_RARITY_MAT_SUFFIX: dict[Rarity, str] = {
    Rarity.COMMON: "",
    Rarity.UNCOMMON: "_Uncommon",
    Rarity.RARE: "_Rare",
    Rarity.VERY_RARE: "_VeryRare",
}


def _build_element_materials(
    mats_pkg: Any,
    tex_pkg_name: str,
    manifest: dict[str, Any],
    donor_mat_part: Any,
    donor_mats_by_rarity: dict[Rarity, Any] | None = None,
) -> tuple[dict[str, Any], dict[tuple[str, Rarity], Any]]:
    """Construct `MIC_Inkblade_<Elem>` + 24 rarity-matched `Mat_Inkblade_<Elem>[_<Rarity>]` parts.

    Cloning each rarity tier from its matching donor material (`Mat_Hyperion_1..4`) preserves native
    White/Green/Blue/Purple ground loot beams (`WillowPickup.InventoryRarityLevel`), item-card title
    bar colors, and rarity-scaled sell values (`MonetaryValueMod`).
    """
    shader = _find("MaterialInterface", MASTER_CLASSMOD_SHADER_PATH)
    if shader is None:
        log_warn(f"Master shader {MASTER_CLASSMOD_SHADER_PATH} not found; cannot build Inkblade MICs.")
        return {}, {}

    normal_name = str(manifest.get("normal_texture", "T_Inkblade_Normal_Flat"))
    normal_tex = _find("Texture2D", f"{tex_pkg_name}.{normal_name}")
    if normal_tex is not None:
        STATE.keep_alive(normal_tex)

    elem_tex_map: dict[str, str] = dict(manifest.get("element_textures") or {})
    built_base: dict[str, Any] = {}
    built_by_rarity: dict[tuple[str, Rarity], Any] = {}

    for elem_id, atlas_key in ELEMENT_ID_TO_ATLAS_KEY.items():
        tex_obj_name = elem_tex_map.get(atlas_key, f"T_Inkblade_Atlas_{atlas_key}")
        diffuse_tex = _find("Texture2D", f"{tex_pkg_name}.{tex_obj_name}")
        if diffuse_tex is None:
            log_warn(f"Missing atlas Texture2D {tex_pkg_name}.{tex_obj_name}")
            continue
        STATE.keep_alive(diffuse_tex)

        mic_name = f"MIC_Inkblade_{atlas_key}"
        mic = _find("MaterialInstanceConstant", f"{obj_path(mats_pkg)}.{mic_name}")
        if mic is None:
            mic = unrealsdk.construct_object(
                "MaterialInstanceConstant",
                mats_pkg,
                mic_name,
                KEEP_ALIVE_FLAGS,
                None,
            )
        STATE.keep_alive(mic)
        mic.SetParent(shader)
        mic.SetTextureParameterValue("p_Diffuse", diffuse_tex)
        if normal_tex is not None:
            mic.SetTextureParameterValue("p_Normal", normal_tex)

        for rarity, suffix in _RARITY_MAT_SUFFIX.items():
            rarity_donor = (
                donor_mats_by_rarity.get(rarity)
                if donor_mats_by_rarity is not None
                else None
            ) or donor_mat_part
            part_name = f"Mat_Inkblade_{atlas_key}{suffix}"
            mp = _find("WeaponPartDefinition", f"{obj_path(mats_pkg)}.{part_name}")
            if mp is None:
                mp = unrealsdk.construct_object(
                    "WeaponPartDefinition",
                    mats_pkg,
                    part_name,
                    KEEP_ALIVE_FLAGS,
                    rarity_donor,
                )
            STATE.keep_alive(mp)
            for clear_field in (
                "WeaponAttributeEffects",
                "ExternalAttributeEffects",
                "AttributeSlotEffects",
                "AttributeSlotUpgrades",
            ):
                try:
                    assign_struct_array(mp, clear_field, [])
                except Exception:
                    pass
            mp.Material = mic
            set_part_gestalt_fragment(mp, "None")
            # The Material part alone decides item rarity -> stamp the exact tier so White / Green /
            # Blue / Purple loot beams, item-card colours, and sell values are always correct.
            set_part_rarity(mp, rarity)
            built_by_rarity[(elem_id, rarity)] = mp
            if rarity == Rarity.COMMON:
                built_base[elem_id] = mp

    return built_base, built_by_rarity


def _register_variants_for_slot(
    parts_pkg: Any,
    catalog_to_kit: dict[str, str | None],
    base_parts: dict[str, Any],
    frags_by_kit_id: dict[str, list[dict[str, Any]]],
    variant_map: dict[tuple[str, str], Any],
    reverse_path_map: dict[str, str] | None,
) -> None:
    """Bind variant 0 onto `base_parts[cat_id]` and construct `__s1..__sN` twin parts for multi-socket/gap fits."""
    for cat_id, kit_id in catalog_to_kit.items():
        base_part = base_parts.get(cat_id)
        if base_part is None:
            continue
        if kit_id is None:
            set_part_gestalt_fragment(base_part, "None")
            continue

        variants = frags_by_kit_id.get(kit_id) or []
        if not variants:
            log_warn(f"Manifest has no fragment for kit_id={kit_id!r} ({cat_id})")
            set_part_gestalt_fragment(base_part, "None")
            continue

        v0 = variants[0]
        set_part_gestalt_fragment(base_part, str(v0["fragment_name"]))
        for parent_kid in v0.get("parent_part_ids", []):
            variant_map[(cat_id, str(parent_kid))] = base_part

        for var in variants[1:]:
            v_idx = int(var["variant_index"])
            v_frag = str(var["fragment_name"])
            v_obj_name = f"Part_{cat_id}__s{v_idx}"
            v_part = _find("WeaponPartDefinition", f"{obj_path(parts_pkg)}.{v_obj_name}")
            if v_part is None:
                v_part = unrealsdk.construct_object(
                    "WeaponPartDefinition",
                    parts_pkg,
                    v_obj_name,
                    KEEP_ALIVE_FLAGS,
                    base_part,
                )
            STATE.keep_alive(v_part)
            set_part_gestalt_fragment(v_part, v_frag)
            STATE.all_extra_part_objects.append(v_part)
            if reverse_path_map is not None:
                reverse_path_map[obj_path(v_part)] = cat_id
            for parent_kid in var.get("parent_part_ids", []):
                variant_map[(cat_id, str(parent_kid))] = v_part


def setup_custom_inkblade_assets(
    root_pkg: Any,
    parts_pkg: Any,
    mats_pkg: Any,
    donor_mat_part: Any,
    manifest_path: Path | None = None,
    donor_mats_by_rarity: dict[Rarity, Any] | None = None,
) -> bool:
    """Load `MeleeArsenalTextures` + `MeleeArsenalMeshes` and bind `Gestalt_Inkblade` + 6 element atlases."""
    if unrealsdk is None:
        return False

    manifest = load_compiled_manifest(manifest_path)
    if manifest is None:
        log_warn(f"No compiled Inkblade manifest at {manifest_path or MANIFEST_PATH}; using donor pistol meshes.")
        return False

    meshes_pkg_name = str(manifest.get("meshes_package", "MeleeArsenalMeshes"))
    tex_pkg_name = str(manifest.get("textures_package", "MeleeArsenalTextures"))
    mesh_export_name = str(manifest.get("mesh_export", "InkbladeGestaltMesh"))

    try:
        cooked_dir = _locate_cooked_pcconsole_dir()
        _try_stage_bundled_upks(cooked_dir, (tex_pkg_name, meshes_pkg_name))

        tex_pkg = _safe_load_custom_package(tex_pkg_name, cooked_dir)
        meshes_pkg = _safe_load_custom_package(meshes_pkg_name, cooked_dir)
        if tex_pkg is None or meshes_pkg is None:
            return False

        mesh = _find("SkeletalMesh", f"{meshes_pkg_name}.{mesh_export_name}")
        if mesh is None:
            log_warn(f"SkeletalMesh {meshes_pkg_name}.{mesh_export_name} not found after loading package.")
            return False
        STATE.keep_alive(mesh)

        gestalt = _configure_gestalt_definition(root_pkg, mesh, manifest)
        if gestalt is None:
            return False

        elem_mats, elem_mats_by_rarity = _build_element_materials(
            mats_pkg,
            tex_pkg_name,
            manifest,
            donor_mat_part,
            donor_mats_by_rarity=donor_mats_by_rarity,
        )
        if len(elem_mats) < len(ELEMENT_ID_TO_ATLAS_KEY):
            log_warn(
                f"Only built {len(elem_mats)}/{len(ELEMENT_ID_TO_ATLAS_KEY)} Inkblade element materials; aborting custom swap."
            )
            return False

        # Group manifest fragments by `part_id` sorted by `variant_index`
        frags_by_kit_id: dict[str, list[dict[str, Any]]] = {}
        for frag in manifest.get("fragments", []):
            frags_by_kit_id.setdefault(str(frag["part_id"]), []).append(frag)
        for lst in frags_by_kit_id.values():
            lst.sort(key=lambda d: int(d.get("variant_index", 0)))

        # 1. Assign Gestalt_Inkblade onto all 5 custom WeaponTypeDefinitions
        for wt_obj in STATE.maker_to_wt.values():
            wt_obj.GestaltMesh = gestalt

        # 2. Suppress donor pistol Body geometry (`Body` is invisible structural glue on melee weapons)
        for body_part in STATE.body_parts.values():
            set_part_gestalt_fragment(body_part, "None")

        # 3. Bind Blades, Grips, Accessories (including blade-fitted `__s1..__s4` twins), and Elements
        STATE.grip_variant_parts.clear()
        STATE.accessory_variant_parts.clear()
        STATE.element_variant_parts.clear()
        STATE.all_extra_part_objects.clear()

        for blade_id, kit_id in BLADE_TO_KIT_ID.items():
            bp = STATE.blade_parts.get(blade_id)
            variants = frags_by_kit_id.get(kit_id) or []
            if bp is not None and variants:
                set_part_gestalt_fragment(bp, str(variants[0]["fragment_name"]))

        _register_variants_for_slot(
            parts_pkg=parts_pkg,
            catalog_to_kit=GRIP_TO_KIT_ID,
            base_parts=STATE.grip_parts,
            frags_by_kit_id=frags_by_kit_id,
            variant_map=STATE.grip_variant_parts,
            reverse_path_map=STATE.part_path_to_grip_id,
        )
        _register_variants_for_slot(
            parts_pkg=parts_pkg,
            catalog_to_kit=ACCESSORY_TO_KIT_ID,
            base_parts=STATE.accessory_parts,
            frags_by_kit_id=frags_by_kit_id,
            variant_map=STATE.accessory_variant_parts,
            reverse_path_map=STATE.part_path_to_accessory_id,
        )
        _register_variants_for_slot(
            parts_pkg=parts_pkg,
            catalog_to_kit=ELEMENT_TO_KIT_ID,
            base_parts=STATE.element_parts,
            frags_by_kit_id=frags_by_kit_id,
            variant_map=STATE.element_variant_parts,
            reverse_path_map=STATE.part_path_to_element_id,
        )

        STATE.element_material_parts = elem_mats
        STATE.element_material_parts_by_rarity = elem_mats_by_rarity
        STATE.all_extra_part_objects.extend(elem_mats_by_rarity.values())
        STATE.custom_skeletal_mesh = mesh
        STATE.custom_gestalt_def = gestalt
        STATE.custom_kit_revision = str(manifest.get("kit_revision", "unknown"))
        STATE.custom_assets_active = True

        log_info(
            f"Activated custom Inkblade 3D Gestalt ({STATE.custom_kit_revision}): "
            f"{len(manifest.get('fragments', []))} fragments, {len(elem_mats)} element atlases "
            f"({len(elem_mats_by_rarity)} rarity-matched material parts)."
        )
        return True
    except Exception as exc:
        log_error(f"setup_custom_inkblade_assets failed: {exc!r}\n{traceback.format_exc()}")
        return False


def resolve_fitted_grip_part(grip_id: str, blade_id: str | None = None) -> Any:
    if STATE.custom_assets_active and blade_id:
        blade_kid = BLADE_TO_KIT_ID.get(blade_id, "")
        hit = STATE.grip_variant_parts.get((grip_id, blade_kid))
        if hit is not None:
            return hit
    return STATE.grip_parts.get(grip_id)


def resolve_fitted_accessory_part(
    acc_id: str,
    blade_id: str | None = None,
    grip_id: str | None = None,
) -> Any:
    if STATE.custom_assets_active:
        if blade_id:
            blade_kid = BLADE_TO_KIT_ID.get(blade_id, "")
            hit = STATE.accessory_variant_parts.get((acc_id, blade_kid))
            if hit is not None:
                return hit
        if grip_id:
            grip_kid = GRIP_TO_KIT_ID.get(grip_id, "")
            hit = STATE.accessory_variant_parts.get((acc_id, grip_kid))
            if hit is not None:
                return hit
    return STATE.accessory_parts.get(acc_id)


def resolve_fitted_element_part(elem_id: str, blade_id: str | None = None) -> Any:
    if STATE.custom_assets_active and blade_id:
        blade_kid = BLADE_TO_KIT_ID.get(blade_id, "")
        hit = STATE.element_variant_parts.get((elem_id, blade_kid))
        if hit is not None:
            return hit
    return STATE.element_parts.get(elem_id)


def resolve_fitted_material_part(
    maker: Manufacturer,
    rarity: Rarity,
    elem_id: str | None = None,
) -> Any:
    if STATE.custom_assets_active and (STATE.element_material_parts_by_rarity or STATE.element_material_parts):
        target_elem_id = elem_id
        if not target_elem_id or (
            target_elem_id not in STATE.element_material_parts
            and (target_elem_id, rarity) not in STATE.element_material_parts_by_rarity
        ):
            default_elem = MANUFACTURERS[maker].allowed_elements[0]
            target_elem_id = ELEMENT_PART_BY_TYPE[default_elem].part_id
        by_rar = STATE.element_material_parts_by_rarity.get((target_elem_id, rarity))
        if by_rar is not None:
            return by_rar
        if target_elem_id in STATE.element_material_parts:
            return STATE.element_material_parts[target_elem_id]
    return STATE.material_parts.get((maker, rarity)) or STATE.material_parts.get((maker, Rarity.UNCOMMON))


def sync_definition_data_visual_parts(dd: Any) -> bool:
    """Ensure `dd`'s Grip, Accessory1, Elemental, Body, and Material parts match its exact Blade + Element."""
    if not STATE.custom_assets_active or dd is None:
        return False

    barrel_p = obj_path(getattr(dd, "BarrelPartDefinition", None))
    blade_id = STATE.part_path_to_blade_id.get(barrel_p)
    if blade_id is None:
        wt_p = obj_path(getattr(dd, "WeaponTypeDefinition", None))
        bal_p = obj_path(getattr(dd, "BalanceDefinition", None))
        maker = STATE.wt_path_to_maker.get(wt_p)
        if maker is None and bal_p in STATE.balance_path_to_meta:
            maker = STATE.balance_path_to_meta[bal_p][0]
        if maker is None:
            return False
        blade_id = BLADE_BY_MANUFACTURER[maker].part_id

    maker = BLADES[blade_id].manufacturer
    mspec = MANUFACTURERS[maker]
    bal_p = obj_path(getattr(dd, "BalanceDefinition", None))
    rarity = STATE.balance_path_to_meta[bal_p][1] if bal_p in STATE.balance_path_to_meta else Rarity.UNCOMMON

    grip_p = obj_path(getattr(dd, "GripPartDefinition", None))
    grip_id = STATE.part_path_to_grip_id.get(grip_p, f"Grip_{maker.value}")

    acc_p = obj_path(getattr(dd, "Accessory1PartDefinition", None))
    acc_id = STATE.part_path_to_accessory_id.get(acc_p, "Acc_None")

    elem_p = obj_path(getattr(dd, "ElementalPartDefinition", None))
    default_elem_id = ELEMENT_PART_BY_TYPE[mspec.allowed_elements[0]].part_id
    elem_id = STATE.part_path_to_element_id.get(elem_p, default_elem_id)

    changed = False
    body_target = STATE.body_parts.get(maker)
    grip_target = resolve_fitted_grip_part(grip_id, blade_id=blade_id)
    acc_target = resolve_fitted_accessory_part(acc_id, blade_id=blade_id, grip_id=grip_id)
    elem_target = resolve_fitted_element_part(elem_id, blade_id=blade_id)
    mat_target = resolve_fitted_material_part(maker, rarity, elem_id=elem_id)
    if mat_target is not None and is_custom_asset_part(mat_target):
        set_part_rarity(mat_target, rarity)

    for field_name, target_obj in (
        ("BodyPartDefinition", body_target),
        ("GripPartDefinition", grip_target),
        ("Accessory1PartDefinition", acc_target),
        ("ElementalPartDefinition", elem_target),
        ("MaterialPartDefinition", mat_target),
        ("SightPartDefinition", None),
        ("StockPartDefinition", None),
    ):
        if target_obj is None and field_name not in ("SightPartDefinition", "StockPartDefinition"):
            continue
        if getattr(dd, field_name, None) != target_obj:
            setattr(dd, field_name, target_obj)
            changed = True

    return changed
