"""Shared build context for `ensure_definitions_built()`.

The 11 UE3 subpackages and the harvested donor table are created once and handed
to each builder in order, so a builder never reaches into another builder's locals.
Values that later builders need (`fm_donor`, `crit_tpl`, `visible_acc_donor`, ...)
are written back onto the context by the builder that produces them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..state import STATE
from .helpers import _ensure_subpackage


@dataclass
class BuildContext:
    """Root packages + donor cache for one definitions build."""

    donors: dict[str, Any]
    root_pkg: Any
    wt_pkg: Any
    fm_pkg: Any
    blade_pkg: Any
    grip_pkg: Any
    body_pkg: Any
    acc_pkg: Any
    elem_pkg: Any
    mats_pkg: Any
    names_pkg: Any
    bal_pkg: Any

    # Passed between builders (in build order).
    fm_donor: Any = None
    crit_tpl: Any = None
    visible_acc_donor: Any = None
    none_acc_donor: Any = None

    @classmethod
    def create(cls, donors: dict[str, Any]) -> BuildContext:
        """Create the subpackage tree in the exact order the UE3 objects expect."""
        root_pkg = _ensure_subpackage(None, "MeleeArsenalDev")
        STATE.root_package = root_pkg
        return cls(
            donors=donors,
            root_pkg=root_pkg,
            wt_pkg=_ensure_subpackage(root_pkg, "WeaponTypes"),
            fm_pkg=_ensure_subpackage(root_pkg, "FiringModes"),
            blade_pkg=_ensure_subpackage(root_pkg, "Blades"),
            grip_pkg=_ensure_subpackage(root_pkg, "Grips"),
            body_pkg=_ensure_subpackage(root_pkg, "Bodies"),
            acc_pkg=_ensure_subpackage(root_pkg, "Accessories"),
            elem_pkg=_ensure_subpackage(root_pkg, "Elements"),
            mats_pkg=_ensure_subpackage(root_pkg, "Materials"),
            names_pkg=_ensure_subpackage(root_pkg, "Names"),
            bal_pkg=_ensure_subpackage(root_pkg, "Balances"),
        )
