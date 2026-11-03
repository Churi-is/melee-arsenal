"""Harvest the live vanilla `GD_Weap_Pistol` / `GD_Weap_Shotgun` donor UObjects.

Everything built in this package is cloned from these donors, so their paths and
per-rarity balance suffixes live here rather than being scattered through builders.
"""
from __future__ import annotations

from typing import Any

from ...core.enums import Element, Manufacturer, Rarity
from ..log import log_debug, obj_path
from ..state import STATE
from ._sdk import unrealsdk
from .helpers import _find, _inherited_plc


_RARITY_DONOR_SUFFIX: dict[Rarity, tuple[str, ...]] = {
    Rarity.COMMON: ("", "1_Common", "2_Uncommon"),
    Rarity.UNCOMMON: ("2_Uncommon", ""),
    Rarity.RARE: ("3_Rare", "2_Uncommon", ""),
    Rarity.VERY_RARE: ("4_VeryRare", "3_Rare", ""),
}


def _harvest_pistol_donors() -> dict[str, Any]:
    """Ensure base packages are loaded and harvest live `GD_Weap_Pistol` & `GD_Weap_Shotgun` donor UObjects."""
    for pkg_name in ("GD_Manufacturers", "GD_Weap_Pistol", "GD_Weap_Shotgun", "GD_Itempools"):
        try:
            unrealsdk.load_package(pkg_name)
        except Exception as exc:
            log_debug(f"load_package {pkg_name} failed: {exc!r}")

    donors: dict[str, Any] = {
        "wt": {},
        "mfr": {},
        "balances": {},
        "bodies": {},
        "grips": {},
        "barrels": {},
        "materials": {},
        "elements": {},
        "accessories": [],
        "accessory_none": _find("WeaponPartDefinition", "GD_Weap_Pistol.Accessory.Pistol_Accessory_None"),
        "visible_accessories": [],
        "crit_effect_template": None,
        "firing_mode": (
            _find("FiringModeDefinition", "GD_Weap_Shotgun.FiringModes.Bullet_Shotgun_TidalWave")
            or _find("FiringModeDefinition", "GD_Weap_Pistol.FiringModes.Bullet_Pistol_Default")
        ),
        "title_template": _find("WeaponNamePartDefinition", "GD_Weap_Pistol.Name.Title_Hyperion.Title__Shooter"),
        "prefix_template": _find(
            "WeaponNamePartDefinition",
            "GD_Weap_Pistol.Name.Prefix_Hyperion.Prefix__AutoCorrecting",
        ),
    }

    for maker in Manufacturer:
        mname = maker.value
        wt = _find("WeaponTypeDefinition", f"GD_Weap_Pistol.A_Weapons.WeaponType_{mname}_Pistol")
        mfr = _find("ManufacturerDefinition", f"GD_Manufacturers.Manufacturers.{mname}")
        donors["wt"][maker] = wt
        donors["mfr"][maker] = mfr
        STATE.maker_to_donor_manufacturer[maker] = mfr

        if wt is not None:
            if donors["title_template"] is None:
                tlist = list(getattr(wt, "TitleList", []) or [])
                if tlist:
                    donors["title_template"] = tlist[0]
            if donors["prefix_template"] is None:
                plist = list(getattr(wt, "PrefixList", []) or [])
                if plist:
                    donors["prefix_template"] = plist[0]

        for rarity, suffixes in _RARITY_DONOR_SUFFIX.items():
            bal = None
            for suf in suffixes:
                bal_name = f"Pistol_{mname}" if not suf else f"Pistol_{mname}_{suf}"
                bal = _find("WeaponBalanceDefinition", f"GD_Weap_Pistol.A_Weapons.{bal_name}")
                if bal is not None:
                    break
            if bal is not None:
                donors["balances"][(maker, rarity)] = bal
                plc = _inherited_plc(bal)
                if plc is not None:
                    _extract_parts_from_plc(maker, rarity, plc, donors)

    # Harvest a WeaponExtraCriticalHitDamage effect row from Jakobs barrel/type if present
    for cand in list(donors["barrels"].values()) + list(donors["wt"].values()):
        if cand is None or donors["crit_effect_template"] is not None:
            continue
        for field_name in ("WeaponAttributeEffects", "ExternalAttributeEffects"):
            for row in getattr(cand, field_name, []) or []:
                attr_p = obj_path(getattr(row, "AttributeToModify", None)).lower()
                if "crit" in attr_p:
                    donors["crit_effect_template"] = (field_name, row)
                    break

    return donors


def _extract_parts_from_plc(maker: Manufacturer, rarity: Rarity, plc: Any, donors: dict[str, Any]) -> None:
    def _parts_in_slot(slot_name: str) -> list[Any]:
        slot = getattr(plc, slot_name, None)
        if slot is None:
            return []
        out = []
        for wp in getattr(slot, "WeightedParts", []) or []:
            part = getattr(wp, "Part", None)
            if part is not None:
                out.append(part)
        return out

    bodies = _parts_in_slot("BodyPartData")
    if bodies and maker not in donors["bodies"]:
        donors["bodies"][maker] = bodies[0]

    for gpart in _parts_in_slot("GripPartData"):
        pname = obj_path(gpart).lower()
        for gmaker in Manufacturer:
            if gmaker.value.lower() in pname and gmaker not in donors["grips"]:
                donors["grips"][gmaker] = gpart

    for bpart in _parts_in_slot("BarrelPartData"):
        pname = obj_path(bpart).lower()
        for bmaker in Manufacturer:
            if bmaker.value.lower() in pname and bmaker not in donors["barrels"]:
                donors["barrels"][bmaker] = bpart

    mats = _parts_in_slot("MaterialPartData")
    if mats and (maker, rarity) not in donors["materials"]:
        donors["materials"][(maker, rarity)] = mats[0]

    for epart in _parts_in_slot("ElementalPartData"):
        pname = obj_path(epart).lower()
        if "fire" in pname or "incendiary" in pname:
            donors["elements"].setdefault(Element.FIRE, epart)
        elif "shock" in pname:
            donors["elements"].setdefault(Element.SHOCK, epart)
        elif "corrosive" in pname:
            donors["elements"].setdefault(Element.CORROSIVE, epart)
        elif "slag" in pname or "amp" in pname:
            donors["elements"].setdefault(Element.SLAG, epart)
        elif "explosive" in pname:
            donors["elements"].setdefault(Element.EXPLOSIVE, epart)
        elif "none" in pname:
            donors["elements"].setdefault(Element.NONE, epart)

    for apart in _parts_in_slot("Accessory1PartData"):
        if apart not in donors["accessories"]:
            donors["accessories"].append(apart)
        apath_low = obj_path(apart).lower()
        if apath_low.endswith("_none") or ".pistol_accessory_none" in apath_low:
            if donors["accessory_none"] is None:
                donors["accessory_none"] = apart
        elif apart not in donors["visible_accessories"]:
            donors["visible_accessories"].append(apart)
