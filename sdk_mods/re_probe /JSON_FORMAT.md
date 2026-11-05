# RE Probe JSON format (`re_probe.v1`)

This is the single reference for the files RE Probe writes. It explains the
shape of every section and, more importantly, how to join and search them.

RE Probe is read-only: it never edits objects, definitions, saves, or packages.
A dump is a **snapshot** of the UObjects loaded at the time it ran. Running it in
a different map or after spawning different actors produces a different dump.

---

## 1. Where the files are

The mod writes under its own folder:

```
sdk_mods/re_probe/dumps/
└── 20261105-143012/               <- one dump_id per run
    ├── dump.json                  <- live journal (status while running)
    ├── manifest.json              <- written LAST, authoritative index
    ├── classes-0000.json
    ├── classes-0001.json
    ├── functions-0000.json
    ├── structs-0000.json
    ├── enums-0000.json
    ├── consts-0000.json
    ├── objects-0000.json
    ├── ...
    ├── packages-0000.json
    ├── assets-0000.json
    ├── defaults-0000.json
    ├── instances-0000.json
    ├── globals-0000.json
    ├── xrefs-0000.json
    └── traces/
        └── trace-20261105-143512.json   <- written by `reprobe trace stop`
```

Ad-hoc single-object dumps go to `dumps/adhoc/` and are described in §13.

`dump.json` exists from the first second of a run so a crash still leaves
something readable. `manifest.json` is written after every phase has closed its
shards, and also when a run is cancelled (with `status: "cancelled"` and only
the sections that were reached).

### Status values

| File | `status` | Meaning |
| :--- | :--- | :--- |
| `dump.json` | `running` | The job is still working. Some shards may be open/incomplete. |
| `dump.json` | `complete` | Same as a manifest being present. |
| `dump.json` | `cancelled` | Stopped by the user; closed shards are valid, the last shard may be absent. |
| `dump.json` | `error` | Job failed before/while collecting. See `error` / `errors`. |
| `manifest.json` | `complete` | Every section in `sections` was closed cleanly. |
| `manifest.json` | `cancelled` | The run was cancelled; reached sections are listed. |

**Read `manifest.json`, not `dump.json`, for a finished dump.** The manifest
lists the exact shard files that were closed successfully.

---

## 2. Shard files

Sections are split into numbered shards so a dump never has to fit in memory and
a single-file parse does not need multiple gigabytes.

Every shard is a standalone JSON document:

```json
{
  "schema": "re_probe.v1.functions",
  "section": "functions",
  "shard": 0,
  "items": [
    { "...one record...": "..." }
  ]
}
```

To read a whole section:

```bash
jq -s 'map(.items[]) | .[]' sdk_mods/re_probe/dumps/<dump_id>/functions-*.json
```

In Python:

```python
import glob, json

def section(dump_dir: str, name: str):
    for path in sorted(glob.glob(f"{dump_dir}/{name}-*.json")):
        with open(path, encoding="utf-8") as fh:
            yield from json.load(fh)["items"]

for fn in section("sdk_mods/re_probe/dumps/20261105-143012", "functions"):
    if fn["name"] == "ConsumeAmmo":
        print(fn["path"], fn["display_signature"])
```

`manifest.json → sections.<name>.shards` is the authoritative file list; using
the glob is fine for complete dumps but the manifest also records item counts
and byte sizes.

Do not assume a fixed number of shards or that records are sorted. Shard size is
`limits.shard_items` (default 1000) and record order follows GObjects/phase
order. Sort yourself when deterministic output matters.

### 2.1 Manifest fields

`manifest.json` is written last (or on cancel) and is the authoritative index:

