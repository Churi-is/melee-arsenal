"""Strict C++-typed mock-SDK integration tests for `sdk_mods/melee_arsenal/runtime` (v0.2.2).

Verifies:
- Strict `WrappedStruct` & `UObject` schema enforcement (`pyunrealsdk` 1.10 rules).
- Single-item staggered `ServerGrantMissionRewards` (`len(RewardItems) == 1` per grant so BL2 never
  triggers the Choose-1-of-2 quest reward prompt).
- `ConsumeAmmo` + `WillowLightProjectileManager:AddProj` + `WillowPawn:WeaponFired` + `tick_melee_projectiles()`
  primary-fire melee attack pipeline (Single-Target vs Frontal-Cleave, Target Dummy support,
  elemental `FM_Melee_Elem_*` routing, stacked `MyProjs[idx].Damage`, exact meter range expiry,
  and `pawn.Arms.Translation`/`Rotation` procedural slash).
- `ItemCardGFxObject` hooks NEVER call `AdjustHeight()` (preventing shrunk inventory item cards).
"""
from __future__ import annotations

import copy
import py_compile
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
SDK_MODS_DIR = ROOT_DIR / "sdk_mods"


class FakeWrappedStruct:
    """Strict mock of `unrealsdk::unreal::WrappedStruct`."""

    def __init__(self, struct_name: str, **kwargs):
        object.__setattr__(self, "_struct_name", struct_name)
        object.__setattr__(self, "_fields", set(kwargs.keys()))
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)

    def __setattr__(self, name: str, value):
        fields = object.__getattribute__(self, "_fields")
        if name not in fields:
            raise AttributeError(
                f"WrappedStruct {object.__getattribute__(self, '_struct_name')} has no field {name!r}"
            )
        cur = object.__getattribute__(self, name)
        if isinstance(cur, FakeWrappedStruct) and not isinstance(value, FakeWrappedStruct):
            raise RuntimeError(
                f"Unable to cast Python instance of type {type(value)} to C++ type 'unrealsdk::unreal::WrappedStruct'"
            )
        object.__setattr__(self, name, value)

    def __copy__(self):
        data = {
            k: copy.copy(object.__getattribute__(self, k))
            for k in object.__getattribute__(self, "_fields")
        }
        return FakeWrappedStruct(object.__getattribute__(self, "_struct_name"), **data)


STRICT_SCHEMAS: dict[str, set[str]] = {
    "FiringModeDefinition": {
        "FireType",
        "DamageTypeDefinition",
        "ExplosionOverrideDefinition",
        "ExplosionOverideInstanceDataName",
        "OnAnyImpact",
        "TimerEvents",
        "FiringPatternLines",
        "Speed",
        "Acceleration",
        "WaveFreq",
        "WaveAmp",
        "WavePhase",
        "TraceExtent",
        "Lifetime",
        "TraceInterval",
        "AirFriction",
        "RicochetFriction",
        "PartSysTemplate",
        "ConditionalPartSysTemplate",
        "SoundDefinition",
        "bSuppressWeaponSpread",
        "bFireRandomlyFromPattern",
        "bResetPatternAfterEachShot",
        "bScalePatternByWeaponSpread",
        "bPenetratePawn",
        "NumRicochets",
    },
    "WeaponTypeDefinition": {
        "DefaultFiringModeDefinition",
        "ShotCost",
        "ClipSize",
        "bDisableReloadMessages",
        "ReloadTime",
        "FireRate",
        "Range",
        "Spread",
        "PerShotAccuracyImpulse",
        "MuzzleFlashPSTemplates",
        "MuzzleFlashLightTemplate",
        "ShellCasingPSCTemplate",
        "TracerTemplate",
        "Typename",
        "WeaponAttributeEffects",
        "WeaponFireAnimations",
        "WeaponKickSpeed",
        "WeaponKickUp",
        "WeaponKickDown",
        "WeaponKickLeft",
        "WeaponKickRight",
        "ZoomedRecoilAnimScale",
        "FireShake",
        "InstantHitDamage",
        "FireSounds",
        "FireTailSounds",
        "GestaltMesh",
        "TitleList",
        "PrefixList",
        "bUIMeshRotationNeedsTransformFixup",
        "UIMeshRotation",
        "Presentation",
        "FocusRadius",
        "FocusOffset",
        "MinimumHorizontalPercentage",
        "MinimumVerticalPercentage",
        "ThirdPersonMeshScale",
    },
    "SkeletalMesh": {
        "Origin",
        "RotOrigin",
        "Sockets",
    },
    "GestaltSkeletalMeshDefinition": {
        "GestaltSkeletalMesh",
        "GestaltInfos",
        "GestaltPartBounds",
        "GestaltSocketMappings",
    },
    "WeaponBalanceDefinition": {
        "BaseDefinition",
        "InventoryDefinition",
        "RuntimePartListCollection",
        "WeaponPartListCollection",
        "Manufacturers",
    },
    "PackageAssetSublibrary": {
        "LibraryType",
        "CachedPackageName",
        "AssetPaths",
    },
}


class FakeUObject:
    """Strict mock UE3 UObject enforcing declared properties and `WrappedStruct` cast rules."""

    _CORE_ATTRS = {
        "_cls_name",
        "_strict_props",
        "Class",
        "Outer",
        "Name",
        "ObjectFlags",
        "GetValue",
        "ApplyBehaviorToContext",
        "SetParent",
        "SetTextureParameterValue",
    }

    def __init__(self, cls_name: str, outer: FakeUObject | None, name: str, **kwargs):
        object.__setattr__(self, "_cls_name", cls_name)
        object.__setattr__(self, "_strict_props", STRICT_SCHEMAS.get(cls_name))
        object.__setattr__(self, "Class", types.SimpleNamespace(Name=cls_name))
        object.__setattr__(self, "Outer", outer)
        object.__setattr__(self, "Name", name)
        object.__setattr__(self, "ObjectFlags", 0)
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)

    def __setattr__(self, name: str, value):
        if name in self._CORE_ATTRS:
            object.__setattr__(self, name, value)
            return

        strict = object.__getattribute__(self, "_strict_props")
        if strict is not None and name not in strict:
            raise AttributeError(
                f"UObject {object.__getattribute__(self, '_cls_name')} has no property {name!r}"
            )

        d = object.__getattribute__(self, "__dict__")
        if name in d:
            cur = d[name]
            if isinstance(cur, FakeWrappedStruct) and not isinstance(value, FakeWrappedStruct):
                raise RuntimeError(
                    f"Unable to cast Python instance of type {type(value)} to C++ type 'unrealsdk::unreal::WrappedStruct'"
                )
            if isinstance(cur, list) and cur and isinstance(cur[0], FakeWrappedStruct):
                for item in value:
                    if not isinstance(item, FakeWrappedStruct):
                        raise RuntimeError(
                            f"Unable to cast list element {type(item)} to C++ type 'unrealsdk::unreal::WrappedStruct'"
                        )
        object.__setattr__(self, name, value)

    def _path_name(self) -> str:
        outer = object.__getattribute__(self, "Outer")
        nm = object.__getattribute__(self, "Name")
        return nm if outer is None else f"{outer._path_name()}.{nm}"

    def __repr__(self) -> str:
        return f"{object.__getattribute__(self, '_cls_name')}'{self._path_name()}'"


