#!/usr/bin/env python3
"""Offline self-test for `sdk_mods/melee_arsenal_probe`.

The probe cannot be exercised without Borderlands 2, and a probe that silently returns nothing is
worse than no probe at all. This harness installs a miniature fake `unrealsdk` + `mods_base`
describing a handful of BL2 objects, runs every probe section against it, and asserts the ground
truth that comes back — so CI catches a broken probe before anyone spends a play session on it.

Run: python3 tools/probe_selftest.py  (exits non-zero on failure)
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sdk_mods"))

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


# ---------------------------------------------------------------------------------------------
# Fake UE3 reflection surface
# ---------------------------------------------------------------------------------------------


class FakeProp:
    """One property on a fake class / struct."""

    def __init__(
        self,
        name: str,
        *,
        struct: "FakeClass | None" = None,
        enum: "FakeEnum | None" = None,
        flags: int = 0,
        array_dim: int = 1,
    ) -> None:
        self.Name = name
        self.Struct = struct
        self.Enum = enum
        self.PropertyFlags = flags
        self.ArrayDim = array_dim


class FakeEnum:
    def __init__(self, name: str, values: list[str]) -> None:
        self.Name = name
        self._values = values

    def _names(self) -> list[str]:
        return list(self._values)

    def _path_name(self) -> str:
        return f"FakeGame.{self.Name}"


class FakeClass:
    """Stand-in for a UClass / UScriptStruct: a name, a property list, and a CDO."""

    def __init__(self, name: str, props: list[FakeProp] | None = None) -> None:
        self.Name = name
        self._props = props or []
        self.ClassDefaultObject: "FakeObject | None" = None
        self._functions: dict[str, "FakeClass"] = {}

    def _properties(self) -> list[FakeProp]:
        return list(self._props)

    def _path_name(self) -> str:
        return f"FakeGame.{self.Name}"

    def add_function(self, name: str, params: list[FakeProp]) -> None:
        func = FakeClass(name, params)
        self._functions[name] = func

    def _find(self, name: str) -> "FakeClass | None":
        return self._functions.get(name)


class FakeObject:
    """A live object: attribute bag plus a stable path."""

    def __init__(self, path: str, cls: FakeClass, **attrs: Any) -> None:
        self._path = path
        self.Class = cls
        self.ObjectFlags = 0
        self.Name = path.rsplit(".", 1)[-1]
        for key, value in attrs.items():
            setattr(self, key, value)

    def _path_name(self) -> str:
        return self._path


class FakeStructValue(dict):
    """Struct by value: attribute access over a dict, which is all the probe needs."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def build_world() -> dict[str, Any]:
    """A small but representative slice of BL2, biased toward what the probe must read."""
    compare_arrow = FakeEnum("ECompareStat", ["ECS_None", "ECS_Better", "ECS_Equal", "ECS_Worse"])
    vector = FakeClass("Vector", [FakeProp("X"), FakeProp("Y"), FakeProp("Z")])
    _ = vector  # registered below so nested Vector structs resolve
    attribute_init_data = FakeClass(
        "AttributeInitializationData",
        [
            FakeProp("BaseValueConstant"),
            FakeProp("BaseValueAttribute"),
            FakeProp("InitializationDefinition"),
            FakeProp("BaseValueScaleConstant"),
        ],
    )

    behavior_explode_cls = FakeClass(
        "Behavior_Explode",
        [
            FakeProp("OverrideName"),
            FakeProp("DamageFormula", struct=attribute_init_data),
            FakeProp("DamageRadiusFormula", struct=attribute_init_data),
            FakeProp("MomentumFormula", struct=attribute_init_data),
            FakeProp("bDisableRadiusDamageFalloff"),
            FakeProp("bCanDamageFriendlies"),
        ],
    )
    firing_mode_cls = FakeClass(
        "FiringModeDefinition",
        [
            FakeProp("OnAnyImpact", array_dim=8),
            FakeProp("OnHit", array_dim=8),
            FakeProp("TimerEvents", array_dim=8),
            FakeProp("ExplosionOverrideDefinition"),
            FakeProp("ShotsPerFire"),
        ],
    )
    item_pool_cls = FakeClass(
        "ItemPoolDefinition",
        [
            FakeProp("BalancedItems", array_dim=16),
            FakeProp("Quantity", struct=attribute_init_data),
            FakeProp("MinGameStageRequirement"),
            FakeProp("MaxGameStageRequirement"),
            FakeProp("bAutoReadyItems"),
        ],
    )
    anim_slot_cls = FakeClass("AnimNodeSlot", [FakeProp("NodeName"), FakeProp("SkelComponent")])
    skeletal_mesh_cls = FakeClass(
        "SkeletalMeshComponent",
        [
            FakeProp("RotOrigin"),
            FakeProp("Origin"),
            FakeProp("SkeletalMesh"),
            FakeProp("Materials", array_dim=16),
            FakeProp("AnimSets", array_dim=8),
        ],
    )
    weapon_cls = FakeClass(
        "WillowWeapon",
        [
            FakeProp("DefinitionData"),
            FakeProp("Mesh"),
            FakeProp("InstantHitDamage"),
            FakeProp("ProjectilesPerShot"),
            FakeProp("ClipSize"),
            FakeProp("CurrentFiringModeDefinition"),
        ],
    )
    weapon_cls.add_function(
        "InitializeInternal",
        [FakeProp("ReturnValue", flags=0x400)],
    )
    weapon_cls.add_function(
        "ParentNone",
        [],
    )
    weapon_definition_data_cls = FakeClass(
        "WillowWeaponDefinitionData",
        [
            FakeProp("WeaponTypeDefinition"),
            FakeProp("BalanceDefinition"),
            FakeProp("BarrelPartDefinition"),
            FakeProp("GripPartDefinition"),
            FakeProp("ElementalPartDefinition"),
            FakeProp("Accessory1PartDefinition"),
            FakeProp("MaterialPartDefinition"),
            FakeProp("FiringModeDefinition"),
        ],
    )
    card_object_cls = FakeClass(
        "ItemCardGFxObject",
        [],
    )
    card_object_cls.add_function(
        "SetTopStat",
        [
            FakeProp("StatIndex"),
            FakeProp("CompareArrow", enum=compare_arrow),
            FakeProp("Label"),
            FakeProp("IconName"),
        ],
    )
    pc_cls = FakeClass(
        "WillowPlayerController",
        [FakeProp("Pawn"), FakeProp("WorldInfo"), FakeProp("GetHUDMovie")],
    )
    engine_cls = FakeClass("Engine", [FakeProp("GamePlayers")])

    # --- Instances ---------------------------------------------------------------------------
    explode = FakeObject(
        "FakeGame.Behavior_Explode.InkbladeSplash",
        behavior_explode_cls,
        OverrideName="ExplosionOverride",
        DamageFormula=FakeStructValue(BaseValueScaleConstant=0.5, BaseValueConstant=1.0),
        DamageRadiusFormula=FakeStructValue(BaseValueConstant=180.0),
        MomentumFormula=FakeStructValue(BaseValueConstant=18000.0),
        bDisableRadiusDamageFalloff=False,
        bCanDamageFriendlies=False,
    )
    init_def = FakeObject(
        "FakeGame.AttributeInitializationDefinition.Weight_4_Rare",
        FakeClass("AttributeInitializationDefinition"),
        ValueFormula=FakeStructValue(Multiplier=FakeStructValue(BaseValueConstant=1.0)),
    )
    flakker = FakeObject(
        "GD_Weap_Shotgun.FiringModes.Bullets_Shotgun_Torgue_Flakker_Child",
        firing_mode_cls,
        OnAnyImpact=[explode],
        OnHit=[],
        TimerEvents=[],
        ExplosionOverrideDefinition=None,
        ShotsPerFire=1,
    )
    pistol_fm = FakeObject(
        "FakeGame.FiringModeDefinition.InkbladeSwing",
        firing_mode_cls,
        OnAnyImpact=[],
        OnHit=[],
        TimerEvents=[],
        ExplosionOverrideDefinition=None,
        ShotsPerFire=1,
    )

    def _entry(item_path: str, init_name: str, constant: float) -> FakeStructValue:
        init = FakeObject(f"FakeGame.AttributeInitializationDefinition.{init_name}", FakeClass("X"))
        probability = FakeStructValue(
            BaseValueConstant=None,
            BaseValueScaleConstant=1.0,
            BaseValueAttribute=None,
            InitializationDefinition=init,
        )
        item = FakeObject(item_path, weapon_cls)
        return FakeStructValue(Item=item, Probability=probability, Quantity=FakeStructValue(BaseValueConstant=1.0))

    pistol_pool = FakeObject(
        "FakeGame.ItemPoolDefinition.Pool_Melee_Pistol",
        item_pool_cls,
        BalancedItems=[
            _entry("FakeGame.WillowWeapon.InkbladeKatana", "Weight_4_Rare", 1.0),
            _entry("FakeGame.WillowWeapon.InkbladeSabre", "Weight_2_Uncommon", 10.0),
        ],
        Quantity=FakeStructValue(BaseValueConstant=1.0),
        MinGameStageRequirement=None,
        MaxGameStageRequirement=None,
        bAutoReadyItems=False,
    )
    shop_pool = FakeObject(
        "GD_ItemPools_Shop.WeaponPools.ShopPool_Weapons_SMG",
        item_pool_cls,
        BalancedItems=[_entry("FakeGame.WillowWeapon.InkbladeMachete", "Weight_1_Common", 100.0)],
        Quantity=FakeStructValue(BaseValueConstant=2.0),
        MinGameStageRequirement=None,
        MaxGameStageRequirement=None,
        bAutoReadyItems=True,
    )

    explosion_ps = FakeObject(
        "FX_WEP_Explosions.Particles.Default.Part_ExplosiveExplosion_Medium",
        FakeClass("ParticleSystem"),
    )
    unrelated_ps = FakeObject("FX_Env.Particles.Leaves", FakeClass("ParticleSystem"))
    explosion_ak = FakeObject(
        "Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_MED",
        FakeClass("AkEvent"),
    )

    material_one = FakeObject("FakeGame.MaterialInstanceConstant.Inkblade_Fire", FakeClass("MaterialInstanceConstant"))
    material_two = FakeObject("FakeGame.MaterialInstanceConstant.Inkblade_Shock", FakeClass("MaterialInstanceConstant"))
    mesh = FakeObject(
        "FakeGame.SkeletalMeshComponent.WeaponMesh",
        skeletal_mesh_cls,
        RotOrigin=FakeStructValue(Pitch=0, Yaw=0, Roll=0),
        Origin=FakeStructValue(X=0.0, Y=0.0, Z=0.0),
        SkeletalMesh=FakeObject("FakeGame.SkeletalMesh.InkbladeGestalt", FakeClass("SkeletalMesh")),
        # Mixed material array: a bare material and a `{Material, ...}` struct entry.
        Materials=[material_one, FakeStructValue(Material=material_two)],
    )
    arms_mesh = FakeObject(
        "FakeGame.SkeletalMeshComponent.FirstPersonArms",
        skeletal_mesh_cls,
        RotOrigin=FakeStructValue(Pitch=120, Yaw=-40, Roll=7),
        Origin=FakeStructValue(X=1.0, Y=2.0, Z=3.0),
        SkeletalMesh=FakeObject("FakeGame.SkeletalMesh.PlayerArms", FakeClass("SkeletalMesh")),
        Materials=[],
        AnimSets=[
            FakeObject(
                "FakeGame.AnimSet.PlayerArmsAnims",
                FakeClass("AnimSet"),
                Sequences=[
                    FakeObject("FakeGame.AnimSequence.Bladed_Melee", FakeClass("AnimSequence"), SequenceName="Bladed_Melee"),
                    FakeObject("FakeGame.AnimSequence.Reload", FakeClass("AnimSequence"), SequenceName="Reload"),
                ],
            )
        ],
    )
    weapon_action_slot = FakeObject(
        "FakeGame.AnimNodeSlot.WeaponAction",
        anim_slot_cls,
        NodeName="WeaponAction",
        SkelComponent=arms_mesh,
    )
    custom_slot = FakeObject(
        "FakeGame.AnimNodeSlot.CustomAnimSlot",
        anim_slot_cls,
        NodeName="CustomAnimSlot",
        SkelComponent=arms_mesh,
    )
    foreign_slot = FakeObject(
        "FakeGame.AnimNodeSlot.OtherPawnSlot",
        anim_slot_cls,
        NodeName="CustomAnimSlot",
        SkelComponent=FakeObject("FakeGame.SkeletalMeshComponent.OtherArms", skeletal_mesh_cls),
    )

    definition_data = FakeObject(
        "FakeGame.WillowWeaponDefinitionData.InkbladeKatana",
        weapon_definition_data_cls,
        WeaponTypeDefinition=FakeObject("FakeGame.WillowWeaponTypeDefinition.Katana", FakeClass("W")),
        BalanceDefinition=FakeObject("FakeGame.WillowWeaponBalanceDefinition.Katana", FakeClass("B")),
        BarrelPartDefinition=None,
        GripPartDefinition=None,
        ElementalPartDefinition=None,
        Accessory1PartDefinition=None,
        MaterialPartDefinition=None,
        FiringModeDefinition=pistol_fm,
    )
    held_weapon = FakeObject(
        "FakeGame.WillowWeapon.InkbladeKatana",
        weapon_cls,
        DefinitionData=definition_data,
        Mesh=mesh,
        InstantHitDamage=1234.5,
        ProjectilesPerShot=1,
        ClipSize=100,
        CurrentFiringModeDefinition=pistol_fm,
    )
    pawn = FakeObject(
        "FakeGame.WillowPlayerPawn.PlayerPawn",
        FakeClass("WillowPlayerPawn"),
        Weapon=held_weapon,
        Arms=arms_mesh,
        ExpLevel=50,
    )
    world_info = FakeObject("FakeGame.WorldInfo.World", FakeClass("WorldInfo"))
    world_info.GetStreamingPersistentMapName = lambda: "SouthernShelf_P"
    pc = FakeObject(
        "FakeGame.WillowPlayerController.PlayerController",
        pc_cls,
        Pawn=pawn,
        WorldInfo=world_info,
    )
    pc.GetHUDMovie = lambda: None
    game_player = FakeObject("FakeGame.LocalPlayer.GamePlayer", FakeClass("LocalPlayer"), Actor=pc)
    engine_cdo = FakeObject("FakeGame.Engine.CDO", engine_cls, GamePlayers=[game_player])
    engine_cls.ClassDefaultObject = engine_cdo
    engine_cdo.GetEngine = lambda: engine_cdo

    target_dummy = FakeObject(
        "FakeGame.WillowAIPawn.TargetDummy_Sanctuary",
        FakeClass("WillowAIPawn"),
    )

    objects_by_class: dict[str, list[FakeObject]] = {
        "ParticleSystem": [explosion_ps, unrelated_ps],
        "AkEvent": [explosion_ak],
        "FiringModeDefinition": [flakker, pistol_fm],
        "Behavior_Explode": [explode],
        "ItemPoolDefinition": [pistol_pool, shop_pool],
        "AttributeInitializationDefinition": [init_def],
        "AnimNodeSlot": [weapon_action_slot, custom_slot, foreign_slot],
        "WillowAIPawn": [target_dummy],
        "BehaviorProviderDefinition": [],
    }
    classes = {
        name: cls
        for name, cls in (
            ("WillowWeapon", weapon_cls),
            ("WillowWeaponDefinitionData", weapon_definition_data_cls),
            ("FiringModeDefinition", firing_mode_cls),
            ("Behavior_Explode", behavior_explode_cls),
            ("ItemPoolDefinition", item_pool_cls),
            ("AnimNodeSlot", anim_slot_cls),
            ("SkeletalMeshComponent", skeletal_mesh_cls),
            ("WillowPlayerController", pc_cls),
            ("Engine", engine_cls),
        )
    }
    # Every class needs a class-default object: that is what the probe reads default values from.
    for cls in classes.values():
        if cls.ClassDefaultObject is None:
            cls.ClassDefaultObject = FakeObject(f"FakeGame.{cls.Name}.CDO", cls)

    # Only the real UE3 form is registered: a UFunction's outer is its class, so the path uses `:`.
    objects_by_path = {
        "WillowGame.WillowWeapon:InitializeInternal": weapon_cls._functions["InitializeInternal"],
        "WillowGame.ItemCardGFxObject:SetTopStat": card_object_cls._functions["SetTopStat"],
    }
    return {
        "classes": classes,
        "find_object_calls": [],
        "load_package_calls": [],
        "objects_by_class": objects_by_class,
        "objects_by_path": objects_by_path,
        "compare_arrow": compare_arrow,
        "explode": explode,
        "pistol_pool": pistol_pool,
        "arms_mesh": arms_mesh,
        "held_weapon": held_weapon,
        "pistol_fm": pistol_fm,
    }