| Field | Meaning |
| :--- | :--- |
| `format` | `bl2-re-probe`. |
| `schema_version` / `schema` | `1` / `re_probe.v1.manifest`. |
| `dump_id` | Directory name / timestamp. |
| `profile` | `full`, `definitions`, or `core`. |
| `status` | `complete` or `cancelled`. |
| `started_at` / `finished_at` | UTC timestamps. |
| `environment` | Python version/bits/platform/executable, unrealsdk/pyunrealsdk/mods_base versions, current game/tree if available. |
| `summary` | Total object/class/function/etc. counts, `objects_by_kind`, `instance_candidates`, `xrefs_truncated`, `errors`. |
| `limits` | Every setting that affects what is present: profile, depth, array/string limits, instance/xref limits, property cap, inherited-defaults flag, capture flags, tick budget, shard size. |
| `sections` | Per-section `{schema, count, shards:[{path, shard, items, bytes}], errors}`. `path` is relative to the dump directory. |
| `phases` | Per-phase `{name, finished_at, seconds, items, errors}`. |
| `errors` | `{count, samples:[{where, error}]}` (samples capped). |
| `notes` | Human-readable caveats. |

### 2.2 Journal fields (`dump.json`)

`dump.json` is overwritten while the job runs (at most every couple of seconds).
It has the same `format`/`schema_version`/`dump_id`/`profile`/`status`, plus:

| Field | Meaning |
| :--- | :--- |
| `phase`, `phase_index`, `phase_count`, `phase_items` | Current phase and progress. |
| `sections` | `{name: {count, errors}}` for phases that have already closed. |
| `objects_by_kind` | Live counts during the object phase. |
| `errors` | Error count so far. |
| `started_at` / `updated_at` / `finished_at` | UTC timestamps. |
| `output` | Absolute dump directory. |

Use the journal to monitor a run, and the manifest to read a finished one.

---

## 3. Common conventions

### 3.1 Paths are the primary key

Almost every record has a `path`. Paths are UE3 object path names, produced by
`UObject._path_name()`. They are stable within a game build and are what the
debugger, console, and hook API use.

| Shape | Example | What it is |
| :--- | :--- | :--- |
| `Package.Object` | `WillowGame.Default__WillowWeapon` | An object or a class default object (CDO). |
| `Package.Group.Object` | `WillowGame.Meshes.SM_Blade` | An object inside an asset group. |
| `Package.Class` | `WillowGame.WillowWeapon` | A `UClass`. |
| `Package.Class:Function` | `WillowGame.WillowWeapon:ConsumeAmmo` | A `UFunction` declared on a class. |
| `Package.Class:Function:Param` | `WillowGame.WillowWeapon:ConsumeAmmo:Amount` | A property/parameter belonging to a function. |
| `Package.Class:Property` | `WillowGame.WillowWeapon:ClipSize` | A property declared on a class. |

Paths are case-insensitive in UE3, but the dump preserves the engine's spelling.
When matching, prefer case-insensitive comparisons (`test("..."; "i")` in jq).

`class` on an object record is the **path of the object's UClass**:

* an instance of `WillowWeapon` → `class: "WillowGame.WillowWeapon"`
* the `WillowWeapon` class object itself → `class: "Core.Class"` (or
  `Engine.Class` depending on the build)
* a `UFunction` → `class: "Core.Function"`

`class_name` is the short name (`WillowWeapon`, `Class`, `Function`) for quick
filtering.

### 3.2 Shared object-index fields

Every object in `objects-*.json` has:

| Field | Type | Meaning |
| :--- | :--- | :--- |
| `path` | string | Canonical path. |
| `name` | string | Short object name. |
| `class` | string | Path of the UClass. |
| `class_name` | string | Short class name. |
| `outer` | string \| null | Path of the `Outer`. |
| `package` | string \| null | Root package path. |
| `kind` | string | `object`, `class`, `function`, `struct`, `enum`, `const`, `package`, `state`, `property`, `cdo`. |
| `flags` | integer | Raw `ObjectFlags`. Not decoded. |
| `index` | integer | `InternalIndex` (GObjects slot). Process-local. |
| `address` | integer | Process-local pointer, useful when cross-referencing a debugger. Not stable between runs. |
| `cdo_of` | string | Only for `kind: "cdo"`: the class this is the default object for. |
| `asset_type` | string | Only when the class is in the asset-class list (§8). |

`kind` is a convenience classifier, not an engine field. Definition records also
appear in the object index; the same path appears in `classes`, `functions`,
etc. Use the section that fits the question, and use the object index when you
want one flat list of everything.

### 3.3 Value markers

Values in `defaults`, `instances`, `globals`, `trace` samples and ad-hoc dumps
are plain JSON where possible. These reserved keys carry type information. They
start with `$`, which cannot occur in a UE3 identifier, so they never collide
with a real field.

