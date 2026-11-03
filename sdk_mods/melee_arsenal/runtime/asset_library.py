"""AssetLibraryManager registration so runtime melee definitions serialize cleanly in memory.

Follows the exact verified BL2 `AssLibMan` structure:
- `library.Sublibraries` is an array of `str` sublibrary paths (`"MeleeArsenalDev.MeleeSublib_..."`).
- `library.SublibraryLinks` is an array of `PackageAssetSublibrary` UObjects.
- Each `PackageAssetSublibrary` holds `LibraryType`, `CachedPackageName`, and `AssetPaths`
  (`NameBasedObjectPath` structs).
"""
from __future__ import annotations

from typing import Any, Iterable
import json
from pathlib import Path

try:
    import unrealsdk  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    unrealsdk = None  # type: ignore[assignment]

from .log import log_debug, log_info, log_warn, obj_path
from .state import KEEP_ALIVE_FLAGS, STATE


_SUBLIBRARIES_BY_TYPE: dict[str, Any] = {}
_ASSET_LIBRARY_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "asset_library_manifest.json"
_ASSET_ORDER_CACHE: dict[str, list[str]] | None = None
_LAST_REGISTERED_PATHS: dict[str, list[str]] = {}


def _load_asset_order() -> dict[str, list[str]]:
    """Frozen registration order for bank/save compatibility.

    New assets are appended after the frozen prefix; existing entries are never reordered.
    Missing manifest falls back to deterministic path order (still stable, but not certified).
    """
    global _ASSET_ORDER_CACHE
    if _ASSET_ORDER_CACHE is not None:
        return _ASSET_ORDER_CACHE
    try:
        raw = json.loads(_ASSET_LIBRARY_MANIFEST_PATH.read_text(encoding="utf-8"))
        if raw.get("schema") != "melee_arsenal.asset_library/1.0":
            raise ValueError(f"unexpected schema {raw.get('schema')!r}")
        libs = raw.get("libraries")
        if not isinstance(libs, dict):
            raise ValueError("missing libraries")
        _ASSET_ORDER_CACHE = {
            str(k): [str(p) for p in v]
            for k, v in libs.items()
            if isinstance(v, list)
        }
    except Exception as exc:
        log_warn(f"Asset-library order manifest unavailable ({exc!r}); using path order.")
        _ASSET_ORDER_CACHE = {}
    return _ASSET_ORDER_CACHE


def order_assets_for_registration(class_name: str, assets: Iterable[Any]) -> list[Any]:
    by_path: dict[str, Any] = {}
    for asset in assets:
        if asset is None:
            continue
        by_path.setdefault(obj_path(asset), asset)
    frozen = _load_asset_order().get(class_name, [])
    ordered: list[Any] = []
    seen: set[str] = set()
    for path in frozen:
        asset = by_path.get(path)
        if asset is not None and path not in seen:
            ordered.append(asset)
            seen.add(path)
    for path in sorted(by_path):
        if path not in seen:
            ordered.append(by_path[path])
            seen.add(path)
    return ordered


def _make_name_based_object_path(full_obj_path: str, package_name: str) -> Any:
    prefix = f"{package_name}."
    rel = full_obj_path[len(prefix) :] if full_obj_path.startswith(prefix) else full_obj_path
    parts = rel.replace(":", ".").split(".")
    padded = ["None"] * max(0, 6 - len(parts)) + parts[-6:]
    return unrealsdk.make_struct(
        "NameBasedObjectPath",
        PathComponentNames=padded,
        IsSubobjectMask=0,
    )


def _get_live_managers() -> list[Any]:
    if unrealsdk is None:
        return []
    managers: list[Any] = []
    try:
        wg = unrealsdk.find_class("WillowGlobals").ClassDefaultObject.GetWillowGlobals()
        if wg is not None and getattr(wg, "AssLibMan", None) is not None:
            managers.append(wg.AssLibMan)
    except Exception as exc:
        log_debug(f"_get_live_managers failed: {exc!r}")
    return managers


