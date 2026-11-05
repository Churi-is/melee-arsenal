"""The incremental, frame-budgeted dump job.

A B2 session can contain hundreds of thousands of UObjects.  Doing the whole
dump synchronously would freeze the game for minutes, so the job is a small
state machine driven from a tick hook:

    collect -> classes -> functions -> structs -> enums -> consts
            -> objects -> packages -> assets
            -> defaults -> instances -> globals -> xrefs -> manifest

Each phase streams items into numbered JSON shards.  The tick loop stops at a
time budget (a few milliseconds by default), so the game stays playable while a
dump is running.
"""

from __future__ import annotations

import fnmatch
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import api, reflect
from .names import INSTANCE_PRIORITY, PRIORITY_CLASS_GLOBS, is_definition_class_name
from .values import ValueSerializer
from .writer import ShardWriter

UTC = timezone.utc

MAX_ERRORS = 100

CORE_PHASES = ("classes", "functions", "structs", "enums", "consts")
DEFINITION_PHASES = CORE_PHASES + ("objects", "packages", "assets")
FULL_PHASES = DEFINITION_PHASES + ("defaults", "instances", "globals", "xrefs")

PROFILES: dict[str, tuple[str, ...]] = {
    "core": CORE_PHASES,
    "definitions": DEFINITION_PHASES,
    "full": FULL_PHASES,
}


@dataclass
class DumpSettings:
    profile: str = "full"
    include_inherited_defaults: bool = False
    capture_defaults: bool = True
    capture_instances: bool = True
    capture_globals: bool = True
    value_depth: int = 2
    array_limit: int = 24
    string_limit: int = 384
    max_instances: int = 1000
    max_xrefs: int = 200_000
    max_props_per_object: int = 512
    shard_items: int = 1000
    tick_budget_ms: float = 6.0


@dataclass
class PhaseStat:
    name: str
    started_at: str = ""
    finished_at: str = ""
    seconds: float = 0.0
    items: int = 0
    errors: int = 0


