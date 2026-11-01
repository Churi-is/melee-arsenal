"""Pure-CPython CLI simulator to inspect rolled Melee Arsenal weapons & item cards offline."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "sdk_mods") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "sdk_mods"))

from melee_arsenal.core import (  # noqa: E402
    Manufacturer,
    PlayerMeleeContext,
    Rarity,
    build_item_card_payload,
    compute_attack_damage,
    compute_card_stats,
    roll_melee_weapon,
)


def main() -> None:
    print("=== MELEE ARSENAL — LEVEL 50 SAMPLE ROLLS (CPython Offline Verification) ===\n")
    ctx = PlayerMeleeContext(melee_damage_scale=2.20, roid_flat_damage=12_000.0, is_uvhm=True)

    for idx, maker in enumerate(Manufacturer):
        wpn = roll_melee_weapon(
            game_stage=50,
            rarity=Rarity.VERY_RARE,
            manufacturer=maker,
            seed=f"demo:{maker.value}:{idx}",
            unique_id=5000 + idx,
        )
        stats = compute_card_stats(wpn)
        card = build_item_card_payload(stats)
        hit = compute_attack_damage(stats, ctx, is_crit=stats.can_weakspot_crit, target_is_slagged=True)

        print(f"[{stats.rarity.label.upper()}] {card.title} ({card.weapon_type_subtitle}, Lv.{card.level_requirement})")
        print(
            f"  Parts      : Blade={wpn.parts.blade_id}, Grip={wpn.parts.grip_id}"
            f"{' [MATCHING!]' if stats.matching_grip else ''}, "
            f"Acc={wpn.parts.accessory_id}, Elem={wpn.parts.element_id}"
        )
        for row in card.top_stats:
            if row.visible:
                print(f"  {row.label:<11}: {row.formatted_value}")
        if card.elemental_section:
            es = card.elemental_section
            print(
                f"  Elemental  : [{es.element.value}] {es.dps_label}: {es.dps_formatted} | "
                f"{es.chance_label}: {es.chance_formatted}"
            )
        print(
            f"  On-Attack  : BaseCard={stats.card_damage:,} -> With +120% Melee, 12k Roid"
            f"{', Crit' if stats.can_weakspot_crit else ''} & UVHM Slag = {hit.final_hit_damage:,} dmg"
            + (f" (+{hit.splash_hit_damage:,} splash)" if hit.splash_hit_damage else "")
        )
        print("-" * 86)


if __name__ == "__main__":
    main()