def install_fake_sdk(world: dict[str, Any]) -> None:
    """Publish fake `unrealsdk` + `mods_base` modules so the probe imports against them."""
    unrealsdk = types.ModuleType("unrealsdk")
    hooks_module = types.ModuleType("unrealsdk.hooks")

    class Type:
        PRE = "PRE"
        POST = "POST"

    unrealsdk.find_class = lambda name: world["classes"].get(name)

    def _find_object(cls: str, path: str) -> Any:
        # Missing objects return None, like the real SDK. A lookup for an unknown class name is
        # also just a miss: the probe must never depend on it succeeding.
        world["find_object_calls"].append((cls, path))
        if cls == "Function":
            return world["objects_by_path"].get(path)
        return None

    def _load_package(name: str) -> None:
        # UE3 `LoadPackage` fatals on a name that is not a real .upk (that crash took the game
        # down at `WillowGame.ItemCardGFxObject:SetBottomStat`). The probe must never call it.
        world["load_package_calls"].append(name)
        raise AssertionError(f"load_package({name!r}) must never be used by the probe")

    unrealsdk.find_all = lambda name, exact=False: list(world["objects_by_class"].get(name, []))
    unrealsdk.find_object = _find_object
    unrealsdk.load_package = _load_package
    unrealsdk.make_struct = lambda name, **kwargs: FakeStructValue(kwargs)
    unrealsdk.construct_object = lambda *args, **kwargs: None
    unrealsdk.__version__ = "3.2.0-fake"
    unrealsdk.logging = types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

    hooks_module.Type = Type
    hooks_module.prevent_hooking_direct_calls = lambda: types.SimpleNamespace(
        __enter__=lambda self: None, __exit__=lambda self, *a: None
    )
    unrealsdk.hooks = hooks_module

    mods_base = types.ModuleType("mods_base")

    class _FakeOption:
        def __init__(self, *args, **kwargs) -> None:
            self.value = args[1] if len(args) > 1 else None

    class _FakeCommand:
        """Mirrors `mods_base`'s decorator: the name binds to the Command, not the function."""

        def __init__(self, name: str, **kwargs: Any) -> None:
            self.name = name
            self.func = None

        def __call__(self, func: Any) -> "_FakeCommand":
            self.func = func
            return self

        def add_argument(self, *args: Any, **kwargs: Any) -> None:
            return None

    mods_base.BoolOption = _FakeOption
    mods_base.SliderOption = _FakeOption
    mods_base.ButtonOption = _FakeOption
    mods_base.build_mod = lambda **kwargs: types.SimpleNamespace(**kwargs)
    mods_base.command = lambda *args, **kwargs: _FakeCommand(*args, **kwargs)
    mods_base.hook = lambda *args, **kwargs: (lambda fn: fn)
    mods_base.Game = types.SimpleNamespace(BL2="BL2")
    mods_base.MODS_DIR = REPO_ROOT / "sdk_mods"

    sys.modules["unrealsdk"] = unrealsdk
    sys.modules["unrealsdk.hooks"] = hooks_module
    sys.modules["mods_base"] = mods_base


