"""Static tables used to turn raw UE3 bitfields and class names into readable data.

These are deliberately *best effort*.  Borderlands 2 is a shipped UE3 build: the
class/property/function flag definitions match the engine generation, but a few
names are version-specific and some engine flags are undocumented.  The raw
integer is always preserved next to the decoded list, so nothing is lost when
a name is missing or wrong.
"""

from __future__ import annotations

from typing import Iterable

# ---------------------------------------------------------------------------
# Function flags (UE3 / BL2)
# ---------------------------------------------------------------------------

FUNCTION_FLAGS: tuple[tuple[int, str], ...] = (
    (0x00000001, "Final"),
    (0x00000002, "Defined"),
    (0x00000004, "Iterator"),
    (0x00000008, "Latent"),
    (0x00000010, "PreOperator"),
    (0x00000020, "PostOperator"),
    (0x00000040, "Operator"),
    (0x00000080, "Static"),
    (0x00000100, "NoExport"),
    (0x00000200, "Const"),
    (0x00000400, "Native"),
    (0x00000800, "Event"),
    (0x00001000, "InEvent"),
    (0x00002000, "Public"),
    (0x00004000, "Private"),
    (0x00008000, "Protected"),
    (0x00010000, "Delegate"),
    (0x00020000, "Net"),
    (0x00040000, "NetReliable"),
    (0x00080000, "Simulated"),
    (0x00100000, "Exec"),
    (0x00200000, "Server"),
    (0x00400000, "Client"),
    (0x00800000, "RepNotify"),
)

FUNC_NATIVE = 0x00000400
FUNC_STATIC = 0x00000080
FUNC_EVENT = 0x00000800
FUNC_LATENT = 0x00000008
FUNC_EXEC = 0x00100000
FUNC_PRIVATE = 0x00004000
FUNC_PROTECTED = 0x00008000

# ---------------------------------------------------------------------------
# Property flags (UE3 / BL2)
# ---------------------------------------------------------------------------

PROPERTY_FLAGS: tuple[tuple[int, str], ...] = (
    (0x00000001, "Edit"),
    (0x00000002, "Const"),
    (0x00000004, "Input"),
    (0x00000008, "ExportObject"),
    (0x00000010, "Optional"),
    (0x00000020, "Net"),
    (0x00000040, "EditConst"),
    (0x00000080, "Parm"),
    (0x00000100, "OutParm"),
    (0x00000200, "SkipParm"),
    (0x00000400, "ReturnParm"),
    (0x00000800, "CoerceParm"),
    (0x00001000, "Native"),
    (0x00002000, "Transient"),
    (0x00004000, "Config"),
    (0x00008000, "Localized"),
    (0x00010000, "Travel"),
    (0x00020000, "EditInline"),
    (0x00040000, "EditConstArray"),
    (0x00080000, "DisplayName"),
    (0x00100000, "NoImport"),
    (0x00200000, "EditFixedSize"),
    (0x00400000, "NoClear"),
    (0x00800000, "EditFixedArray"),
)

PROP_PARAM = 0x00000080
PROP_OUT = 0x00000100
PROP_RETURN = 0x00000400
PROP_OPTIONAL = 0x00000010

# ---------------------------------------------------------------------------
# Struct flags (UE3)
# ---------------------------------------------------------------------------

STRUCT_FLAGS: tuple[tuple[int, str], ...] = (
    (0x00000001, "Transient"),
    (0x00000002, "Atomic"),
    (0x00000004, "Immutable"),
    (0x00000008, "AddStructs"),
    (0x00000010, "Native"),
    (0x00000020, "HasComponents"),
    (0x00000040, "HasObjectPointers"),
    (0x00000080, "HasScriptStruct"),
)

# ---------------------------------------------------------------------------
# Property type -> canonical value kind
# ---------------------------------------------------------------------------

PROPERTY_KIND: dict[str, str] = {
    "BoolProperty": "bool",
    "ByteProperty": "byte",
    "Int8Property": "int8",
    "Int16Property": "int16",
    "IntProperty": "int32",
    "Int64Property": "int64",
    "UInt16Property": "uint16",
    "UInt32Property": "uint32",
    "UInt64Property": "uint64",
    "FloatProperty": "float",
    "DoubleProperty": "double",
    "StrProperty": "string",
    "NameProperty": "name",
    "TextProperty": "text",
    "ObjectProperty": "object",
    "ClassProperty": "class",
    "ComponentProperty": "component",
    "InterfaceProperty": "interface",
    "WeakObjectProperty": "weak_object",
    "LazyObjectProperty": "lazy_object",
    "SoftObjectProperty": "soft_object",
    "SoftClassProperty": "soft_class",
    "StructProperty": "struct",
    "ArrayProperty": "array",
    "MapProperty": "map",
    "EnumProperty": "enum",
    "DelegateProperty": "delegate",
    "MulticastDelegateProperty": "multicast_delegate",
    "ByteAttributeProperty": "byte_attribute",
    "FloatAttributeProperty": "float_attribute",
    "GbxDefPtrProperty": "gbx_def_ptr",
    "GameDataHandleProperty": "game_data_handle",
    "GbxInlineStructProperty": "gbx_inline_struct",
}