| Shape | Meaning |
| :--- | :--- |
| `{"$ref": "Package.Object"}` | Reference to another UObject. Look the path up in `objects-*.json`. |
| `{"$type": "Package.Struct", ...fields...}` | Struct value. `$type` is the UScriptStruct/class path. |
| `{"$type": "...", "$elided": "depth"}` | Struct was not expanded because the depth limit was reached. |
| `{"$array_len": 1234, "$elided": "depth"}` | Array was not expanded because the depth limit was reached. |
| `{"$truncated": 987}` | Last element of a truncated array; `987` elements were not written (`null` if the length could not be read). |
| `{"$truncated_props": true}` | A struct/object had more properties than `limits.max_props_per_object`; the extra ones were omitted. |
| `{"$error": "..."}` | Reading this one value failed. The enclosing item may also have an `errors` list. |
| `{"$repr": "..."}` | Unknown wrapper type; the string representation was kept. |
| `{"$native": true, "X": 1.0, ...}` | Native struct with no reflected properties; common fields were read best-effort. |
| `{"$bytes": "0a1b2c"}` | Byte-array/unknown binary value as hex. |
| `{"$float": "nan"}` | Non-finite float (JSON has no NaN/Infinity). |
| `null` | Null object/field. |

Strings longer than `limits.string_limit` end with `…<truncated>`. Arrays have
at most `limits.array_limit` elements (when non-zero).

### 3.4 Flags

`flags` is always the raw integer. `flags_decoded` is a best-effort list of UE3
flag names. The raw value is authoritative; engine builds differ and some names
are undocumented. See `probe/names.py` for the exact tables used.

---

## 4. `classes-*.json`

One record per `UClass` (including `BlueprintGeneratedClass`).

```json
{
  "path": "WillowGame.WillowWeapon",
  "name": "WillowWeapon",
  "package": "WillowGame",
  "object_flags": 0,
  "internal_index": 476,
  "address": 123456789,
  "super": "Engine.Actor",
  "super_chain": ["Engine.Actor", "Engine.Object"],
  "subclasses": ["WillowGame.WillowWeapon_Unique"],
  "interfaces": [],
  "cdo": "WillowGame.Default__WillowWeapon",
  "property_size": 4321,
  "min_alignment": 4,
  "fields": [
    {"name": "ClipSize", "kind": "property", "path": "WillowGame.WillowWeapon:ClipSize"},
    {"name": "ConsumeAmmo", "kind": "function", "path": "WillowGame.WillowWeapon:ConsumeAmmo"}
  ],
  "functions": ["WillowGame.WillowWeapon:ConsumeAmmo"],
  "properties": [ "...property records..." ]
}
```

| Field | Meaning |
| :--- | :--- |
| `super` | Immediate `SuperField` path, or null. |
| `super_chain` | Ancestors **excluding** this class, immediate parent first, root last. |
| `subclasses` | Direct subclasses only. Walk `subclasses` recursively or search all classes for `super == this`. |
| `interfaces` | Implemented interface class paths. |
| `cdo` | Class default object path; null if none was exposed. |
| `property_size` | Reflected memory size, when the SDK exposes it. |
| `fields` | **Fields declared directly on this class**: properties, functions, structs, enums, consts, states. Inherited fields are not repeated. |
| `functions` | Paths of functions declared directly on this class. |
| `properties` | Property records declared directly on this class. |

**Inherited members**: walk `super` / `super_chain`. Each parent's record
contains its own `properties` and `functions`. This is deliberate: a class with
1,000 effective properties does not duplicate all of them in every subclass.

### Property records

These appear inside `classes[].properties`, `functions[].params`,
`functions[].locals`, and nested `inner`/`underlying` fields.

```json
{
  "id": "WillowGame.WillowWeapon:AmmoType",
  "name": "AmmoType",
  "type": "ByteProperty",
  "kind": "byte",
  "declared_in": "WillowGame.WillowWeapon",
  "value_type": "enum EAmmoType",
  "array_dim": 1,
  "element_size": 1,
  "offset": 4,
  "size": 1,
  "end": 5,
  "flags": 1,
  "flags_decoded": ["Edit"],
  "is_param": false,
  "is_out": false,
  "is_return": false,
  "is_optional": false,
  "enum": "WillowGame.EAmmoType",
  "enum_name": "EAmmoType"
}
```

