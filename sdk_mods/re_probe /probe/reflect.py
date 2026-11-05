"""Turns live UE3 reflection objects into plain JSON-ready dictionaries.

Nothing in here caches engine objects across frames: a record is built from the
live wrapper while the dump job holds it.  Every engine read is wrapped, because
an exotic property type or a partially-loaded package must never abort a dump.
"""

from __future__ import annotations

from typing import Any, Iterable

from . import api
from .names import (
    ASSET_CLASS_NAMES,
    FUNC_EVENT,
    FUNC_EXEC,
    FUNC_LATENT,
    FUNC_NATIVE,
    FUNC_PRIVATE,
    FUNC_PROTECTED,
    FUNC_STATIC,
    FUNCTION_FLAGS,
    PROPERTY_FLAGS,
    PROPERTY_KIND,
    PROP_OUT,
    PROP_OPTIONAL,
    PROP_PARAM,
    PROP_RETURN,
    STRUCT_FLAGS,
    decode_flags,
    is_property_class_name,
    property_kind,
)

_MAX_PROPERTY_DEPTH = 6
_MAX_STRUCT_CHAIN = 64


# ---------------------------------------------------------------------------
# Property records
# ---------------------------------------------------------------------------


def _value_type_summary(prop: Any) -> str:
    """A short, human-readable type such as ``array<struct WillowItemData>``."""
    type_name = api.property_type_name(prop)
    kind = PROPERTY_KIND.get(type_name, property_kind(type_name))

    if kind == "array":
        inner = api.safe_getattr(prop, "Inner", None)
        return f"array<{_value_type_summary(inner) if inner is not None else 'unknown'}>"
    if kind == "struct":
        struct = api.safe_getattr(prop, "Struct", None)
        return f"struct {api.name_of(struct) or '?'}"
    if kind in {"enum", "byte"}:
        enum = api.safe_getattr(prop, "Enum", None)
        if enum is not None:
            return f"enum {api.name_of(enum) or '?'}"
        return "byte"
    if kind in {"object", "class", "component", "interface", "weak_object"}:
        target = (
            api.safe_getattr(prop, "PropertyClass", None)
            or api.safe_getattr(prop, "InterfaceClass", None)
            or api.safe_getattr(prop, "MetaClass", None)
        )
        return f"{kind} {api.name_of(target) or '?'}"
    if kind in {"delegate", "multicast_delegate"}:
        signature = api.safe_getattr(prop, "Signature", None)
        return f"{kind} {api.name_of(signature) or '?'}"
    return kind


