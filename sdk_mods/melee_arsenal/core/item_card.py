"""Scaleform Item Card payload builder & stat comparator for Melee Weapons."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .enums import Element, SecondaryAttack, TargetingMode
from .secondary import THROW_DAMAGE_MULTIPLIER
from .stats import ComputedCardStats


class CompareArrow(IntEnum):
    """Comparison arrow state for ItemCardGFxObject stat slots."""

    NONE = 0
    BETTER = 1
    WORSE = -1
    EQUAL = 2


ELEMENT_CARD_COLORS: dict[Element, str] = {
    Element.NONE: "#ffffff",
    Element.FIRE: "#ff6a00",
    Element.SHOCK: "#4da6ff",
    Element.CORROSIVE: "#4ce633",
    Element.SLAG: "#b84dff",
    Element.EXPLOSIVE: "#ffd633",
}


@dataclass(frozen=True, slots=True)
class CardStatRow:
    """One top-section stat row on a Borderlands 2 ItemCardGFxObject."""

    slot_index: int
    label: str
    formatted_value: str
    numeric_value: float
    arrow: CompareArrow = CompareArrow.NONE
    visible: bool = True


@dataclass(frozen=True, slots=True)
class ElementalCardSection:
    """Optional elemental section on the item card."""

    element: Element
    color_hex: str
    dps_label: str
    dps_formatted: str
    chance_label: str
    chance_formatted: str
    dps_arrow: CompareArrow = CompareArrow.NONE
    chance_arrow: CompareArrow = CompareArrow.NONE


@dataclass(frozen=True, slots=True)
class ItemCardPayload:
    """Complete item card representation ready to apply to `ItemCardGFxObject`."""

    title: str
    rarity_color_hex: str
    manufacturer_name: str
    weapon_type_subtitle: str     # e.g. "Hyperion Katana" / "Bandit Machete"
    level_requirement: int
    top_stats: tuple[CardStatRow, ...]
    elemental_section: ElementalCardSection | None
    fun_stats_lines: tuple[str, ...]
    fun_stats_html: str


def _compare_values(candidate: float, equipped: float | None, eps: float = 1e-3) -> CompareArrow:
    if equipped is None:
        return CompareArrow.NONE
    diff = candidate - equipped
    if abs(diff) <= eps:
        return CompareArrow.EQUAL
    return CompareArrow.BETTER if diff > 0 else CompareArrow.WORSE


def build_item_card_payload(
    card: ComputedCardStats,
    compared_against: ComputedCardStats | None = None,
) -> ItemCardPayload:
    """Build a complete, readable, gun-comparable item card for a melee weapon.

    Top Stats (3 active rows; gun Reload Speed & Magazine Size slots are hidden):
      Row 0: Damage       (integer, gun-scaled)
      Row 1: Range        (meters, e.g. '3.40m')
      Row 2: Swing Speed  (swings/sec, e.g. '1.85 /s')
      Row 3: [Hidden - replaces Reload Speed]
      Row 4: [Hidden - replaces Magazine Size]
    """
    other_dmg = float(compared_against.card_damage) if compared_against else None
    other_rng = compared_against.range_meters if compared_against else None
    other_spd = compared_against.swing_speed if compared_against else None

    top_stats = (
        CardStatRow(
            slot_index=0,
            label="Damage",
            formatted_value=f"{card.card_damage:,}",
            numeric_value=float(card.card_damage),
            arrow=_compare_values(float(card.card_damage), other_dmg, eps=0.5),
            visible=True,
        ),
        CardStatRow(
            slot_index=1,
            label="Range",
            formatted_value=f"{card.range_meters:.2f}m",
            numeric_value=card.range_meters,
            arrow=_compare_values(card.range_meters, other_rng, eps=0.01),
            visible=True,
        ),
        CardStatRow(
            slot_index=2,
            label="Swing Speed",
            formatted_value=f"{card.swing_speed:.2f} /s",
            numeric_value=card.swing_speed,
            arrow=_compare_values(card.swing_speed, other_spd, eps=0.01),
            visible=True,
        ),
        CardStatRow(
            slot_index=3,
            label="",
            formatted_value="",
            numeric_value=0.0,
            visible=False,
        ),
        CardStatRow(
            slot_index=4,
            label="",
            formatted_value="",
            numeric_value=0.0,
            visible=False,
        ),
    )

    elem_section: ElementalCardSection | None = None
    if card.show_elemental_stats:
        color = ELEMENT_CARD_COLORS[card.element]
        if card.element == Element.EXPLOSIVE:
            elem_section = ElementalCardSection(
                element=Element.EXPLOSIVE,
                color_hex=color,
                dps_label="Splash Dmg",
                dps_formatted=f"{card.splash_damage:,}",
                chance_label="Blast Radius",
                chance_formatted=f"{card.splash_radius_uu / 100.0:.1f}m",
            )
        else:
            other_edps = (
                compared_against.elemental_dps
                if (compared_against and compared_against.show_elemental_stats)
                else None
            )
            other_echance = (
                compared_against.elemental_chance_pct
                if (compared_against and compared_against.show_elemental_stats)
                else None
            )
            elem_label = {
                Element.FIRE: "Burn Dmg/s",
                Element.SHOCK: "Electrocute/s",
                Element.CORROSIVE: "Corrode Dmg/s",
                Element.SLAG: "Slag Dmg/s",
            }[card.element]
            elem_section = ElementalCardSection(
                element=card.element,
                color_hex=color,
                dps_label=elem_label,
                dps_formatted=f"{card.elemental_dps:,.1f}",
                chance_label="Effect Chance",
                chance_formatted=f"{card.elemental_chance_pct:.1f}%",
                dps_arrow=_compare_values(card.elemental_dps, other_edps, eps=0.1),
                chance_arrow=_compare_values(card.elemental_chance_pct, other_echance, eps=0.1),
            )

    # Bottom white/colored FunStats modifier lines.
    # Keep the defensive line first and short: long trailing lines clip into the manufacturer logo.
    lines: list[str] = [
        f"<font color='#7fd4ff'>Damage Reduction {card.damage_reduction_pct}% (Equipped)</font>"
    ]

    # RMB / Left Trigger secondary attack (Maliwan deliberately prints nothing).
    if card.secondary_attack == SecondaryAttack.THROW:
        lines.append(
            f"<font color='#ffc266'>RMB Throw: {THROW_DAMAGE_MULTIPLIER:.0%} Damage, "
            f"{card.throw_range_m:.1f}m Arc ({card.throw_weight_kg:.1f}kg)</font>"
        )
    elif card.secondary_attack == SecondaryAttack.GUARD:
        lines.append(
            f"<font color='#9fd8ff'>RMB Hold: Guard {card.guard_reduction_pct}% DR, "
            f"{card.guard_cooldown_sec:.1f}s Cooldown</font>"
        )

    if card.targeting_mode == TargetingMode.SINGLE_TARGET:
        lines.append("<font color='#d8e6ff'>Single-Target Precision Strike</font>")
    else:
        lines.append(
            f"<font color='#ffe0a3'>Cleaves All Enemies in Front ({card.cleave_arc_degrees:.0f}° Arc)</font>"
        )

    if card.crit_bonus_pct > 0 and card.can_weakspot_crit:
        lines.append(f"<font color='#ffffff'>+{card.crit_bonus_pct}% Critical Hit Damage</font>")

    if card.semi_auto_click:
        lines.append("<font color='#ffffff'>Swings as fast as you pull the trigger</font>")

    if card.element == Element.EXPLOSIVE and card.splash_damage > 0:
        lines.append(
            f"<font color='#ffd633'>Deals Bonus Explosive Splash ({card.splash_radius_uu / 100.0:.1f}m)</font>"
        )
    elif card.element == Element.SLAG:
        lines.append("<font color='#b84dff'>Slagged targets take amplified damage</font>")

    return ItemCardPayload(
        title=card.full_name,
        rarity_color_hex=card.rarity.hex_color,
        manufacturer_name=card.manufacturer.value,
        weapon_type_subtitle=f"{card.manufacturer.value} {card.archetype_name}",
        level_requirement=card.game_stage,
        top_stats=top_stats,
        elemental_section=elem_section,
        fun_stats_lines=tuple(lines),
        fun_stats_html="\n".join(lines),
    )