| Field | Meaning |
| :--- | :--- |
| `id` | Property path (the property's own key). |
| `type` | Raw UE3 property class name (`IntProperty`, `StructProperty`, ...). |
| `kind` | Normalized value kind (`int32`, `struct`, `object`, `array`, ...). Prefer this for filtering across engine versions. |
| `declared_in` | Path of the UStruct/UFunction that declared this property. |
| `value_type` | Human-readable type summary, e.g. `array<struct WillowWeaponPartData>`. |
| `offset` | Offset inside the owning struct/object, in bytes. |
| `size` | `array_dim * element_size`, when both are known. |
| `end` | `offset + size`. |
| `is_param` / `is_out` / `is_return` / `is_optional` | Decoded `CPF_Parm` / `CPF_OutParm` / `CPF_ReturnParm` / `CPF_OptionalParm`. |
| `struct` / `struct_name` / `struct_size` | Struct property: target struct path, short name, and byte size. |
| `object_class` / `object_class_name` | Object/class property: allowed class path and short name. |
| `interface_class` | Interface property target. |
| `signature` | Delegate property signature UFunction path. |
| `inner` | Array property: the element property record, recursively. The element's own `struct`, `object_class`, etc. are on `inner`. |
| `underlying` | Enum property: the underlying integer property. |
| `field_mask`, `type_handle` | Bool/GameDataHandle specifics. |
| `modifier_stack`, `other_attribute` | Gearbox attribute-property extras. |

If a field does not exist for a property type, the key is omitted.

---

## 5. `functions-*.json`

One record per `UFunction`. This is the section for **parameter names, types,
offsets and flags**.

```json
{
  "path": "WillowGame.WillowWeapon:ConsumeAmmo",
  "name": "ConsumeAmmo",
  "owner": "WillowGame.WillowWeapon",
  "owner_name": "WillowWeapon",
  "package": "WillowGame",
  "flags": 1024,
  "flags_decoded": ["Native", "Public"],
  "native": true,
  "static": false,
  "event": false,
  "latent": false,
  "exec": false,
  "private": false,
  "protected": false,
  "num_params": 3,
  "params_size": 12,
  "return_value_offset": 8,
  "property_size": 64,
  "display_signature": "(int32 Amount, bool bPlaySound) -> int32",
  "return_type": "int32",
  "return_param": "Remaining",
  "params": [
    {
      "name": "Amount", "type": "IntProperty", "kind": "int32",
      "offset": 0, "is_param": true, "is_out": false,
      "is_return": false, "param_index": 0
    },
    {
      "name": "bPlaySound", "type": "BoolProperty", "kind": "bool",
      "offset": 4, "is_param": true, "is_out": false,
      "is_return": false, "is_optional": true, "param_index": 1
    },
    {
      "name": "Remaining", "type": "IntProperty", "kind": "int32",
      "offset": 8, "is_param": true, "is_out": true,
      "is_return": true, "param_index": 2
    }
  ],
  "locals": [
    {"name": "Temp", "type": "IntProperty", "kind": "int32", "local_index": 0}
  ],
  "overrides": "WillowGame.ParentWeapon:ConsumeAmmo",
  "bytecode_available": false
}
```

| Field | Meaning |
| :--- | :--- |
| `params` | Parameter properties, sorted by `offset`. **The return parameter is included** and has `is_return: true`; use `is_return` to exclude it. |
| `param_index` | Position in the sorted parameter list. |
| `locals` | Non-parameter properties on the function (local variables). |
| `return_param` | Name of the return parameter, or null for void. |
| `return_type` | Normalized return type summary. |
| `display_signature` | Convenience string, e.g. `(out int32 A, string B) -> bool`. |
| `overrides` | Same-named function in the nearest superclass, if found. |
| `bytecode_available` | Always `false` for BL2; see §14. |

`num_params` is the engine's count and normally equals `len(params)`.

### "Who calls it and what does it call?"

There is **no caller/callee data in this section**. BL2's shipped UStruct layout
has no UnrealScript bytecode, so a static call graph cannot be recovered from
reflection. Call relationships are recorded by the runtime tracer instead; see
§12. The `xrefs` section separately records static references (including
delegate bindings to functions), which is a different thing.

---

## 6. `structs-*.json`

One record per `UScriptStruct` (not per class).

```json
{
  "path": "WillowGame.WeaponPartData",
  "name": "WeaponPartData",
  "package": "WillowGame",
  "super": null,
  "super_chain": [],
  "subclasses": [],
  "struct_flags": 0,
  "struct_flags_decoded": [],
  "property_size": 48,
  "min_alignment": 4,
  "properties": [
    {
      "name": "PartName", "type": "NameProperty", "kind": "name",
      "offset": 0, "size": 8, "declared_in": "WillowGame.WeaponPartData"
    },
    {
      "name": "Weight", "type": "FloatProperty", "kind": "float",
      "offset": 8, "size": 4, "declared_in": "WillowGame.WeaponPartData"
    },
    {
      "name": "Nested", "type": "StructProperty", "kind": "struct",
      "offset": 12, "size": 8, "struct": "WillowGame.NestedData",
      "declared_in": "WillowGame.WeaponPartData"
    }
  ],
  "property_count": 3
}
```

* `properties` includes inherited fields (struct inheritance is rare in BL2) and
  is sorted by `offset`, so it doubles as a memory-layout table.
* `declared_in` tells you which superfield actually declared a field.
* Struct values are in `defaults` / `instances`, not here.

---

## 7. `enums-*.json` and `consts-*.json`

Enums:

```json
{
  "path": "WillowGame.EAmmoType",
  "name": "EAmmoType",
  "package": "WillowGame",
  "values": [
    {"name": "AMMO_Pistol", "value": 0, "alias_of": null},
    {"name": "AMMO_SMG", "value": 1, "alias_of": null},
    {"name": "AMMO_Shotgun", "value": 2, "alias_of": null}
  ],
  "value_count": 3
}
```

`alias_of` is the first name seen for that numeric value; engine enums can have
duplicate values. Sorting is by `(value, name)`.

Constants:

```json
{
  "path": "WillowGame.MAX_PARTS",
  "name": "MAX_PARTS",
  "package": "WillowGame",
  "value": "8"
}
```

`value` is a string because UE3 `UConst` stores text.

---

## 8. `objects-*.json`

The complete flat index of every UObject returned by
`unrealsdk.find_all("Object")` at the start of the dump. Collection snapshots
only each object's `path` and class name, then releases the live wrappers; the
`objects` phase re-resolves every path with `find_object()` immediately before
reading it. This is deliberate: holding 200k+ wrappers across a long dump means
a live object can be destroyed and leave a dangling pointer. The trade-off is a
short pause at `collect` (the snapshot) and the possibility that an object
destroyed mid-dump is absent from `objects`. Use it to:

* look up a `$ref` path,
* enumerate every loaded asset/actor/component,
* find objects by class or package,
* cross-reference an `address` in a debugger.

The record shape is §3.2. `kind` classification:

| `kind` | Objects |
| :--- | :--- |
| `class` | `UClass` and `BlueprintGeneratedClass` objects. |
| `function` | `UFunction`. |
| `struct` | `UScriptStruct` / `Struct`. |
| `enum` | `UEnum`. |
| `const` | `UConst`. |
| `package` | `UPackage`. |
| `property` | Any `*Property` UObject (BL2 properties are UObjects). |
| `state` | `UState`. |
| `cdo` | Any object whose name begins `Default__`. `cdo_of` names its class. |
| `object` | Everything else (instances, components, assets, ...). |

`asset_type` is set when `class_name` exactly matches one of the asset classes
in `probe/names.py` (`StaticMesh`, `SkeletalMesh`, `Material`,
`MaterialInstanceConstant`, `Texture2D`, `ParticleSystem`, `AkEvent`,
`AnimSequence`, `Behavior`, ...). This is a convenience list, not exhaustive.

---

## 9. `packages-*.json`

One record per loaded package, with object counts gathered during the object
pass:

```json
{
  "path": "WillowGame",
  "name": "WillowGame",
  "object_count": 18234,
  "class_counts": {
    "WillowWeapon": 42,
    "WillowWeaponDefinition": 850
  }
}
```

`class_counts` is capped implicitly by the number of distinct classes in the
package (it is complete, not sampled). Packages that only appeared as an
`outer/package` path still get a synthetic record with `object_flags` and
`internal_index` null.

---

## 10. `assets-*.json`

A filtered view of `objects` for asset classes:

```json
{
  "path": "WillowGame.Meshes.SM_Blade",
  "name": "SM_Blade",
  "class": "Engine.StaticMesh",
  "class_name": "StaticMesh",
  "package": "WillowGame",
  "asset_type": "StaticMesh"
}
```

CDOs are excluded. Use the `objects` index if you need assets whose class is not
in the built-in list.

---

## 11. `defaults-*.json`, `instances-*.json`, `globals-*.json`

### 11.1 Defaults (CDO values)

One record per class whose CDO has at least one readable direct property:

```json
{
  "class": "WillowGame.WillowWeapon",
  "cdo": "WillowGame.Default__WillowWeapon",
  "values": {
    "ClipSize": 12,
    "AmmoType": 1,
    "WeaponType": {"$ref": "WillowGame.Default__WeaponTypeDefinition"},
    "Parts": [
      {"$type": "WillowGame.WeaponPartData", "PartName": "Blade", "Weight": 1.5}
    ],
    "OwnerActor": null,
    "OnFired": [
      {"function": "WillowGame.WillowWeapon:InstantFire",
       "object": "WillowGame.Default__WillowWeapon"}
    ]
  },
  "errors": []
}
```

* `values` is keyed by **property name**.
* By default it contains only properties **declared directly on that class**, to
  avoid repeating the entire inherited state in every subclass. Set
  `Include inherited CDO values` to dump every effective property instead.
* To reconstruct an effective default, start at the root class and overlay
  values down the `super_chain`.
* Classes whose CDO has no direct properties (or no readable values) are
  omitted; the class is still in `classes`.
* `errors` is present only if some values failed to read.

### 11.2 Instances (live object values)

```json
{
  "kind": "instance",
  "path": "WillowGame.WillowWeapon_0",
  "class": "WillowGame.WillowWeapon",
  "class_name": "WillowWeapon",
  "values": { "...": "..." },
  "errors": []
}
```

Instances are selected from the non-definition objects, sorted by a priority
heuristic (player/controller/weapon/world/game info first, then actors,
components), and capped at `limits.max_instances`. `manifest.summary` reports
`instance_candidates` (how many were available) versus `instances` (how many
were written).

Only non-empty records are written. CDOs, classes, functions, structs, enums,
consts, packages, states and properties are excluded.

### 11.3 Globals

`globals-*.json` mixes two item types:

```json
{"type": "constant", "path": "WillowGame.MAX_PARTS", "name": "MAX_PARTS", "value": "8"}
{"type": "global", "kind": "global", "path": "WillowGame.WillowGameEngine_0",
 "class": "WillowGame.WillowGameEngine", "class_name": "WillowGameEngine",
 "values": { "...": "..." }}
```

Constants duplicate `consts` intentionally (one small file to answer "what
global constants exist"). Global objects are live singletons matching engine,
game info, world info, viewport, player controller, player pawn and globals class
name patterns, capped at 64. CDOs are excluded (they are in `defaults`).

The sections map to the original request as follows:

| Asked for | Where |
| :--- | :--- |
| all objects | `objects` |
| functions with parameter types/names | `functions[].params` |
| who calls it / what it calls | `traces/trace-*.json` (runtime) |
| asset paths | `objects`, `assets`, `packages` |
| structs | `structs` (+ nested struct values) |
| globals | `consts`, `globals`, `defaults` |
| everything else | `classes`, `properties` inside them, `enums`, `xrefs`, `instances` |

---

## 12. `xrefs-*.json` and runtime traces

### 12.1 Xrefs: static references, not calls

```json
{
  "from": "WillowGame.Default__WillowWeapon",
  "to": "WillowGame.Default__WeaponTypeDefinition",
  "via": "WeaponType",
  "kind": "object"
}
```

| Field | Meaning |
| :--- | :--- |
| `from` | Object whose value was read (CDO path or instance path). |
| `to` | Referenced object path. |
| `via` | Property path inside the `from` value, e.g. `Parts[0].PartName` or `OnFired[0]`. |
| `kind` | `object`, `function`, or `delegate_object`. |

`xrefs` is built from values actually read (defaults, instances, globals), not
from every object. It is capped by `limits.max_xrefs`; see
`summary.xrefs_truncated`. To find referrers to a target:

```bash
jq -s 'map(.items[]) | map(select(.to == "WillowGame.Default__WeaponTypeDefinition"))' \
  dumps/<id>/xrefs-*.json
```

`kind: "function"` means a delegate value points at a `UFunction`. That is a
**reference**, not proof the function was called.

### 12.2 Trace: observed caller → callee edges

Written by `reprobe trace stop` (or `reprobe trace write`) to
`dumps/traces/trace-<session>.json`:

```json
{
  "schema": "re_probe.v1.trace",
  "session": {
    "session_id": "20261105-143512",
    "started_at": "2026-11-05T14:35:12Z",
    "stopped_at": "2026-11-05T14:36:40Z",
    "patterns": ["WillowGame.WillowWeapon:*"],
    "settings": {
      "max_hooks": 512,
      "capture_args": true,
      "max_samples_per_edge": 2,
      "max_edges": 100000,
      "measure_time": false
    },
    "hooks": 48,
    "calls": 12000,
    "edges": 37,
    "functions": 12,
    "samples": 74,
    "errors": 0,
    "dropped_edges": 0,
    "stack_drift": 0
  },
  "functions": [
    {
      "path": "WillowGame.WillowWeapon:ConsumeAmmo",
      "calls": 700,
      "callers": [
        {"path": "WillowGame.WillowPlayerController:PlayerTick", "count": 500},
        {"path": null, "count": 200}
      ],
      "objects": [
        {"path": "WillowGame.WillowWeapon_0", "count": 400}
      ],
      "objects_truncated": false
    }
  ],
  "edges": [
    {
      "caller": "WillowGame.WillowPlayerController:PlayerTick",
      "callee": "WillowGame.WillowWeapon:ConsumeAmmo",
      "count": 500,
      "max_depth": 3,
      "total_seconds": null,
      "samples": [
        {
          "object": "WillowGame.WillowWeapon_0",
          "args": {
            "$type": "WillowGame.WillowWeapon:ConsumeAmmo",
            "Amount": 3,
            "bPlaySound": true,
            "Remaining": 0
          }
        }
      ]
    }
  ],
  "notes": ["..."]
}
```

* **`caller: null`** means the call entered from outside the hooked function
  set (engine tick, native caller, or a function you did not arm).
* `functions[].callers` / `callees` are derived from `edges`; `edges` is the
  authoritative list. `functions` aggregates by callee.
* `objects` is the objects the function was called on, capped at 32 per function.
* `samples` are capped snapshots of arguments, **not** every call. `args` uses
  the same value markers as §3.3.
* `max_depth` is the hook-stack depth observed for that edge.
* `total_seconds` is present only when timing was enabled (off by default).
* `stack_drift` counts POST hooks that could not find a matching PRE frame.

#### Important limits of the trace

1. BL2's cooked data has no UnrealScript bytecode (`bytecode_available: false`),
   so this is **observed runtime behaviour**, not a static call graph.
2. pyunrealsdk hooks run on UE3 `ProcessEvent`/`CallFunction`. Calls made
   entirely inside native C++ code are invisible. Script→native and
   native→script calls that route through those functions are visible.
3. Every hooked function adds Python callback overhead. Keep the set narrow;
   `Trace hook limit` caps it (default 512, max 4096). Hooking hot tick
   functions can visibly slow the game.
4. Calls are attributed to the nearest hooked ancestor. If A→B→C and only C is
   hooked, C's caller is null, not B.
5. The session is a snapshot of what happened while armed. Arm, reproduce the
   behaviour, then stop.

---

## 13. Ad-hoc single-object dumps

`reprobe dump <path> [depth]` writes one file under `dumps/adhoc/`:

```json
{
  "schema": "re_probe.v1.object",
  "dumped_at": "2026-11-05T14:40:00Z",
  "depth": 3,
  "object": { "...objects record..." },
  "values": { "...same value shapes as defaults..." },
  "errors": []
}
```

This is useful for inspecting a live weapon/pawn without re-running a whole dump.
It is a point-in-time read of that object's effective properties.

---

## 14. Limits, omissions, and why

| Symptom | Cause / workaround |
| :--- | :--- |
| `functions[].bytecode_available` is false | BL2 ships without the `UStruct` script bytecode array. Static disassembly/call graphs are not possible from reflection. Use the trace. |
| No callers/callees in `functions` | Same reason. Arm a trace for the relevant classes. |
| A function's callers only show `null` | Only functions in the armed set are tracked. Widen `reprobe trace arm`. |
| Missing inherited properties on a class | `classes[].properties` is declared-only. Walk `super_chain`. |
| Defaults only show a few values | CDO values are direct-only by default. Enable `Include inherited CDO values`. |
| Instances section is small | It is a capped, priority-sorted sample; see `max_instances` and `instance_candidates`. |
| Arrays/strings are short | `limits.array_limit` / `limits.string_limit`. Raise the options and re-dump. |
| `$truncated_props` appears | More properties than `limits.max_props_per_object`; raise it and re-dump. |
| `xrefs_truncated: true` | Hit `limits.max_xrefs`; raise the option or narrow the dump profile. |
| Values show `{"$error": ...}` | One field could not be read (exotic type or partially loaded object). Other fields are still valid. |
| An object appears in `objects` but has no values | Definitions, CDOs and empty instances are indexed but not value-dumped. |
| An object that existed at start is missing from `objects` | Collection snapshots `path` + class name, then the phase re-resolves each entry. Objects destroyed while the dump runs are skipped; re-run in a calmer state. |
| Game fatal error during a long dump | Older builds held live object wrappers for too long. 1.0.1 snapshots path/class at collection, drops the wrappers, and re-resolves each object just before reading it; destroyed objects are skipped. If it persists, use `reprobe start definitions`, disable other mods, and inspect the last object path logged before the crash. |
| Addresses differ between runs | `address` is a process-local pointer, useful only against the same running process. |
| Asset list misses an asset | `asset_type` uses an exact class-name list. Query `objects` by class instead. |
| Enum names/values look odd | `UEnum._as_py()` may expose aliases; `alias_of` marks duplicate values. |
| Flags names are wrong/incomplete | `flags_decoded` is best effort. Trust the raw integer and the tables in `probe/names.py`. |

A full dump of a busy BL2 session is typically tens to hundreds of megabytes,
because `objects` alone contains every loaded UObject. Use `profile:
definitions` or `core` when values are not needed.

---

## 15. Reproducible queries with SQLite/DuckDB

DuckDB can read the shard wrappers directly:

```sql
-- All function names with a "Weapon" prefix
SELECT u.path, u.display_signature
FROM read_json_auto('dumps/<id>/functions-*.json') AS s,
     unnest(s.items) AS t(u)
WHERE u.name ILIKE '%Weapon%';

-- Parameter layout for one function
SELECT p.value.name, p.value.type, p.value.offset, p.value.is_return
FROM read_json_auto('dumps/<id>/functions-*.json') AS s,
     unnest(s.items) AS t(u),
     unnest(u.params) AS p
WHERE u.path = 'WillowGame.WillowWeapon:ConsumeAmmo';
```

SQLite via Python:

```python
import json, sqlite3, glob

con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE fn(path TEXT, name TEXT, sig TEXT)")

for path in glob.glob("dumps/<id>/functions-*.json"):
    for fn in json.load(open(path, encoding="utf-8"))["items"]:
        con.execute("INSERT INTO fn VALUES (?,?,?)", (fn["path"], fn["name"], fn["display_signature"]))

for row in con.execute("SELECT path FROM fn WHERE name = 'ConsumeAmmo'"):
    print(row)
```

---

## 16. Versioning

* `manifest.schema_version` is `1`.
* `manifest.schema` is `re_probe.v1.manifest`; each shard declares
  `re_probe.v1.<section>`.
* Additive fields may appear in a v1 dump. Consumers should ignore unknown keys.
* A breaking change will bump `schema_version` and the section schema prefix.
* This document describes schema version 1, written by RE Probe 1.0.1.