def describe_property(prop: Any, *, depth: int = 0) -> dict[str, Any]:
    """Full metadata for one UProperty/FField, including array/struct details."""
    type_name = api.property_type_name(prop)
    kind = PROPERTY_KIND.get(type_name, property_kind(type_name))
    name = api.name_of(prop)
    declared_in = api.property_owner_path(prop)
    prop_path = api.path_of(prop)
    if not prop_path:
        prop_path = f"{declared_in}.{name}" if declared_in else name

    flags = api.int_attr(prop, "PropertyFlags")
    array_dim = api.int_attr(prop, "ArrayDim")
    element_size = api.int_attr(prop, "ElementSize")
    offset = api.int_attr(prop, "Offset_Internal")

    record: dict[str, Any] = {
        "id": prop_path,
        "name": name,
        "type": type_name,
        "kind": kind,
        "declared_in": declared_in,
        "value_type": _value_type_summary(prop),
        "array_dim": array_dim,
        "element_size": element_size,
        "offset": offset,
        "flags": flags,
        "flags_decoded": decode_flags(flags, PROPERTY_FLAGS),
        "is_param": bool(flags is not None and flags & PROP_PARAM),
        "is_out": bool(flags is not None and flags & PROP_OUT),
        "is_return": bool(flags is not None and flags & PROP_RETURN),
        "is_optional": bool(flags is not None and flags & PROP_OPTIONAL),
    }

    if array_dim is not None and element_size is not None:
        record["size"] = array_dim * element_size
    if offset is not None and "size" in record:
        record["end"] = offset + record["size"]

    # Type-specific links.  Only emit keys that actually exist so the JSON is
    # not padded with thousands of nulls.
    struct = api.safe_getattr(prop, "Struct", None)
    if struct is not None:
        record["struct"] = api.path_of(struct)
        record["struct_name"] = api.name_of(struct)
        record["struct_size"] = api.struct_size(struct)

    enum = api.safe_getattr(prop, "Enum", None)
    if enum is not None:
        record["enum"] = api.path_of(enum)
        record["enum_name"] = api.name_of(enum)

    object_class = api.safe_getattr(prop, "PropertyClass", None)
    if object_class is not None:
        record["object_class"] = api.path_of(object_class)
        record["object_class_name"] = api.name_of(object_class)

    interface_class = api.safe_getattr(prop, "InterfaceClass", None)
    if interface_class is not None:
        record["interface_class"] = api.path_of(interface_class)

    meta_class = api.safe_getattr(prop, "MetaClass", None)
    if meta_class is not None:
        record["meta_class"] = api.path_of(meta_class)

    signature = api.safe_getattr(prop, "Signature", None)
    if signature is not None:
        record["signature"] = api.path_of(signature)

    field_mask = api.int_attr(prop, "FieldMask")
    if field_mask is not None:
        record["field_mask"] = field_mask

    type_handle = api.int_attr(prop, "TypeHandle")
    if type_handle is not None:
        record["type_handle"] = type_handle

    # Container/attribute sub-properties.
    if depth < _MAX_PROPERTY_DEPTH:
        inner = api.safe_getattr(prop, "Inner", None)
        if inner is not None:
            record["inner"] = describe_property(inner, depth=depth + 1)

        underlying = api.safe_getattr(prop, "UnderlyingProp", None)
        if underlying is not None:
            record["underlying"] = describe_property(underlying, depth=depth + 1)

    modifier_stack = api.safe_getattr(prop, "ModifierStackProperty", None)
    if modifier_stack is not None:
        record["modifier_stack"] = api.path_of(modifier_stack)

    other_attribute = api.safe_getattr(prop, "OtherAttributeProperty", None)
    if other_attribute is not None:
        record["other_attribute"] = api.path_of(other_attribute)

    return record


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def _param_sort_key(prop: Any) -> tuple[int, str]:
    offset = api.int_attr(prop, "Offset_Internal")
    return (offset if offset is not None else 1 << 30, api.name_of(prop) or "")


def describe_function(function: Any, *, overrides: str | None = None) -> dict[str, Any]:
    fn_path = api.path_of(function)
    owner_path = api.path_of(api.safe_getattr(function, "Outer", None))
    flags = api.int_attr(function, "FunctionFlags")
    direct_props: list[Any] = []
    for prop in api.properties_of(function):
        declared_in = api.property_owner_path(prop)
        if declared_in is None or declared_in == fn_path:
            direct_props.append(prop)

    direct_props.sort(key=_param_sort_key)
    params: list[dict[str, Any]] = []
    locals_: list[dict[str, Any]] = []
    return_param: dict[str, Any] | None = None

    for index, prop in enumerate(direct_props):
        record = describe_property(prop)
        flags_value = api.int_attr(prop, "PropertyFlags") or 0
        if flags_value & PROP_PARAM:
            record["param_index"] = len(params)
            params.append(record)
            if flags_value & PROP_RETURN and return_param is None:
                return_param = record
        else:
            record["local_index"] = len(locals_)
            locals_.append(record)

    param_summary = []
    for record in params:
        if record.get("is_return"):
            continue
        prefix = "out " if record.get("is_out") else ""
        param_summary.append(f"{prefix}{record.get('value_type', '?')} {record.get('name', '?')}")

    return_summary = return_param.get("value_type") if return_param else "void"
    display_signature = f"({', '.join(param_summary)}) -> {return_summary}"

    return {
        "path": fn_path,
        "name": api.name_of(function),
        "owner": owner_path,
        "owner_name": api.name_of(api.safe_getattr(function, "Outer", None)),
        "package": _package_from_path(owner_path),
        "object_flags": api.int_attr(function, "ObjectFlags"),
        "internal_index": api.int_attr(function, "InternalIndex"),
        "address": _address(function),
        "flags": flags,
        "flags_decoded": decode_flags(flags, FUNCTION_FLAGS),
        "native": bool(flags is not None and flags & FUNC_NATIVE),
        "static": bool(flags is not None and flags & FUNC_STATIC),
        "event": bool(flags is not None and flags & FUNC_EVENT),
        "latent": bool(flags is not None and flags & FUNC_LATENT),
        "exec": bool(flags is not None and flags & FUNC_EXEC),
        "private": bool(flags is not None and flags & FUNC_PRIVATE),
        "protected": bool(flags is not None and flags & FUNC_PROTECTED),
        "num_params": api.int_attr(function, "NumParams"),
        "params_size": api.int_attr(function, "ParamsSize"),
        "return_value_offset": api.int_attr(function, "ReturnValueOffset"),
        "property_size": api.struct_size(function),
        "display_signature": display_signature,
        "return_type": return_summary,
        "return_param": return_param.get("name") if return_param else None,
        "params": params,
        "locals": locals_,
        "overrides": overrides,
        # BL2 ships without UFunction script bytecode.  Call relationships can
        # only be observed at runtime, via the trace section.
        "bytecode_available": False,
    }