def _clone_val(v):
    if isinstance(v, FakeUObject):
        return v
    if isinstance(v, FakeWrappedStruct):
        return copy.copy(v)
    if isinstance(v, list):
        return [_clone_val(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_clone_val(x) for x in v)
    return copy.deepcopy(v)


class MockBL2World:
    """Realistic strict mock `unrealsdk` + `mods_base` BL2 object graph."""

    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir
        self.objects: dict[str, FakeUObject] = {}
        self.melee_mult: float = 1.0
        self.roid_buff: float = 0.0
        self.player_crit: float = 0.0
        self.status_mult: float = 1.0
        self.played_ak: list[str] = []
        self.played_canims: list[str] = []
        # (CameraAnim path, full positional args) for every `ClientPlayCameraAnim` call
        self.camera_anim_calls: list[tuple[str, tuple]] = []
        self.reward_call_lengths: list[int] = []
        self.ground_pickups: list = []
        self.block_sentinel = object()
        self._populate_vanilla_donors()

    def register(self, obj: FakeUObject) -> FakeUObject:
        self.objects[obj._path_name()] = obj
        return obj

    def _client_play_camera_anim(self, cam_anim, *args) -> None:
        self.played_canims.append(cam_anim._path_name())
        self.camera_anim_calls.append((cam_anim._path_name(), args))

    def construct_object(
        self,
        cls_name: str,
        outer: FakeUObject | None,
        name: str,
        flags: int = 0,
        template: FakeUObject | None = None,
    ) -> FakeUObject:
        obj = FakeUObject(cls_name, outer, name)
        if template is not None:
            for k, v in vars(template).items():
                if k in FakeUObject._CORE_ATTRS:
                    continue
                object.__setattr__(obj, k, _clone_val(v))
        elif cls_name == "Behavior_SpawnLootAroundPoint":
            object.__setattr__(obj, "ItemPools", [])
            object.__setattr__(
                obj,
                "CustomLocation",
                FakeWrappedStruct("CustomLocationStruct", bUseCustomLocation=False, Location=self._vec()),
            )
            object.__setattr__(obj, "CircularScatterRadius", 80.0)
            object.__setattr__(obj, "SpawnVelocityRelativeTo", 0)
            object.__setattr__(obj, "SpawnVelocity", self._vec())
            loc_prop = types.SimpleNamespace(Name="Location", Struct=types.SimpleNamespace(Name="Vector"))
            use_prop = types.SimpleNamespace(Name="bUseCustomLocation")
            loc_field = types.SimpleNamespace(
                Struct=types.SimpleNamespace(_properties=lambda: [use_prop, loc_prop])
            )
            ab_fn = types.SimpleNamespace(
                _properties=lambda: [
                    types.SimpleNamespace(Name="KernelInfo", Struct=types.SimpleNamespace(Name="BehaviorKernelInfo")),
                    types.SimpleNamespace(Name="EventData", Struct=types.SimpleNamespace(Name="BehaviorEventData")),
                ]
            )
            obj.Class = types.SimpleNamespace(
                Name=cls_name,
                _find=lambda fn_or_prop: loc_field if fn_or_prop == "CustomLocation" else ab_fn,
            )
            obj.ApplyBehaviorToContext = lambda **kw: self._run_drop_spawner(obj, kw)
        elif cls_name == "MaterialInstanceConstant":
            object.__setattr__(obj, "Parent", None)
            object.__setattr__(obj, "_tex_params", {})
            obj.SetParent = lambda parent: object.__setattr__(obj, "Parent", parent)
            obj.SetTextureParameterValue = lambda pname, tex: obj._tex_params.__setitem__(pname, tex)
        obj.ObjectFlags = flags
        return self.register(obj)

    def find_object(self, cls_name: str, path: str):
        if path not in self.objects:
            raise ValueError(f"Unable to find object {cls_name} {path}")
        return self.objects[path]

    def _attr_init(self, val: float = 1.0, scale: float = 1.0) -> FakeWrappedStruct:
        return FakeWrappedStruct(
            "AttributeInitializationData",
            BaseValueConstant=float(val),
            BaseValueAttribute=None,
            InitializationDefinition=None,
            BaseValueScaleConstant=float(scale),
        )

    def _vec(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> FakeWrappedStruct:
        return FakeWrappedStruct("Vector", X=float(x), Y=float(y), Z=float(z))

    def _make_slot(self, parts: list[FakeUObject]) -> FakeWrappedStruct:
        return FakeWrappedStruct(
            "CustomWeaponPartTypeData",
            bEnabled=bool(parts),
            WeightedParts=[
                FakeWrappedStruct(
                    "PartWeightDatum",
                    Part=p,
                    Manufacturers=[],
                    MinGameStageIndex=0,
                    MaxGameStageIndex=1,
                    DefaultWeightIndex=0,
                )
                for p in parts
            ],
        )

    def _populate_vanilla_donors(self) -> None:
        gd_pistol = self.register(FakeUObject("Package", None, "GD_Weap_Pistol"))
        a_weap = self.register(FakeUObject("Package", gd_pistol, "A_Weapons"))
        fm_pkg = self.register(FakeUObject("Package", gd_pistol, "FiringModes"))
        name_pkg = self.register(FakeUObject("Package", gd_pistol, "Name"))
        th_pkg = self.register(FakeUObject("Package", name_pkg, "Title_Hyperion"))
        ph_pkg = self.register(FakeUObject("Package", name_pkg, "Prefix_Hyperion"))

        title_tpl = self.register(
            FakeUObject(
                "WeaponNamePartDefinition",
                th_pkg,
                "Title__Shooter",
                PartName="Shooter",
                bNameIsUnique=False,
                WeaponAttributeEffects=[],
                ExternalAttributeEffects=[],
                AttributeSlotEffects=[],
                CustomPresentations=[],
            )
        )
        prefix_tpl = self.register(
            FakeUObject(
                "WeaponNamePartDefinition",
                ph_pkg,
                "Prefix__AutoCorrecting",
                PartName="Correcting",
                bNameIsUnique=False,
                WeaponAttributeEffects=[],
                ExternalAttributeEffects=[],
                AttributeSlotEffects=[],
                CustomPresentations=[],
            )
        )
        fm_default = self.register(
            FakeUObject(
                "FiringModeDefinition",
                fm_pkg,
                "Bullet_Pistol_Default",
                FireType=0,
                DamageTypeDefinition=None,
                ExplosionOverrideDefinition=None,
                ExplosionOverideInstanceDataName="ExplosionOverride",
                OnAnyImpact=[],
                TimerEvents=[],
                FiringPatternLines=[],
                Speed=21500.0,
                Acceleration=self._vec(),
                WaveFreq=self._vec(),
                WaveAmp=self._vec(),
                WavePhase=self._vec(),
                TraceExtent=self._vec(),
                Lifetime=5.0,
                TraceInterval=0.2,
                AirFriction=0.0,
                RicochetFriction=0.0,
                PartSysTemplate="FX_Tracer",
                ConditionalPartSysTemplate="FX_CondTracer",
                SoundDefinition="Snd_Pistol",
                bSuppressWeaponSpread=False,
                bPenetratePawn=False,
                NumRicochets=0,
            )
        )
        gd_sg = self.register(FakeUObject("Package", None, "GD_Weap_Shotgun"))
        sg_fm_pkg = self.register(FakeUObject("Package", gd_sg, "FiringModes"))
        self.register(
            FakeUObject(
                "FiringModeDefinition",
                sg_fm_pkg,
                "Bullet_Shotgun_TidalWave",
                FireType=0,
                DamageTypeDefinition=None,
                ExplosionOverrideDefinition=None,
                ExplosionOverideInstanceDataName="ExplosionOverride",
                OnAnyImpact=[],
                TimerEvents=[],
                FiringPatternLines=["PatternLine0"],
                Speed=1800.0,
                Acceleration=self._vec(0.0, 0.0, -50.0),
                WaveFreq=self._vec(10.0, 10.0, 10.0),
                WaveAmp=self._vec(20.0, 20.0, 20.0),
                WavePhase=self._vec(),
                TraceExtent=self._vec(),
                Lifetime=4.0,
                TraceInterval=0.2,
                AirFriction=0.0,
                RicochetFriction=0.0,
                PartSysTemplate="FX_Tidal",
                ConditionalPartSysTemplate="FX_CondTidal",
                SoundDefinition="Snd_Shotgun",
                bSuppressWeaponSpread=False,
                bFireRandomlyFromPattern=False,
                bResetPatternAfterEachShot=True,
                bScalePatternByWeaponSpread=True,
                bPenetratePawn=True,
                NumRicochets=2,
            )
        )

        elem_pkg = self.register(FakeUObject("Package", gd_pistol, "elemental"))
        elem_parts = [
            self.register(
                FakeUObject(
                    "WeaponPartDefinition",
                    elem_pkg,
                    f"Pistol_Elemental_{ename}",
                    GestaltModeSkeletalMeshName="Acc_Barrel_Elemental1",
                    AdditionalGestaltModeSkeletalMeshNames=(),
                    WeaponAttributeEffects=[],
                    ExternalAttributeEffects=[],
                    AttributeSlotEffects=[],
                    AttributeSlotUpgrades=[],
                    TitleList=[],
                    PrefixList=[],
                    CustomDamageTypeDefinition=None,
                    CustomFiringModeDefinition=None,
                    bHiddenNoMesh=(ename == "None"),
                )
            )
            for ename in ("None", "Fire", "Shock", "Corrosive", "Slag")
        ]

        acc_pkg = self.register(FakeUObject("Package", gd_pistol, "Accessory"))
        acc_none = self.register(
            FakeUObject(
                "WeaponPartDefinition",
                acc_pkg,
                "Pistol_Accessory_None",
                GestaltModeSkeletalMeshName="Acc_Barrel_Elemental3",
                AdditionalGestaltModeSkeletalMeshNames=("None", "None"),
                WeaponAttributeEffects=[],
                ExternalAttributeEffects=[],
                AttributeSlotEffects=[],
                AttributeSlotUpgrades=[],
                TitleList=[],
                PrefixList=[],
                CustomFiringModeDefinition=None,
                bHiddenNoMesh=True,
            )
        )
        acc_bayonet = self.register(
            FakeUObject(
                "WeaponPartDefinition",
                acc_pkg,
                "Pistol_Accessory_Bayonet_1",
                GestaltModeSkeletalMeshName="Acc_Barrel_Blade1",
                AdditionalGestaltModeSkeletalMeshNames=(),
                WeaponAttributeEffects=[],
                ExternalAttributeEffects=["BayonetMeleeBonus"],
                AttributeSlotEffects=[],
                AttributeSlotUpgrades=[],
                TitleList=[],
                PrefixList=[],
                CustomFiringModeDefinition=None,
                bHiddenNoMesh=False,
            )
        )

        pres_pkg = self.register(FakeUObject("Package", None, "GD_InventoryPresentations"))
        pres_defs = self.register(FakeUObject("Package", pres_pkg, "Definitions"))
        pres_pistol = self.register(
            FakeUObject(
                "InventoryCardPresentationDefinition",
                pres_defs,
                "Pistol",
                Zoom=1.0,
            )
        )

        gd_mfr = self.register(FakeUObject("Package", None, "GD_Manufacturers"))
        mfr_sub = self.register(FakeUObject("Package", gd_mfr, "Manufacturers"))
        body_pkg = self.register(FakeUObject("Package", gd_pistol, "Body"))
        grip_pkg = self.register(FakeUObject("Package", gd_pistol, "Grip"))
        barrel_pkg = self.register(FakeUObject("Package", gd_pistol, "Barrel"))
        mat_pkg = self.register(FakeUObject("Package", gd_pistol, "ManufacturerMaterials"))

        makers = ("Hyperion", "Bandit", "Jakobs", "Maliwan", "Torgue")
        all_grips = [
            self.register(
                FakeUObject(
                    "WeaponPartDefinition",
                    grip_pkg,
                    f"Pistol_Grip_{m}",
                    GestaltModeSkeletalMeshName=f"Pistol_Grip_{m}",
                    AdditionalGestaltModeSkeletalMeshNames=(),
                    WeaponAttributeEffects=[],
                    ExternalAttributeEffects=[],
                    AttributeSlotEffects=[],
                    AttributeSlotUpgrades=[],
                    TitleList=[],
                    PrefixList=[],
                    CustomFiringModeDefinition=None,
                )
            )
            for m in makers
        ]
        all_barrels = [
            self.register(
                FakeUObject(
                    "WeaponPartDefinition",
                    barrel_pkg,
                    f"Pistol_Barrel_{m}",
                    GestaltModeSkeletalMeshName=f"Pistol_Barrel_{m}",
                    AdditionalGestaltModeSkeletalMeshNames=(),
                    WeaponAttributeEffects=[],
                    ExternalAttributeEffects=[],
                    AttributeSlotEffects=[],
                    AttributeSlotUpgrades=[],
                    TitleList=[],
                    PrefixList=[],
                    CustomFiringModeDefinition="TorgueRocketFM" if m == "Torgue" else None,
                )
            )
            for m in makers
        ]

        for m in makers:
            mfr_obj = self.register(FakeUObject("ManufacturerDefinition", mfr_sub, m))
            self.register(
                FakeUObject(
                    "WeaponTypeDefinition",
                    a_weap,
                    f"WeaponType_{m}_Pistol",
                    ShotCost=1,
                    ClipSize=12,
                    bDisableReloadMessages=False,
                    ReloadTime=1.8,
                    FireRate=0.2,
                    Range=16384.0,
                    Spread=1.5,
                    PerShotAccuracyImpulse=-2.0,
                    MuzzleFlashPSTemplates="FX_Muzzle",
                    MuzzleFlashLightTemplate="Light_Muzzle",
                    ShellCasingPSCTemplate=None,
                    TracerTemplate=None,
                    DefaultFiringModeDefinition=fm_default,
                    InstantHitDamage=self._attr_init(20.0, 1.5),
                    WeaponAttributeEffects=[],
                    WeaponFireAnimations=["Fire_Pistol_Recoil"],
                    WeaponKickSpeed=15.0,
                    WeaponKickUp=1.0,
                    WeaponKickDown=0.1,
                    WeaponKickLeft=1.0,
                    WeaponKickRight=1.0,
                    ZoomedRecoilAnimScale=0.5,
                    FireShake=FakeWrappedStruct("ViewShake", OffsetTime=0.1, RotTime=0.1, FOVTime=0.0),
                    TitleList=[title_tpl],
                    PrefixList=[prefix_tpl],
                    FireSounds=[FakeWrappedStruct("WeaponFireSound", Event="GunShot", Cue=None, Expressions=[])],
                    FireTailSounds=[FakeWrappedStruct("WeaponFireSound", Event="GunTail", Cue=None, Expressions=[])],
                    GestaltMesh=None,
                    Typename="Pistol",
                    bUIMeshRotationNeedsTransformFixup=False,
                    UIMeshRotation=FakeWrappedStruct("Rotator", Pitch=0, Yaw=0, Roll=0),
                    Presentation=pres_pistol,
                    FocusRadius=55.0,
                    FocusOffset=FakeWrappedStruct("Vector", X=-0.4, Y=-0.075, Z=0.0),
                    MinimumHorizontalPercentage=0.0,
                    MinimumVerticalPercentage=0.4,
                    ThirdPersonMeshScale=0.85,
                )
            )
            body_part = self.register(
                FakeUObject(
                    "WeaponPartDefinition",
                    body_pkg,
                    f"Pistol_Body_{m}",
                    GestaltModeSkeletalMeshName=f"Pistol_Body_{m}",
                    AdditionalGestaltModeSkeletalMeshNames=(),
                    ManufacturerDefinitionForScopeMovie=mfr_obj,
                    WeaponAttributeEffects=[],
                    ExternalAttributeEffects=[],
                    AttributeSlotEffects=[],
                    AttributeSlotUpgrades=[],
                    TitleList=[],
                    PrefixList=[],
                    CustomFiringModeDefinition=None,
                )
            )
            prev_bal = None
            for idx, suf in enumerate(("", "2_Uncommon", "3_Rare", "4_VeryRare"), start=1):
                mat_part = self.register(
                    FakeUObject(
                        "WeaponPartDefinition",
                        mat_pkg,
                        f"Mat_{m}_{idx}",
                        Material=None,
                        # Match real BL2: vanilla Material parts resolve `Rarity` through a
                        # `BaseValueAttribute` resolver chain, NOT a literal `BaseValueConstant`.
                        Rarity=FakeWrappedStruct(
                            "AttributeInitializationData",
                            BaseValueConstant=0.0,
                            BaseValueAttribute=types.SimpleNamespace(
                                ValueResolverChain=[
                                    types.SimpleNamespace(ConstantValue=float(idx))
                                ]
                            ),
                            InitializationDefinition=None,
                            BaseValueScaleConstant=1.0,
                        ),
                        GestaltModeSkeletalMeshName="None",
                        AdditionalGestaltModeSkeletalMeshNames=("None", "None"),
                        WeaponAttributeEffects=[],
                        ExternalAttributeEffects=[],
                        AttributeSlotEffects=[],
                        AttributeSlotUpgrades=[],
                    )
                )
                plc = self.register(
                    FakeUObject(
                        "WeaponPartListCollectionDefinition",
                        a_weap,
                        f"PLC_{m}_{suf or '1_Common'}",
                        AssociatedWeaponType=None,
                        ConsolidatedAttributeInitData=[self._attr_init(16.0), self._attr_init(1.0)],
                        BodyPartData=self._make_slot([body_part]),
                        GripPartData=self._make_slot(all_grips),
                        BarrelPartData=self._make_slot(all_barrels),
                        MaterialPartData=self._make_slot([mat_part]),
                        ElementalPartData=self._make_slot(elem_parts),
                        # Match real BL2: Common PLC ONLY has `Pistol_Accessory_None`!
                        Accessory1PartData=self._make_slot([acc_none] if not suf else [acc_none, acc_bayonet]),
                        Accessory2PartData=self._make_slot([]),
                        SightPartData=self._make_slot([]),
                        StockPartData=self._make_slot([]),
                    )
                )
                grade = FakeWrappedStruct(
                    "InventoryGameStageGradeWeightData",
                    GameStageRequirement=FakeWrappedStruct("GameStageRange", MinGameStage=1, MaxGameStage=100),
                )
                mfr_datum = FakeWrappedStruct("BalanceMfrDatum", Manufacturer=mfr_obj, Grades=[grade])
                bal_name = f"Pistol_{m}" if not suf else f"Pistol_{m}_{suf}"
                # Match real BL2: ONLY Common (`Pistol_<Maker>`) has Manufacturers populated;
                # higher rarities leave `Manufacturers = []` and inherit via `BaseDefinition`!
                prev_bal = self.register(
                    FakeUObject(
                        "WeaponBalanceDefinition",
                        a_weap,
                        bal_name,
                        BaseDefinition=prev_bal,
                        InventoryDefinition=None,
                        RuntimePartListCollection=plc,
                        WeaponPartListCollection=plc,
                        Manufacturers=[mfr_datum] if not suf else [],
                    )
                )

        # DamageTypes, Impacts, AkEvents, CameraAnims, Attributes, Mission Spawner
        for pkg_n, sub_n, obj_n in (
            ("GD_Impact", "DamageType", "DmgType_Normal"),
            ("GD_Incendiary", "DamageType", "DmgType_Incendiary_Impact"),
            ("GD_Incendiary", "DamageType", "DmgType_Incendiary_Impact_NoDoT"),
            ("GD_Shock", "DamageType", "DmgType_Shock_Impact"),
            ("GD_Shock", "DamageType", "DmgType_Shock_Impact_NoDoT"),
            ("GD_Corrosive", "DamageType", "DmgType_Corrosive_Impact"),
            ("GD_Corrosive", "DamageType", "DmgType_Corrosive_Impact_NoDoT"),
            ("GD_Amp", "DamageType", "DmgType_Amp_Impact"),
            ("GD_Explosive", "DamageType", "DmgType_Explosive_Impact_ForceFlinch"),
        ):
            p1 = self.objects.get(pkg_n) or self.register(FakeUObject("Package", None, pkg_n))
            p2 = self.objects.get(f"{pkg_n}.{sub_n}") or self.register(FakeUObject("Package", p1, sub_n))
            self.register(FakeUObject("WillowDamageTypeDefinition", p2, obj_n))

        imp_p1 = self.register(FakeUObject("Package", None, "GD_Impacts"))
        imp_p2 = self.register(FakeUObject("Package", imp_p1, "Bullets"))
        self.register(FakeUObject("ImpactDefinition", imp_p2, "Bullet_LargeCaliber"))
        imp_p3 = self.register(FakeUObject("Package", imp_p1, "Melee"))
        self.register(FakeUObject("ImpactDefinition", imp_p3, "Impact_MeleeGeneric"))

        ak_p = self.register(FakeUObject("Package", None, "Ake_Wep_Melee"))
        self.register(FakeUObject("AkEvent", ak_p, "Ak_Play_Wep_Melee_Generic_Attack"))
        self.register(FakeUObject("AkEvent", ak_p, "Ak_Play_wep_melee_BladedWeap_hit"))

        ca_p1 = self.register(FakeUObject("Package", None, "Anim_CameraAnimations"))
        ca_p2 = self.register(FakeUObject("Package", ca_p1, "Melee"))
        self.register(FakeUObject("CameraAnim", ca_p2, "Canim_RepeaterPistol_Melee"))
        self.register(FakeUObject("CameraAnim", ca_p2, "Canim_CombatShotgun_Melee"))
        self.register(FakeUObject("CameraAnim", ca_p2, "Canim_RocketLauncher_Melee"))

        da_p = self.register(FakeUObject("Package", None, "D_Attributes"))
        dsm_p = self.register(FakeUObject("Package", da_p, "DamageSourceModifiers"))
        ga_p = self.register(FakeUObject("Package", da_p, "GameplayAttributes"))

        a_mm = self.register(FakeUObject("AttributeDefinition", dsm_p, "InstigatedMeleeDamageModifier"))
        a_mm.GetValue = lambda _ctx: (self.melee_mult, None)
        a_mb = self.register(FakeUObject("AttributeDefinition", dsm_p, "InstigatedMeleeDamageAddedBuff"))
        a_mb.GetValue = lambda _ctx: (self.roid_buff, None)
        a_pc = self.register(FakeUObject("AttributeDefinition", ga_p, "PlayerCriticalHitBonus"))
        a_pc.GetValue = lambda _ctx: (self.player_crit, None)
        a_sm = self.register(FakeUObject("AttributeDefinition", dsm_p, "InstigatedStatusEffectDamageModifier"))
        a_sm.GetValue = lambda _ctx: (self.status_mult, None)

        ip_p1 = self.register(FakeUObject("Package", None, "GD_Itempools"))
        ip_p2 = self.register(FakeUObject("Package", ip_p1, "WeaponPools"))
        ip_enemy = self.register(FakeUObject("Package", ip_p1, "EnemyDropPools"))
        ip_chest = self.register(FakeUObject("Package", ip_p1, "Treasure_ChestPools"))
        ip_loot = self.register(FakeUObject("Package", ip_p1, "LootablePools"))
        ip_sh_pkg = self.register(FakeUObject("Package", ip_p1, "ShieldPools"))
        shield_pool = self.register(FakeUObject("ItemPoolDefinition", ip_sh_pkg, "Pool_Shields_All", BalancedItems=[]))

        bal_pkg_w = self.register(FakeUObject("Package", None, "GD_Balance"))
        wt_init_pkg = self.register(FakeUObject("Package", bal_pkg_w, "Weighting"))
        w2_init = self.register(FakeUObject("AttributeInitializationDefinition", wt_init_pkg, "Weight_2_Uncommon"))
        w3_init = self.register(FakeUObject("AttributeInitializationDefinition", wt_init_pkg, "Weight_3_Uncommoner"))

        pistol_subs: dict[str, FakeUObject] = {}
        for tier in ("01_Common", "02_Uncommon", "04_Rare", "05_VeryRare"):
            pistol_sub = self.register(
                FakeUObject(
                    "ItemPoolDefinition",
                    ip_p2,
                    f"Pool_Weapons_Pistols_{tier}",
                    MinGameStageRequirement="GD_Balance.Stages.Gamestage_02",
                    MaxGameStageRequirement="GD_Balance.Stages.Gamestage_99",
                    bAutoReadyItems=True,
                    Quantity=self._attr_init(2.0),
                    BalancedItems=[
                        FakeWrappedStruct(
                            "ItemPoolInfo",
                            ItmPoolDefinition=None,
                            InvBalanceDefinition=None,
                            bDropOnDeath=True,
                            Probability=self._attr_init(1.0),
                        )
                    ],
                )
            )
            pistol_subs[tier] = pistol_sub
            self.register(
                FakeUObject(
                    "ItemPoolDefinition",
                    ip_p2,
                    f"Pool_Weapons_All_{tier}",
                    BalancedItems=[
                        FakeWrappedStruct(
                            "ItemPoolInfo",
                            ItmPoolDefinition=pistol_sub,
                            InvBalanceDefinition=None,
                            bDropOnDeath=True,
                            Probability=self._attr_init(520.0),
                        )
                    ],
                )
            )

        # Standard enemy / badass / boss death-drop pools (tiered `Pool_GunsAndGear_*`)
        for tier in ("01_Common", "02_Uncommon", "04_Rare", "05_VeryRare"):
            self.register(
                FakeUObject(
                    "ItemPoolDefinition",
                    ip_enemy,
                    f"Pool_GunsAndGear_{tier}",
                    BalancedItems=[
                        FakeWrappedStruct(
                            "ItemPoolInfo",
                            ItmPoolDefinition=pistol_subs[tier],
                            InvBalanceDefinition=None,
                            bDropOnDeath=True,
                            Probability=self._attr_init(100.0),
                        )
                    ],
                )
            )

        # Marcus gun vendors re-roll shelves from these pools
        ven_pkg = self.register(FakeUObject("Package", None, "Sanctuary"))
        self.vendor_resets: int = 0
        self.vendor_machine = self.register(
            FakeUObject(
                "WillowVendingMachine",
                ven_pkg,
                "WillowVendingMachine_0",
                ResetInventory=lambda: setattr(self, "vendor_resets", self.vendor_resets + 1),
            )
        )

        # Class-biased enemy death pool mixing weapon entries (100) + shield entries (85)
        self.register(
            FakeUObject(
                "ItemPoolDefinition",
                ip_enemy,
                "Pool_GunsAndGear_WeightedPistols",
                BalancedItems=[
                    FakeWrappedStruct(
                        "ItemPoolInfo",
                        ItmPoolDefinition=pistol_subs["01_Common"],
                        InvBalanceDefinition=None,
                        bDropOnDeath=True,
                        Probability=self._attr_init(100.0),
                    ),
                    FakeWrappedStruct(
                        "ItemPoolInfo",
                        ItmPoolDefinition=shield_pool,
                        InvBalanceDefinition=None,
                        bDropOnDeath=True,
                        Probability=self._attr_init(85.0),
                    ),
                ],
            )
        )

        # Regular & Epic chest pools
        self.register(
            FakeUObject(
                "ItemPoolDefinition",
                ip_chest,
                "Pool_WeaponChest_Weapons_LongGuns",
                BalancedItems=[
                    FakeWrappedStruct(
                        "ItemPoolInfo",
                        ItmPoolDefinition=pistol_subs["01_Common"],
                        InvBalanceDefinition=None,
                        bDropOnDeath=True,
                        Probability=self._attr_init(1.0),
                    )
                ],
            )
        )
        self.register(
            FakeUObject(
                "ItemPoolDefinition",
                ip_chest,
                "Pool_EpicChest_Weapons_Pistols",
                BalancedItems=[
                    FakeWrappedStruct(
                        "ItemPoolInfo",
                        ItmPoolDefinition=pistol_subs["02_Uncommon"],
                        InvBalanceDefinition=None,
                        bDropOnDeath=True,
                        Probability=self._attr_init(1.0),
                    )
                ],
            )
        )

        # Locker pool using Gearbox `InitializationDefinition` weights (`Weight_2_Uncommon=10` + `Weight_3_Uncommoner=5` = 15)
        prob_w2 = self._attr_init(0.0)
        prob_w2.InitializationDefinition = w2_init
        prob_w3 = self._attr_init(0.0)
        prob_w3.InitializationDefinition = w3_init
        self.register(
            FakeUObject(
                "ItemPoolDefinition",
                ip_loot,
                "Pool_Locker_Items_SMGsAndPistols",
                BalancedItems=[
                    FakeWrappedStruct(
                        "ItemPoolInfo",
                        ItmPoolDefinition=pistol_subs["01_Common"],
                        InvBalanceDefinition=None,
                        bDropOnDeath=True,
                        Probability=prob_w2,
                    ),
                    FakeWrappedStruct(
                        "ItemPoolInfo",
                        ItmPoolDefinition=pistol_subs["02_Uncommon"],
                        InvBalanceDefinition=None,
                        bDropOnDeath=True,
                        Probability=prob_w3,
                    ),
                ],
            )
        )

        # Marcus Weapon Vending Machine & Item of the Day pools (`GD_ItemPools_Shop`)
        shop_p1 = self.register(FakeUObject("Package", None, "GD_ItemPools_Shop"))
        shop_p2 = self.register(FakeUObject("Package", shop_p1, "WeaponPools"))
        self.register(
            FakeUObject(
                "ItemPoolDefinition",
                shop_p2,
                "ShopPool_Weapons_Pistols",
                BalancedItems=[
                    FakeWrappedStruct(
                        "ItemPoolInfo",
                        ItmPoolDefinition=pistol_subs["01_Common"],
                        InvBalanceDefinition=None,
                        bDropOnDeath=True,
                        Probability=self._attr_init(100.0),
                    ),
                    FakeWrappedStruct(
                        "ItemPoolInfo",
                        ItmPoolDefinition=pistol_subs["02_Uncommon"],
                        InvBalanceDefinition=None,
                        bDropOnDeath=True,
                        Probability=self._attr_init(10.0),
                    ),
                ],
            )
        )
        self.register(
            FakeUObject(
                "ItemPoolDefinition",
                shop_p2,
                "Shoppool_FeaturedItem_WeaponMachine",
                BalancedItems=[
                    FakeWrappedStruct(
                        "ItemPoolInfo",
                        ItmPoolDefinition=pistol_subs["02_Uncommon"],
                        InvBalanceDefinition=None,
                        bDropOnDeath=True,
                        Probability=self._attr_init(6.0),
                    )
                ],
            )
        )

        # AI enemy-held weapon pool (`GD_ItempoolsEnemyUse`) — MUST NEVER be modified!
        ai_p1 = self.register(FakeUObject("Package", None, "GD_ItempoolsEnemyUse"))
        ai_p2 = self.register(FakeUObject("Package", ai_p1, "Weapons"))
        self.register(
            FakeUObject(
                "ItemPoolDefinition",
                ai_p2,
                "Pool_Weapons_Pistols_EnemyUse",
                BalancedItems=[
                    FakeWrappedStruct(
                        "ItemPoolInfo",
                        ItmPoolDefinition=pistol_subs["01_Common"],
                        InvBalanceDefinition=None,
                        bDropOnDeath=True,
                        Probability=self._attr_init(100.0),
                    )
                ],
            )
        )

        ep_pkg = self.register(FakeUObject("Package", None, "GD_Episode01"))
        self.register(
            FakeUObject(
                "MissionDefinition",
                ep_pkg,
                "M_Ep1_Champion",
                GameStage=5,
                Reward=FakeWrappedStruct("MissionRewardData", RewardItems=["OrigReward"], RewardItemPools=["OrigPool"]),
            )
        )

        self.asslib_libs = [
            types.SimpleNamespace(
                LibraryType=types.SimpleNamespace(Name=tname, SuperField=None),
                Sublibraries=["GD_Weap_Pistol.PackageAssetSublibrary_0"],
                SublibraryLinks=[],
            )
            for tname in (
                "WeaponTypeDefinition",
                "WeaponPartDefinition",
                "WeaponNamePartDefinition",
                "InventoryBalanceDefinition",
            )
        ]
        self.asslib_mgr = types.SimpleNamespace(
            RuntimeAssetLibraries=[types.SimpleNamespace(Id=0, Libraries=self.asslib_libs)]
        )

        # Player, Arms (`SkeletalMesh.Origin` & `RotOrigin`), & LightProjectileManager
        self.backpack: list = []
        inv_mgr = types.SimpleNamespace(
            Backpack=self.backpack,
            InventoryChain=None,
            AddInventoryToBackpack=lambda w: self.backpack.append(w),
        )
        self.arms_skel_mesh = FakeUObject(
            "SkeletalMesh",
            None,
            "Hands_Assassin",
            Origin=self._vec(1.0, -2.0, 3.0),
            RotOrigin=FakeWrappedStruct("Rotator", Pitch=0, Yaw=-16384, Roll=0),
        )
        self.force_update_calls: int = 0
        arms = types.SimpleNamespace(
            ObjectFlags=0,
            SkeletalMesh=self.arms_skel_mesh,
            Translation=self._vec(),
            Rotation=FakeWrappedStruct("Rotator", Pitch=0, Yaw=0, Roll=0),
            ForceUpdate=lambda _b: setattr(self, "force_update_calls", self.force_update_calls + 1),
            BeginDeferredUpdateTransform=lambda: None,
        )
        self.played_custom_anims: list[tuple[str, str, float]] = []
        self.stopped_slots: list[str] = []
        self.custom_anim_slot = types.SimpleNamespace(
            ObjectFlags=0,
            SkelComponent=arms,
            NodeName="CustomAnimSlot",
            PlayCustomAnim=lambda seq, rate, *a: (
                self.played_custom_anims.append(("CustomAnimSlot", str(seq), float(rate))) or 0.55
            ),
            StopCustomAnim=lambda _b: self.stopped_slots.append("CustomAnimSlot"),
        )
        self.weapon_action_slot = types.SimpleNamespace(
            ObjectFlags=0,
            SkelComponent=arms,
            NodeName="WeaponAction",
            PlayCustomAnim=lambda seq, rate, *a: (
                self.played_custom_anims.append(("WeaponAction", str(seq), float(rate))) or 0.55
            ),
            StopCustomAnim=lambda _b: self.stopped_slots.append("WeaponAction"),
        )
        self.weapon_recoil_slot = types.SimpleNamespace(
            ObjectFlags=0,
            SkelComponent=arms,
            NodeName="WeaponRecoil",
            PlayCustomAnim=lambda seq, rate, *a: 0.25,
            StopCustomAnim=lambda _b: self.stopped_slots.append("WeaponRecoil"),
        )
        self.caused_damages: list[dict] = []
        self.caused_radius_damages: list[dict] = []
        self.next_center_trace_override = None  # optional `(actor, hit_vec, bone_name)`
        self.wall_blocked_names: set[str] = set()
        self.wall_actor = types.SimpleNamespace(
            Name="StaticMeshActor_Wall",
            Class=types.SimpleNamespace(Name="StaticMeshActor"),
            ObjectFlags=0,
        )
        self.spawned_emitters: list[tuple[str, tuple[float, float, float]]] = []
        fx_exp_pkg = self.register(FakeUObject("Package", None, "FX_WEP_Explosions"))
        fx_exp_sub = self.register(FakeUObject("Package", fx_exp_pkg, "Particles"))
        self.register(FakeUObject("ParticleSystem", fx_exp_sub, "Part_Gyrojet_Explosion"))

        ake_exp_pkg = self.register(FakeUObject("Package", None, "Ake_Wep_Explosions"))
        self.register(FakeUObject("AkEvent", ake_exp_pkg, "Ak_Play_Wep_Explosion_Gyrojet"))

        canim_root_pkg = self.objects.get("Anim_CameraAnimations") or self.register(
            FakeUObject("Package", None, "Anim_CameraAnimations")
        )
        canim_exp_sub = self.register(FakeUObject("Package", canim_root_pkg, "Explosions"))
        self.register(FakeUObject("CameraAnim", canim_exp_sub, "Canim_Explosion_Medium"))

        self.emitter_pool = types.SimpleNamespace(
            ObjectFlags=0,
            SpawnEmitter=lambda ps, loc, *a: self.spawned_emitters.append(
                (ps._path_name(), (float(loc.X), float(loc.Y), float(loc.Z)))
            ),
        )
        self.world_info = types.SimpleNamespace(
            PawnList=None,
            MyEmitterPool=self.emitter_pool,
            GetStreamingPersistentMapName=lambda: "Sanctuary_P",
        )
        self.pawn = types.SimpleNamespace(
            Name="WillowPlayerPawn_0",
            ObjectFlags=0,
            Location=self._vec(),
            Rotation=FakeWrappedStruct("Rotator", Pitch=0, Yaw=0, Roll=0),
            BaseEyeHeight=64.0,
            EyeHeight=64.0,
            ExpLevel=30,
            InstigatedMeleeDamageAdded=0.0,
            InvManager=inv_mgr,
            Arms=arms,
            Weapon=None,
            WorldInfo=self.world_info,
            Controller=None,
            PlayAkEvent=lambda ak: self.played_ak.append(ak._path_name()),
            IsEnemy=lambda other: getattr(other, "hostile", True),
            Behavior_CauseDamage=lambda **kw: self.caused_damages.append(
                {
                    **kw,
                    "_wpn_base_chance_mod": getattr(kw.get("DamageCauser"), "BaseStatusEffectChanceModifier", None),
                    "_wpn_chance_mod": getattr(kw.get("DamageCauser"), "StatusEffectChanceModifier", None),
                    "_wpn_dot_dmg": getattr(kw.get("DamageCauser"), "StatusEffectDamage", None),
                }
            ),
            Behavior_CauseRadiusDamage=lambda **kw: self.caused_radius_damages.append(kw),
        )
        self.pc = types.SimpleNamespace(
            Name="WillowPlayerController_0",
            Pawn=self.pawn,
            WorldInfo=self.world_info,
            Rotation=FakeWrappedStruct("Rotator", Pitch=0, Yaw=0, Roll=0),
            PlayerReplicationInfo=types.SimpleNamespace(ExpLevel=30),
            SaveGameName="Save0001",
            GetCurrentPlaythrough=lambda: 1,
            ClientPlayCameraAnim=self._client_play_camera_anim,
            GetHUDMovie=lambda: None,
            ServerGrantMissionRewards=self._server_grant_mission_rewards,
        )
        self.pawn.Controller = self.pc

        self.proj_mgr = types.SimpleNamespace(
            PawnOwner=self.pawn,
            LastTimeSeconds=10.0,
            MyProjs=[],
            AddProj=self.native_add_proj,
            _path_name=lambda: "Sanctuary_P.TheWorld:PersistentLevel.WillowLightProjectileManager_0",
        )

    def native_add_proj(
        self,
        StartTrace,
        EndTrace,
        ProjectileInstigator,
        FiringModeDefinition,
        bStartParticleTracerAtWeapon=False,
        bOvercharged=False,
        bRicochetTowardsEnemy=False,
    ) -> int:
        idx = len(self.proj_mgr.MyProjs)
        proj = FakeWrappedStruct(
            "LightProjectile",
            Id=idx + 1,
            bEnabled=True,
            bPendingAdd=False,
            StartTime=10.0,
            Damage=50.0,
            FireDef=FiringModeDefinition,
            Location=copy.copy(StartTrace),
            EndTrace=copy.copy(EndTrace),
        )
        self.proj_mgr.MyProjs.append(proj)
        return idx

    def _assemble_weapon_from_balance(self, bal, stage: int):
        # Match real BL2 C++ `UInventoryBalanceDefinition`: empty `Manufacturers` with `BaseDefinition=None` spawns NOTHING!
        if not bal.Manufacturers:
            return None
        plc = bal.RuntimePartListCollection
        init_table = list(plc.ConsolidatedAttributeInitData)
        for slot_name in ("BodyPartData", "GripPartData", "BarrelPartData", "ElementalPartData", "Accessory1PartData", "MaterialPartData"):
            wp0 = getattr(plc, slot_name).WeightedParts[0]
            min_s = init_table[wp0.MinGameStageIndex].BaseValueConstant
            max_s = init_table[wp0.MaxGameStageIndex].BaseValueConstant
            if not (min_s <= stage <= max_s):
                return None

        dd = FakeWrappedStruct(
            "WeaponDefinitionData",
            WeaponTypeDefinition=bal.InventoryDefinition,
            BalanceDefinition=bal,
            ManufacturerDefinition=bal.Manufacturers[0].Manufacturer,
            ManufacturerGradeIndex=stage,
            BodyPartDefinition=plc.BodyPartData.WeightedParts[0].Part,
            GripPartDefinition=plc.GripPartData.WeightedParts[0].Part,
            BarrelPartDefinition=plc.BarrelPartData.WeightedParts[0].Part,
            SightPartDefinition=None,
            StockPartDefinition=None,
            ElementalPartDefinition=plc.ElementalPartData.WeightedParts[0].Part,
            Accessory1PartDefinition=plc.Accessory1PartData.WeightedParts[0].Part,
            Accessory2PartDefinition=None,
            MaterialPartDefinition=plc.MaterialPartData.WeightedParts[0].Part,
            PrefixPartDefinition=None,
            TitlePartDefinition=None,
            GameStage=stage,
            UniqueId=10000 + len(self.backpack) + len(self.ground_pickups),
        )
        return self._make_live_weapon(dd)

    def _run_drop_spawner(self, spawner, _kw) -> None:
        from melee_arsenal.runtime import resolver

        spawned_batch = []
        for pool in spawner.ItemPools:
            for item in pool.BalancedItems:
                bal = item.InvBalanceDefinition
                if bal is None:
                    continue
                # Simulate Sanctuary hub map stage = 7 before PlaceSpawnedItems stamps player's stage (30)!
                wpn = self._assemble_weapon_from_balance(bal, stage=7)
                if wpn is None:
                    continue
                pickup = types.SimpleNamespace(
                    Name=f"WillowPickup_{len(self.ground_pickups) + 1}",
                    Inventory=wpn,
                    Location=copy.copy(spawner.CustomLocation.Location),
                )
                self.ground_pickups.append(pickup)
                spawned_batch.append(pickup)

        resolver.on_place_spawned_items_post(
            spawner,
            types.SimpleNamespace(SpawnedLoot=spawned_batch),
            None,
            None,
        )

    def _server_grant_mission_rewards(self, mission, _flag: bool) -> None:
        from melee_arsenal.runtime import resolver

        self.reward_call_lengths.append(len(mission.Reward.RewardItems))
        for bal in mission.Reward.RewardItems:
            wpn = self._assemble_weapon_from_balance(bal, stage=mission.GameStage)
            if wpn is None:
                continue
            dd = wpn.DefinitionData
            resolver.on_weapon_initialize_from_def_pre(
                wpn, types.SimpleNamespace(NewDefinitionData=dd), None, None
            )
            resolver.on_weapon_initialize_internal_pre(wpn, None, None, None)
            resolver.on_weapon_initialize_internal_post(wpn, None, None, None)
            resolver.on_choose_random_name_parts_post(wpn, None, None, None)
            self.backpack.append(wpn)

    def _mock_calc_weapon_fire(self, StartTrace, EndTrace, ImpactList=None, Extent=None, bTestTrace=False):
        if self.next_center_trace_override is not None:
            act, hloc, bone = self.next_center_trace_override
            self.next_center_trace_override = None
            imp = types.SimpleNamespace(
                HitActor=act,
                HitLocation=hloc,
                HitInfo=types.SimpleNamespace(BoneName=bone),
            )
            return (imp, ImpactList)

        # Check if ray passes through a wall in front of a wall-blocked pawn
        ex, ey = float(EndTrace.X), float(EndTrace.Y)
        sx, sy = float(StartTrace.X), float(StartTrace.Y)
        rdx, rdy = ex - sx, ey - sy
        rlen2 = max(1e-4, rdx * rdx + rdy * rdy)
        cur = self.world_info.PawnList
        while cur is not None:
            if cur.Name in self.wall_blocked_names:
                px, py = float(cur.Location.X) - sx, float(cur.Location.Y) - sy
                t = max(0.0, min(1.0, (px * rdx + py * rdy) / rlen2))
                perp2 = (px - rdx * t) ** 2 + (py - rdy * t) ** 2
                if perp2 < 100.0 and t > 0.5:
                    imp = types.SimpleNamespace(
                        HitActor=self.wall_actor,
                        HitLocation=self._vec(float(cur.Location.X) * 0.45, float(cur.Location.Y) * 0.45, 64.0),
                        HitInfo=types.SimpleNamespace(BoneName="None"),
                    )
                    return (imp, ImpactList)
            cur = cur.NextPawn

        imp = types.SimpleNamespace(
            HitActor=None,
            HitLocation=EndTrace,
            HitInfo=types.SimpleNamespace(BoneName="None"),
        )
        return (imp, ImpactList)

    def _make_live_weapon(self, dd):
        wpn = types.SimpleNamespace(
            Name=f"WillowWeapon_{len(self.backpack) + 1}",
            ObjectFlags=0,
            Instigator=self.pawn,
            Owner=self.pawn,
            DefinitionData=dd,
            InstantHitDamage=0.0,
            FireInterval=0.5,
            ShotCost=1,
            ClipSize=10,
            ReloadCnt=0,
            Spread=1.0,
            StatusEffectDamage=0.0,
            StatusEffectChanceModifier=1.0,
            QuickSelectSlot=0,
            Inventory=None,
            pending_fire_cleared=False,
            InstantFireStartTrace=lambda: self._vec(0.0, 0.0, 64.0),
            CalcWeaponFire=self._mock_calc_weapon_fire,
            GetTraceImpact=lambda: self.find_object("ImpactDefinition", "GD_Impacts.Melee.Impact_MeleeGeneric"),
            PlayImpactEffects=lambda *a: None,
        )
        wpn.ClearPendingFire = lambda _m: setattr(wpn, "pending_fire_cleared", True)
        return wpn

    def set_enemies(self, specs: list[tuple[float, float, float, bool, str]]) -> None:
        head = None
        prev = None
        for idx, (x, y, z, hostile, name_tag) in enumerate(specs):
            is_dummy = "dummy" in (name_tag or "").lower()
            ai = types.SimpleNamespace(
                Name=name_tag or f"WillowAIPawn_{idx}",
                Class=types.SimpleNamespace(Name="WillowAIPawn"),
                ObjectFlags=0,
                AIClass=None,
                Location=self._vec(x, y, z),
                CylinderComponent=types.SimpleNamespace(CollisionRadius=45.0, CollisionHeight=75.0),
                hostile=hostile,
                IsAliveAndWell=lambda: True,
                NextPawn=None,
                _path_name=lambda n=(name_tag or f"WillowAIPawn_{idx}"), d=is_dummy: (
                    f"Sanctuary_P.TheWorld:PersistentLevel.{n}"
                ),
            )
            if head is None:
                head = ai
            if prev is not None:
                prev.NextPawn = ai
            prev = ai
        self.world_info.PawnList = head

    def make_sdk_modules(self) -> dict[str, types.ModuleType]:
        fake_unrealsdk = types.ModuleType("unrealsdk")
        fake_unrealsdk.__version__ = "3.2.0-mock"
        fake_unrealsdk.logging = types.SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
        )
        fake_unrealsdk.find_object = self.find_object
        fake_unrealsdk.construct_object = self.construct_object
        fake_unrealsdk.load_package = lambda _pkg: None
        fake_unrealsdk.make_struct = lambda name, **kw: FakeWrappedStruct(name, **kw)

        def find_class(name: str):
            if name == "WillowGlobals":
                return types.SimpleNamespace(
                    ClassDefaultObject=types.SimpleNamespace(
                        GetWillowGlobals=lambda: types.SimpleNamespace(AssLibMan=self.asslib_mgr)
                    )
                )
            if name == "Engine":
                return types.SimpleNamespace(
                    ClassDefaultObject=types.SimpleNamespace(
                        GetEngine=lambda: types.SimpleNamespace(
                            GamePlayers=[types.SimpleNamespace(Actor=self.pc)]
                        )
                    )
                )
            if name == "WeaponBalanceDefinition":
                return types.SimpleNamespace(
                    Name="WeaponBalanceDefinition",
                    SuperField=types.SimpleNamespace(Name="InventoryBalanceDefinition", SuperField=None),
                )
            return types.SimpleNamespace(Name=name, SuperField=None)

        fake_unrealsdk.find_class = find_class

        def fake_find_all(cls_name: str, **kw):
            if cls_name == "WillowPlayerController":
                return [self.pc]
            if cls_name == "WillowLightProjectileManager":
                return [self.proj_mgr]
            if cls_name == "AnimNodeSlot":
                return [self.weapon_recoil_slot, self.weapon_action_slot, self.custom_anim_slot]
            return [
                o
                for o in self.objects.values()
                if isinstance(o, FakeUObject) and object.__getattribute__(o, "_cls_name") == cls_name
            ]

        fake_unrealsdk.find_all = fake_find_all

        fake_hooks = types.ModuleType("unrealsdk.hooks")
        fake_hooks.Block = self.block_sentinel
        fake_hooks.Type = types.SimpleNamespace(PRE="PRE", POST="POST")

        class _NoopCtx:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        fake_hooks.prevent_hooking_direct_calls = _NoopCtx

        fake_mods_base = types.ModuleType("mods_base")
        fake_mods_base.MODS_DIR = self.temp_dir
        fake_mods_base.Game = types.SimpleNamespace(BL2="BL2")
        fake_mods_base.build_mod = lambda **kw: types.SimpleNamespace(**kw)
        fake_mods_base.get_pc = lambda: self.pc
        fake_mods_base.get_ordered_mod_list = lambda: []
        fake_mods_base.BoolOption = lambda name, val, **kw: types.SimpleNamespace(name=name, value=val, **kw)
        fake_mods_base.SliderOption = lambda name, val, *a, **kw: types.SimpleNamespace(name=name, value=val, **kw)
        fake_mods_base.ButtonOption = lambda name, **kw: types.SimpleNamespace(name=name, **kw)

        def fake_hook(*_a, **_kw):
            def deco(fn):
                fn.enable = lambda: None
                fn.disable = lambda: None
                return fn
            return deco

        def fake_command(*_a, **_kw):
            def deco(fn):
                fn.add_argument = lambda *a, **kw: None
                return fn
            return deco

        fake_mods_base.hook = fake_hook
        fake_mods_base.command = fake_command

        return {
            "unrealsdk": fake_unrealsdk,
            "unrealsdk.hooks": fake_hooks,
            "mods_base": fake_mods_base,
        }

    def populate_custom_inkblade_packages(self) -> None:
        """Register mock `MeleeArsenalMeshes`, `MeleeArsenalTextures`, `GestaltDef_Pistol`, and `Master_ClassMod`."""
        wp_pkg = self.register(FakeUObject("Package", None, "Weap_Pistol"))
        tpl_part_entry = FakeWrappedStruct(
            "GestaltPartEntry",
            SkeletalMeshFragmentName="Pistol_Barrel_Bandit",
            FirstIndex=0,
            NumPrimitives=10,
        )
        tpl_bound = FakeWrappedStruct(
            "GestaltPartBoundsEntry",
            SkeletalMeshFragmentName="Pistol_Barrel_Bandit",
            ReferencePoseBounds=FakeWrappedStruct(
                "BoxSphereBounds",
                Origin=self._vec(0.0, 0.0, 0.0),
                BoxExtent=self._vec(1.0, 1.0, 1.0),
                SphereRadius=1.732,
            ),
        )
        tpl_mappings = [
            FakeWrappedStruct(
                "GestaltSocketMapping",
                SkeletalMeshFragmentName="Pistol_Barrel_Bandit",
                OriginalSocketName="Muzzle",
                MangledSocketName="Pistol_Barrel_Bandit_Muzzle",
            ),
            FakeWrappedStruct(
                "GestaltSocketMapping",
                SkeletalMeshFragmentName="Pistol_Body_Dahl",
                OriginalSocketName="EyeSocket2",
                MangledSocketName="Pistol_Body_Dahl_EyeSocket2",
            ),
            FakeWrappedStruct(
                "GestaltSocketMapping",
                SkeletalMeshFragmentName="Acc_Barrel_Elemental2",
                OriginalSocketName="elemental",
                MangledSocketName="Acc_Barrel_Elemental2_Elemental",
            ),
        ]
        self.register(
            FakeUObject(
                "GestaltSkeletalMeshDefinition",
                wp_pkg,
                "GestaltDef_Pistol",
                GestaltSkeletalMesh=None,
                GestaltInfos=[FakeWrappedStruct("GestaltSkeletalMeshModeInfo", Parts=[tpl_part_entry])],
                GestaltPartBounds=[tpl_bound],
                GestaltSocketMappings=tpl_mappings,
            )
        )

        icm_pkg = self.register(FakeUObject("Package", None, "Item_ClassMods"))
        icm_mat_pkg = self.register(FakeUObject("Package", icm_pkg, "Mat"))
        self.register(FakeUObject("Material", icm_mat_pkg, "Master_ClassMod"))

        meshes_pkg = self.register(FakeUObject("Package", None, "MeleeArsenalMeshes"))
        elem_sock = self.register(
            FakeUObject(
                "SkeletalMeshSocket",
                meshes_pkg,
                "SkeletalMeshSocket_0",
                SocketName="Acc_Barrel_Elemental2_Elemental",
                BoneName="Root",
                RelativeLocation=self._vec(-3.1, -7.42, 5.65),
            )
        )
        self.register(
            FakeUObject(
                "SkeletalMesh",
                meshes_pkg,
                "InkbladeGestaltMesh",
                Sockets=[elem_sock],
            )
        )

        tex_pkg = self.register(FakeUObject("Package", None, "MeleeArsenalTextures"))
        for elem_key in ("None", "Fire", "Shock", "Corrosive", "Slag", "Explosive"):
            self.register(FakeUObject("Texture2D", tex_pkg, f"T_Inkblade_Atlas_{elem_key}"))
        self.register(FakeUObject("Texture2D", tex_pkg, "T_Inkblade_Normal_Flat"))


class TestMeleeArsenalRuntime(unittest.TestCase):
    def test_all_runtime_files_compile_cleanly(self) -> None:
        runtime_dir = SDK_MODS_DIR / "melee_arsenal" / "runtime"
        py_files = sorted(runtime_dir.glob("*.py"))
        self.assertGreaterEqual(len(py_files), 8)
        for f in py_files:
            py_compile.compile(str(f), doraise=True)

    def test_strict_wrapped_struct_and_full_lifecycle(self) -> None:
        if str(SDK_MODS_DIR) not in sys.path:
            sys.path.insert(0, str(SDK_MODS_DIR))

        with tempfile.TemporaryDirectory() as tmp:
            world = MockBL2World(Path(tmp))
            sdk_mods = world.make_sdk_modules()

            for mod_name in list(sys.modules):
                if mod_name == "melee_arsenal" or mod_name.startswith("melee_arsenal.runtime"):
                    sys.modules.pop(mod_name, None)

            with mock.patch.dict(sys.modules, sdk_mods):
                import melee_arsenal  # type: ignore[import-not-found]
                from melee_arsenal.core.catalog import ACCESSORIES
                from melee_arsenal.core.schemas import PlayerMeleeContext
                from melee_arsenal.core.stats import (
                    compute_attack_damage,
                    compute_damage_reduction_pct,
                )
                from melee_arsenal.runtime import (
                    combat,
                    commands,
                    definitions,
                    loot_pools,
                    persistence_bridge,
                    resolver,
                    state,
                    ui_item_card,
                )

                self.assertIsNotNone(melee_arsenal.mod)

                # 1. Build Definitions under strict WrappedStruct & schema checks
                self.assertTrue(definitions.ensure_definitions_built())
                self.assertEqual(len(state.STATE.maker_to_wt), 5)
                self.assertEqual(len(state.STATE.element_to_firing_mode), 6)
                self.assertEqual(len(state.STATE.blade_parts), 5)
                self.assertEqual(len(state.STATE.accessory_parts), len(ACCESSORIES))
                self.assertEqual(len(state.STATE.balances), 20)

                # Verify EVERY balance (Common, Uncommon, Rare, VeryRare) inherited Manufacturers cleanly!
                for bal_key, bal_obj in state.STATE.balances.items():
                    self.assertEqual(len(bal_obj.Manufacturers), 1, f"{bal_key} has empty Manufacturers!")

                # Verify Torgue rocket CustomFiringModeDefinition was cleared on Blade_Torgue_BlastCleaver
                self.assertIsNone(
                    state.STATE.blade_parts["Blade_Torgue_BlastCleaver"].CustomFiringModeDefinition
                )

                # Verify v0.2.5 FiringMode & WeaponType recoil stripping + zero TraceExtent
                for efm in state.STATE.element_to_firing_mode.values():
                    self.assertEqual(efm.TraceExtent.X, 0.0)
                    self.assertEqual(efm.TraceExtent.Y, 0.0)
                    self.assertEqual(efm.TraceExtent.Z, 0.0)
                    self.assertEqual(list(efm.FiringPatternLines), [])
                    self.assertFalse(efm.bPenetratePawn)
                for wt_obj in state.STATE.maker_to_wt.values():
                    self.assertEqual(list(wt_obj.WeaponFireAnimations), [])
                    self.assertEqual(wt_obj.WeaponKickUp, 0.0)
                    self.assertEqual(wt_obj.Range, 4096.0)

                # 2. Loot Pool Injection
                self.assertTrue(loot_pools.sync_loot_pools())

                # 3. Drop All 5 Archetypes in front of player — ZERO XP (`reward_call_lengths == []`)!
                count_all = commands.spawn_all_five_archetypes("purple")
                self.assertEqual(count_all, 5)
                self.assertEqual(len(world.ground_pickups), 5)
                self.assertEqual(world.reward_call_lengths, [])

                # Verify newly dropped melee pickups start with ReloadCnt == 100 and exact player GameStage == 30
                self.assertEqual(world.ground_pickups[0].Inventory.ReloadCnt, 100)
                self.assertEqual(world.ground_pickups[0].Inventory.DefinitionData.GameStage, 30)

                # Verify exact element filter (`melee_spawn hyperion purple fire`)
                world.ground_pickups.clear()
                commands.spawn_single_command("hyperion", "purple", "fire")
                hyp_fire = world.ground_pickups[-1].Inventory
                self.assertTrue(
                    hyp_fire.DefinitionData.ElementalPartDefinition._path_name().endswith("Part_Elem_Fire")
                )
                self.assertEqual(hyp_fire.DefinitionData.GameStage, 30)

                # Drop Jakobs Sabre, Maliwan Fire Phaseblade, and Torgue Blast-Cleaver & pick them up
                world.ground_pickups.clear()
                commands.spawn_single_command("jakobs", "purple", "")
                sabre = world.ground_pickups[-1].Inventory
                commands.spawn_single_command("maliwan", "purple", "fire")
                phaseblade = world.ground_pickups[-1].Inventory
                commands.spawn_single_command("torgue", "purple", "")
                cleaver = world.ground_pickups[-1].Inventory
                world.backpack.extend([sabre, phaseblade, cleaver])

                # 4. Combat v0.2.6: `InstantFire` PRE + `Bladed_Melee` on `WeaponAction` + `CalcWeaponFire` + `Behavior_CauseDamage`
                world.set_enemies(
                    [
                        (210.0, 0.0, 64.0, False, "Pawn_TargetDummy_0"),    # Sanctuary Target Dummy!
                        (220.0, -65.0, 64.0, True, "WillowAIPawn_Bandit_1"),
                        (220.0, 70.0, 64.0, True, "WillowAIPawn_Bandit_2"),
                        (205.0, 35.0, 64.0, True, "Wall_Blocked_Bandit"),   # behind wall -> MUST NOT be hit!
                        (200.0, 20.0, 64.0, False, "Sanctuary_Citizen"),    # friendly NPC -> ignored!
                        (900.0, 0.0, 64.0, True, "Far_Bandit"),             # out of melee reach!
                    ]
                )
                world.wall_blocked_names = {"Wall_Blocked_Bandit"}
                world.melee_mult = 2.0
                world.roid_buff = 500.0

                # 4A. Single-Target Jakobs Sabre Headshot Crit (`BoneName="Head"` -> 4.0x `+100%` crit!)
                world.pawn.Weapon = sabre
                world.caused_damages.clear()
                world.played_custom_anims.clear()
                world.stopped_slots.clear()
                combat._LAST_SWING_AT = 0.0

                _, sabre_card = resolver.resolve_melee_weapon(sabre)
                expected_sabre_crit = compute_attack_damage(
                    sabre_card,
                    PlayerMeleeContext(melee_damage_scale=2.0, roid_flat_damage=500.0),
                    is_crit=True,
                ).final_hit_damage

                # Aim directly at Pawn_TargetDummy_0's Head!
                dummy_pawn = world.world_info.PawnList
                world.next_center_trace_override = (dummy_pawn, world._vec(210.0, 0.0, 95.0), "Head")

                combat.on_weapon_consume_ammo_post(sabre, None, None, None)
                self.assertTrue(sabre.pending_fire_cleared)

                res_if = combat.on_weapon_instant_fire_pre(sabre, None, None, None)
                self.assertEqual(res_if, world.block_sentinel)
                # Played real `Bladed_Melee` on `CustomAnimSlot` WITHOUT touching `WeaponAction` (`SpecialMove_FirstPerson`)!
                self.assertEqual(len(world.played_custom_anims), 1)
                self.assertEqual(world.played_custom_anims[0][0], "CustomAnimSlot")
                self.assertEqual(world.played_custom_anims[0][1], "Bladed_Melee")
                self.assertNotIn("WeaponAction", world.stopped_slots)
                self.assertIn("WeaponRecoil", world.stopped_slots)
                # Dealt exact headshot crit stacked melee+roid damage to ONLY the single target!
                self.assertEqual(len(world.caused_damages), 1)
                self.assertEqual(world.caused_damages[0]["Target"], dummy_pawn)
                self.assertAlmostEqual(world.caused_damages[0]["DamageAmount"], float(expected_sabre_crit))
                # ...and the crit was tagged so BL2's floating number can be re-issued as a CRITICAL
                self.assertIn(id(dummy_pawn), combat._PENDING_CRIT_FEEDBACK)
                combat.clear_all_pending_crit_feedback()

                # Mid-swap guard: if `pawn.PendingWeapon` is active, swing must NOT touch `Arms` anim slots!
                world.pawn.PendingWeapon = phaseblade
                try:
                    combat._LAST_SWING_AT -= 0.35
                    combat.on_weapon_consume_ammo_post(sabre, None, None, None)
                    combat.on_weapon_instant_fire_pre(sabre, None, None, None)
                    self.assertEqual(len(world.played_custom_anims), 1)
                finally:
                    world.pawn.PendingWeapon = None

                # 4A-2. Consecutive Swing 2 ALSO plays `Bladed_Melee` cleanly without touching `Origin` / `RotOrigin`:
                combat._LAST_SWING_AT -= 0.35
                combat.on_weapon_consume_ammo_post(sabre, None, None, None)
                combat.on_weapon_instant_fire_pre(sabre, None, None, None)
                self.assertFalse(combat.STATE.swing_motion.active)
                self.assertEqual(len(world.played_custom_anims), 2)
                self.assertEqual(world.played_custom_anims[1][1], "Bladed_Melee")
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.X, 1.0)
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.Y, -2.0)
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.Z, 3.0)
                self.assertEqual(world.arms_skel_mesh.RotOrigin.Yaw, -16384)

                # Verify gun bullet, fire effects, & recoil hooks all return Block for Melee Arsenal weapons
                self.assertEqual(combat.on_weapon_firing_mode_fire_pre(sabre, None, None, None), world.block_sentinel)
                self.assertEqual(combat.on_weapon_play_fire_effects_pre(sabre, None, None, None), world.block_sentinel)
                self.assertEqual(
                    combat.on_pawn_weapon_fired_pre(
                        world.pawn,
                        types.SimpleNamespace(InWeapon=sabre, bViaReplication=False, HitLocation=world._vec()),
                        None,
                        None,
                    ),
                    world.block_sentinel,
                )

                # Verify PlayAnimation blocks `Fire_*` recoil sequences without calling `func` or mutating `DefinitionData`,
                # and passes `Equip`/`Idle` straight through (`None`) without re-entry!
                res_fire_seq = resolver.on_play_weapon_animation_pre(
                    sabre,
                    types.SimpleNamespace(
                        AnimType=0, Sequence="Fire_Jakobs", fDesiredDuration=0.26, bLoop=False, BlendInTime=0.0, BlendOutTime=0.0
                    ),
                    None,
                    lambda **kw: (_ for _ in ()).throw(AssertionError("func must never be called")),
                )
                self.assertEqual(res_fire_seq, (world.block_sentinel, 0.0))
                res_equip_seq = resolver.on_play_weapon_animation_pre(
                    sabre,
                    types.SimpleNamespace(
                        AnimType=0, Sequence="Equip_Jakobs", fDesiredDuration=0.33, bLoop=False, BlendInTime=0.0, BlendOutTime=0.0
                    ),
                    None,
                    lambda **kw: (_ for _ in ()).throw(AssertionError("func must never be called")),
                )
                self.assertIsNone(res_equip_seq)
                self.assertFalse(hasattr(resolver, "on_play_first_person_body_anim_pre"))

                # 4B. Frontal-Cleave Maliwan Fire Phaseblade:
                # Hits TargetDummy + Bandit_1 + Bandit_2 (= 3 targets), while Wall_Blocked_Bandit,
                # Sanctuary_Citizen, and Far_Bandit are all excluded!
                world.pawn.Weapon = phaseblade
                world.caused_damages.clear()
                combat._LAST_SWING_AT = 0.0
                combat.on_weapon_consume_ammo_post(phaseblade, None, None, None)
                combat.on_weapon_instant_fire_pre(phaseblade, None, None, None)
                hit_names = {d["Target"].Name for d in world.caused_damages}
                self.assertEqual(
                    hit_names,
                    {"Pawn_TargetDummy_0", "WillowAIPawn_Bandit_1", "WillowAIPawn_Bandit_2"},
                )
                for d in world.caused_damages:
                    dtd_path = d["DamageTypeDefinition"]._path_name()
                    self.assertIn(
                        dtd_path,
                        {
                            "GD_Incendiary.DamageType.DmgType_Incendiary_Impact",
                            "GD_Incendiary.DamageType.DmgType_Incendiary_Impact_NoDoT",
                        },
                    )
                    self.assertGreater(d["_wpn_dot_dmg"], 0.0)
                    if dtd_path.endswith("_NoDoT"):
                        self.assertEqual(d["_wpn_base_chance_mod"], 0.0)
                        self.assertEqual(d["_wpn_chance_mod"], 0.0)
                    else:
                        self.assertEqual(d["_wpn_base_chance_mod"], 50.0)
                        self.assertEqual(d["_wpn_chance_mod"], 50.0)
                # Restored clean modifier state after swing loop
                self.assertEqual(phaseblade.BaseStatusEffectChanceModifier, 1.0)

                # 4C. Frontal-Cleave Torgue Blast-Cleaver (`SharedFireAmmunition` backup + Explosive Splash + VFX/SFX!)
                world.pawn.Weapon = cleaver
                world.caused_damages.clear()
                world.caused_radius_damages.clear()
                world.spawned_emitters.clear()
                combat._LAST_SWING_AT = 0.0
                combat.on_weapon_consume_ammo_post(cleaver, None, None, None)
                with mock.patch.object(combat.STATE, "debug_attack_log", True), mock.patch.object(
                    combat, "log_info"
                ) as combat_log:
                    combat.on_weapon_shared_fire_post(cleaver, None, None, None)
                checkpoints = [call.args[0] for call in combat_log.call_args_list]
                for stage in ("swing start", "before direct damage", "after direct damage",
                              "before radius damage", "after radius damage",
                              "before explosion effects", "after explosion effects"):
                    self.assertTrue(any(stage in line for line in checkpoints), stage)
                self.assertEqual(len(world.caused_damages), 3)
                self.assertEqual(len(world.caused_radius_damages), 1)
                self.assertAlmostEqual(world.caused_radius_damages[0]["Radius"], 180.0)
                self.assertEqual(
                    world.caused_radius_damages[0]["DamageTypeDefinition"]._path_name(),
                    "GD_Explosive.DamageType.DmgType_Explosive_Impact_ForceFlinch",
                )
                self.assertEqual(len(world.spawned_emitters), 1)
                self.assertEqual(
                    world.spawned_emitters[0][0],
                    "FX_WEP_Explosions.Particles.Part_Gyrojet_Explosion",
                )
                self.assertIn("Ake_Wep_Explosions.Ak_Play_Wep_Explosion_Gyrojet", world.played_ak)
                self.assertIn("Anim_CameraAnimations.Explosions.Canim_Explosion_Medium", world.played_canims)

                # 4D. Defensive Blade Guard: the held Melee Weapon's rolled level-scaled Damage
                # Reduction blocks the incoming `WillowPlayerPawn:TakeDamage` and re-calls it reduced!
                world.pawn.Weapon = sabre
                sabre_inst_dr, sabre_dr_card = resolver.resolve_melee_weapon(sabre, sync_weapon_props=False)
                dr_pct = sabre_dr_card.damage_reduction_pct
                self.assertTrue(
                    30 <= dr_pct <= 70, f"Damage Reduction {dr_pct}% outside the 30-70% band!"
                )
                self.assertEqual(dr_pct, compute_damage_reduction_pct(sabre_inst_dr))
                # A Lv30 blade must sit well under the Lv80+OP10-only 70% cap
                self.assertLess(dr_pct, 70)
                self.assertIn(
                    melee_arsenal.runtime.hook_player_take_damage, melee_arsenal.runtime.ALL_HOOKS
                )

                td_re_calls = []
                td_args = FakeWrappedStruct("WillowPlayerPawn.TakeDamage", Damage=1000.0)
                expected_reduced = 1000.0 * (1.0 - dr_pct / 100.0)

                res_dr = combat.on_player_take_damage_pre(
                    world.pawn,
                    td_args,
                    None,
                    lambda *a, **k: td_re_calls.append((a, k)),
                )
                self.assertEqual(res_dr, world.block_sentinel)
                self.assertEqual(len(td_re_calls), 1)
                # `func(args)` re-call received the SAME args struct, now carrying reduced damage
                self.assertIs(td_re_calls[0][0][0], td_args)
                self.assertAlmostEqual(td_args.Damage, expected_reduced, places=3)

                # A vanilla gun in hand (or no weapon) must never reduce incoming damage
                vanilla_wpn = types.SimpleNamespace(
                    Name="WillowWeapon_VanillaPistol",
                    ObjectFlags=0,
                    DefinitionData=FakeWrappedStruct(
                        "WeaponDefinitionData",
                        WeaponTypeDefinition=None,
                        BarrelPartDefinition=None,
                        BalanceDefinition=None,
                    ),
                )
                for held in (vanilla_wpn, None):
                    world.pawn.Weapon = held
                    td_re_calls.clear()
                    self.assertIsNone(
                        combat.on_player_take_damage_pre(
                            world.pawn, td_args, None, lambda *a: td_re_calls.append(a)
                        )
                    )
                    self.assertEqual(td_re_calls, [])

                # Co-op safety: only the LOCAL player's own pawn is ever modified
                co_op_pawn = types.SimpleNamespace(
                    Name="WillowPlayerPawn_CoOp", ObjectFlags=0, Weapon=sabre
                )
                td_re_calls.clear()
                self.assertIsNone(
                    combat.on_player_take_damage_pre(
                        co_op_pawn, td_args, None, lambda *a: td_re_calls.append(a)
                    )
                )
                self.assertEqual(td_re_calls, [])

                # The Mod Menu toggle turns the whole defensive perk off
                world.pawn.Weapon = sabre
                state.STATE.damage_reduction_enabled = False
                td_re_calls.clear()
                try:
                    self.assertIsNone(
                        combat.on_player_take_damage_pre(
                            world.pawn, td_args, None, lambda *a: td_re_calls.append(a)
                        )
                    )
                    self.assertEqual(td_re_calls, [])
                finally:
                    state.STATE.damage_reduction_enabled = True

                # 4E. Weakspot crits must surface the vanilla floating "CRITICAL" popup.
                # `Behavior_CauseDamage` has no HitInfo, so the engine's recent-damage tracker never
                # sets the crit bit itself; we tag the victim and re-issue the display call with it.
                combat.clear_all_pending_crit_feedback()
                crit_display_calls: list[object] = []
                crit_event = types.SimpleNamespace(
                    PC=world.pc,
                    DamageEventData=types.SimpleNamespace(
                        DamagedActor=dummy_pawn, DamageEventFlags=0, TotalDamageForDamageType=123.0
                    ),
                )
                # Unknown victim: never forced, vanilla display runs untouched
                self.assertIsNone(
                    combat.on_display_recent_damage_pre(
                        object(), crit_event, None, lambda *a: crit_display_calls.append(a)
                    )
                )
                self.assertEqual(crit_display_calls, [])

                # Co-op safety: a remote player's floating numbers are never modified
                combat._mark_pending_crit_feedback(dummy_pawn)
                remote_event = types.SimpleNamespace(
                    PC=types.SimpleNamespace(Name="WillowPlayerController_CoOp"),
                    DamageEventData=types.SimpleNamespace(
                        DamagedActor=dummy_pawn, DamageEventFlags=0
                    ),
                )
                self.assertIsNone(
                    combat.on_display_recent_damage_pre(
                        object(), remote_event, None, lambda *a: crit_display_calls.append(a)
                    )
                )
                self.assertEqual(crit_display_calls, [])
                self.assertEqual(remote_event.DamageEventData.DamageEventFlags, 0)

                # Local crit: display re-called exactly once with the crit bit (1) forced on
                self.assertIs(
                    combat.on_display_recent_damage_pre(
                        object(), crit_event, None, lambda *a: crit_display_calls.append(a)
                    ),
                    world.block_sentinel,
                )
                self.assertEqual(len(crit_display_calls), 1)
                self.assertEqual(crit_event.DamageEventData.DamageEventFlags, 1)
                # Tag consumed: a follow-up DoT/normal popup on the same actor is never force-crit
                crit_event.DamageEventData.DamageEventFlags = 0
                self.assertIsNone(
                    combat.on_display_recent_damage_pre(
                        object(), crit_event, None, lambda *a: crit_display_calls.append(a)
                    )
                )
                self.assertEqual(len(crit_display_calls), 1)

                # Expired tags are ignored (no stale crits when a swing is interrupted/lagging)
                combat._mark_pending_crit_feedback(dummy_pawn)
                combat._PENDING_CRIT_FEEDBACK[id(dummy_pawn)] = 0.0
                self.assertIsNone(
                    combat.on_display_recent_damage_pre(
                        object(), crit_event, None, lambda *a: crit_display_calls.append(a)
                    )
                )
                self.assertEqual(len(crit_display_calls), 1)
                combat.clear_all_pending_crit_feedback()

                # 4F. RMB / ADS zoom is disabled on Melee Weapons (reserved for secondary attacks)
                zoom_func_calls: list[object] = []
                world.pawn.Weapon = sabre
                self.assertIs(
                    combat.on_melee_weapon_start_zoom_pre(
                        sabre, None, None, lambda *a: zoom_func_calls.append(a)
                    ),
                    world.block_sentinel,
                )
                self.assertEqual(zoom_func_calls, [])
                # ...but a vanilla gun in hand keeps its native scope/ADS behaviour!
                self.assertIsNone(
                    combat.on_melee_weapon_start_zoom_pre(
                        vanilla_wpn, None, None, lambda *a: zoom_func_calls.append(a)
                    )
                )
                self.assertFalse(combat.MELEE_SECONDARY_ATTACK_ENABLED)
                self.assertFalse(
                    combat._try_melee_secondary_attack(sabre, sabre_inst_dr, sabre_dr_card)
                )
                self.assertIn(melee_arsenal.runtime.hook_melee_start_zoom, melee_arsenal.runtime.ALL_HOOKS)
                self.assertIn(
                    melee_arsenal.runtime.hook_display_recent_damage, melee_arsenal.runtime.ALL_HOOKS
                )

                # 4G. Camera shake must be soft & smoothly blended for fast blades (no more jerks)
                world.camera_anim_calls.clear()
                world.pawn.Weapon = sabre
                combat._LAST_SWING_AT = 0.0
                combat.on_weapon_consume_ammo_post(sabre, None, None, None)
                combat.on_weapon_instant_fire_pre(sabre, None, None, None)
                self.assertTrue(world.camera_anim_calls, "expected a CameraAnim on swing")
                for path, cam_args in world.camera_anim_calls:
                    self.assertIn("Melee", path)
                    scale, rate, blend_in, blend_out = cam_args[0], cam_args[1], cam_args[2], cam_args[3]
                    # A 2.35/s Sabre used to play 0.45 @ 1.35x with 40/120ms blends; it must now be
                    # both quieter and eased in/out so repeated quick swings never jerk the camera.
                    self.assertLessEqual(scale, 0.25, f"camera shake scale {scale} too strong")
                    self.assertLessEqual(rate, 1.0, f"camera shake rate {rate} too fast")
                    self.assertGreaterEqual(blend_in, 0.08)
                    self.assertGreaterEqual(blend_out, 0.20)
                # ...and the Mod Menu slider can dial it to zero (fully disable) or up for impact
                world.camera_anim_calls.clear()
                world.played_canims.clear()
                state.STATE.camera_shake_scale = 0.0
                combat._LAST_SWING_AT = 0.0
                combat.on_weapon_consume_ammo_post(sabre, None, None, None)
                combat.on_weapon_instant_fire_pre(sabre, None, None, None)
                self.assertEqual(world.camera_anim_calls, [], "0% camera shake must stay silent")
                try:
                    state.STATE.camera_shake_scale = 1.5
                    world.camera_anim_calls.clear()
                    combat._LAST_SWING_AT = 0.0
                    combat.on_weapon_consume_ammo_post(sabre, None, None, None)
                    combat.on_weapon_instant_fire_pre(sabre, None, None, None)
                    boosted = [c[1][0] for c in world.camera_anim_calls]
                    self.assertTrue(boosted)
                    self.assertTrue(all(0.0 < b <= 0.55 for b in boosted), boosted)
                finally:
                    state.STATE.camera_shake_scale = 1.0
                world.camera_anim_calls.clear()
                world.played_canims.clear()

                # 5. Item Card GFx Hooks — MUST NOT call AdjustHeight()!
                top_rows_written: dict[int, tuple] = {}
                gfx_card = types.SimpleNamespace(
                    SetTopStat=lambda idx, lbl, val, arr, aux, ic: top_rows_written.__setitem__(
                        idx, (lbl, val, arr, ic)
                    ),
                    SetFunStats=lambda txt: None,
                    SetTitle=lambda *a: None,
                    AdjustHeight=lambda: (_ for _ in ()).throw(
                        AssertionError("AdjustHeight() must NEVER be called — it shrinks inventory cards!")
                    ),
                )
                ui_item_card.on_set_item_card_ex_pre(
                    gfx_card,
                    types.SimpleNamespace(InventoryItem=phaseblade, CompareAgainstInventoryItem=sabre),
                    None,
                    None,
                )
                ui_item_card.on_set_item_card_ex_post(gfx_card, None, None, None)
                self.assertEqual(top_rows_written[0][0], "Damage")
                self.assertEqual(top_rows_written[1][0], "Range")
                self.assertEqual(top_rows_written[2][0], "Swing Speed")
                self.assertEqual(top_rows_written[3][0], "Burn Dmg/s")
                self.assertEqual(top_rows_written[4][0], "Effect Chance")

                # 5B. Title colour must never render White for a higher-rarity Melee Weapon: the engine
                # can still hand us the stale Common level for items we re-assembled after init.
                title_calls: list[tuple] = []
                title_card = types.SimpleNamespace(
                    SetTitle=lambda *a: title_calls.append(a),
                    SetTopStat=lambda *a: None,
                    SetFunStats=lambda *a: None,
                )
                ui_item_card.on_set_item_card_ex_pre(
                    title_card,
                    types.SimpleNamespace(InventoryItem=phaseblade, CompareAgainstInventoryItem=None),
                    None,
                    None,
                )
                ui_item_card.on_set_title_pre(
                    title_card,
                    types.SimpleNamespace(
                        Manufacturer="Maliwan",
                        Rarity=1,
                        TypeIcon="pistol",
                        ElementalIcon="",
                        bIsReadied=False,
                    ),
                    None,
                    None,
                )
                self.assertEqual(len(title_calls), 1)
                self.assertEqual(title_calls[0][2], 4)  # promoted 1 (White) -> 4 (Purple)
                ui_item_card.on_set_item_card_ex_post(title_card, None, None, None)

                # 6. Sidecar Save/Restore
                saved = persistence_bridge.snapshot_player_melee_inventory(pc=world.pc)
                self.assertEqual(saved, 3)
                world.backpack.clear()
                restored = persistence_bridge.restore_player_melee_inventory(pc=world.pc)
                self.assertEqual(restored, 3)
                for _ in range(30):
                    resolver.tick_spawner_restore()
                self.assertEqual(len(world.backpack), 3)

    def test_custom_inkblade_gestalt_variants_and_element_atlases(self) -> None:
        if str(SDK_MODS_DIR) not in sys.path:
            sys.path.insert(0, str(SDK_MODS_DIR))

        with tempfile.TemporaryDirectory() as tmp:
            world = MockBL2World(Path(tmp))
            world.populate_custom_inkblade_packages()
            sdk_mods = world.make_sdk_modules()

            for mod_name in list(sys.modules):
                if mod_name == "melee_arsenal" or mod_name.startswith("melee_arsenal.runtime"):
                    sys.modules.pop(mod_name, None)

            with mock.patch.dict(sys.modules, sdk_mods):
                import melee_arsenal  # type: ignore[import-not-found]
                from melee_arsenal.core.enums import Rarity
                from melee_arsenal.core.schemas import MeleeWeaponInstance, MeleeWeaponParts
                from melee_arsenal.runtime import commands, custom_assets, definitions, resolver, state

                self.assertEqual(melee_arsenal.__version__, "0.3.5")
                self.assertTrue(definitions.ensure_definitions_built())
                self.assertTrue(state.STATE.custom_assets_active)
                self.assertEqual(state.STATE.custom_kit_revision, "bold_ink_visual_revision_of_r4")
                self.assertEqual(len(state.STATE.element_material_parts_by_rarity), 24)

                # Every Inkblade Material part carries its exact BL2 rarity level (1 White … 4 Purple):
                # the Material part alone decides card colour, loot-beam colour (`InventoryRarityLevel`)
                # and sell value, so a stale/Common clone would render every Melee Weapon white.
                for (elem_id, rarity), mat_obj in state.STATE.element_material_parts_by_rarity.items():
                    self.assertAlmostEqual(
                        custom_assets.get_part_rarity(mat_obj),
                        float(int(rarity)),
                        msg=f"{elem_id} {rarity.name} material has wrong Rarity!",
                    )
                    self.assertIsNone(mat_obj.Rarity.InitializationDefinition)
                    self.assertIsNone(mat_obj.Rarity.BaseValueAttribute)
                    self.assertAlmostEqual(mat_obj.Rarity.BaseValueScaleConstant, 1.0)

                # Verify equal-size UPK restage detection compares 129B UPK summary/GUID header
                p_old = Path(tmp) / "old.upk"
                p_new = Path(tmp) / "new.upk"
                p_old.write_bytes(b"A" * 200)
                p_new.write_bytes(b"A" * 45 + b"B" * 16 + b"A" * 139)
                self.assertEqual(p_old.stat().st_size, p_new.stat().st_size)
                self.assertTrue(custom_assets._upk_needs_restage(p_new, p_old))
                self.assertFalse(custom_assets._upk_needs_restage(p_new, p_new))

                # v0.3.1 Regression 1: Verify `Acc_None` / `Elem_None` vs visible donor separation!
                # Even though `PLC_Common` ONLY contains `Pistol_Accessory_None` (`bHiddenNoMesh=True`),
                # ZERO physical accessories (or `__s1..__s4` twins) and ZERO active elements (`Elem_Explosive`!)
                # may inherit `bHiddenNoMesh=True`!
                self.assertTrue(state.STATE.accessory_parts["Acc_None"].bHiddenNoMesh)
                for acc_id, apart in state.STATE.accessory_parts.items():
                    if acc_id != "Acc_None":
                        self.assertFalse(apart.bHiddenNoMesh, f"{acc_id} cloned from hidden None donor!")
                for (_aid, _pkid), vpart in state.STATE.accessory_variant_parts.items():
                    self.assertFalse(vpart.bHiddenNoMesh)

                self.assertTrue(state.STATE.element_parts["Elem_None"].bHiddenNoMesh)
                for elem_id, epart in state.STATE.element_parts.items():
                    if elem_id != "Elem_None":
                        self.assertFalse(epart.bHiddenNoMesh, f"{elem_id} cloned from hidden None donor!")

                # Verify VERY_RARE (purple) PLCs exclude `Acc_None` (100% guaranteed physical accessory!)
                none_acc_obj = state.STATE.accessory_parts["Acc_None"]
                for (_mkr, rar), bal_obj in state.STATE.balances.items():
                    plc_obj = bal_obj.RuntimePartListCollection
                    acc_parts_in_plc = [r.Part for r in plc_obj.Accessory1PartData.WeightedParts]
                    if rar == Rarity.VERY_RARE:
                        self.assertGreater(len(acc_parts_in_plc), 0)
                        self.assertNotIn(none_acc_obj, acc_parts_in_plc)

                # v0.3.2 Regression 2: Verify unsquished (+26 deg Yaw) & full-slot horizontal UI preview settings!
                for wt_obj in state.STATE.maker_to_wt.values():
                    self.assertEqual(wt_obj.UIMeshRotation.Roll, -12015)
                    self.assertEqual(wt_obj.UIMeshRotation.Yaw, 4733)
                    self.assertFalse(wt_obj.bUIMeshRotationNeedsTransformFixup)
                    self.assertAlmostEqual(wt_obj.FocusRadius, 22.0)
                    self.assertAlmostEqual(wt_obj.MinimumVerticalPercentage, 0.0)
                    self.assertAlmostEqual(wt_obj.MinimumHorizontalPercentage, 0.85)
                    self.assertAlmostEqual(wt_obj.ThirdPersonMeshScale, 1.05)
                    self.assertEqual(wt_obj.Presentation.Name, "Presentation_Melee")

                # Verify `GestaltPartBounds` were tightened around rotated horizontal sword center
                hyp_bound = next(
                    b for b in state.STATE.custom_gestalt_def.GestaltPartBounds
                    if b.SkeletalMeshFragmentName == "Ink_blade_hyperion"
                )
                self.assertLess(hyp_bound.ReferencePoseBounds.BoxExtent.Y, 4.0)
                self.assertLess(hyp_bound.ReferencePoseBounds.BoxExtent.Z, 18.0)

                hyp_wt = next(iter(state.STATE.maker_to_wt.values()))
                preview_comp = types.SimpleNamespace(
                    Class=types.SimpleNamespace(Name="GestaltSkeletalMeshComponent"),
                    Scale=0.85,
                    Scale3D=world._vec(1.0, 1.0, 1.0),
                    Rotation=FakeWrappedStruct("Rotator", Pitch=0, Yaw=0, Roll=0),
                    Translation=world._vec(),
                )
                preview_comp.SetScale = lambda s: setattr(preview_comp, "Scale", float(s))
                # Static call (`obj = Default__WillowWeapon`, `args.InvDefinition = hyp_wt`)
                resolver.on_init_construct_preview_post(
                    types.SimpleNamespace(Name="Default__WillowWeapon"),
                    types.SimpleNamespace(Existing=None, InvDefinition=hyp_wt),
                    preview_comp,
                    None,
                )
                self.assertAlmostEqual(preview_comp.Scale, 2.05)

                # Vanilla pistol preview MUST NOT be altered!
                pistol_wt = world.find_object("WeaponTypeDefinition", "GD_Weap_Pistol.A_Weapons.WeaponType_Hyperion_Pistol")
                pistol_preview = types.SimpleNamespace(
                    Class=types.SimpleNamespace(Name="GestaltSkeletalMeshComponent"),
                    Scale=0.85,
                )
                pistol_preview.SetScale = lambda s: setattr(pistol_preview, "Scale", float(s))
                resolver.on_init_construct_preview_post(
                    types.SimpleNamespace(Name="Default__WillowWeapon"),
                    types.SimpleNamespace(Existing=None, InvDefinition=pistol_wt),
                    pistol_preview,
                    None,
                )
                self.assertAlmostEqual(pistol_preview.Scale, 0.85)

                # Live `melee_preview` tuner updates all WTs + `GestaltPartBounds` dynamically
                cfg = commands.tune_ui_preview_command(
                    roll_deg="-68", scale="2.20", focus_radius="20", yaw_deg="28", bounds_z_scale="0.55"
                )
                self.assertAlmostEqual(cfg["scale"], 2.20)
                self.assertAlmostEqual(cfg["bounds_z_scale"], 0.55)
                self.assertEqual(hyp_wt.UIMeshRotation.Roll, int(round(-68.0 * 65536.0 / 360.0)))
                self.assertEqual(hyp_wt.UIMeshRotation.Yaw, int(round(28.0 * 65536.0 / 360.0)))
                self.assertAlmostEqual(hyp_wt.FocusRadius, 20.0)

                gestalt = state.STATE.custom_gestalt_def
                self.assertIsNotNone(gestalt)
                self.assertEqual(len(gestalt.GestaltInfos[0].Parts), 42)
                self.assertEqual(len(gestalt.GestaltPartBounds), 42)
                self.assertGreaterEqual(len(gestalt.GestaltSocketMappings), 15)

                # Every custom WeaponTypeDefinition points to Gestalt_Inkblade & every Body part has 'None' fragment
                for wt_obj in state.STATE.maker_to_wt.values():
                    self.assertEqual(wt_obj.GestaltMesh, gestalt)
                for body_part in state.STATE.body_parts.values():
                    self.assertEqual(body_part.GestaltModeSkeletalMeshName, "None")

                # Drop a Hyperion Shock Serrated Katana vs a Torgue Explosive Serrated Blast-Cleaver
                hyp_inst = MeleeWeaponInstance(
                    unique_id=9001,
                    game_stage=30,
                    rarity=Rarity.VERY_RARE,
                    parts=MeleeWeaponParts(
                        blade_id="Blade_Hyperion_Katana",
                        grip_id="Grip_Hyperion",
                        accessory_id="Acc_Serrated",
                        element_id="Elem_Shock",
                    ),
                )
                tor_inst = MeleeWeaponInstance(
                    unique_id=9002,
                    game_stage=30,
                    rarity=Rarity.VERY_RARE,
                    parts=MeleeWeaponParts(
                        blade_id="Blade_Torgue_BlastCleaver",
                        grip_id="Grip_Torgue",
                        accessory_id="Acc_Serrated",
                        element_id="Elem_Explosive",
                    ),
                )
                world.ground_pickups.clear()
                dropped = resolver.spawn_melee_weapon_batch([hyp_inst, tor_inst], pc=world.pc, delivery="drop")
                self.assertEqual(dropped, 2)
                hyp_wpn = world.ground_pickups[0].Inventory
                tor_wpn = world.ground_pickups[1].Inventory

                # Hyperion gets variant 0 (`Ink_accessory_serrated_edge`) + `Mat_Inkblade_Shock_VeryRare` (`Rarity == 4.0`)
                self.assertEqual(
                    hyp_wpn.DefinitionData.BarrelPartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_blade_hyperion",
                )
                self.assertEqual(
                    hyp_wpn.DefinitionData.Accessory1PartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_accessory_serrated_edge",
                )
                self.assertEqual(
                    hyp_wpn.DefinitionData.ElementalPartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_element_shock",
                )
                self.assertTrue(
                    hyp_wpn.DefinitionData.MaterialPartDefinition._path_name().endswith("Mat_Inkblade_Shock_VeryRare")
                )
                self.assertEqual(
                    hyp_wpn.DefinitionData.MaterialPartDefinition.Rarity.BaseValueConstant,
                    4.0,
                )

                # Loot beam + item-card colour levels must match the rolled tier on the live objects
                # (`WillowPickup.InventoryRarityLevel` / `WillowInventory.RarityLevel`).
                for pickup in world.ground_pickups:
                    self.assertAlmostEqual(pickup.InventoryRarityLevel, 4.0)
                    self.assertAlmostEqual(pickup.Inventory.RarityLevel, 4.0)

                # Torgue gets exact blade-fitted variant 4 (`Ink_accessory_serrated_edge__s4`) + `Mat_Inkblade_Explosive_VeryRare`
                self.assertEqual(
                    tor_wpn.DefinitionData.BarrelPartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_blade_torgue",
                )
                self.assertEqual(
                    tor_wpn.DefinitionData.Accessory1PartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_accessory_serrated_edge__s4",
                )
                self.assertEqual(
                    tor_wpn.DefinitionData.ElementalPartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_element_explosive",
                )
                self.assertTrue(
                    tor_wpn.DefinitionData.MaterialPartDefinition._path_name().endswith("Mat_Inkblade_Explosive_VeryRare")
                )
                self.assertEqual(
                    tor_wpn.DefinitionData.MaterialPartDefinition.Rarity.BaseValueConstant,
                    4.0,
                )

                # Reverse-resolving `tor_wpn` maps `Part_Acc_Serrated__s4` cleanly back to `Acc_Serrated`!
                world.pawn.Weapon = tor_wpn
                dump = commands.dump_held_melee_weapon()
                self.assertIsNotNone(dump)
                self.assertEqual(dump["parts"]["accessory"], "Acc_Serrated")
                self.assertEqual(dump["visuals"]["accessory_fragment"], "Ink_accessory_serrated_edge__s4")
                self.assertTrue(dump["visuals"]["material_part"].endswith("Mat_Inkblade_Explosive_VeryRare"))

    def test_world_drop_pools_hotfix_recovery_and_natural_drop_finalization(self) -> None:
        if str(SDK_MODS_DIR) not in sys.path:
            sys.path.insert(0, str(SDK_MODS_DIR))

        with tempfile.TemporaryDirectory() as tmp:
            world = MockBL2World(Path(tmp))
            world.populate_custom_inkblade_packages()
            sdk_mods = world.make_sdk_modules()

            for mod_name in list(sys.modules):
                if mod_name == "melee_arsenal" or mod_name.startswith("melee_arsenal.runtime"):
                    sys.modules.pop(mod_name, None)

            with mock.patch.dict(sys.modules, sdk_mods):
                import melee_arsenal  # type: ignore[import-not-found]
                from melee_arsenal.core.enums import Manufacturer, Rarity
                from melee_arsenal.runtime import definitions, loot_pools, persistence_bridge, resolver, state

                self.assertIsNotNone(melee_arsenal.mod)
                self.assertTrue(definitions.ensure_definitions_built())
                self.assertTrue(loot_pools.sync_loot_pools())

                # 1. Verify ALL custom melee pools stripped donor `Gamestage_02`/`Gamestage_99` gates & `Quantity=1`
                self.assertGreaterEqual(len(state.STATE.custom_melee_pools), 7)
                for pname, pobj in state.STATE.custom_melee_pools.items():
                    self.assertIsNone(pobj.MinGameStageRequirement, f"{pname} kept MinGameStageRequirement!")
                    self.assertIsNone(pobj.MaxGameStageRequirement, f"{pname} kept MaxGameStageRequirement!")
                    self.assertFalse(pobj.bAutoReadyItems)
                    self.assertAlmostEqual(pobj.Quantity.BaseValueConstant, 1.0)
                for dpool in state.STATE.balance_drop_pools.values():
                    self.assertIsNone(dpool.MinGameStageRequirement)
                    self.assertIsNone(dpool.MaxGameStageRequirement)

                # 2. Verify `Pool_Weapons_Melee_Epic` excludes White Commons (`01_Common`)
                epic_sub = state.STATE.custom_melee_pools["Pool_Weapons_Melee_Epic"]
                epic_child_names = [it.ItmPoolDefinition.Name for it in epic_sub.BalancedItems]
                self.assertNotIn("Pool_Weapons_Melee_01_Common", epic_child_names)
                self.assertIn("Pool_Weapons_Melee_04_Rare", epic_child_names)
                self.assertIn("Pool_Weapons_Melee_05_VeryRare", epic_child_names)

                # 3. Verify proportional 16.13% (`100 / 620`) class share across world, enemy, chest, locker & shop pools
                report = loot_pools.inspect_loot_pools(resync_first=False)
                self.assertTrue(report["subpool_stage_gates_cleared"])
                # 4 world + 4 real enemy death-drop tiers + 4 class-weighted + chest/container/shop
                self.assertEqual(report["injected_pools"], 14)
                self.assertAlmostEqual(report["avg_weapon_share_pct"], 16.13, places=1)

                # Standard enemies roll `Pool_GunsAndGear_<tier>` on death, so Melee Weapons must be
                # present there (mapped to the matching rarity sub-pool), not just in the AI-weighted pools.
                for tier, melee_key in (
                    ("01_Common", "01_Common"),
                    ("02_Uncommon", "02_Uncommon"),
                    ("04_Rare", "04_Rare"),
                    ("05_VeryRare", "05_VeryRare"),
                ):
                    enemy_tier_pool = world.find_object(
                        "ItemPoolDefinition", f"GD_Itempools.EnemyDropPools.Pool_GunsAndGear_{tier}"
                    )
                    tier_melee = [
                        it
                        for it in enemy_tier_pool.BalancedItems
                        if "Pool_Weapons_Melee_" in it.ItmPoolDefinition._path_name()
                    ]
                    self.assertEqual(len(tier_melee), 1, f"Pool_GunsAndGear_{tier} has no melee entry!")
                    self.assertTrue(
                        tier_melee[0]
                        .ItmPoolDefinition._path_name()
                        .endswith(f"Pool_Weapons_Melee_{melee_key}")
                    )

                # Vendors already stocked before injection must be re-rolled once so shelves show melee
                self.assertGreaterEqual(world.vendor_resets, 1)

                # Verify `Pool_GunsAndGear_WeightedPistols` scaled against weapon weight (100), NOT shield weight (85)!
                gg_pool = world.find_object("ItemPoolDefinition", "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_WeightedPistols")
                gg_melee = [it for it in gg_pool.BalancedItems if "Pool_Weapons_Melee_" in it.ItmPoolDefinition._path_name()]
                self.assertEqual(len(gg_melee), 1)
                self.assertAlmostEqual(gg_melee[0].Probability.BaseValueConstant, 100.0 * (100.0 / 520.0), places=3)

                # Verify `Pool_Locker_Items_SMGsAndPistols` evaluated `Weight_2_Uncommon(10) + Weight_3_Uncommoner(5) = 15`
                locker_pool = world.find_object("ItemPoolDefinition", "GD_Itempools.LootablePools.Pool_Locker_Items_SMGsAndPistols")
                locker_melee = [it for it in locker_pool.BalancedItems if "Pool_Weapons_Melee_" in it.ItmPoolDefinition._path_name()]
                self.assertEqual(len(locker_melee), 1)
                self.assertAlmostEqual(locker_melee[0].Probability.BaseValueConstant, 15.0 * (100.0 / 520.0), places=3)

                # Verify AI `GD_ItempoolsEnemyUse` pool was NEVER touched!
                ai_pool = world.find_object("ItemPoolDefinition", "GD_ItempoolsEnemyUse.Weapons.Pool_Weapons_Pistols_EnemyUse")
                self.assertEqual(len(ai_pool.BalancedItems), 1)

                # 4. Simulate a SparkLevelPatchEntry / BLCMM hotfix wiping `Pool_Weapons_All_01_Common` after menu init!
                all_common = world.find_object("ItemPoolDefinition", "GD_Itempools.WeaponPools.Pool_Weapons_All_01_Common")
                all_common.BalancedItems = [all_common.BalancedItems[0]]  # Wiped back to vanilla!
                self.assertEqual(len(all_common.BalancedItems), 1)
                # Map-load `sync_loot_pools()` restores `Pool_Weapons_Melee_01_Common` without duplicating other pools!
                self.assertTrue(loot_pools.sync_loot_pools())
                self.assertEqual(len(all_common.BalancedItems), 2)
                self.assertEqual(len(gg_pool.BalancedItems), 3)

                # 5. Verify `PLC_Melee_*` `WeightedParts` carry non-empty `Manufacturers` (`DefaultWeightIndex -> 1.0`) & `MaxGameStage=10000`
                mal_bal = state.STATE.balances[(Manufacturer.MALIWAN, Rarity.VERY_RARE)]
                self.assertEqual(mal_bal.Manufacturers[0].Grades[0].GameStageRequirement.MaxGameStage, 10000)
                mal_plc = mal_bal.RuntimePartListCollection
                init_table = list(mal_plc.ConsolidatedAttributeInitData)
                for wp in mal_plc.ElementalPartData.WeightedParts:
                    self.assertEqual(len(wp.Manufacturers), 1)
                    self.assertIsNone(wp.Manufacturers[0].Manufacturer)
                    self.assertEqual(init_table[wp.Manufacturers[0].DefaultWeightIndex].BaseValueConstant, 1.0)
                    self.assertEqual(init_table[wp.MaxGameStageIndex].BaseValueConstant, 10000.0)

                # 6. Simulate a 100% NATURAL enemy world drop (NO `_PENDING_DROP_INSTANCES`, `UniqueId=0`):
                # Native `SpawnBalancedInventoryFromPool` rolls `Balance_Melee_Maliwan_VERY_RARE` with `Grip_Bandit`,
                # base `Acc_Serrated` (needing Maliwan blade-fitted `__s3` twin!), and `Elem_Shock` at OP10 stage 90.
                natural_dd = FakeWrappedStruct(
                    "WeaponDefinitionData",
                    WeaponTypeDefinition=mal_bal.InventoryDefinition,
                    BalanceDefinition=mal_bal,
                    ManufacturerDefinition=mal_bal.Manufacturers[0].Manufacturer,
                    ManufacturerGradeIndex=90,
                    BodyPartDefinition=state.STATE.body_parts[Manufacturer.MALIWAN],
                    GripPartDefinition=state.STATE.grip_parts["Grip_Bandit"],
                    BarrelPartDefinition=state.STATE.blade_parts["Blade_Maliwan_Phaseblade"],
                    SightPartDefinition=None,
                    StockPartDefinition=None,
                    ElementalPartDefinition=state.STATE.element_parts["Elem_Shock"],
                    Accessory1PartDefinition=state.STATE.accessory_parts["Acc_Serrated"],
                    Accessory2PartDefinition=None,
                    MaterialPartDefinition=mal_plc.MaterialPartData.WeightedParts[0].Part,
                    PrefixPartDefinition=None,
                    TitlePartDefinition=None,
                    GameStage=90,
                    UniqueId=0,
                )
                natural_wpn = world._make_live_weapon(natural_dd)
                resolver.on_choose_random_name_parts_post(natural_wpn, None, None, None)

                # Must immediately finalize Shock VeryRare material (`Rarity == 4.0` Purple beam),
                # Maliwan-fitted `__s3` serrated edge twin, exact Prefix/Title, and non-zero `UniqueId`!
                self.assertTrue(
                    natural_wpn.DefinitionData.MaterialPartDefinition._path_name().endswith(
                        "Mat_Inkblade_Shock_VeryRare"
                    )
                )
                self.assertEqual(
                    natural_wpn.DefinitionData.MaterialPartDefinition.Rarity.BaseValueConstant,
                    4.0,
                )
                self.assertEqual(
                    natural_wpn.DefinitionData.Accessory1PartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_accessory_serrated_edge__s3",
                )
                self.assertIsNotNone(natural_wpn.DefinitionData.TitlePartDefinition)
                self.assertIsNotNone(natural_wpn.DefinitionData.PrefixPartDefinition)
                self.assertGreater(natural_wpn.DefinitionData.UniqueId, 0)
                self.assertEqual(natural_wpn.ReloadCnt, 100)

                # Natural drop persists cleanly in sidecar save/load
                world.backpack.clear()
                world.backpack.append(natural_wpn)
                self.assertEqual(persistence_bridge.snapshot_player_melee_inventory(pc=world.pc), 1)

                # Toggle Enable World Drops = False removes melee entries cleanly
                state.STATE.world_drops_enabled = False
                self.assertTrue(loot_pools.sync_loot_pools())
                self.assertEqual(len(all_common.BalancedItems), 1)
                self.assertFalse(state.STATE.pools_injected)


if __name__ == "__main__":
    unittest.main()
