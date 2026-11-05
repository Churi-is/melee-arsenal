"""Reads live property values into JSON-safe structures.

The serializer is driven by UE3 property metadata, not by Python types alone:
that is what lets it distinguish an object reference from a string, find the
inner type of an array, and know the struct type of a nested value.

Value shapes produced here:

* scalars        -> JSON number / bool / string / null
* object refs     -> {"$ref": "Package.Object"} (plus an xref edge)
* delegates       -> {"function": "...", "object": "..."} or a list of those
* structs         -> {"$type": "Package.Struct", "Field": value, ...}
* arrays          -> a JSON list; when truncated the final element is
                     {"$truncated": <remaining_count>}
* unreadable      -> {"$error": "..."} (and an entry in the item's errors list)
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable

from . import api
from .names import PROPERTY_KIND, property_kind

RefCallback = Callable[[str, str, str, str], None]

_NATIVE_STRUCT_FIELDS = (
    "X",
    "Y",
    "Z",
    "W",
    "R",
    "G",
    "B",
    "A",
    "Pitch",
    "Yaw",
    "Roll",
    "U",
    "V",
    "Size",
)


class ValueSerializer:
    """Converts values from one or more objects into JSON-safe Python values."""

    def __init__(
        self,
        *,
        depth: int = 2,
        array_limit: int = 24,
        string_limit: int = 384,
        max_props: int = 512,
        on_ref: RefCallback | None = None,
    ) -> None:
        self.depth = max(0, int(depth))
        self.array_limit = max(0, int(array_limit))
        self.string_limit = max(0, int(string_limit))
        self.max_props = max(1, int(max_props))
        self.on_ref = on_ref

    # -- object/struct entry points ----------------------------------------

    def dump_object_values(
        self,
        obj: Any,
        properties: Iterable[Any],
        *,
        from_path: str | None,
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        values: dict[str, Any] = {}
        errors: list[dict[str, str]] = []
        prop_list = list(properties)
        if len(prop_list) > self.max_props:
            prop_list = prop_list[: self.max_props]
            values["$truncated_props"] = True

        for prop in prop_list:
            name = api.name_of(prop) or "?"
            read, raw = self._read_property(obj, prop)
            if not read:
                values[name] = {"$error": raw}
                self._error(errors, name, raw)
                continue
            try:
                values[name] = self.serialize_property(
                    prop,
                    raw,
                    depth=self.depth,
                    via=name,
                    from_path=from_path,
                    errors=errors,
                )
            except Exception as exc:  # pragma: no cover - defensive
                values[name] = {"$error": repr(exc)}
                self._error(errors, name, repr(exc))
        return values, errors

    def dump_struct(
        self,
        struct_value: Any,
        *,
        via: str,
        from_path: str | None,
        depth: int | None = None,
        errors: list[dict[str, str]] | None = None,
    ) -> Any:
        """Serializes a WrappedStruct (hook args, nested struct, etc.)."""
        errors = errors if errors is not None else []
        depth = self.depth if depth is None else depth
        struct_type = api.safe_getattr(struct_value, "_type", None)
        type_path = api.path_of(struct_type)

        if depth <= 0:
            return {"$type": type_path, "$elided": "depth"}

        properties = api.properties_of(struct_type) if struct_type is not None else []
        if not properties:
            fallback = self._native_struct_fields(struct_value)
            if fallback:
                if type_path:
                    fallback["$type"] = type_path
                fallback["$native"] = True
                return fallback
            return {"$type": type_path, "$repr": api.shorten(repr(struct_value), self.string_limit)}

        values: dict[str, Any] = {}
        if type_path:
            values["$type"] = type_path
        if len(properties) > self.max_props:
            properties = properties[: self.max_props]
            values["$truncated_props"] = True

        for prop in properties:
            name = api.name_of(prop) or "?"
            read, raw = self._read_property(struct_value, prop)
            if not read:
                values[name] = {"$error": raw}
                self._error(errors, f"{via}.{name}" if via else name, raw)
                continue
            try:
                values[name] = self.serialize_property(
                    prop,
                    raw,
                    depth=depth - 1,
                    via=f"{via}.{name}" if via else name,
                    from_path=from_path,
                    errors=errors,
                )
            except Exception as exc:  # pragma: no cover - defensive
                values[name] = {"$error": repr(exc)}
                self._error(errors, f"{via}.{name}" if via else name, repr(exc))
        return values

    # -- property/value dispatch -------------------------------------------

    def serialize_property(
        self,
        prop: Any,
        value: Any,
        *,
        depth: int,
        via: str,
        from_path: str | None,
        errors: list[dict[str, str]],
    ) -> Any:
        if value is None:
            return None

        type_name = api.property_type_name(prop)
        kind = PROPERTY_KIND.get(type_name, property_kind(type_name))

        # A few property types need resolving before they look like a UObject.
        if kind == "weak_object":
            value = self._resolve_weak(value)

        # Static arrays are returned as tuples by pyunrealsdk.
        array_dim = api.int_attr(prop, "ArrayDim") or 1
        if array_dim > 1 and isinstance(value, (tuple, list)):
            inner = api.safe_getattr(prop, "Inner", None) or prop
            items = []
            limit = self.array_limit
            for index, element in enumerate(value):
                if limit and index >= limit:
                    items.append({"$truncated": len(value) - index})
                    break
                items.append(
                    self.serialize_value(
                        element,
                        inner,
                        depth=depth,
                        via=f"{via}[{index}]",
                        from_path=from_path,
                        errors=errors,
                    )
                )
            return items

        return self.serialize_value(
            value,
            prop,
            depth=depth,
            via=via,
            from_path=from_path,
            errors=errors,
        )

    def serialize_value(
        self,
        value: Any,
        prop: Any,
        *,
        depth: int,
        via: str,
        from_path: str | None,
        errors: list[dict[str, str]],
    ) -> Any:
        if value is None:
            return None

        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            # SDK enums are IntFlag (an int subclass); storing the integer keeps
            # values compact while enums.json supplies the name mapping.
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                return {"$float": repr(value)}
            return value
        if isinstance(value, str):
            return api.shorten(value, self.string_limit)
        if isinstance(value, (bytes, bytearray)):
            return {"$bytes": bytes(value).hex()}

        # Single-cast delegate (BoundFunction) or a weak pointer wrapper.
        type_name = type(value).__name__
        if type_name == "BoundFunction":
            return self._bound_function(value, via=via, from_path=from_path)
        if type_name == "WeakPointer":
            return self.serialize_value(
                self._resolve_weak(value),
                prop,
                depth=depth,
                via=via,
                from_path=from_path,
                errors=errors,
            )
        if type_name == "WrappedMulticastDelegate":
            return self._multicast_delegate(value, via=via, from_path=from_path)

        # Live UObject reference.
        if callable(api.safe_getattr(value, "_path_name", None)):
            path = api.path_of(value)
            if path:
                self._ref(from_path, path, via, "object")
            return {"$ref": path}

        if type_name == "WrappedStruct":
            return self.dump_struct(
                value,
                via=via,
                from_path=from_path,
                depth=depth,
                errors=errors,
            )

        # Dynamic array.
        if type_name == "WrappedArray" or (
            isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes))
        ):
            inner = api.safe_getattr(prop, "Inner", None) or prop
            return self._sequence(
                value,
                inner,
                depth=depth,
                via=via,
                from_path=from_path,
                errors=errors,
            )

        if isinstance(value, dict):
            return {
                api.shorten(str(key), self.string_limit): self.serialize_value(
                    item,
                    prop,
                    depth=depth - 1,
                    via=f"{via}.{key}",
                    from_path=from_path,
                    errors=errors,
                )
                for key, item in list(value.items())[: self.max_props]
            }

        # Text values and unknown wrappers.  Keep a readable representation
        # rather than dropping the data entirely.
        try:
            rendered = str(value)
        except Exception:
            rendered = repr(value)
        return {"$repr": api.shorten(rendered, self.string_limit)}

    # -- internals ----------------------------------------------------------

    def _sequence(
        self,
        value: Any,
        inner: Any,
        *,
        depth: int,
        via: str,
        from_path: str | None,
        errors: list[dict[str, str]],
    ) -> Any:
        try:
            length = len(value)
        except Exception:
            length = None

        if depth <= 0:
            return {"$array_len": length, "$elided": "depth"}

        items: list[Any] = []
        limit = self.array_limit
        try:
            for index, element in enumerate(value):
                if limit and index >= limit:
                    items.append({"$truncated": length - index if length is not None else None})
                    break
                items.append(
                    self.serialize_value(
                        element,
                        inner,
                        depth=depth - 1,
                        via=f"{via}[{index}]",
                        from_path=from_path,
                        errors=errors,
                    )
                )
        except Exception as exc:  # pragma: no cover - defensive
            items.append({"$error": repr(exc)})
            self._error(errors, via, repr(exc))
        return items

    def _multicast_delegate(self, value: Any, *, via: str, from_path: str | None) -> list[Any]:
        result: list[Any] = []
        try:
            length = len(value)
        except Exception:
            length = None

        try:
            for index, bound in enumerate(value):
                if self.array_limit and index >= self.array_limit:
                    result.append({"$truncated": length - index if length is not None else None})
                    break
                if type(bound).__name__ != "BoundFunction":
                    result.append({"$repr": api.shorten(repr(bound), self.string_limit)})
                    continue
                result.append(self._bound_function(bound, via=f"{via}[{index}]", from_path=from_path))
        except Exception as exc:  # pragma: no cover - defensive
            result.append({"$error": repr(exc)})
        return result

    def _bound_function(self, bound: Any, *, via: str, from_path: str | None) -> dict[str, Any]:
        fn = api.safe_getattr(bound, "func", None)
        obj = api.safe_getattr(bound, "object", None)
        fn_path = api.path_of(fn)
        obj_path = api.path_of(obj)
        if from_path and fn_path:
            self._ref(from_path, fn_path, via, "function")
        if from_path and obj_path:
            self._ref(from_path, obj_path, via, "delegate_object")
        return {"function": fn_path, "object": obj_path}

    def _native_struct_fields(self, struct_value: Any) -> dict[str, Any]:
        """Best-effort fallback for native structs with no reflected properties."""
        result: dict[str, Any] = {}
        for field in _NATIVE_STRUCT_FIELDS:
            value = api.safe_getattr(struct_value, field, None)
            if isinstance(value, (bool, int, float, str)):
                result[field] = value
        return result

    @staticmethod
    def _read_property(obj: Any, prop: Any) -> tuple[bool, Any]:
        """Reads a field off a UObject or WrappedStruct."""
        getter = api.safe_getattr(obj, "_get_field", None)
        if callable(getter):
            try:
                return True, getter(prop)
            except Exception:
                pass
        name = api.name_of(prop)
        if not name:
            return False, "property has no name"
        try:
            return True, getattr(obj, name)
        except Exception as exc:
            return False, repr(exc)

    @staticmethod
    def _resolve_weak(value: Any) -> Any:
        if value is None:
            return None
        if type(value).__name__ == "WeakPointer" or callable(value) and not callable(
            api.safe_getattr(value, "_path_name", None)
        ):
            try:
                resolved = value()
                return resolved
            except Exception:
                return None
        return value

    def _ref(self, from_path: str | None, to_path: str, via: str, kind: str) -> None:
        if self.on_ref is not None and from_path:
            try:
                self.on_ref(from_path, to_path, via, kind)
            except Exception:
                pass

    def _error(self, errors: list[dict[str, str]], where: str, message: str) -> None:
        if len(errors) < 64:
            errors.append({"where": where, "error": api.shorten(str(message), 512)})