# ---------------------------------------------------------------------------
# Classes, structs, enums, constants
# ---------------------------------------------------------------------------


def _struct_chain(struct: Any) -> list[str]:
    chain: list[str] = []
    current = struct
    seen: set[int] = set()
    while current is not None and len(chain) < _MAX_STRUCT_CHAIN:
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        path = api.path_of(current)
        if path:
            chain.append(path)
        current = api.super_struct(current)
    return chain


def _direct_properties(struct: Any, struct_path: str | None) -> list[Any]:
    direct: list[Any] = []
    for prop in api.properties_of(struct):
        owner = api.property_owner_path(prop)
        if struct_path is None or owner is None or owner == struct_path:
            direct.append(prop)
    return direct


def _direct_fields(struct: Any, struct_path: str | None) -> list[Any]:
    direct: list[Any] = []
    for field in api.fields_of(struct):
        owner = api.path_of(api.safe_getattr(field, "Outer", None))
        if struct_path is None or owner is None or owner == struct_path:
            direct.append(field)
    return direct


def _field_kind(field: Any) -> str:
    class_name = api.class_name_of(field)
    if class_name == "Function":
        return "function"
    if class_name == "Const":
        return "const"
    if class_name == "Enum":
        return "enum"
    if class_name in {"ScriptStruct", "Struct"}:
        return "struct"
    if class_name == "State":
        return "state"
    if is_property_class_name(class_name):
        return "property"
    return class_name or "field"


def describe_class(
    cls: Any,
    *,
    function_paths: Iterable[str] = (),
    direct_subclasses: Iterable[str] = (),
) -> dict[str, Any]:
    cls_path = api.path_of(cls)
    direct_props = _direct_properties(cls, cls_path)
    direct_fields = _direct_fields(cls, cls_path)

    fields: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    for field in direct_fields:
        field_path = api.path_of(field)
        if not field_path or field_path in seen_fields:
            continue
        seen_fields.add(field_path)
        fields.append(
            {
                "name": api.name_of(field),
                "kind": _field_kind(field),
                "path": field_path,
            }
        )

    cdo = api.safe_getattr(cls, "ClassDefaultObject", None)
    interfaces = []
    for interface in api.iterable(api.safe_getattr(cls, "Interfaces", None)):
        interface_path = api.path_of(interface)
        if interface_path:
            interfaces.append(interface_path)

    return {
        "path": cls_path,
        "name": api.name_of(cls),
        "package": _package_from_path(cls_path),
        "object_flags": api.int_attr(cls, "ObjectFlags"),
        "internal_index": api.int_attr(cls, "InternalIndex"),
        "address": _address(cls),
        "super": api.path_of(api.super_struct(cls)),
        "super_chain": _struct_chain(api.super_struct(cls)),
        "subclasses": sorted(set(direct_subclasses)),
        "interfaces": interfaces,
        "cdo": api.path_of(cdo),
        "property_size": api.struct_size(cls),
        "min_alignment": api.int_attr(cls, "MinAlignment"),
        "fields": fields,
        "functions": sorted(set(function_paths)),
        "properties": [describe_property(prop) for prop in direct_props],
    }


def describe_struct(struct: Any, *, direct_subclasses: Iterable[str] = ()) -> dict[str, Any]:
    struct_path = api.path_of(struct)
    all_props = api.properties_of(struct)
    all_props.sort(key=lambda prop: (api.int_attr(prop, "Offset_Internal") or 0, api.name_of(prop) or ""))
    records = [describe_property(prop) for prop in all_props]
    flags = api.int_attr(struct, "StructFlags")

    return {
        "path": struct_path,
        "name": api.name_of(struct),
        "package": _package_from_path(struct_path),
        "object_flags": api.int_attr(struct, "ObjectFlags"),
        "internal_index": api.int_attr(struct, "InternalIndex"),
        "address": _address(struct),
        "super": api.path_of(api.super_struct(struct)),
        "super_chain": _struct_chain(api.super_struct(struct)),
        "subclasses": sorted(set(direct_subclasses)),
        "struct_flags": flags,
        "struct_flags_decoded": decode_flags(flags, STRUCT_FLAGS),
        "property_size": api.struct_size(struct),
        "min_alignment": api.int_attr(struct, "MinAlignment"),
        "properties": records,
        "property_count": len(records),
    }