class DumpJob:
    def __init__(self, settings: DumpSettings | None = None, *, output_root: Path | None = None) -> None:
        self.settings = settings or DumpSettings()
        self.output_root = output_root or (Path(__file__).resolve().parent.parent / "dumps")
        self.state = "idle"
        self.dump_id: str | None = None
        self.root: Path | None = None
        self.error: str | None = None
        self.started_at: str | None = None
        self.finished_at: str | None = None

        self._objects: list[Any] = []
        self._object_paths: list[str] = []
        self._object_classes: list[str] = []
        self._instance_meta: list[tuple[int, str, str]] = []
        self._object_count = 0
        self._classes: list[Any] = []
        self._functions: list[Any] = []
        self._structs: list[Any] = []
        self._enums: list[Any] = []
        self._consts: list[Any] = []
        self._packages: list[Any] = []

        self._class_paths: set[str] = set()
        self._class_by_path: dict[str, Any] = {}
        self._function_index: dict[tuple[str, str], str] = {}
        self._functions_by_owner: dict[str, list[str]] = {}
        self._direct_subclasses: dict[str, list[str]] = {}
        self._struct_subclasses: dict[str, list[str]] = {}

        self._plan: list[str] = []
        self._phase_index = -1
        self._phase_name = ""
        self._phase_started = 0.0
        self._iterator: Iterator[Any] | None = None
        self._writer: ShardWriter | None = None
        self._phase_errors = 0
        self._phase_items = 0
        self._phase_stats: list[PhaseStat] = []
        self._section_stats: dict[str, dict[str, Any]] = {}
        self._in_tick = False
        self._last_journal = 0.0
        self._items_since_flush = 0

        self._package_object_count: dict[str, int] = {}
        self._package_class_counts: dict[str, dict[str, int]] = {}
        self._assets: list[dict[str, Any]] = []
        self._instances: list[Any] = []
        self._instance_candidates = 0
        self._globals_consts: list[Any] = []

        self._xref_list: list[dict[str, Any]] = []
        self._xref_seen: set[tuple[str, str, str, str]] = set()
        self._xrefs_truncated = False

        self._objects_by_kind: dict[str, int] = {}
        self._errors: list[dict[str, str]] = []
        self._error_count = 0

        self._serializer: ValueSerializer | None = None

    # ==================================================================
    # Lifecycle
    # ==================================================================

    def start(self, profile: str | None = None) -> dict[str, Any]:
        if self.state == "running":
            return {"ok": False, "error": "a dump is already running"}

        profile_name = (profile or self.settings.profile).lower()
        if profile_name not in PROFILES:
            return {"ok": False, "error": f"unknown profile {profile_name!r}; use {sorted(PROFILES)}"}

        # Freeze the settings for this run.  Option callbacks mutate
        # PROBE.settings while a dump is running; the job must not change shape
        # halfway through a phase.
        self.settings = replace(self.settings, profile=profile_name)

        self._reset()
        self.state = "collecting"
        self.started_at = api.utc_now()
        self.dump_id = self._new_dump_id()
        self.root = self.output_root / self.dump_id
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.state = "error"
            self.error = f"cannot create output directory: {exc!r}"
            return {"ok": False, "error": self.error}

        try:
            self._collect()
        except Exception as exc:
            self.state = "error"
            self.error = f"collection failed: {exc!r}"
            self._record_error("collect", exc)
            self._write_journal("error")
            return {"ok": False, "error": self.error}

        self._plan = self._build_plan(profile_name)
        self._write_journal("running")
        self.state = "running"
        api.info(
            f"dump {self.dump_id} started ({profile_name}); "
            f"{self._object_count} objects, {len(self._classes)} classes, "
            f"{len(self._functions)} functions; output {self.root}"
        )
        return {
            "ok": True,
            "dump_id": self.dump_id,
            "output": str(self.root),
            "profile": profile_name,
            "phases": list(self._plan),
            "objects": self._object_count,
            "classes": len(self._classes),
            "functions": len(self._functions),
        }

    def tick(self, *, budget_ms: float | None = None) -> None:
        if self.state != "running" or self._in_tick:
            return
        self._in_tick = True
        budget = (self.settings.tick_budget_ms if budget_ms is None else budget_ms) / 1000.0
        deadline = time.perf_counter() + max(0.0005, budget)
        processed = 0
        try:
            while self.state == "running":
                if self._writer is None and not self._begin_next_phase():
                    break
                assert self._iterator is not None
                try:
                    item = next(self._iterator)
                except StopIteration:
                    self._finish_phase()
                    continue
                except Exception as exc:
                    self._record_error(self._phase_name, exc)
                    self._finish_phase()
                    continue

                self._phase_errors += self._process_item(self._phase_name, item)
                self._phase_items += 1
                processed += 1
                self._items_since_flush += 1

                if self._items_since_flush >= 128:
                    if self._writer is not None:
                        self._writer.flush()
                    self._items_since_flush = 0
                    self._maybe_journal()

                if time.perf_counter() >= deadline:
                    break
        except Exception as exc:  # pragma: no cover - job must survive
            self._record_error("tick", exc)
            api.exception("dump tick failed", exc)
        finally:
            if self._writer is not None:
                self._writer.flush()
            self._in_tick = False

        if self.state == "running" and self._phase_index >= len(self._plan):
            self._finalize()

    def cancel(self) -> dict[str, Any]:
        if self.state not in {"running", "collecting"}:
            return {"ok": False, "error": f"dump is {self.state}"}
        self.state = "cancelled"
        self.finished_at = api.utc_now()
        self._close_writer()
        if self.root is not None:
            try:
                api.write_json_atomic(
                    self.root / "manifest.json",
                    self._build_manifest(status="cancelled"),
                    indent=1,
                )
            except Exception as exc:  # pragma: no cover - disk full/permission
                api.exception("could not write cancelled manifest", exc)
        self._write_journal("cancelled")
        api.info(f"dump {self.dump_id} cancelled; partial shards left in {self.root}")
        self._release_snapshots()
        return {"ok": True, "dump_id": self.dump_id, "output": str(self.root) if self.root else None}

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "dump_id": self.dump_id,
            "profile": self.settings.profile,
            "phase": self._phase_name,
            "phase_index": self._phase_index + 1,
            "phase_count": len(self._plan),
            "phase_items": self._phase_items,
            "counts": self._section_stats,
            "objects_by_kind": dict(self._objects_by_kind),
            "errors": self._error_count,
            "error": self.error,
            "output": str(self.root) if self.root else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "xrefs": len(self._xref_list),
            "xrefs_truncated": self._xrefs_truncated,
            "assets": len(self._assets),
            "instance_candidates": self._instance_candidates,
        }

    # ==================================================================
    # Collection / indexing
    # ==================================================================

    def _reset(self) -> None:
        self.state = "idle"
        self.dump_id = None
        self.root = None
        self.error = None
        self.started_at = None
        self.finished_at = None
        self._objects = []
        self._object_paths = []
        self._object_classes = []
        self._instance_meta = []
        self._object_count = 0
        self._classes = []
        self._functions = []
        self._structs = []
        self._enums = []
        self._consts = []
        self._packages = []
        self._class_paths = set()
        self._class_by_path = {}
        self._function_index = {}
        self._functions_by_owner = {}
        self._direct_subclasses = {}
        self._struct_subclasses = {}
        self._plan = []
        self._phase_index = -1
        self._phase_name = ""
        self._iterator = None
        self._writer = None
        self._phase_errors = 0
        self._phase_items = 0
        self._phase_stats = []
        self._section_stats = {}
        self._package_object_count = {}
        self._package_class_counts = {}
        self._assets = []
        self._instances = []
        self._instance_candidates = 0
        self._globals_consts = []
        self._xref_list = []
        self._xref_seen = set()
        self._xrefs_truncated = False
        self._objects_by_kind = {}
        self._errors = []
        self._error_count = 0
        self._serializer = None

    def _new_dump_id(self) -> str:
        base = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        candidate = base
        suffix = 1
        while (self.output_root / candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _collect(self) -> None:
        self._objects = [obj for obj in api.find_all("Object") if obj is not None]
        self._classes = [cls for cls in api.find_all("Class") if cls is not None]
        self._functions = [fn for fn in api.find_all("Function") if fn is not None]
        self._structs = [st for st in api.find_all("ScriptStruct") if st is not None]
        self._enums = [en for en in api.find_all("Enum") if en is not None]
        self._consts = [c for c in api.find_all("Const") if c is not None]
        self._packages = [p for p in api.find_all("Package") if p is not None]

        if not self._objects and not self._classes:
            raise RuntimeError(
                "unrealsdk returned no objects; the SDK may still be initializing or "
                "the 'Object'/'Class' class lookups failed"
            )

        # Some SDK builds fail to resolve a bare meta-class name (Enum, Const,
        # Package, ...) even though the objects are present.  Recover the typed
        # lists from the complete object index rather than writing empty
        # sections.  This also de-duplicates any overlap.
        if self._objects:
            if not self._classes:
                self._classes = [obj for obj in self._objects if reflect.object_kind(obj) == "class"]
            if not self._functions:
                self._functions = [obj for obj in self._objects if reflect.object_kind(obj) == "function"]
            if not self._structs:
                self._structs = [obj for obj in self._objects if reflect.object_kind(obj) == "struct"]
            if not self._enums:
                self._enums = [obj for obj in self._objects if reflect.object_kind(obj) == "enum"]
            if not self._consts:
                self._consts = [obj for obj in self._objects if reflect.object_kind(obj) == "const"]
            if not self._packages:
                self._packages = [obj for obj in self._objects if reflect.object_kind(obj) == "package"]

        # Snapshot only the stable metadata (path + class name) while the
        # wrappers are known good, then drop the wrappers.  Every later phase
        # re-resolves a path with find_object() immediately before reading it,
        # so a live object that is destroyed mid-dump cannot leave us holding a
        # dangling pointer.  Keeping 200k+ wrappers for the whole dump was both
        # a memory cost and a fatal-error risk.
        api.info(
            f"dump {self.dump_id}: snapshotting path/class metadata for "
            f"{len(self._objects)} objects (one-time pause)"
        )
        object_paths: list[str] = []
        object_classes: list[str] = []
        instance_meta: list[tuple[int, str, str]] = []
        total_objects = len(self._objects)
        for index, obj in enumerate(self._objects):
            if index and index % 5000 == 0:
                api.info(
                    f"dump {self.dump_id}: metadata snapshot "
                    f"{index}/{total_objects}"
                )
            try:
                path = api.path_of(obj)
                if not path:
                    continue
                class_name = api.class_name_of(obj)
                if not class_name:
                    continue
                class_name = sys.intern(class_name)
                object_paths.append(path)
                object_classes.append(class_name)
                kind = reflect.object_kind(obj)
                if kind in {"class", "function", "struct", "enum", "const", "package", "state", "property", "cdo"}:
                    continue
                if is_definition_class_name(class_name):
                    continue
                instance_meta.append((_cheap_instance_priority(class_name), path, class_name))
            except Exception:
                continue
        self._object_paths = object_paths
        self._object_classes = object_classes
        self._instance_meta = instance_meta
        self._object_count = len(object_paths)
        self._instance_candidates = len(instance_meta)
        self._objects = []

        for cls in self._classes:
            path = api.path_of(cls)
            if not path:
                continue
            self._class_paths.add(path)
            self._class_by_path[path] = cls
            super_path = api.path_of(api.super_struct(cls))
            if super_path:
                self._direct_subclasses.setdefault(super_path, []).append(path)

        for struct in self._structs:
            path = api.path_of(struct)
            super_path = api.path_of(api.super_struct(struct))
            if path and super_path:
                self._struct_subclasses.setdefault(super_path, []).append(path)

        for function in self._functions:
            fn_path = api.path_of(function)
            owner = api.path_of(api.safe_getattr(function, "Outer", None))
            name = api.name_of(function)
            if not fn_path or not owner or not name:
                continue
            self._function_index[(owner, name)] = fn_path
            self._functions_by_owner.setdefault(owner, []).append(fn_path)

        api.info(
            f"collected {self._object_count} objects / {len(self._classes)} classes / "
            f"{len(self._functions)} functions / {len(self._structs)} structs / "
            f"{len(self._enums)} enums / {len(self._consts)} consts / "
            f"{len(self._packages)} packages"
        )

    def _build_plan(self, profile: str) -> list[str]:
        phases = list(PROFILES.get(profile, FULL_PHASES))
        if not self.settings.capture_defaults and "defaults" in phases:
            phases.remove("defaults")
        if not self.settings.capture_instances and "instances" in phases:
            phases.remove("instances")
        if not self.settings.capture_globals and "globals" in phases:
            phases.remove("globals")
        if "xrefs" in phases and not any(p in phases for p in ("defaults", "instances", "globals")):
            phases.remove("xrefs")
        return phases

    def _override_for(self, function: Any) -> str | None:
        owner = api.safe_getattr(function, "Outer", None)
        name = api.name_of(function)
        if owner is None or not name:
            return None
        current = api.super_struct(owner)
        seen: set[int] = set()
        while current is not None and len(seen) < 64:
            marker = id(current)
            if marker in seen:
                break
            seen.add(marker)
            super_path = api.path_of(current)
            if super_path:
                candidate = self._function_index.get((super_path, name))
                if candidate:
                    return candidate
            current = api.super_struct(current)
        return None

    # ==================================================================
    # Phase machinery
    # ==================================================================

    def _begin_next_phase(self) -> bool:
        if self._writer is not None:
            return True
        self._phase_index += 1
        if self._phase_index >= len(self._plan):
            return False

        self._phase_name = self._plan[self._phase_index]
        self._phase_started = time.perf_counter()
        self._phase_items = 0
        self._phase_errors = 0
        self._section_stats.pop(self._phase_name, None)

        if self._phase_name == "xrefs":
            # xrefs are collected by earlier phases; do not create a serializer.
            self._serializer = None
        else:
            self._serializer = ValueSerializer(
                depth=self.settings.value_depth,
                array_limit=self.settings.array_limit,
                string_limit=self.settings.string_limit,
                max_props=self.settings.max_props_per_object,
                on_ref=self._add_xref,
            )

        self._iterator = self._phase_iterator(self._phase_name)
        if self._iterator is None:
            self._finish_phase()
            return self._writer is not None
        self._writer = ShardWriter(
            self.root or Path("."),
            self._phase_name,
            shard_items=self.settings.shard_items,
        )
        api.info(
            f"dump {self.dump_id}: phase {self._phase_name} "
            f"({self._phase_index + 1}/{len(self._plan)})"
        )
        return True

    def _phase_iterator(self, phase: str) -> Iterator[Any] | None:
        if phase == "classes":
            return iter(self._classes)
        if phase == "functions":
            return iter(self._functions)
        if phase == "structs":
            return iter(self._structs)
        if phase == "enums":
            return iter(self._enums)
        if phase == "consts":
            return iter(self._consts)
        if phase == "objects":
            return self._object_records()
        if phase == "packages":
            return iter(self._package_records())
        if phase == "assets":
            return iter(sorted(self._assets, key=lambda item: (item.get("class_name") or "", item.get("path") or "")))
        if phase == "defaults":
            return iter(self._default_targets())
        if phase == "instances":
            return iter(self._instance_targets())
        if phase == "globals":
            return iter(self._global_items())
        if phase == "xrefs":
            return iter(self._xref_list)
        return None

    def _finish_phase(self) -> None:
        if not self._phase_name:
            return
        if self._writer is not None:
            self._writer.close()
            stats = self._writer.stats()
            stats["errors"] = self._phase_errors
            self._section_stats[self._phase_name] = stats
        self._writer = None
        self._iterator = None

        stat = PhaseStat(
            name=self._phase_name,
            started_at="",
            finished_at=api.utc_now(),
            seconds=round(time.perf_counter() - self._phase_started, 3),
            items=self._phase_items,
            errors=self._phase_errors,
        )
        self._phase_stats.append(stat)
        self._write_journal("running")
        api.info(
            f"dump {self.dump_id}: phase {self._phase_name} finished "
            f"({self._phase_items} items, {stat.seconds:.1f}s, {self._phase_errors} errors)"
        )
        self._phase_name = ""

    def _process_item(self, phase: str, item: Any) -> int:
        """Processes one item, returning 1 if it was replaced by an error record."""
        try:
            if phase == "classes":
                path = api.path_of(item)
                record = reflect.describe_class(
                    item,
                    function_paths=self._functions_by_owner.get(path or "", ()),
                    direct_subclasses=self._direct_subclasses.get(path or "", ()),
                )
                self._writer_add(record)
            elif phase == "functions":
                record = reflect.describe_function(item, overrides=self._override_for(item))
                self._writer_add(record)
            elif phase == "structs":
                path = api.path_of(item)
                record = reflect.describe_struct(
                    item,
                    direct_subclasses=self._struct_subclasses.get(path or "", ()),
                )
                self._writer_add(record)
            elif phase == "enums":
                self._writer_add(reflect.describe_enum(item))
            elif phase == "consts":
                self._writer_add(reflect.describe_const(item))
            elif phase == "objects":
                self._process_object_record(item)
            elif phase == "packages":
                self._writer_add(item)
            elif phase == "assets":
                self._writer_add(item)
            elif phase == "defaults":
                self._process_default(item)
            elif phase == "instances":
                self._process_instance(item)
            elif phase == "globals":
                self._writer_add(item)
            elif phase == "xrefs":
                self._writer_add(item)
            return 0
        except Exception as exc:
            if isinstance(item, dict):
                where = item.get("path") or "?"
            else:
                where = api.path_of(item) or api.name_of(item) or "?"
            self._record_error(f"{phase}:{where}", exc)
            if phase in {"classes", "functions", "structs", "enums", "consts", "objects"}:
                self._writer_add(
                    {
                        "path": item.get("path") if isinstance(item, dict) else api.path_of(item),
                        "error": api.shorten(repr(exc), 512),
                    }
                )
            return 1

    # ==================================================================
    # Phase item builders
    # ==================================================================

    def _object_records(self) -> Iterator[dict[str, Any]]:
        """Yields object-index records without trusting stale wrappers.

        At collection time we snapshot each object's path and class name, then
        drop the wrappers.  Here each object is re-resolved with find_object()
        immediately before its metadata is read, so objects destroyed while the
        dump is running are skipped instead of crashing the process.
        """
        total = len(self._object_paths)
        for index, (path, class_name) in enumerate(zip(self._object_paths, self._object_classes)):
            if index % 1000 == 0:
                # Log before reading, so if the process still faults the last
                # line names the object that was being resolved (within 1000).
                api.info(
                    f"dump {self.dump_id}: object index {index}/{total} "
                    f"(reading {path})"
                )
            obj = self._resolve(path, class_name)
            if obj is None:
                continue
            try:
                record = reflect.describe_object(obj, package_path=self._package_of(obj))
            except Exception as exc:
                # Do not let one bad object abort the whole section.
                self._record_error(f"objects:describe:{path}", exc)
                continue
            yield record
            obj = None  # do not keep a wrapper alive across a tick

    def _process_object_record(self, record: dict[str, Any]) -> None:
        self._writer_add(record)

        package_path = record.get("package")
        kind = record.get("kind") or "object"
        self._objects_by_kind[kind] = self._objects_by_kind.get(kind, 0) + 1

        if package_path:
            self._package_object_count[package_path] = (
                self._package_object_count.get(package_path, 0) + 1
            )
            class_counts = self._package_class_counts.setdefault(package_path, {})
            class_name = record.get("class_name") or "?"
            class_counts[class_name] = class_counts.get(class_name, 0) + 1

        if record.get("asset_type") and kind != "cdo":
            self._assets.append(
                {
                    "path": record.get("path"),
                    "name": record.get("name"),
                    "class": record.get("class"),
                    "class_name": record.get("class_name"),
                    "package": package_path,
                    "asset_type": record.get("asset_type"),
                }
            )

    def _package_records(self) -> Iterator[dict[str, Any]]:
        # Package wrappers collected at start can be unloaded by streaming before
        # this phase runs.  Read a fresh list and build all records immediately.
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for package in api.find_all("Package"):
            try:
                path = api.path_of(package)
                if not path:
                    continue
                seen.add(path)
                records.append(
                    reflect.describe_package(
                        package,
                        object_count=self._package_object_count.get(path, 0),
                        class_counts=self._package_class_counts.get(path, {}),
                    )
                )
            except Exception as exc:
                self._record_error("packages:describe", exc)

        # Packages which only appeared as an Outer path still get a synthetic
        # record so the object index joins cleanly.
        for path in sorted(set(self._package_object_count) - seen):
            records.append(
                {
                    "path": path,
                    "name": (path or "").split(".")[-1],
                    "object_flags": None,
                    "internal_index": None,
                    "object_count": self._package_object_count.get(path, 0),
                    "class_counts": self._package_class_counts.get(path, {}),
                }
            )

        records.sort(key=lambda item: item.get("path") or "")
        package = None  # release the last live wrapper before yielding
        for record in records:
            yield record

    def _default_targets(self) -> Iterator[Any]:
        # Classes are usually stable, but dynamic classes can be created and
        # destroyed by mods.  Keep paths only and re-resolve each class just
        # before reading its CDO.
        paths: list[str] = []
        seen: set[str] = set()
        for cls in api.find_all("Class"):
            path = api.path_of(cls)
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
        paths.sort()

        for path in paths:
            cls = api.find_object("Class", path)
            if cls is None:
                # find_object with a fully qualified class path is supported by
                # current pyunrealsdk, but tolerate older call styles.
                for candidate in api.find_all("Class"):
                    if api.path_of(candidate) == path:
                        cls = candidate
                        break
            if cls is None:
                continue
            cdo = api.safe_getattr(cls, "ClassDefaultObject", None)
            if cdo is not None:
                yield cls
                cls = None
                cdo = None

    def _instance_targets(self) -> Iterator[Any]:
        # The candidate list was snapshotted at collection time.  Re-resolve
        # each chosen object now so destroyed objects are skipped safely.
        raw = list(self._instance_meta)
        raw.sort(key=lambda item: (item[0], item[1]))
        wanted = max(0, self.settings.max_instances)
        for _, path, class_name in raw[:wanted]:
            target = self._resolve(path, class_name)
            if target is not None and api.class_name_of(target) == class_name:
                yield target
                target = None

    def _global_items(self) -> Iterator[dict[str, Any]]:
        for const in self._consts:
            record = reflect.describe_const(const)
            yield {
                "type": "constant",
                "path": record.get("path"),
                "name": record.get("name"),
                "value": record.get("value"),
            }

        # Re-resolve matching live singletons from the collection snapshot;
        # never hold their wrappers across ticks.
        records: list[dict[str, Any]] = []
        for path, class_name in zip(self._object_paths, self._object_classes):
            if len(records) >= 64:
                break
            if not any(fnmatch.fnmatchcase(class_name, pattern) for pattern in PRIORITY_CLASS_GLOBS):
                continue
            if (path.rsplit(".", 1)[-1] or "").startswith("Default__"):
                continue
            try:
                obj = self._resolve(path, class_name)
                if obj is None or api.class_name_of(obj) != class_name:
                    continue
                records.append(self._value_record(obj, kind="global"))
            except Exception as exc:
                self._record_error(f"globals:read:{path}", exc)

        obj = None  # release the last live wrapper before yielding
        for record in records:
            yield record

    # ==================================================================
    # Value phases
    # ==================================================================

    def _process_default(self, cls: Any) -> None:
        cdo = api.safe_getattr(cls, "ClassDefaultObject", None)
        if cdo is None or self._serializer is None:
            return
        cls_path = api.path_of(cls)
        properties = api.properties_of(cls)
        if not self.settings.include_inherited_defaults:
            direct = [
                prop
                for prop in properties
                if api.property_owner_path(prop) in {None, cls_path}
            ]
            properties = direct

        cdo_path = api.path_of(cdo)
        values, errors = self._serializer.dump_object_values(cdo, properties, from_path=cdo_path)
        if not values and not errors:
            return
        item: dict[str, Any] = {
            "class": cls_path,
            "cdo": cdo_path,
            "values": values,
        }
        if errors:
            item["errors"] = errors
        self._writer_add(item)

    def _process_instance(self, obj: Any) -> None:
        if self._serializer is None:
            return
        item = self._value_record(obj, kind="instance")
        if item.get("values") or item.get("errors"):
            self._writer_add(item)

    def _value_record(self, obj: Any, *, kind: str) -> dict[str, Any]:
        cls = api.class_of(obj)
        path = api.path_of(obj)
        values, errors = self._serializer.dump_object_values(  # type: ignore[union-attr]
            obj,
            api.properties_of(cls),
            from_path=path,
        )
        item: dict[str, Any] = {
            "kind": kind,
            "path": path,
            "class": api.path_of(cls),
            "class_name": api.class_name_of(obj),
            "values": values,
        }
        if errors:
            item["errors"] = errors
        return item

    # ==================================================================
    # Cross references
    # ==================================================================

    def _add_xref(self, from_path: str, to_path: str, via: str, kind: str) -> None:
        if len(self._xref_list) >= self.settings.max_xrefs:
            self._xrefs_truncated = True
            return
        key = (from_path, to_path, via[:256], kind)
        if key in self._xref_seen:
            return
        self._xref_seen.add(key)
        self._xref_list.append(
            {
                "from": from_path,
                "to": to_path,
                "via": via[:256],
                "kind": kind,
            }
        )

    # ==================================================================
    # Finalization / persistence
    # ==================================================================

    def _finalize(self) -> None:
        if self.state != "running":
            return
        self._close_writer()
        self.finished_at = api.utc_now()
        self.state = "complete"
        manifest = self._build_manifest()
        if self.root is not None:
            api.write_json_atomic(self.root / "manifest.json", manifest, indent=1)
        self._write_journal("complete")
        api.info(
            f"dump {self.dump_id} complete: {self.root} "
            f"({self._error_count} errors, {len(self._xref_list)} xrefs)"
        )
        self._release_snapshots()

    def _release_snapshots(self) -> None:
        """Drop path/class snapshots once the manifest or cancellation is written."""
        self._object_paths = []
        self._object_classes = []
        self._instance_meta = []
        self._objects = []

    def _close_writer(self) -> None:
        if self._writer is not None:
            self._writer.close()
            stats = self._writer.stats()
            stats["errors"] = self._phase_errors
            if self._phase_name:
                self._section_stats[self._phase_name] = stats
            self._writer = None
            self._iterator = None

    def _build_manifest(self, *, status: str = "complete") -> dict[str, Any]:
        environment = api.environment()
        phase_stats = [
            {
                "name": stat.name,
                "finished_at": stat.finished_at,
                "seconds": stat.seconds,
                "items": stat.items,
                "errors": stat.errors,
            }
            for stat in self._phase_stats
        ]

        sections = {}
        for name in self._plan:
            stats = self._section_stats.get(name)
            if stats is None:
                continue
            sections[name] = {
                "schema": stats.get("schema", f"re_probe.v1.{name}"),
                "count": stats.get("count", 0),
                "shards": stats.get("shards", []),
                "errors": stats.get("errors", 0),
            }

        return {
            "format": "bl2-re-probe",
            "schema_version": 1,
            "schema": "re_probe.v1.manifest",
            "dump_id": self.dump_id,
            "profile": self.settings.profile,
            "status": status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "environment": environment,
            "summary": {
                "objects": self._object_count,
                "objects_by_kind": dict(sorted(self._objects_by_kind.items())),
                "classes": len(self._classes),
                "functions": len(self._functions),
                "structs": len(self._structs),
                "enums": len(self._enums),
                "consts": len(self._consts),
                "packages": self._section_stats.get("packages", {}).get("count", len(self._packages)),
                "assets": len(self._assets),
                "defaults": self._section_stats.get("defaults", {}).get("count", 0),
                "instances": self._section_stats.get("instances", {}).get("count", 0),
                "instance_candidates": self._instance_candidates,
                "xrefs": len(self._xref_list),
                "xrefs_truncated": self._xrefs_truncated,
                "errors": self._error_count,
            },
            "limits": {
                "profile": self.settings.profile,
                "value_depth": self.settings.value_depth,
                "array_limit": self.settings.array_limit,
                "string_limit": self.settings.string_limit,
                "max_instances": self.settings.max_instances,
                "max_xrefs": self.settings.max_xrefs,
                "max_props_per_object": self.settings.max_props_per_object,
                "include_inherited_defaults": self.settings.include_inherited_defaults,
                "capture_defaults": self.settings.capture_defaults,
                "capture_instances": self.settings.capture_instances,
                "capture_globals": self.settings.capture_globals,
                "tick_budget_ms": self.settings.tick_budget_ms,
                "shard_items": self.settings.shard_items,
            },
            "sections": sections,
            "phases": phase_stats,
            "errors": {
                "count": self._error_count,
                "samples": self._errors,
            },
            "notes": [
                "classes/functions/structs/enums/consts contain definitions declared by the class itself; walk super/super_chain for inherited members.",
                "objects is an index of every UObject loaded at dump time, including definition objects and CDOs.",
                "defaults contains CDO property values declared directly on each class (or all properties when include_inherited_defaults is true).",
                "instances is a capped, priority-sorted sample of non-definition live objects.",
                "xrefs is a reference graph collected while reading values; it is not a call graph.",
                "BL2 ships without UFunction script bytecode, so static caller/callee data is unavailable. Use the runtime trace (trace-*.json) instead.",
                "Array and string values are capped by limits.array_limit and limits.string_limit; truncated arrays end with a {\"$truncated\": n} element.",
            ],
        }

    def _write_journal(self, status: str) -> None:
        now = time.perf_counter()
        if status == "running" and now - self._last_journal < 2.0:
            return
        self._last_journal = now
        if self.root is None:
            return
        document = {
            "format": "bl2-re-probe",
            "schema_version": 1,
            "dump_id": self.dump_id,
            "status": status,
            "profile": self.settings.profile,
            "phase": self._phase_name,
            "phase_index": self._phase_index + 1,
            "phase_count": len(self._plan),
            "phase_items": self._phase_items,
            "sections": {
                name: {"count": stats.get("count", 0), "errors": stats.get("errors", 0)}
                for name, stats in self._section_stats.items()
            },
            "objects_by_kind": dict(sorted(self._objects_by_kind.items())),
            "errors": self._error_count,
            "started_at": self.started_at,
            "updated_at": api.utc_now(),
            "finished_at": self.finished_at,
            "output": str(self.root),
        }
        try:
            api.write_json_atomic(self.root / "dump.json", document, indent=1)
        except Exception as exc:  # pragma: no cover - disk full/permission
            api.exception("could not write dump journal", exc)

    def _maybe_journal(self) -> None:
        if self.state == "running":
            self._write_journal("running")

    # ==================================================================
    # Helpers
    # ==================================================================

    def _writer_add(self, item: Any) -> None:
        if self._writer is None:
            raise RuntimeError("no active writer")
        self._writer.add(item)

    def _package_of(self, obj: Any) -> str | None:
        current = obj
        seen: set[int] = set()
        for _ in range(32):
            if current is None:
                break
            marker = id(current)
            if marker in seen:
                break
            seen.add(marker)
            if api.class_name_of(current) == "Package":
                return api.path_of(current)
            current = api.safe_getattr(current, "Outer", None)
        return None

    def _resolve(self, path: str, class_name: str | None) -> Any:
        """Re-resolve a path, tolerating either class-name or root-class lookup."""
        obj = api.find_object(class_name, path) if class_name else None
        if obj is None:
            # StaticFindObject with the UObject root accepts any object at the
            # path, which covers meta-objects (Package, Enum, Const, ...) even
            # if a class-name lookup is unavailable.
            obj = api.find_object("Object", path)
        return obj

    def _record_error(self, where: str, exc: BaseException) -> None:
        self._error_count += 1
        if len(self._errors) < MAX_ERRORS:
            self._errors.append({"where": api.shorten(where, 300), "error": api.shorten(repr(exc), 500)})
        if self._error_count <= 20:
            api.exception(f"dump error at {where}", exc)

def _cheap_instance_priority(class_name: str) -> int:
    """Name based approximation of names.instance_priority().

    Exact class priority wins.  Otherwise the common Willow naming patterns are
    enough to decide what to sample first without walking every super chain.
    """
    priority = INSTANCE_PRIORITY.get(class_name)
    if priority is not None:
        return priority

    lowered = class_name.lower()
    if "pawn" in lowered or "controller" in lowered:
        return 1
    if any(token in lowered for token in ("weapon", "inventory", "item", "pickup", "mission")):
        return 1
    if "component" in lowered:
        return 3
    if "actor" in lowered:
        return 2
    return 4