# ---------------------------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------------------------


def run_checks() -> None:
    import melee_arsenal_probe
    from melee_arsenal_probe import mod_surface
    from melee_arsenal_probe.probe import assets, census, common, live, report, schema

    # The in-game surface must build against `mods_base` without touching the engine.
    check(melee_arsenal_probe.mod is not None, "mod_surface did not build a mod instance")
    check(len(mod_surface.ALL_COMMANDS) == 8, "console commands not registered")
    check(len(mod_surface.ALL_OPTIONS) == 3, "mod-menu options not registered")
    check(len(mod_surface.HOOKS) >= 1, "census hooks were not built")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        common.OUT_DIR = out
        common.LOG_PATH = out / "probe.log"
        report.JSON_PATH = out / "ground_truth.json"
        report.REPORT_PATH = out / "GROUND_TRUTH.md"

        payload = report.run_sections()
        check(not payload["errors"], f"probe sections raised: {payload['errors']}")
        check(
            set(payload["sections_run"]) == {"schema", "catalogue", "live", "census"},
            f"unexpected sections: {payload['sections_run']}",
        )

        # --- functions -----------------------------------------------------------------------
        functions = payload["schema"]["functions"]
        check(functions["weapon_init_internal_pre"]["exists"], "existing UFunction not detected")
        check(
            "ReturnValue" in functions["weapon_init_internal_pre"]["param_names"],
            "function parameters not read",
        )
        check(not functions["weapon_start_zoom_pre"]["exists"], "missing UFunction reported as present")
        # Regression: `SetBottomStat` does not exist on `ItemCardGFxObject`. Resolving it must
        # report `exists: False` and must not reach for `load_package` (a UE3 fatal error).
        check(
            not functions["card_set_bottom_stat_pre"]["exists"],
            "a non-existent UFunction was reported as present",
        )
        check(
            functions["card_set_bottom_stat_pre"].get("note", "") != "",
            "missing UFunction lacks the deletable-code note",
        )
        top_stat = functions["card_set_top_stat_pre"]
        check(
            top_stat.get("enum_params", {}).get("CompareArrow") == [
                "ECS_None",
                "ECS_Better",
                "ECS_Equal",
                "ECS_Worse",
            ],
            "enum-valued parameter not resolved",
        )

        # --- classes / enums / structs --------------------------------------------------------
        classes = payload["schema"]["classes"]
        weapon_props = classes["weapon"]["property_names"]
        check("DefinitionData" in weapon_props and "Mesh" in weapon_props, "class schema incomplete")
        check(classes["weapon"].get("cdo_defaults") is not None, "class defaults not dumped")
        check(
            payload["schema"]["enums"].get("FakeGame.ECompareStat", {}).get("values", {}).get(1)
            == "ECS_Better",
            "enum literals not indexed by value",
        )
        check(
            "BaseValueConstant" in (payload["schema"]["structs"].get("AttributeInitializationData", {}).get("fields") or {}),
            "struct layout not dumped",
        )

        # --- asset catalogues -----------------------------------------------------------------
        catalogues = payload["catalogue"]["catalogues"]
        ps_paths = [row["path"] for row in catalogues["particle_systems"]["matched"]]
        check(
            any("Part_ExplosiveExplosion_Medium" in path for path in ps_paths),
            "explosion ParticleSystem not catalogued",
        )
        check(
            not any("Leaves" in path for path in ps_paths),
            "keyword filter let an unrelated ParticleSystem through",
        )
        ak_paths = [row["path"] for row in catalogues["ak_events"]["matched"]]
        check(any("Ak_Play_Exp_Elemental_Explosive_MED" in path for path in ak_paths), "AkEvent not catalogued")

        # --- behaviour graph / weights --------------------------------------------------------
        behaviours = payload["catalogue"]["behaviours"]
        donors = behaviours["explosion_donors"]
        check(donors["count"] == 1, f"expected 1 Behavior_Explode donor, got {donors['count']}")
        check(
            donors["donors"][0]["firing_mode"].endswith("Flakker_Child"),
            "wrong Behavior_Explode donor located",
        )
        init_defs = {row["name"]: row for row in behaviours["attribute_init_defs"]}
        check(
            init_defs.get("Weight_4_Rare", {}).get("multiplier_base_value_constant") == 1.0,
            "init-def multiplier not resolved",
        )

        # --- pools ----------------------------------------------------------------------------
        pools = {row["path"]: row for row in payload["catalogue"]["pools"]["pools"]}
        check(len(pools) == 2, "ItemPoolDefinition catalogue incomplete")
        pistol = next(row for path, row in pools.items() if "Pool_Melee_Pistol" in path)
        check(pistol["entry_count"] == 2, "pool entries not dumped")
        check(
            pistol["entries"][0]["probability"]["InitializationDefinitionName"] == "Weight_4_Rare",
            "pool probability init-def not captured",
        )

        # --- live -----------------------------------------------------------------------------
        live_data = payload["live"]
        check(live_data["player"]["found"], "local player not located")
        check(live_data["player"]["map"] == "SouthernShelf_P", "map name not read")
        check(live_data["held_weapon"]["InstantHitDamage"] == 1234.5, "held weapon stats not read")
        arms = live_data["arms"]
        check(arms["found"], "Pawn.Arms not located")
        node_names = arms["anim_slots"]["node_names"]
        check(node_names == ["WeaponAction", "CustomAnimSlot"], f"unexpected anim slots: {node_names}")
        check(arms["bladed_melee_available"], "Bladed_Melee sequence not discovered")
        check(
            [name for name in arms["melee_sequences"] if name == "Reload"] == [],
            "non-melee sequence leaked into the melee list",
        )
        materials = live_data["held_weapon"]["mesh"]["materials"]
        check(materials[0]["is_struct"] is False, "bare material misreported as a struct")
        check(materials[1]["is_struct"] is True, "{Material, ...} entry misreported as bare")
        check(
            live_data["world_actors"]["WillowAIPawn"]["count"] == 1,
            "world actor census incomplete",
        )

        # --- census ---------------------------------------------------------------------------
        plan = census.describe_plan()
        check(plan["total_hooks"] >= 2, "census armed no hooks against the fake SDK")
        check("frame_tick" in plan["missing"], "unresolvable hooks not reported as missing")

        # --- outputs --------------------------------------------------------------------------
        report.write(payload)
        written = json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))
        check(written["schema_version"] == common.SCHEMA_VERSION, "JSON schema tag missing")
        markdown = (out / "GROUND_TRUTH.md").read_text(encoding="utf-8")
        check("Cleanup ledger" in markdown, "review sheet is missing the cleanup ledger")
        check(str(report.TARGETS[0]["section"]) in markdown, "review sheet omits probe targets")

        # --- section isolation ----------------------------------------------------------------
        broken = report.run_sections(["schema"])
        check(broken["errors"] == {}, "single-section run reported errors")

        _ = assets, live, schema  # imported for coverage of every module


def main() -> int:
    world = build_world()
    install_fake_sdk(world)
    run_checks()
    check(
        not world["load_package_calls"],
        f"probe called load_package, which fatals BL2: {world['load_package_calls']}",
    )
    check(
        any(call[0] == "Function" for call in world["find_object_calls"]),
        "probe resolved functions without find_object",
    )
    if FAILURES:
        print("probe self-test FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("probe self-test passed: every probe section produced the expected ground truth.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