def describe_enum(enum: Any) -> dict[str, Any]:
    enum_path = api.path_of(enum)
    values: list[dict[str, Any]] = []
    error: str | None = None

    try:
        py_enum = api.call(enum, "_as_py", default=None)
        members = getattr(py_enum, "__members__", None) if py_enum is not None else None
        if members:
            seen_values: dict[int, str] = {}
            for name, member in members.items():
                try:
                    value = int(member)
                except Exception:
                    value = int(getattr(member, "value", 0))
                alias_of = seen_values.get(value)
                values.append(
                    {
                        "name": str(name),
                        "value": value,
                        "alias_of": alias_of,
                    }
                )
                seen_values.setdefault(value, str(name))
            values.sort(key=lambda item: (item["value"], item["name"]))
    except Exception as exc:  # pragma: no cover - defensive
        error = repr(exc)

    record: dict[str, Any] = {
        "path": enum_path,
        "name": api.name_of(enum),
        "package": _package_from_path(enum_path),
        "object_flags": api.int_attr(enum, "ObjectFlags"),
        "internal_index": api.int_attr(enum, "InternalIndex"),
        "address": _address(enum),
        "values": values,
        "value_count": len(values),
    }
    if error:
        record["error"] = error
    return record


def describe_const(const: Any) -> dict[str, Any]:
    value = api.safe_getattr(const, "Value", None)
    return {
        "path": api.path_of(const),
        "name": api.name_of(const),
        "package": _package_from_path(api.path_of(const)),
        "object_flags": api.int_attr(const, "ObjectFlags"),
        "internal_index": api.int_attr(const, "InternalIndex"),
        "value": str(value) if value is not None else None,
    }


def describe_package(package: Any, *, object_count: int = 0, class_counts: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "path": api.path_of(package),
        "name": api.name_of(package),
        "object_flags": api.int_attr(package, "ObjectFlags"),
        "internal_index": api.int_attr(package, "InternalIndex"),
        "object_count": object_count,
        "class_counts": class_counts or {},
    }


# ---------------------------------------------------------------------------
# Object index
# ---------------------------------------------------------------------------


def object_kind(obj: Any) -> str:
    class_name = api.class_name_of(obj)
    name = api.name_of(obj) or ""

    if class_name in {"Class", "BlueprintGeneratedClass"}:
        return "class"
    if class_name == "Function":
        return "function"
    if class_name in {"ScriptStruct", "Struct"}:
        return "struct"
    if class_name == "Enum":
        return "enum"
    if class_name == "Const":
        return "const"
    if class_name == "Package":
        return "package"
    if class_name == "State":
        return "state"
    if is_property_class_name(class_name):
        return "property"
    if name.startswith("Default__"):
        return "cdo"
    return "object"


def describe_object(obj: Any, *, package_path: str | None = None) -> dict[str, Any]:
    class_name = api.class_name_of(obj)
    class_path = api.class_path_of(obj)
    name = api.name_of(obj)
    kind = object_kind(obj)

    record: dict[str, Any] = {
        "path": api.path_of(obj),
        "name": name,
        "class": class_path,
        "class_name": class_name,
        "outer": api.path_of(api.safe_getattr(obj, "Outer", None)),
        "package": package_path,
        "kind": kind,
        "flags": api.int_attr(obj, "ObjectFlags"),
        "index": api.int_attr(obj, "InternalIndex"),
        "address": _address(obj),
    }

    if kind == "cdo" and name and name.startswith("Default__"):
        record["cdo_of"] = class_path

    if class_name in ASSET_CLASS_NAMES:
        record["asset_type"] = class_name

    return record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _address(obj: Any) -> int | None:
    getter = api.safe_getattr(obj, "_get_address", None)
    if callable(getter):
        try:
            value = getter()
            return int(value) if value is not None else None
        except Exception:
            return None
    return None


def _package_from_path(path: str | None) -> str | None:
    if not path:
        return None
    return path.split(".", 1)[0]
