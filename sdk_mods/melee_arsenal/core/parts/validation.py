"""Catalog invariants for the part registries — run after editing/adding a part.

`validate_catalog()` is pure Python and cheap: it catches the mistakes that are easy
to make when adding a part by hand (id/key mismatch, a missing donor path, an
unknown element in a manufacturer's allow-list, duplicate Gestalt fragment names,
etc.). `python -m unittest tests.test_parts_catalog` runs it on every build.
"""
from __future__ import annotations

from typing import Any

from ..enums import Element, Manufacturer, PartSlot
from .accessories import ACCESSORIES
from .blades import BLADES, BLADE_BY_MANUFACTURER
from .elements import ELEMENTS, ELEMENT_PART_BY_TYPE, eligible_elements_for_blade
from .grips import GRIPS
from .manufacturers import MANUFACTURERS

# Every gameplay part slot, in the order a weapon rolls them. `BODY_HOST` and
# `MATERIAL` are SDK-side structural slots and have no data-side registry.
PART_REGISTRIES: dict[PartSlot, dict[str, Any]] = {
    PartSlot.BLADE: BLADES,
    PartSlot.GRIP: GRIPS,
    PartSlot.ACCESSORY: ACCESSORIES,
    PartSlot.ELEMENT: ELEMENTS,
}


def validate_catalog() -> list[str]:
    """Return human-readable violations (empty list == catalog is consistent)."""
    errors: list[str] = []
    seen_ids: dict[str, PartSlot] = {}
    seen_fragments: dict[str, str] = {}
    seen_kit_ids: dict[str, str] = {}

    for slot, registry in PART_REGISTRIES.items():
        for part_id, spec in registry.items():
            if spec.part_id != part_id:
                errors.append(f"{slot.value}: key {part_id!r} != spec.part_id {spec.part_id!r}")
            if part_id in seen_ids:
                errors.append(f"duplicate part id {part_id!r} in {seen_ids[part_id].value} and {slot.value}")
            seen_ids[part_id] = slot
            if not spec.donor_part_path:
                errors.append(f"{slot.value} {part_id!r}: missing donor_part_path")
            weight = getattr(spec, "weight", None)
            if weight is not None and weight <= 0:
                errors.append(f"{slot.value} {part_id!r}: roll weight must be positive")

            gestalt = getattr(spec, "gestalt", None)
            fragment = getattr(gestalt, "fragment_name", None)
            if fragment:
                if fragment in seen_fragments:
                    errors.append(
                        f"duplicate Gestalt fragment {fragment!r} on "
                        f"{seen_fragments[fragment]} and {part_id!r}"
                    )
                seen_fragments[fragment] = part_id
            kit_id = getattr(spec, "kit_id", None)
            if kit_id:
                if kit_id in seen_kit_ids:
                    errors.append(
                        f"duplicate InkBlade kit id {kit_id!r} on {seen_kit_ids[kit_id]} and {part_id!r}"
                    )
                seen_kit_ids[kit_id] = part_id

    for maker in Manufacturer:
        if maker not in MANUFACTURERS:
            errors.append(f"manufacturer {maker.value!r} has no ManufacturerSpec")
        elif MANUFACTURERS[maker].manufacturer is not maker:
            errors.append(f"MANUFACTURERS[{maker.value!r}] spec has a different manufacturer")
        if maker not in BLADE_BY_MANUFACTURER:
            errors.append(f"manufacturer {maker.value!r} has no Blade")

    for maker_key, spec in MANUFACTURERS.items():
        if not spec.donor_manufacturer_path:
            errors.append(f"manufacturer {maker_key.value!r}: missing donor_manufacturer_path")
        for element in spec.allowed_elements:
            if element not in ELEMENT_PART_BY_TYPE:
                errors.append(
                    f"manufacturer {maker_key.value!r} allows {element.value!r} with no ElementPartSpec"
                )

    for element in Element:
        if element not in ELEMENT_PART_BY_TYPE:
            errors.append(f"element {element.value!r} has no ElementPartSpec")

    for blade_id, blade in BLADES.items():
        if blade.manufacturer not in MANUFACTURERS:
            errors.append(f"blade {blade_id!r}: unknown manufacturer {blade.manufacturer!r}")
        if not eligible_elements_for_blade(blade_id):
            errors.append(f"blade {blade_id!r}: ElementPolicy produces an empty element pool")

    for maker, blade in BLADE_BY_MANUFACTURER.items():
        if BLADES.get(blade.part_id) is not blade:
            errors.append(f"BLADE_BY_MANUFACTURER[{maker.value!r}] is stale")
    for element, spec in ELEMENT_PART_BY_TYPE.items():
        if ELEMENTS.get(spec.part_id) is not spec:
            errors.append(f"ELEMENT_PART_BY_TYPE[{element.value!r}] is stale")

    return errors
