# Engine facts this mod relies on

Everything below was printed by Borderlands 2 itself, in a normal session (Sanctuary, `unrealsdk
v3.2.0`, `pyunrealsdk 1.10.0`, Python 3.14.6 32-bit), on 2026-11-05. It is recorded because the code
would otherwise have to guess — and because the guesses it used to make are what made it long.

The measurements were taken by a temporary developer mod that hooked the candidate functions, counted
calls, walked the UE3 reflection data and dumped loaded asset paths. That mod has been removed; this
file is the part worth keeping. When a change conflicts with a fact here, the fact wins.

## Hooks: what exists, what fires

| function | calls per session | note |
| --- | --- | --- |
| `WillowGame.WillowPlayerController:PlayerTick` | ~12,000 | **the only tick driver the mod needs** |
| `WillowGame.WillowGameViewportClient:Tick` | ~15,000 | not hooked (one driver is enough) |
| `Engine.GameViewportClient:Tick` | **0** | resolves, so a hook installs silently and never runs |
| `WillowWeapon:ConsumeAmmo` / `SharedFireAmmunition` / `InstantFire` | 11–20 each | all fire per swing |
| `WillowWeapon:FiringModeDefinitionFire` | 4–12 | fires |
| `WillowWeapon:InitializeInternal` / `ChooseRandomNameParts` / `InitializeFromDefinitionData` | 80–240 | `InitializeInternal` wraps `ChooseRandomNameParts`; `InitializeFromDefinitionData` is the save-restore path |
| `WillowWeapon:StartZoom` + `ClientStartZoom`, `StopZoom` + `ClientStopZoom` | all four fire | **not** collapsible to one pair |
| `ItemCardGFxObject:SetTopStat` / `SetItemCardEx` / `SetTitle` / `SetFunStats` / `SetItemCard` | 24–730 | all live |
| `WillowAIPawn:TakeDamage`, `WillowPlayerPawn:TakeDamage` | 5–70 | both can fire |
| `WillowDamageTypeDefinition:DisplayRecentDamageForPlayer` | ~66 | fires |
| `WillowHUDMovieBase:AddTrainingText` | — | **does not resolve**: no such UFunction |
| `ItemCardGFxObject:SetBottomStat` | — | **does not resolve** |

Because `PlayerTick` is the only tick hook, `_run_frame_tick` has no per-frame de-duplication guard.

## Item card (`ItemCardGFxObject`)

`SetTopStat(StatIndex, LabelText, ValueText, CompareArrow, AuxText, IconName, NewLabelText,
NewValueText, NewAuxText)` — nine parameters, three of them out-params. `SetTitle(Manufacturer, Title,
Rarity, TypeIcon, ElementalIcon, bIsReadied)`.

* `SetTitle`'s **`Rarity` is a `Color` struct** (`{B: 0, G: 180, R: 255, A: 255}`), not a 1-based
  level. Nothing can turn it into an integer, so the mod renders the engine's colour unchanged.
* `CompareArrow` is typed **`ETopStatArrow`**. RE Probe reflected
  `WillowGame.InventoryCardGFx:ETopStatArrow`: blank=0, up=1, down=2, same=3.
  An earlier guess had down and same reversed; the item-card mapping now uses the reflected values.
* `TypeIcon` values are capitalised: `Pistol`.
* Stat icons, by card slot: `0 weaponDamage`, `1 weaponAccuracy`, `2 weaponFireRate`,
  `3 weaponsReloadSpeed`, `4 weaponClipSize`, `5/6 element<Element>` — `elementCorrosive` and
  `elementFire` both observed. Note `elementAmp` also appears in slot 5 on some weapons, so the
  element rows are written from the element, not sniffed from the engine's slot.
* Only slot 0 of a melee card keeps the engine's compare arrow; the mod supplies the rest.

## Classes: who declares what

UE3 reflection here is per class declaration, **not** per object: a subclass does not list its
parents' properties, and native members (`Origin`, `RotOrigin`, `PlayAkEvent`) and UFunctions are not
listed at all even though they are readable. So "missing from the dump" is not evidence that a
`hasattr` guard is dead — only a `hasattr` on the live object is.