# Object/class/interface properties all yield object references.
OBJECT_KINDS: frozenset[str] = frozenset(
    {
        "object",
        "class",
        "component",
        "interface",
        "weak_object",
        "lazy_object",
        "soft_object",
        "soft_class",
    }
)

# ---------------------------------------------------------------------------
# Asset classes
# ---------------------------------------------------------------------------

# Classes whose instances are (usually) loaded assets, as opposed to actors,
# components, definitions, or engine bookkeeping.  Matching is by exact class
# name; consumers can still query arbitrary classes from objects.json.
ASSET_CLASS_NAMES: frozenset[str] = frozenset(
    {
        "AkEvent",
        "AkSoundCue",
        "AnimSequence",
        "AnimSet",
        "AnimTree",
        "Archetype",
        "Behavior",
        "BehaviorSequence",
        "CameraAnim",
        "CubeMap",
        "DecalMaterial",
        "DecalComponent",
        "DistributionFloat",
        "DistributionVector",
        "FaceFXAnimSet",
        "Font",
        "GFxMovie",
        "GFxObject",
        "Level",
        "Material",
        "MaterialInstance",
        "MaterialInstanceConstant",
        "MaterialInstanceTimeVarying",
        "MorphTargetSet",
        "ParticleSystem",
        "PhysicsAsset",
        "Prefab",
        "Sequence",
        "ShaderCache",
        "SkeletalMesh",
        "SkeletalMeshSocket",
        "SoundCue",
        "SoundNodeWave",
        "SpeedTree",
        "StaticMesh",
        "StaticMeshCollectionActor",
        "SwfMovie",
        "Terrain",
        "Texture",
        "Texture2D",
        "Texture2DArray",
        "TextureCube",
        "TextureMovie",
        "TextureRenderTarget2D",
        "UIObject",
        "World",
    }
)

# ---------------------------------------------------------------------------
# Definition-like classes (skipped when picking live objects to dump)
# ---------------------------------------------------------------------------

DEFINITION_CLASS_NAMES: frozenset[str] = frozenset(
    {
        "Class",
        "BlueprintGeneratedClass",
        "Function",
        "ScriptStruct",
        "Struct",
        "Enum",
        "Const",
        "Package",
        "State",
        "MetaData",
        "Property",
        "Field",
    }
)

# Classes whose live instances are especially interesting to dump by default.
# Higher priority = earlier in the instance section (and more likely to fit in
# the configured object budget).
INSTANCE_PRIORITY: dict[str, int] = {
    "WillowPlayerController": 0,
    "WillowPlayerPawn": 0,
    "WillowWeapon": 0,
    "WillowInventoryManager": 0,
    "WillowWorldInfo": 0,
    "WillowGameInfo": 0,
    "WillowGameEngine": 0,
    "WillowGameViewportClient": 0,
    "WillowMissionTracker": 0,
    "WillowGlobals": 0,
    "WillowPawn": 1,
    "WillowAIPawn": 1,
    "WillowInventory": 1,
    "WillowItem": 1,
    "WillowUsableItem": 1,
    "WillowPickup": 1,
}

PRIORITY_CLASS_GLOBS: tuple[str, ...] = (
    "*GameEngine",
    "*GameInfo",
    "*WorldInfo",
    "*GameViewportClient",
    "*PlayerController",
    "*PlayerPawn",
    "*Globals",
    "*GearboxGlobals",
)

# ---------------------------------------------------------------------------
# Decoding helpers
# ---------------------------------------------------------------------------


def decode_flags(raw: int | None, table: Iterable[tuple[int, str]]) -> list[str]:
    if not isinstance(raw, int) or raw == 0:
        return []
    return [name for bit, name in table if raw & bit]


def property_kind(type_name: str) -> str:
    kind = PROPERTY_KIND.get(type_name)
    if kind is not None:
        return kind
    # Falls back to the class name, minus the 'Property' suffix, for the long
    # tail of engine/Gearbox property subclasses.
    if type_name.endswith("Property"):
        type_name = type_name[: -len("Property")]
    return type_name[:1].lower() + type_name[1:] if type_name else "unknown"


def is_property_class_name(class_name: str) -> bool:
    return class_name.endswith("Property")


def is_definition_class_name(class_name: str) -> bool:
    return (
        class_name in DEFINITION_CLASS_NAMES
        or class_name.endswith("Property")
        or class_name in {"Function", "ScriptStruct", "Enum", "Const", "Package", "Class"}
    )


def instance_priority(class_name: str, class_chain: Iterable[str]) -> int:
    priority = INSTANCE_PRIORITY.get(class_name)
    if priority is not None:
        return priority
    chain = tuple(class_chain)
    if "WillowPlayerController" in chain or "WillowPlayerPawn" in chain:
        return 0
    if "Actor" in chain:
        return 2
    if "Component" in chain:
        return 3
    return 4
