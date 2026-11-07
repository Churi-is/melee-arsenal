"""Regression tests for the live damage-source attribute paths.

The status-effect *chance* attribute used to be `InstigatedStatusEffectChanceModifier`,
which does not exist in Borderlands 2 (it has zero occurrences in a real player-controller
dump). `_safe_find` therefore returned None and every player status-chance bonus (Flicker,
elemental relics, Badass Rank) was silently dropped. The real attribute is
`InstigatedStatusEffectStatusChanceModifier` (note the doubled "Status").

Sources cross-checked for these names:
  * BLCM/BLCMods — "Borderlands 2 mods/AutumnWitch/Quality of life changes/CharactorData.txt"
    (a live dump of the player controller's DamageSourceModifiers).
  * zuhuHix/BL2_ReBased — docs/verification/NATIVE_WEAPON_FIRING.md §4.2 and
    NATIVE_DAMAGE_DEATH.md §8.2.
See docs/ENGINE_FACTS.md ("Status-effect attributes").
"""
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk_mods"))
import melee_arsenal  # noqa: E402,F401  (import-guarded; loads core offline)

# The runtime package's __init__ requires the game; expose its modules offline.
_runtime = types.ModuleType("melee_arsenal.runtime")
_runtime.__path__ = [str(ROOT / "sdk_mods/melee_arsenal/runtime")]
sys.modules.setdefault("melee_arsenal.runtime", _runtime)

resources = importlib.import_module("melee_arsenal.runtime.resources")

from melee_arsenal.core.enums import Manufacturer, Rarity  # noqa: E402
from melee_arsenal.core.parts import MeleeWeaponInstance, MeleeWeaponParts  # noqa: E402
from melee_arsenal.core.parts.weapon import PlayerMeleeContext  # noqa: E402
from melee_arsenal.core.stats import compute_attack_damage, compute_card_stats  # noqa: E402


# The authoritative live paths (see module docstring).
LIVE_ATTR_PATHS = {
    "ATTR_MELEE_MODIFIER": "D_Attributes.DamageSourceModifiers.InstigatedMeleeDamageModifier",
    "ATTR_ROID_BUFF": "D_Attributes.DamageSourceModifiers.InstigatedMeleeDamageAddedBuff",
    "ATTR_STATUS_DMG_MOD": "D_Attributes.DamageSourceModifiers.InstigatedStatusEffectDamageModifier",
    "ATTR_STATUS_CHANCE_MOD": "D_Attributes.DamageSourceModifiers.InstigatedStatusEffectStatusChanceModifier",
}


class _FakeAttr:
    """Stand-in for an AttributeDefinition whose GetValue returns a fixed float."""

    def __init__(self, value):
        self.value = value

    def GetValue(self, _pawn):
        return (self.value,)


def _fire_phaseblade_card():
    inst = MeleeWeaponInstance(
        unique_id=1,
        game_stage=20,
        rarity=Rarity.UNCOMMON,
        parts=MeleeWeaponParts(
            blade_id="Blade_Maliwan_Phaseblade",
            grip_id="Grip_Maliwan",
            accessory_id="Acc_None",
            element_id="Elem_Fire",
        ),
        grade_index=20,
    )
    return compute_card_stats(inst)


class AttributePathTests(unittest.TestCase):
    def test_paths_match_live_dump(self):
        for name, expected in LIVE_ATTR_PATHS.items():
            self.assertEqual(getattr(resources, name), expected, f"{name} drifted from the live attribute path")

    def test_status_chance_is_the_real_doubled_status_name(self):
        # The buggy path (no second "Status") does not exist in the game; guard against a revert.
        self.assertTrue(resources.ATTR_STATUS_CHANCE_MOD.endswith("InstigatedStatusEffectStatusChanceModifier"))
        self.assertNotEqual(
            resources.ATTR_STATUS_CHANCE_MOD,
            "D_Attributes.DamageSourceModifiers.InstigatedStatusEffectChanceModifier",
        )


class StatusChanceFlowTests(unittest.TestCase):
    def _read_context(self, chance_value):
        mods_stub = types.ModuleType("mods_base")
        mods_stub.hook = lambda *a, **k: lambda fn: fn
        mods_stub.get_pc = lambda: None
        sdk = importlib.import_module("melee_arsenal.runtime.sdk")
        with patch.dict(sys.modules, {"mods_base": mods_stub, "unrealsdk": types.ModuleType("unrealsdk")}), \
             patch.object(sdk, "Type", types.SimpleNamespace(PRE="pre")):
            dmg = importlib.import_module("melee_arsenal.runtime.combat.damage")
            state = importlib.import_module("melee_arsenal.runtime.state")
            # Key the cache by the *literal* live paths (the game's real registry), not by the
            # mod's constants: a reverted constant would look up a missing key and default to 1.0.
            cache = {
                LIVE_ATTR_PATHS["ATTR_MELEE_MODIFIER"]: _FakeAttr(1.0),
                LIVE_ATTR_PATHS["ATTR_STATUS_DMG_MOD"]: _FakeAttr(1.0),
                LIVE_ATTR_PATHS["ATTR_STATUS_CHANCE_MOD"]: _FakeAttr(chance_value),
            }
            with patch.object(resources, "ensure_combat_resources_cached", lambda: None), \
                 patch.object(resources, "_CACHED_ATTRS", cache), \
                 patch.object(state.STATE, "stack_player_melee_bonuses", True), \
                 patch.object(state.STATE, "stack_roid_shields", False):
                return dmg.read_player_melee_context(pawn=object(), pc=None)

    def test_player_chance_bonus_is_read_from_the_corrected_attribute(self):
        ctx = self._read_context(1.5)
        self.assertAlmostEqual(ctx.status_chance_scale, 1.5)
        # The damage-source attribute is unchanged and must not be confused with the chance one.
        self.assertAlmostEqual(ctx.status_damage_scale, 1.0)

    def test_chance_bonus_raises_effective_proc_chance(self):
        card = _fire_phaseblade_card()
        self.assertEqual(card.manufacturer, Manufacturer.MALIWAN)
        self.assertGreater(card.elemental_chance_pct, 0.0)
        self.assertLess(card.elemental_chance_pct, 100.0)  # headroom so the bonus is observable

        base = compute_attack_damage(card, PlayerMeleeContext(status_chance_scale=1.0))
        boosted = compute_attack_damage(card, self._read_context(1.5))
        self.assertAlmostEqual(
            boosted.effective_proc_chance,
            min(1.0, base.effective_proc_chance * 1.5),
            places=4,
        )
        self.assertGreater(boosted.effective_proc_chance, base.effective_proc_chance)


if __name__ == "__main__":
    unittest.main()