| object | class (measured) | declares |
| --- | --- | --- |
| weapon type | **`WeaponTypeDefinition`** (249) | `Presentation`, `UIMeshRotation`, `bUIMeshRotationNeedsTransformFixup`, `FocusRadius`, `MinimumVerticalPercentage`, `MinimumHorizontalPercentage`, `ThirdPersonMeshScale`, `FireShake`, all kick/recoil fields, `DefaultFiringModeDefinition`, `ClipSize`, `ShotCost`, `Range`, `Spread`, `Typename`, `FireSounds`, `TitleList`, `PrefixList` |
| weapon balance | **`WeaponBalanceDefinition`** (23) | `WeaponPartListCollection`, `RuntimePartListCollection`, `InventoryDefinition`, `BaseDefinition`, `Manufacturers`, `PartListCollection`, `bInterpolateExpLevel` — and **not** `MinGameStageIndex` / `MaxGameStageIndex` / `GameStageRequirement` |
| weapon part | `WeaponPartDefinition` (66) | `WeaponAttributeEffects`, `ExternalAttributeEffects`, `AttributeSlotEffects`, `AttributeSlotUpgrades`, `CustomFiringModeDefinition`, `Material`, `GestaltModeSkeletalMeshName`, `AdditionalGestaltModeSkeletalMeshNames`, `ManufacturerDefinitionForScopeMovie`, `Rarity`, `CustomDamageTypeDefinition` |
| name part | `WeaponNamePartDefinition` (72) | `PartName`, `bNameIsUnique`, `CustomPresentations`, `CustomFiringModeDefinition` |
| firing mode | `FiringModeDefinition` (68) | `SoundDefinition`, `PartSysTemplate`, `ConditionalPartSysTemplate`, `FiringPatternLines`, `OnAnyImpact`, `TimerEvents`, `Acceleration`, `WaveFreq/Amp/Phase`, `TraceExtent`, `bPenetratePawn`, `NumRicochets`, `RicochetFriction`, `ExplosionOverrideDefinition`, `ExplosionOverideInstanceDataName` |
| weapon instance | `WillowWeapon` (476) | `DefinitionData`, `InstantHitDamage`, `ProjectilesPerShot`, `ClipSize` — and **no `Mesh`**, no `WeaponTypeDefinition`, no `CurrentFiringModeDefinition`, no `bCanBeUsed` |
| pawn | `WillowPlayerPawn` (963) | `Arms`, `Weapon`, `Mesh`, `PlayAkEvent`, `Location` — `PendingWeapon` belongs to `InventoryManager`, not the pawn |
| arms component | `GearboxSkeletalMeshComponent` (348) | `Rotation`, `Translation`, `ForceUpdate`, `Materials`, `SkeletalMesh`, `AnimSets` |
| item pool | `ItemPoolDefinition` (23) | `BalancedItems`, `Quantity`, `MinGameStageRequirement`, `MaxGameStageRequirement`, `bAutoReadyItems`, `bShopsHaveInfiniteQuantity` — there is **no `ItemPool`** property |

Two consequences that are easy to get wrong:

* **First-person hands.** `Pawn.Arms` is a `GearboxSkeletalMeshComponent`; the `RotOrigin` / `Origin`
  the pose writes live on `Arms.SkeletalMesh` — the hands *mesh asset* — and the component only gets
  `ForceUpdate(False)`. Looking for `RotOrigin` on the component makes the pose look dead when it
  works perfectly.
* **Thrown blade.** The weapon exposes no `Mesh`, so there is no first-person weapon mesh to copy
  materials from or to hide: the flying blade is always the cooked gestalt mesh, tinted by the
  weapon's own `MaterialPartDefinition` material instance.

## Pool entries and weights