def _find_library_for_class(class_name: str, libraries: list[Any]) -> Any:
    try:
        cls = unrealsdk.find_class(class_name)
        chain: list[str] = []
        while cls is not None:
            chain.append(str(cls.Name))
            cls = cls.SuperField
        ranked = [
            (chain.index(str(lib.LibraryType.Name)), lib)
            for lib in libraries
            if lib is not None and lib.LibraryType is not None and str(lib.LibraryType.Name) in chain
        ]
        return min(ranked, key=lambda pair: pair[0])[1] if ranked else None
    except Exception:
        for lib in libraries:
            if lib is not None and lib.LibraryType is not None and str(lib.LibraryType.Name) == class_name:
                return lib
        return None


def register_assets_in_library(class_name: str, assets: Iterable[Any], set_id: int = 0) -> int:
    """Register custom definitions of `class_name` into `WillowGlobals.AssLibMan` (`set_id=0`)."""
    if unrealsdk is None or STATE.root_package is None:
        return 0

    asset_list = order_assets_for_registration(class_name, assets)
    _LAST_REGISTERED_PATHS[class_name] = [obj_path(a) for a in asset_list]
    if not asset_list:
        return 0

    managers = _get_live_managers()
    if not managers:
        return 0

    pkg = STATE.root_package
    pkg_name = str(pkg.Name)
    placed_count = 0

    try:
        for mgr in managers:
            base_libs: list[Any] = []
            for asset_set in getattr(mgr, "RuntimeAssetLibraries", []) or []:
                if int(getattr(asset_set, "Id", -1)) == set_id:
                    base_libs = [
                        lib
                        for lib in asset_set.Libraries
                        if lib is not None and getattr(lib, "LibraryType", None) is not None
                    ]
                    break
            if not base_libs:
                continue

            target_lib = _find_library_for_class(class_name, base_libs)
            if target_lib is None:
                continue

            lib_type_name = str(target_lib.LibraryType.Name)
            sub_name = f"MeleeArsenalSublib_{lib_type_name}"
            sub_full = f"{pkg_name}.{sub_name}"

            sub = _SUBLIBRARIES_BY_TYPE.get(lib_type_name)
            if sub is None:
                try:
                    sub = unrealsdk.find_object("PackageAssetSublibrary", sub_full)
                except Exception:
                    sub = None
            if sub is None:
                sub = unrealsdk.construct_object(
                    "PackageAssetSublibrary",
                    pkg,
                    sub_name,
                    KEEP_ALIVE_FLAGS,
                    None,
                )
                STATE.keep_alive(sub)

            sub.LibraryType = target_lib.LibraryType
            sub.CachedPackageName = pkg_name

            # Merge any previously registered paths with the new assets (append-only order)
            existing_paths = list(getattr(sub, "AssetPaths", []) or [])
            seen_keys: set[tuple[str, ...]] = {
                tuple(str(x) for x in getattr(ep, "PathComponentNames", []))
                for ep in existing_paths
            }
            new_paths = list(existing_paths)
            for asset in asset_list:
                nb_path = _make_name_based_object_path(obj_path(asset), pkg_name)
                key = tuple(str(x) for x in nb_path.PathComponentNames)
                if key not in seen_keys:
                    seen_keys.add(key)
                    new_paths.append(nb_path)
                    placed_count += 1
            sub.AssetPaths = new_paths
            _SUBLIBRARIES_BY_TYPE[lib_type_name] = sub

            existing_names = [str(entry) for entry in target_lib.Sublibraries]
            if sub_full not in existing_names:
                target_lib.Sublibraries.append(sub_full)
            if hasattr(target_lib, "SublibraryLinks"):
                if not any(link is not None and link == sub for link in target_lib.SublibraryLinks):
                    target_lib.SublibraryLinks.append(sub)

        if placed_count > 0:
            log_info(f"Registered {placed_count} {class_name} assets in AssLibMan.")
    except Exception as exc:
        log_warn(f"Non-fatal AssLibMan registration warning for {class_name}: {exc!r}")

    return placed_count


def snapshot_asset_library_manifest() -> dict[str, Any]:
    """Return the actual registration order observed in this process (for freezing a release)."""
    pkg_name = str(STATE.root_package.Name) if STATE.root_package is not None else "MeleeArsenalDev"
    return {
        "schema": "melee_arsenal.asset_library/1.0",
        "package": pkg_name,
        "libraries": {k: list(dict.fromkeys(v)) for k, v in _LAST_REGISTERED_PATHS.items()},
    }
