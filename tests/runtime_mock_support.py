"""Strict fake Unreal SDK objects and world fixtures for runtime integration tests."""

from __future__ import annotations

import argparse
import copy
import types
from pathlib import Path

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
            raise AttributeError(f"WrappedStruct {object.__getattribute__(self, '_struct_name')} has no field {name!r}")
        cur = object.__getattribute__(self, name)
        if isinstance(cur, FakeWrappedStruct) and not isinstance(value, FakeWrappedStruct):
            raise RuntimeError(
                f"Unable to cast Python instance of type {type(value)} to C++ type 'unrealsdk::unreal::WrappedStruct'"
            )
        object.__setattr__(self, name, value)

    def __copy__(self):
        data = {k: copy.copy(object.__getattribute__(self, k)) for k in object.__getattribute__(self, "_fields")}
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
            raise AttributeError(f"UObject {object.__getattribute__(self, '_cls_name')} has no property {name!r}")

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
        if outer is None:
            return nm
        try:
            return f"{outer._path_name()}.{nm}"
        except AttributeError:
            # Components are constructed with the (mock) pawn actor as their outer.
            return f"{getattr(outer, 'Name', repr(outer))}.{nm}"

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
            loc_field = types.SimpleNamespace(Struct=types.SimpleNamespace(_properties=lambda: [use_prop, loc_prop]))
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
        elif cls_name == "SkeletalMeshComponent":
            object.__setattr__(obj, "SkeletalMesh", None)
            object.__setattr__(obj, "Materials", [])
            object.__setattr__(obj, "Location", self._vec())
            object.__setattr__(obj, "Rotation", FakeWrappedStruct("Rotator", Pitch=0, Yaw=0, Roll=0))
            object.__setattr__(obj, "Hidden", False)
            object.__setattr__(obj, "Absolute", False)
            record: dict = {
                "name": name,
                "mesh": None,
                "materials": {},
                "location": (0.0, 0.0, 0.0),
                "pitch": 0,
                "hidden": False,
                "moves": 0,
            }
            self.thrown_blade_visuals.append(record)

            def _set_mesh(mesh, _obj=obj, _rec=record):
                object.__setattr__(_obj, "SkeletalMesh", mesh)
                _rec["mesh"] = mesh

            def _set_material(index, material, _obj=obj, _rec=record):
                mats = list(_obj.Materials)
                while len(mats) <= int(index):
                    mats.append(None)
                mats[int(index)] = material
                object.__setattr__(_obj, "Materials", mats)
                _rec["materials"][int(index)] = material

            def _set_translation(vec, _obj=obj, _rec=record):
                object.__setattr__(_obj, "Location", copy.copy(vec))
                _rec["location"] = (float(vec.X), float(vec.Y), float(vec.Z))
                _rec["moves"] += 1

            def _set_rotation(rot, _obj=obj, _rec=record):
                object.__setattr__(_obj, "Rotation", copy.copy(rot))
                _rec["pitch"] = int(getattr(rot, "Pitch", 0))

            def _set_hidden(flag, _obj=obj, _rec=record):
                object.__setattr__(_obj, "Hidden", bool(flag))
                _rec["hidden"] = bool(flag)

            def _set_absolute(*flags, _obj=obj):
                object.__setattr__(_obj, "Absolute", all(bool(f) for f in flags))

            obj.SetSkeletalMesh = _set_mesh
            obj.SetMaterial = _set_material
            obj.SetTranslation = _set_translation
            obj.SetRotation = _set_rotation
            obj.SetHiddenGame = _set_hidden
            obj.SetAbsolute = _set_absolute
            obj.SetBlockRigidBody = lambda _flag: None
            obj.SetCollision = lambda *_a: None
            obj.SetScale3D = lambda scale, _rec=record: _rec.__setitem__("scale", scale)
            obj.SetScale = lambda scale, _rec=record: _rec.__setitem__("scale", scale)
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
                                ValueResolverChain=[types.SimpleNamespace(ConstantValue=float(idx))]
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
        # Thrown-blade visual actor bookkeeping (Tediore-style flying weapon mesh) + HUD callouts
        self.thrown_blade_visuals: list[dict] = []
        self.attached_components: list[object] = []
        self.detached_components: list[object] = []
        self.hud_messages: list[tuple[str, str]] = []
        self.thrown_blade_source_mesh = FakeUObject("SkeletalMesh", None, "Inkblade_Assembly")
        self.thrown_blade_source_material = types.SimpleNamespace(Name="Mat_Inkblade_Thrown", ObjectFlags=0)
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
            _path_name=lambda: "MockWorld.WillowPlayerPawn_0",
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
            AttachComponent=lambda comp: self.attached_components.append(comp),
            DetachComponent=lambda comp: self.detached_components.append(comp),
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
            GetHUDMovie=lambda: types.SimpleNamespace(
                AddTrainingText=lambda message, title, *a: self.hud_messages.append((str(title), str(message)))
            ),
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
        for slot_name in (
            "BodyPartData",
            "GripPartData",
            "BarrelPartData",
            "ElementalPartData",
            "Accessory1PartData",
            "MaterialPartData",
        ):
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
            resolver.on_weapon_initialize_from_def_pre(wpn, types.SimpleNamespace(NewDefinitionData=dd), None, None)
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
        mesh_state = {"hidden": False}
        held_mesh = types.SimpleNamespace(
            ObjectFlags=0,
            SkeletalMesh=self.thrown_blade_source_mesh,
            Materials=[self.thrown_blade_source_material],
            Scale3D=self._vec(1.0, 1.0, 1.0),
            RotOrigin=FakeWrappedStruct("Rotator", Pitch=0, Yaw=0, Roll=0),
            SetHiddenGame=lambda flag: mesh_state.__setitem__("hidden", bool(flag)),
            _state=mesh_state,
        )
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
            Mesh=held_mesh,
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
                        GetEngine=lambda: types.SimpleNamespace(GamePlayers=[types.SimpleNamespace(Actor=self.pc)])
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
            parser = argparse.ArgumentParser(**_kw)

            def deco(fn):
                fn.add_argument = parser.add_argument
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