`ItemPoolDefinition.BalancedItems` holds `WillowGame.ItemPoolDefinition:BalancedInventoryData` rows whose payload fields are
`InvBalanceDefinition` (a balance or an item definition) and `ItmPoolDefinition` (a nested pool) —
not `Item`. Each row's `Probability` is an `AttributeInitializationData` with exactly
`BaseValueConstant`, `BaseValueAttribute`, `BaseValueScaleConstant`, `InitializationDefinition`.

Rarity weights, resolved: `Weight_0_VeryCommon` 200, `Weight_1_Common` 100, `Weight_2_Uncommon` 10,
`Weight_3_Uncommoner` 5, `Weight_4_Rare` 1, `Weight_5_VeryRare` 0.1, `Weight_6_Legendary` 0.01.
`Weight_1_Common_RareMod`, `Weight_2_Uncommon_RareMod` and `Weight_Lobelia_Pearlescent_Tubbles` are 0.

## Explosive splash donor

`Behavior_Explode` is reachable from a firing mode's **`OnAnyImpact`** list. All 38 loaded explosive
firing modes carry it there; **none** carry it in `TimerEvents`. The donor is
`GD_Weap_Shotgun.FiringModes.Bullets_Shotgun_Torgue_Flakker_Child` (with `_2`/`_3` siblings), and the
mod clones its behavior and points `FM_Melee_Elem_EXPLOSIVE.OnAnyImpact` at the clone.

## Assets confirmed loaded

`Anim_CameraAnimations.Melee.Canim_{RepeaterPistol,CombatShotgun,RocketLauncher}_Melee`,
`Anim_CameraAnimations.Explosions.Canim_Explosion_Medium`,
`Ake_Wep_Melee.Ak_Play_Wep_Melee_Generic_Attack`, `Ake_Wep_Melee.Ak_Play_wep_melee_BladedWeap_hit`,
`Ake_Wep_Melee.Ak_Play_wep_melee_Merc_impact`,
`Ake_Mon_Thresher.v2.Ak_Play_Mon_Thresher_Melee_Swipe`,
`Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_{MED,SM,LG}`,
`FX_WEP_Explosions.Particles.Default.Part_ExplosiveExplosion_{Medium,Small}`,
`GD_Impacts.Melee.Impact_MeleeGeneric`,
`GD_Weap_Shared_Effects.Default_Elemental_Explosions`,
`GD_Impact.DamageType.DmgType_Normal` and the four elemental `DmgType_*_Impact_NoDoT` definitions,
`Char_Assassin.Mesh.Hands_Assassin` (the first-person hands).

## What is still deliberately cautious

Measured facts do not make the mod brave everywhere. These are genuinely variable and stay guarded:

* **Custom object construction** (`construct_object` with a donor template) — every field copy can
  fail on a different donor, and a half-built part is worse than a missing one.
* **Save/sidecar restore** — the item you get back was written by an older build of the mod.
* **Map-load ordering** — packages finish loading at unpredictable times, which is why the pool sync
  is idempotent and re-runs on map change.
* **Field writes on structs** (`BalancedInventoryData` rows, manufacturer weight rows,
  `ManufacturerCustomPartWeightData`) — reflection confirms the pool row layout, but not
  safe writes or native behavior. Manufacturer layouts and behavior need more evidence.
* **`load_package` for the damage-type packages.** Those packages being loaded is a *consequence* of
  this call, not an independent fact, so it stays.

## RE Probe follow-up (2026-11-05)

A full dump with Melee Arsenal enabled captured five `Pool_Weapons_All_*` objects. Each
contained six class entries with weights 100, 80, 80, 80, 55 and 20 (total **415**).
Four also contained an injected melee entry of about 63.846. These are live, possibly
hotfixed values, not proof of every pool or its pristine state. The mod retains 520 as
the slider reference denominator, while measuring each pool separately; changing that
constant would change the slider behavior.

The reflected `BalancedInventoryData` layout has `ItmPoolDefinition`,
`InvBalanceDefinition`, `Probability` and `bDropOnDeath`; the previously used name
`WeightedInventoryBalanceData` was incorrect for these rows.

The captured traces had hook registration errors and stack drift; do not treat their
call edges or unobserved functions as evidence until repeated with a corrected tracer.
