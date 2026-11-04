"""Exact BL2 (`ArVer=832 / LicenseeVer=46`) UPK binary codec for `USkeletalMesh` and `UTexture2D`.

Verified byte-for-byte against `Ribs Arsenal 1.4` cooked packages (`MaliwanBarrelMeshes.upk`,
`BriarMaterialMeshes.upk`, `MaliwanBarrelTextures.upk`) and `corporateweapon/bl2-part-pipeline`.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import math
import struct
from typing import Any, Callable

from .archive import ExportEntry, UE3PackageArchive

UPK_TAG = 0x9E2A83C1
AR_VER = 832
LIC_VER = 46
ENGINE_VER = 1712575
COOKER_VER = 134
PACKAGE_FLAGS_COOKED = 0x80880009
NAME_FLAGS_DEFAULT = 0x0007001000000000
OBJ_FLAGS_TOP_LEVEL = 0x0007000400000000
OBJ_FLAGS_SUB_OBJECT = 0x0007000000000000

TEX_HASH_A = bytes.fromhex("6d37b5801b4f3d349d543a999e799abd")
TEX_HASH_B = bytes.fromhex("6d37b5801b4f3d349d543a999d799abd")

ATOMIC_STRUCTS = frozenset(
    {
        "Vector",
        "Rotator",
        "Guid",
        "Color",
        "LinearColor",
        "Quat",
        "Vector2D",
        "Vector4",
        "Plane",
        "Matrix",
        "Box",
        "IntPoint",
    }
)

STRUCT_ARRAY_PROPS = frozenset(
    {
        "LODInfo",
        "TriangleSortSettings",
    }
)


def _pack_fstring(text: str) -> bytes:
    raw = text.encode("latin1") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def _det_guid(seed: str) -> bytes:
    return hashlib.md5(seed.encode("utf-8")).digest()


def pack_normal(nx: float, ny: float, nz: float, w_sign: float = 1.0) -> int:
    """Pack unit vector `(nx, ny, nz)` + handedness `w_sign` into UE3 `FPackedNormal` (`uint32`).

    UE3 unpacks `(byte / 127.5) - 1.0`, so `127/128` -> `~0.0`, `0` -> `-1.0`, `255` -> `+1.0`.
    """
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length > 1e-8:
        nx /= length
        ny /= length
        nz /= length
    else:
        nx, ny, nz = 0.0, 0.0, 1.0

    def _b(v: float) -> int:
        return max(0, min(255, int(round((v + 1.0) * 127.5))))

    bx = _b(nx)
    by = _b(ny)
    bz = _b(nz)
    bw = 255 if w_sign >= 0.0 else 0
    return bx | (by << 8) | (bz << 16) | (bw << 24)


def unpack_normal(packed: int) -> tuple[float, float, float, float]:
    bx = packed & 0xFF
    by = (packed >> 8) & 0xFF
    bz = (packed >> 16) & 0xFF
    bw = (packed >> 24) & 0xFF
    return (
        (bx / 127.5) - 1.0,
        (by / 127.5) - 1.0,
        (bz / 127.5) - 1.0,
        (bw / 127.5) - 1.0,
    )


def quat_rotate_vec(
    q: tuple[float, float, float, float],
    v: tuple[float, float, float],
    conjugate: bool = False,
) -> tuple[float, float, float]:
    """Rotate 3D vector `v` by quaternion `(qx, qy, qz, qw)`."""
    qx, qy, qz, qw = q
    if conjugate:
        qx, qy, qz = -qx, -qy, -qz
    vx, vy, vz = v
    # t = 2 * cross(q.xyz, v)
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    # v' = v + qw * t + cross(q.xyz, t)
    rx = vx + qw * tx + (qy * tz - qz * ty)
    ry = vy + qw * ty + (qz * tx - qx * tz)
    rz = vz + qw * tz + (qx * ty - qy * tx)
    return (rx, ry, rz)


# Tagged UProperty Reader & FName Remapper


@dataclass
class TaggedProp:
    name: str
    name_num: int
    prop_type: str
    prop_type_num: int
    array_index: int
    struct_name: str = ""
    struct_num: int = 0
    enum_name: str = ""
    enum_num: int = 0
    bool_val: bool = False
    byte_enum_val: tuple[str, int] | None = None
    byte_raw: int = 0
    name_val: tuple[str, int] | None = None
    raw_payload: bytes = b""
    nested_struct_props: list[TaggedProp] | None = None
    struct_array_items: list[list[TaggedProp]] | None = None


def parse_tagged_props(
    buf: bytes | memoryview,
    pos: int,
    name_lookup: Callable[[int], str],
) -> tuple[list[TaggedProp], int]:
    """Parse a `None`-terminated UE3 tagged property list starting at `pos`."""
    props: list[TaggedProp] = []
    while True:
        ni, nn = struct.unpack_from("<ii", buf, pos)
        pos += 8
        pname = name_lookup(ni)
        if pname == "None":
            break
        ti, tn, psize, arr_idx = struct.unpack_from("<iiii", buf, pos)
        pos += 16
        ptype = name_lookup(ti)

        if ptype == "BoolProperty":
            bval = bool(buf[pos])
            pos += 1
            props.append(
                TaggedProp(
                    name=pname,
                    name_num=nn,
                    prop_type=ptype,
                    prop_type_num=tn,
                    array_index=arr_idx,
                    bool_val=bval,
                )
            )
        elif ptype == "ByteProperty":
            ei, en = struct.unpack_from("<ii", buf, pos)
            pos += 8
            ename = name_lookup(ei)
            if ename == "None":
                braw = int(buf[pos])
                pos += 1
                props.append(
                    TaggedProp(
                        name=pname,
                        name_num=nn,
                        prop_type=ptype,
                        prop_type_num=tn,
                        array_index=arr_idx,
                        enum_name="None",
                        enum_num=en,
                        byte_raw=braw,
                    )
                )
            else:
                vi, vn = struct.unpack_from("<ii", buf, pos)
                pos += 8
                props.append(
                    TaggedProp(
                        name=pname,
                        name_num=nn,
                        prop_type=ptype,
                        prop_type_num=tn,
                        array_index=arr_idx,
                        enum_name=ename,
                        enum_num=en,
                        byte_enum_val=(name_lookup(vi), vn),
                    )
                )
        elif ptype == "NameProperty":
            vi, vn = struct.unpack_from("<ii", buf, pos)
            pos += 8
            props.append(
                TaggedProp(
                    name=pname,
                    name_num=nn,
                    prop_type=ptype,
                    prop_type_num=tn,
                    array_index=arr_idx,
                    name_val=(name_lookup(vi), vn),
                )
            )
        elif ptype == "StructProperty":
            si, sn = struct.unpack_from("<ii", buf, pos)
            pos += 8
            sname = name_lookup(si)
            raw = bytes(buf[pos : pos + psize])
            pos += psize
            nested: list[TaggedProp] | None = None
            if sname not in ATOMIC_STRUCTS:
                nested, end_sub = parse_tagged_props(raw, 0, name_lookup)
                if end_sub != len(raw):
                    nested = None
            props.append(
                TaggedProp(
                    name=pname,
                    name_num=nn,
                    prop_type=ptype,
                    prop_type_num=tn,
                    array_index=arr_idx,
                    struct_name=sname,
                    struct_num=sn,
                    raw_payload=raw,
                    nested_struct_props=nested,
                )
            )
        elif ptype == "ArrayProperty":
            raw = bytes(buf[pos : pos + psize])
            pos += psize
            items: list[list[TaggedProp]] | None = None
            if pname in STRUCT_ARRAY_PROPS and len(raw) >= 4:
                (cnt,) = struct.unpack_from("<i", raw, 0)
                sub_pos = 4
                parsed_items: list[list[TaggedProp]] = []
                ok = True
                try:
                    for _ in range(cnt):
                        elem_props, sub_pos = parse_tagged_props(raw, sub_pos, name_lookup)
                        parsed_items.append(elem_props)
                    if sub_pos == len(raw):
                        items = parsed_items
                except Exception:
                    ok = False
                if not ok:
                    items = None
            props.append(
                TaggedProp(
                    name=pname,
                    name_num=nn,
                    prop_type=ptype,
                    prop_type_num=tn,
                    array_index=arr_idx,
                    raw_payload=raw,
                    struct_array_items=items,
                )
            )
        else:
            raw = bytes(buf[pos : pos + psize])
            pos += psize
            props.append(
                TaggedProp(
                    name=pname,
                    name_num=nn,
                    prop_type=ptype,
                    prop_type_num=tn,
                    array_index=arr_idx,
                    raw_payload=raw,
                )
            )
    return props, pos


def serialize_tagged_props(
    props: list[TaggedProp],
    name_index_of: Callable[[str], int],
) -> bytes:
    """Serialize a list of `TaggedProp`s + trailing `None` with remapped FName indices."""
    out = bytearray()
    for p in props:
        ni = name_index_of(p.name)
        ti = name_index_of(p.prop_type)
        if p.prop_type == "BoolProperty":
            out.extend(struct.pack("<iiiiiiB", ni, p.name_num, ti, p.prop_type_num, 0, p.array_index, int(p.bool_val)))
        elif p.prop_type == "ByteProperty":
            ei = name_index_of(p.enum_name)
            if p.enum_name == "None" or p.byte_enum_val is None:
                out.extend(
                    struct.pack(
                        "<iiiiiiiiB",
                        ni,
                        p.name_num,
                        ti,
                        p.prop_type_num,
                        1,
                        p.array_index,
                        ei,
                        p.enum_num,
                        p.byte_raw,
                    )
                )
            else:
                vi = name_index_of(p.byte_enum_val[0])
                out.extend(
                    struct.pack(
                        "<iiiiiiiiii",
                        ni,
                        p.name_num,
                        ti,
                        p.prop_type_num,
                        8,
                        p.array_index,
                        ei,
                        p.enum_num,
                        vi,
                        p.byte_enum_val[1],
                    )
                )
        elif p.prop_type == "NameProperty":
            assert p.name_val is not None
            vi = name_index_of(p.name_val[0])
            out.extend(
                struct.pack(
                    "<iiiiiiii",
                    ni,
                    p.name_num,
                    ti,
                    p.prop_type_num,
                    8,
                    p.array_index,
                    vi,
                    p.name_val[1],
                )
            )
        elif p.prop_type == "StructProperty":
            si = name_index_of(p.struct_name)
            payload = (
                serialize_tagged_props(p.nested_struct_props, name_index_of)
                if p.nested_struct_props is not None
                else p.raw_payload
            )
            out.extend(
                struct.pack(
                    "<iiiiiiii",
                    ni,
                    p.name_num,
                    ti,
                    p.prop_type_num,
                    len(payload),
                    p.array_index,
                    si,
                    p.struct_num,
                )
            )
            out.extend(payload)
        elif p.prop_type == "ArrayProperty":
            if p.struct_array_items is not None:
                buf = bytearray(struct.pack("<i", len(p.struct_array_items)))
                for elem in p.struct_array_items:
                    buf.extend(serialize_tagged_props(elem, name_index_of))
                payload = bytes(buf)
            else:
                payload = p.raw_payload
            out.extend(
                struct.pack(
                    "<iiiiii",
                    ni,
                    p.name_num,
                    ti,
                    p.prop_type_num,
                    len(payload),
                    p.array_index,
                )
            )
            out.extend(payload)
        else:
            out.extend(
                struct.pack(
                    "<iiiiii",
                    ni,
                    p.name_num,
                    ti,
                    p.prop_type_num,
                    len(p.raw_payload),
                    p.array_index,
                )
            )
            out.extend(p.raw_payload)

    out.extend(struct.pack("<ii", name_index_of("None"), 0))
    return bytes(out)


def make_box_sphere_bounds_prop(
    origin: tuple[float, float, float],
    extent: tuple[float, float, float],
    radius: float,
) -> TaggedProp:
    return TaggedProp(
        name="ReferencePoseBounds",
        name_num=0,
        prop_type="StructProperty",
        prop_type_num=0,
        array_index=0,
        struct_name="BoxSphereBounds",
        struct_num=0,
        nested_struct_props=[
            TaggedProp(
                name="Origin",
                name_num=0,
                prop_type="StructProperty",
                prop_type_num=0,
                array_index=0,
                struct_name="Vector",
                struct_num=0,
                raw_payload=struct.pack("<3f", *origin),
            ),
            TaggedProp(
                name="BoxExtent",
                name_num=0,
                prop_type="StructProperty",
                prop_type_num=0,
                array_index=0,
                struct_name="Vector",
                struct_num=0,
                raw_payload=struct.pack("<3f", *extent),
            ),
            TaggedProp(
                name="SphereRadius",
                name_num=0,
                prop_type="FloatProperty",
                prop_type_num=0,
                array_index=0,
                raw_payload=struct.pack("<f", radius),
            ),
        ],
    )


# Cooked UPK PackageBuilder (`ArVer=832 / LicenseeVer=46`)


@dataclass
class BuilderImport:
    class_package: str
    class_name: str
    outer_index: int
    object_name: str
    object_name_num: int = 0


@dataclass
class BuilderExport:
    class_index: int
    super_index: int
    outer_index: int
    object_name: str
    object_name_num: int
    archetype_index: int
    object_flags: int
    # Either static `data` or `data_fn(serial_offset) -> bytes` for absolute-offset fixups (`RawPointIndices`, `Texture2D` mips)
    data: bytes = b""
    data_fn: Callable[[int], bytes] | None = None


class PackageBuilder:
    """Two-pass offset-aware cooked UPK writer (`ArVer=832 / LicenseeVer=46`)."""

    def __init__(self, package_guid_seed: str = "MeleeArsenal") -> None:
        self._guid = _det_guid(package_guid_seed)
        self._name_to_idx: dict[str, int] = {}
        self.names: list[str] = []
        self.imports: list[BuilderImport] = []
        self.exports: list[BuilderExport] = []
        self.name_index("None")

    def name_index(self, name: str) -> int:
        idx = self._name_to_idx.get(name)
        if idx is not None:
            return idx
        idx = len(self.names)
        self.names.append(name)
        self._name_to_idx[name] = idx
        return idx

    def add_import(
        self,
        class_package: str,
        class_name: str,
        outer_index: int,
        object_name: str,
        object_name_num: int = 0,
    ) -> int:
        self.name_index(class_package)
        self.name_index(class_name)
        self.name_index(object_name)
        self.imports.append(
            BuilderImport(
                class_package=class_package,
                class_name=class_name,
                outer_index=outer_index,
                object_name=object_name,
                object_name_num=object_name_num,
            )
        )
        return -len(self.imports)

    def add_export(
        self,
        class_index: int,
        outer_index: int,
        object_name: str,
        object_flags: int,
        data: bytes = b"",
        data_fn: Callable[[int], bytes] | None = None,
        object_name_num: int = 0,
    ) -> int:
        self.name_index(object_name)
        self.exports.append(
            BuilderExport(
                class_index=class_index,
                super_index=0,
                outer_index=outer_index,
                object_name=object_name,
                object_name_num=object_name_num,
                archetype_index=0,
                object_flags=object_flags,
                data=data,
                data_fn=data_fn,
            )
        )
        return len(self.exports)

    def build_bytes(self) -> bytes:
        # Serialize NameTable
        name_buf = bytearray()
        for nm in self.names:
            name_buf.extend(_pack_fstring(nm))
            name_buf.extend(struct.pack("<Q", NAME_FLAGS_DEFAULT))

        # Serialize ImportTable (28B each)
        imp_buf = bytearray()
        for imp in self.imports:
            imp_buf.extend(
                struct.pack(
                    "<iiiiiii",
                    self.name_index(imp.class_package),
                    0,
                    self.name_index(imp.class_name),
                    0,
                    imp.outer_index,
                    self.name_index(imp.object_name),
                    imp.object_name_num,
                )
            )

        # Summary fixed size with PackageGroup="None" and 1 generation:
        # 12 + (4+5) + 48 + 16 + 4 + 12 + 16 + 4 = 121 bytes? Let's compute exact length dynamically!
        dummy_summary = self._pack_summary(0, 0, 0, 0, 0)
        name_offset = len(dummy_summary)
        import_offset = name_offset + len(name_buf)
        export_offset = import_offset + len(imp_buf)
        export_table_size = len(self.exports) * 68  # net_cnt=0 -> 24+8+16+16+4 = 68 bytes per export
        depends_offset = export_offset + export_table_size
        depends_size = len(self.exports) * 4
        headers_size = depends_offset + depends_size

        # Pass 1: resolve exact export payloads & offsets
        cur_offset = headers_size
        resolved_payloads: list[tuple[int, bytes]] = []
        for exp in self.exports:
            payload = exp.data_fn(cur_offset) if exp.data_fn is not None else exp.data
            resolved_payloads.append((cur_offset, payload))
            cur_offset += len(payload)

        # Serialize ExportTable
        exp_buf = bytearray()
        for exp, (ser_off, payload) in zip(self.exports, resolved_payloads, strict=False):
            exp_buf.extend(
                struct.pack(
                    "<iiiiiiQiiii",
                    exp.class_index,
                    exp.super_index,
                    exp.outer_index,
                    self.name_index(exp.object_name),
                    exp.object_name_num,
                    exp.archetype_index,
                    exp.object_flags,
                    len(payload),
                    ser_off,
                    0,  # ExportFlags = 0 (never EF_ForcedExport!)
                    0,  # NetObjectCount = 0
                )
            )
            exp_buf.extend(b"\x00" * 16)  # PackageGuid
            exp_buf.extend(struct.pack("<i", 0))  # U3unk6C

        summary_bytes = self._pack_summary(
            headers_size=headers_size,
            name_offset=name_offset,
            export_offset=export_offset,
            import_offset=import_offset,
            depends_offset=depends_offset,
        )
        assert len(summary_bytes) == name_offset

        out = bytearray()
        out.extend(summary_bytes)
        out.extend(name_buf)
        out.extend(imp_buf)
        out.extend(exp_buf)
        out.extend(b"\x00" * depends_size)
        assert len(out) == headers_size
        for _, payload in resolved_payloads:
            out.extend(payload)
        return bytes(out)

    def _pack_summary(
        self,
        headers_size: int,
        name_offset: int,
        export_offset: int,
        import_offset: int,
        depends_offset: int,
    ) -> bytes:
        version = (LIC_VER << 16) | AR_VER
        buf = bytearray()
        buf.extend(struct.pack("<III", UPK_TAG, version, headers_size))
        buf.extend(_pack_fstring("None"))
        buf.extend(
            struct.pack(
                "<IIIIIIIIIIII",
                PACKAGE_FLAGS_COOKED,
                len(self.names),
                name_offset,
                len(self.exports),
                export_offset,
                len(self.imports),
                import_offset,
                depends_offset,
                headers_size,  # f38 (ImportExportGuidsOffset)
                0,  # f3c
                0,  # f40
                0,  # unk38
            )
        )
        buf.extend(self._guid)
        buf.extend(struct.pack("<i", 1))  # 1 Generation
        buf.extend(struct.pack("<iii", len(self.exports), len(self.names), 0))
        pkg_source = struct.unpack_from("<I", self._guid, 0)[0]
        # EngineVersion, CookerVersion, CompressionFlags=0, CompressedChunks.Num=0,
        # PackageSource, AdditionalPackagesToCook.Num=0, TextureAllocations.TextureTypes.Num=0 (129B total)
        buf.extend(struct.pack("<iiiiIii", ENGINE_VER, COOKER_VER, 0, 0, pkg_source, 0, 0))
        return bytes(buf)


# USkeletalMesh Binary Parser, Fragment Bounds & Custom Geometry Builder


@dataclass
class MeshBone:
    name: str
    name_num: int
    flags: int
    orientation: tuple[float, float, float, float]  # (qx, qy, qz, qw)
    position: tuple[float, float, float]  # (px, py, pz)
    num_children: int
    parent_index: int
    bone_color: int


@dataclass
class SkelMeshSection:
    material_index: int
    chunk_index: int
    base_index: int
    num_triangles: int
    triangle_sorting: int


@dataclass
class SkelMeshChunk:
    base_vertex_index: int
    rigid_vertices_raw: bytes  # count(0)
    soft_vertices_raw: bytes  # count(0)
    bone_map: list[int]  # uint16 indices into RefSkeleton
    num_rigid_vertices: int
    num_soft_vertices: int
    max_bone_influences: int


@dataclass
class GpuSkinVertex:
    tangent_x: int  # uint32 FPackedNormal
    tangent_z: int  # uint32 FPackedNormal (normal + binormal sign in W)
    influence_bones: tuple[int, int, int, int]  # 4 uint8 indices into chunk.bone_map
    influence_weights: tuple[int, int, int, int]  # 4 uint8 summing to 255
    position: tuple[float, float, float]
    uvs: tuple[tuple[float, float], ...]  # len == num_tex_coords


@dataclass
class SocketSubObject:
    object_name: str
    object_name_num: int
    props: list[TaggedProp]


@dataclass
class SkeletalMeshBinary:
    net_index: int
    props: list[TaggedProp]
    sockets: list[SocketSubObject]
    bounds_origin: tuple[float, float, float]
    bounds_extent: tuple[float, float, float]
    bounds_radius: float
    materials: list[int]
    mesh_origin: tuple[float, float, float]
    rot_origin: tuple[int, int, int]
    ref_skeleton: list[MeshBone]
    skeletal_depth: int
    # LOD 0
    sections: list[SkelMeshSection]
    index_data_type_size: int
    indices: list[int]
    active_bone_indices: list[int]
    chunks: list[SkelMeshChunk]
    lod_size: int
    num_vertices: int
    required_bones: list[int]
    num_tex_coords: int
    mesh_extension: tuple[float, float, float]
    mesh_origin_lod: tuple[float, float, float]
    use_full_precision_uvs: int
    use_packed_position: int
    gpu_vertices: list[GpuSkinVertex]
    extra_lods_raw: bytes  # empty if we set LOD count = 1
    name_index_map: list[tuple[str, int, int]]
    tail_zeros: tuple[int, int, int, int]
    streaming_factors: list[float]

    @classmethod
    def from_archive_export(cls, ar: UE3PackageArchive, exp: ExportEntry) -> SkeletalMeshBinary:
        eb = memoryview(ar.read_export_bytes(exp))
        def name_of(idx):
            return ar.names[idx].name
        pos = 0
        (net_index,) = struct.unpack_from("<i", eb, pos)
        pos += 4
        props, pos = parse_tagged_props(eb, pos, name_of)

        # Extract Socket sub-objects if `Sockets` prop references exports in `ar`
        sockets: list[SocketSubObject] = []
        for pr in props:
            if pr.name == "Sockets" and pr.prop_type == "ArrayProperty" and len(pr.raw_payload) >= 4:
                (scnt,) = struct.unpack_from("<i", pr.raw_payload, 0)
                srefs = struct.unpack_from(f"<{scnt}i", pr.raw_payload, 4)
                for sref in srefs:
                    if sref > 0 and (sref - 1) < len(ar.exports):
                        sexp = ar.exports[sref - 1]
                        seb = memoryview(ar.read_export_bytes(sexp))
                        sprops, _ = parse_tagged_props(seb, 4, name_of)
                        sockets.append(
                            SocketSubObject(
                                object_name=sexp.object_name,
                                object_name_num=sexp.object_name_num,
                                props=sprops,
                            )
                        )

        # Keep only LODInfo[0] on `LODInfo` property and strip `bHasVertexColors`
        filtered_props: list[TaggedProp] = []
        for pr in props:
            if pr.name == "bHasVertexColors":
                continue
            if pr.name == "LODInfo" and pr.struct_array_items and len(pr.struct_array_items) > 1:
                pr.struct_array_items = [pr.struct_array_items[0]]
            filtered_props.append(pr)
        has_vertex_colors = any(pr.name == "bHasVertexColors" and pr.bool_val for pr in props)
        props = filtered_props

        bx, by, bz, ex, ey, ez, brad = struct.unpack_from("<7f", eb, pos)
        pos += 28
        (mat_cnt,) = struct.unpack_from("<i", eb, pos)
        pos += 4
        mats = list(struct.unpack_from(f"<{mat_cnt}i", eb, pos))
        pos += 4 * mat_cnt

        mesh_origin = struct.unpack_from("<3f", eb, pos)
        pos += 12
        rot_origin = struct.unpack_from("<3i", eb, pos)
        pos += 12

        (bone_cnt,) = struct.unpack_from("<i", eb, pos)
        pos += 4
        bones: list[MeshBone] = []
        for _ in range(bone_cnt):
            b_ni, b_nn, b_fl, qx, qy, qz, qw, px, py, pz, nchild, par_idx, bcol = struct.unpack_from(
                "<iiI4f3fiiI", eb, pos
            )
            pos += 52
            bones.append(
                MeshBone(
                    name=name_of(b_ni),
                    name_num=b_nn,
                    flags=b_fl,
                    orientation=(qx, qy, qz, qw),
                    position=(px, py, pz),
                    num_children=nchild,
                    parent_index=par_idx,
                    bone_color=bcol,
                )
            )
        (skel_depth,) = struct.unpack_from("<i", eb, pos)
        pos += 4

        (lod_cnt,) = struct.unpack_from("<i", eb, pos)
        pos += 4
        if lod_cnt < 1:
            raise ValueError("SkeletalMesh has 0 LODModels")

        # Parse LOD 0
        (sec_cnt,) = struct.unpack_from("<i", eb, pos)
        pos += 4
        sections: list[SkelMeshSection] = []
        for _ in range(sec_cnt):
            mi, ci, bi, ntri, tsort = struct.unpack_from("<hhiiB", eb, pos)
            pos += 13
            sections.append(SkelMeshSection(mi, ci, bi, ntri, tsort))

        # FMultiSizeIndexContainer in BL2 ArVer=832: NeedsCPUAccess(int32), DataTypeSize(uint8), elem_size(int32), count(int32) -> 13 bytes `<iBii`
        idx_f0, idx_dts, idx_esize, idx_cnt = struct.unpack_from("<iBii", eb, pos)
        pos += 13
        if idx_dts == 2 and idx_esize == 2:
            indices = list(struct.unpack_from(f"<{idx_cnt}H", eb, pos))
            pos += 2 * idx_cnt
        elif idx_dts == 4 and idx_esize == 4:
            indices = list(struct.unpack_from(f"<{idx_cnt}I", eb, pos))
            pos += 4 * idx_cnt
        else:
            raise ValueError(f"Unexpected index container header: f0={idx_f0} dts={idx_dts} esz={idx_esize}")

        (act_cnt,) = struct.unpack_from("<i", eb, pos)
        pos += 4
        active_bones = list(struct.unpack_from(f"<{act_cnt}h", eb, pos))
        pos += 2 * act_cnt

        (chunk_cnt,) = struct.unpack_from("<i", eb, pos)
        pos += 4
        chunks: list[SkelMeshChunk] = []
        for _ in range(chunk_cnt):
            (base_vi,) = struct.unpack_from("<i", eb, pos)
            pos += 4
            (rigid_cnt,) = struct.unpack_from("<i", eb, pos)
            # RigidSkinVertex in UE3 is 48 bytes if present
            rigid_bytes = bytes(eb[pos : pos + 4 + rigid_cnt * 48])
            pos += 4 + rigid_cnt * 48
            (soft_cnt,) = struct.unpack_from("<i", eb, pos)
            soft_bytes = bytes(eb[pos : pos + 4 + soft_cnt * 60])
            pos += 4 + soft_cnt * 60
            (bmap_cnt,) = struct.unpack_from("<i", eb, pos)
            pos += 4
            bmap = list(struct.unpack_from(f"<{bmap_cnt}H", eb, pos))
            pos += 2 * bmap_cnt
            n_rigid, n_soft, max_inf = struct.unpack_from("<iii", eb, pos)
            pos += 12
            chunks.append(
                SkelMeshChunk(
                    base_vertex_index=base_vi,
                    rigid_vertices_raw=rigid_bytes,
                    soft_vertices_raw=soft_bytes,
                    bone_map=bmap,
                    num_rigid_vertices=n_rigid,
                    num_soft_vertices=n_soft,
                    max_bone_influences=max_inf,
                )
            )

        lod_size, num_verts, req_cnt = struct.unpack_from("<iii", eb, pos)
        pos += 12
        req_bones = list(eb[pos : pos + req_cnt])
        pos += req_cnt

        # RawPointIndices FUntypedBulkData
        rp_flags, rp_cnt, rp_disk, _rp_off = struct.unpack_from("<iiii", eb, pos)
        pos += 16
        if not (rp_flags & 0x20) and rp_disk > 0:
            pos += rp_disk

        (ntc,) = struct.unpack_from("<i", eb, pos)
        pos += 4

        # VertexBufferGPUSkin: NumTexCoords(i32), UseFullPrecisionUVs(i32), UsePackedPosition(i32), MeshExtension(3f), MeshOrigin(3f), elem_size(i32), count(i32)
        vb_ntc, use_fp_uv, use_pack_pos = struct.unpack_from("<iii", eb, pos)
        pos += 12
        m_ext = struct.unpack_from("<3f", eb, pos)
        pos += 12
        m_orig = struct.unpack_from("<3f", eb, pos)
        pos += 12
        v_stride, v_cnt = struct.unpack_from("<ii", eb, pos)
        pos += 8
        expected_stride_half = 28 + 4 * ntc
        expected_stride_full = 28 + 8 * ntc
        if vb_ntc != ntc or v_stride not in (expected_stride_half, expected_stride_full):
            raise ValueError(
                f"Unsupported GPU vertex format: ntc={ntc}/{vb_ntc}, stride={v_stride}, "
                f"full_uv={use_fp_uv}, packed_pos={use_pack_pos}"
            )
        uv_fmt = f"<{2 * ntc}f" if v_stride == expected_stride_full else f"<{2 * ntc}e"
        gpu_verts: list[GpuSkinVertex] = []
        for _ in range(v_cnt):
            tx, tz, b0, b1, b2, b3, w0, w1, w2, w3, vx, vy, vz = struct.unpack_from(
                "<II4B4B3f", eb, pos
            )
            uv_flat = struct.unpack_from(uv_fmt, eb, pos + 28)
            pos += v_stride
            uv_pairs = tuple((float(uv_flat[2 * k]), float(uv_flat[2 * k + 1])) for k in range(ntc))
            gpu_verts.append(
                GpuSkinVertex(
                    tangent_x=tx,
                    tangent_z=tz,
                    influence_bones=(b0, b1, b2, b3),
                    influence_weights=(w0, w1, w2, w3),
                    position=(vx, vy, vz),
                    uvs=uv_pairs,
                )
            )

        if has_vertex_colors and pos + 8 <= len(eb):
            cv_esz, cv_cnt = struct.unpack_from("<ii", eb, pos)
            pos += 8 + cv_esz * cv_cnt

        # Canonical NameIndexMap maps every RefSkeleton bone `(name, name_num) -> bone_index`,
        # and CachedStreamingTextureFactors always has `MAX_TEXCOORDS = 4` floats in BL2.
        name_idx_map: list[tuple[str, int, int]] = [
            (b.name, b.name_num, bi) for bi, b in enumerate(bones)
        ]
        sf_vals = [1.0, 1.0, 0.0, 0.0]
        if len(eb) >= 20:
            (last_sf_cnt,) = struct.unpack_from("<i", eb, len(eb) - 20)
            if last_sf_cnt == 4:
                sf_vals = list(struct.unpack_from("<4f", eb, len(eb) - 16))
        tail_zeros = (0, 0, 0, 0)

        return cls(
            net_index=net_index,
            props=props,
            sockets=sockets,
            bounds_origin=(bx, by, bz),
            bounds_extent=(ex, ey, ez),
            bounds_radius=brad,
            materials=[0 for _ in range(max(1, len(mats)))],
            mesh_origin=mesh_origin,
            rot_origin=rot_origin,
            ref_skeleton=bones,
            skeletal_depth=skel_depth,
            sections=sections,
            index_data_type_size=2,
            indices=indices,
            active_bone_indices=active_bones,
            chunks=chunks,
            lod_size=0,
            num_vertices=num_verts,
            required_bones=req_bones,
            num_tex_coords=ntc,
            mesh_extension=m_ext,
            mesh_origin_lod=m_orig,
            use_full_precision_uvs=0,
            use_packed_position=0,
            gpu_vertices=gpu_verts,
            extra_lods_raw=b"",
            name_index_map=name_idx_map,
            tail_zeros=tail_zeros,
            streaming_factors=sf_vals,
        )

    @staticmethod
    def _locate_tail_offset(eb: memoryview, bone_count: int, min_pos: int) -> int:
        total = len(eb)
        # Try `sf_cnt` in 0..8 at the very end of `eb`
        for sf_cnt in range(0, 16):
            cand = total - (4 + 4 * sf_cnt + 16 + 4 + 12 * bone_count)
            if cand < min_pos:
                continue
            (mc,) = struct.unpack_from("<i", eb, cand)
            (sfc,) = struct.unpack_from("<i", eb, total - 4 - 4 * sf_cnt)
            if mc == bone_count and sfc == sf_cnt:
                return cand
        raise ValueError(f"Could not locate USkeletalMesh NameIndexMap tail (bone_count={bone_count})")

    def bone_to_root_transforms(
        self,
    ) -> list[tuple[tuple[float, float, float], tuple[float, float, float, float]]]:
        """Compute `(root_pos, root_quat)` in component/Root space for each bone in `ref_skeleton`.

        Note: UE3 stores bone 0's quaternion conjugated `(qx, qy, qz, -qw)` vs child bones,
        or in standard UE3 `ComposeTransforms`: let's verify against `RefSkeleton` so bone 0 (`Root`)
        at `(0,0,0)` with `(0,0,0,1)` / `(0,0,0,-1)` is identity and child `WeaponOffset` rotates cleanly!
        """
        out: list[tuple[tuple[float, float, float], tuple[float, float, float, float]]] = []
        for idx, b in enumerate(self.ref_skeleton):
            qx, qy, qz, qw = b.orientation
            # Normalize quaternion; in UE3 FMeshBone, non-root bone orientations use conjugated Y/Z/W convention:
            # specifically `quat_rotate_vec((qx, qy, qz, -qw), local_v)` transforms bone-local to parent!
            # Wait: let's check both `SG_TestMesh` and `SMG_BriarTestMesh` in tests!
            local_q = (qx, qy, qz, -qw) if idx > 0 else (qx, qy, qz, qw)
            qlen = math.sqrt(sum(c * c for c in local_q))
            if qlen > 1e-8:
                local_q = (local_q[0] / qlen, local_q[1] / qlen, local_q[2] / qlen, local_q[3] / qlen)
            if idx == 0 or b.parent_index < 0 or b.parent_index >= idx:
                out.append((b.position, local_q))
            else:
                p_pos, p_quat = out[b.parent_index]
                rot_p = quat_rotate_vec(p_quat, b.position)
                w_pos = (p_pos[0] + rot_p[0], p_pos[1] + rot_p[1], p_pos[2] + rot_p[2])
                # Hamilton product p_quat * local_q
                ax, ay, az, aw = p_quat
                bx, by, bz, bw = local_q
                w_quat = (
                    aw * bx + ax * bw + ay * bz - az * by,
                    aw * by - ax * bz + ay * bw + az * bx,
                    aw * bz + ax * by - ay * bx + az * bw,
                    aw * bw - ax * bx - ay * by - az * bz,
                )
                out.append((w_pos, w_quat))
        return out

    def chunk_for_vertex(self, vert_idx: int) -> SkelMeshChunk:
        chosen = self.chunks[0]
        for ch in self.chunks:
            if vert_idx >= ch.base_vertex_index:
                chosen = ch
            else:
                break
        return chosen

    def compute_fragment_bounds(self, start_index: int, num_triangles: int) -> dict[str, Any]:
        """Compute both raw-vertex and Root-transformed 3D bounding boxes for a Gestalt triangle range."""
        idx_slice = self.indices[start_index : start_index + num_triangles * 3]
        if not idx_slice:
            return {"vertex_count": 0, "triangle_count": 0}
        unique_v = sorted(set(idx_slice))
        bone_tf = self.bone_to_root_transforms()

        raw_pts: list[tuple[float, float, float]] = []
        root_pts: list[tuple[float, float, float]] = []
        bones_used: set[str] = set()

        for vi in unique_v:
            gv = self.gpu_vertices[vi]
            raw_pts.append(gv.position)
            ch = self.chunk_for_vertex(vi)
            # Dominant bone influence
            b_slot = gv.influence_bones[0]
            skel_idx = ch.bone_map[b_slot] if b_slot < len(ch.bone_map) else 0
            if 0 <= skel_idx < len(self.ref_skeleton):
                bones_used.add(self.ref_skeleton[skel_idx].name)
                b_pos, b_quat = bone_tf[skel_idx]
                rp = quat_rotate_vec(b_quat, gv.position)
                root_pts.append((b_pos[0] + rp[0], b_pos[1] + rp[1], b_pos[2] + rp[2]))
            else:
                root_pts.append(gv.position)

        def _box(pts: list[tuple[float, float, float]]) -> dict[str, list[float]]:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            zs = [p[2] for p in pts]
            mn = [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)]
            mx = [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)]
            ct = [round((mn[i] + mx[i]) * 0.5, 4) for i in range(3)]
            sz = [round(mx[i] - mn[i], 4) for i in range(3)]
            return {"min": mn, "max": mx, "center": ct, "size": sz}

        return {
            "vertex_count": len(unique_v),
            "triangle_count": num_triangles,
            "bones_used": sorted(bones_used),
            "raw_vertex_bounds": _box(raw_pts),
            "root_space_bounds": _box(root_pts),
        }

    def to_package_bytes(self, mesh_name: str, guid_seed: str | None = None) -> bytes:
        """Serialize this `SkeletalMeshBinary` (plus its `SkeletalMeshSocket` sub-objects) as a standalone cooked `.upk`."""
        pb = PackageBuilder(package_guid_seed=guid_seed or f"SkelMesh_{mesh_name}")
        imp_engine = pb.add_import("Core", "Package", 0, "Engine")
        imp_skel_cls = pb.add_import("Core", "Class", imp_engine, "SkeletalMesh")
        imp_sock_cls = (
            pb.add_import("Core", "Class", imp_engine, "SkeletalMeshSocket")
            if self.sockets
            else 0
        )

        # Pre-register all FNames referenced by tagged props, sockets, bones, and NameIndexMap
        # so `PackageBuilder.names` is frozen before `build_bytes()` computes `headers_size`!
        socket_exp_indices = list(range(2, 2 + len(self.sockets)))
        patched_props: list[TaggedProp] = []
        has_ref_bounds = False
        for pr in self.props:
            if pr.name == "Sockets":
                raw_socks = struct.pack(
                    f"<i{len(socket_exp_indices)}i",
                    len(socket_exp_indices),
                    *socket_exp_indices,
                )
                patched_props.append(
                    TaggedProp(
                        name="Sockets",
                        name_num=pr.name_num,
                        prop_type="ArrayProperty",
                        prop_type_num=pr.prop_type_num,
                        array_index=0,
                        raw_payload=raw_socks,
                    )
                )
            elif pr.name == "ReferencePoseBounds":
                has_ref_bounds = True
                patched_props.append(
                    make_box_sphere_bounds_prop(
                        self.bounds_origin, self.bounds_extent, self.bounds_radius
                    )
                )
            elif pr.name == "LODInfo" and pr.struct_array_items:
                nsec = max(1, len(self.sections))
                lod0_props: list[TaggedProp] = []
                for sp in pr.struct_array_items[0]:
                    if sp.name == "bEnableShadowCasting":
                        lod0_props.append(
                            TaggedProp(
                                name=sp.name,
                                name_num=sp.name_num,
                                prop_type=sp.prop_type,
                                prop_type_num=sp.prop_type_num,
                                array_index=sp.array_index,
                                raw_payload=struct.pack("<i", nsec) + (b"\x01" * nsec),
                            )
                        )
                    elif sp.name == "TriangleSortSettings" and sp.struct_array_items:
                        trimmed = (sp.struct_array_items * nsec)[:nsec]
                        lod0_props.append(
                            TaggedProp(
                                name=sp.name,
                                name_num=sp.name_num,
                                prop_type=sp.prop_type,
                                prop_type_num=sp.prop_type_num,
                                array_index=sp.array_index,
                                struct_array_items=trimmed,
                            )
                        )
                    else:
                        lod0_props.append(sp)
                patched_props.append(
                    TaggedProp(
                        name=pr.name,
                        name_num=pr.name_num,
                        prop_type=pr.prop_type,
                        prop_type_num=pr.prop_type_num,
                        array_index=pr.array_index,
                        struct_array_items=[lod0_props],
                    )
                )
            else:
                patched_props.append(pr)
        if not has_ref_bounds:
            patched_props.insert(
                0,
                make_box_sphere_bounds_prop(
                    self.bounds_origin, self.bounds_extent, self.bounds_radius
                ),
            )

        prop_bytes = serialize_tagged_props(patched_props, pb.name_index)
        for b in self.ref_skeleton:
            pb.name_index(b.name)
        for m_name, _, _ in self.name_index_map:
            pb.name_index(m_name)

        socket_payloads: list[tuple[str, int, bytes]] = []
        for s in self.sockets:
            s_bytes = struct.pack("<i", -1) + serialize_tagged_props(s.props, pb.name_index)
            socket_payloads.append((s.object_name, s.object_name_num, s_bytes))

        def _build_skel_bytes(serial_offset: int) -> bytes:
            buf = bytearray()
            buf.extend(struct.pack("<i", -1))  # NetIndex = -1
            buf.extend(prop_bytes)
            buf.extend(
                struct.pack(
                    "<7f",
                    *self.bounds_origin,
                    *self.bounds_extent,
                    self.bounds_radius,
                )
            )
            mats = [0]
            buf.extend(struct.pack(f"<i{len(mats)}i", len(mats), *mats))
            buf.extend(struct.pack("<3f", *self.mesh_origin))
            buf.extend(struct.pack("<3i", *self.rot_origin))
            buf.extend(struct.pack("<i", len(self.ref_skeleton)))
            for b in self.ref_skeleton:
                buf.extend(
                    struct.pack(
                        "<iiI4f3fiiI",
                        pb.name_index(b.name),
                        b.name_num,
                        b.flags,
                        *b.orientation,
                        *b.position,
                        b.num_children,
                        b.parent_index,
                        b.bone_color,
                    )
                )
            buf.extend(struct.pack("<i", self.skeletal_depth))

            # 1 LODModel
            buf.extend(struct.pack("<i", 1))
            buf.extend(struct.pack("<i", len(self.sections)))
            for sec in self.sections:
                buf.extend(
                    struct.pack(
                        "<hhiiB",
                        sec.material_index,
                        sec.chunk_index,
                        sec.base_index,
                        sec.num_triangles,
                        sec.triangle_sorting,
                    )
                )

            # IndexBuffer (`uint16`, 13-byte `<iBii` header)
            if len(self.gpu_vertices) > 65535:
                raise ValueError(f"GPU vertex count {len(self.gpu_vertices)} exceeds uint16 limit 65535")
            buf.extend(struct.pack("<iBii", 1, 2, 2, len(self.indices)))
            if self.indices:
                buf.extend(struct.pack(f"<{len(self.indices)}H", *self.indices))

            buf.extend(struct.pack(f"<i{len(self.active_bone_indices)}h", len(self.active_bone_indices), *self.active_bone_indices))

            buf.extend(struct.pack("<i", len(self.chunks)))
            for ch in self.chunks:
                buf.extend(struct.pack("<i", ch.base_vertex_index))
                buf.extend(struct.pack("<i", 0))  # RigidVertices = 0
                buf.extend(struct.pack("<i", 0))  # SoftVertices = 0
                buf.extend(struct.pack(f"<i{len(ch.bone_map)}H", len(ch.bone_map), *ch.bone_map))
                buf.extend(
                    struct.pack(
                        "<iii",
                        ch.num_rigid_vertices,
                        ch.num_soft_vertices,
                        ch.max_bone_influences,
                    )
                )

            buf.extend(struct.pack("<iii", 0, len(self.gpu_vertices), len(self.required_bones)))
            buf.extend(bytes(self.required_bones))

            # RawPointIndices empty inline bulk data (`flags=0, count=0, disk=0, abs_offset_after_header`)
            rp_abs_after = serial_offset + len(buf) + 16
            buf.extend(struct.pack("<iiii", 0, 0, 0, rp_abs_after))

            ntc = self.num_tex_coords
            v_stride = 28 + 4 * ntc
            buf.extend(struct.pack("<i", ntc))
            buf.extend(struct.pack("<iii", ntc, 0, 0))
            buf.extend(struct.pack("<3f", *self.mesh_extension))
            buf.extend(struct.pack("<3f", *self.mesh_origin_lod))
            buf.extend(struct.pack("<ii", v_stride, len(self.gpu_vertices)))
            uv_fmt = f"<{2 * ntc}e"
            for gv in self.gpu_vertices:
                buf.extend(
                    struct.pack(
                        "<II4B4B3f",
                        gv.tangent_x,
                        gv.tangent_z,
                        *gv.influence_bones,
                        *gv.influence_weights,
                        *gv.position,
                    )
                )
                flat_uvs: list[float] = []
                for k in range(ntc):
                    u_pair = gv.uvs[k] if k < len(gv.uvs) else gv.uvs[0]
                    flat_uvs.extend((u_pair[0], u_pair[1]))
                buf.extend(struct.pack(uv_fmt, *flat_uvs))

            # VertexInfluences = 0
            buf.extend(struct.pack("<i", 0))

            # NameIndexMap tail + 4 zeros + 4 CachedStreamingTextureFactors floats
            buf.extend(struct.pack("<i", len(self.name_index_map)))
            for m_name, m_num, m_bi in self.name_index_map:
                buf.extend(struct.pack("<iii", pb.name_index(m_name), m_num, m_bi))
            buf.extend(struct.pack("<4i", 0, 0, 0, 0))
            sf = list(self.streaming_factors[:4])
            while len(sf) < 4:
                sf.append(0.0)
            buf.extend(struct.pack("<i4f", 4, *sf))
            return bytes(buf)

        skel_exp_idx = pb.add_export(
            class_index=imp_skel_cls,
            outer_index=0,
            object_name=mesh_name,
            object_flags=OBJ_FLAGS_TOP_LEVEL,
            data_fn=_build_skel_bytes,
        )
        assert skel_exp_idx == 1
        for s_name, s_num, s_bytes in socket_payloads:
            pb.add_export(
                class_index=imp_sock_cls,
                outer_index=skel_exp_idx,
                object_name=s_name,
                object_name_num=s_num,
                object_flags=OBJ_FLAGS_SUB_OBJECT,
                data=s_bytes,
            )
        return pb.build_bytes()

    def strip_to_donor(self) -> None:
        """Replace `LOD0` geometry in place with a single zero-area triangle at `(0,0,0)` bound to Bone 0 (`Root`)."""
        ntc = max(1, self.num_tex_coords)
        tx = pack_normal(1.0, 0.0, 0.0, 1.0)
        tz = pack_normal(0.0, 0.0, 1.0, 1.0)
        zero_uvs = tuple((0.0, 0.0) for _ in range(ntc))
        dummy_v = GpuSkinVertex(
            tangent_x=tx,
            tangent_z=tz,
            influence_bones=(0, 0, 0, 0),
            influence_weights=(255, 0, 0, 0),
            position=(0.0, 0.0, 0.0),
            uvs=zero_uvs,
        )
        self.gpu_vertices = [dummy_v, dummy_v, dummy_v]
        self.num_vertices = 3
        self.indices = [0, 1, 2]
        self.sections = [
            SkelMeshSection(
                material_index=0,
                chunk_index=0,
                base_index=0,
                num_triangles=1,
                triangle_sorting=0,
            )
        ]
        all_bone_indices = list(range(len(self.ref_skeleton)))
        self.active_bone_indices = all_bone_indices
        self.required_bones = [i for i in all_bone_indices if i < 256]
        self.chunks = [
            SkelMeshChunk(
                base_vertex_index=0,
                rigid_vertices_raw=b"\x00\x00\x00\x00",
                soft_vertices_raw=b"\x00\x00\x00\x00",
                bone_map=[0],
                num_rigid_vertices=3,
                num_soft_vertices=0,
                max_bone_influences=1,
            )
        ]


@dataclass(frozen=True)
class CustomFragmentInput:
    """One custom Gestalt part fragment ready to pack into `SkeletalMeshBinary`."""

    name: str
    positions: list[tuple[float, float, float]]  # Weapon Root-space (or target bone-space) UU coords
    normals: list[tuple[float, float, float]]
    tangents: list[tuple[float, float, float, float]]  # (tx, ty, tz, handedness_sign)
    uvs: list[tuple[float, float]]
    triangles: list[tuple[int, int, int]]
    ref_bone_index: int = 0  # 0 = `Root`


@dataclass(frozen=True)
class PackedFragmentRange:
    name: str
    start_index: int
    num_triangles: int
    base_vertex: int
    num_vertices: int
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]


def replace_with_custom_fragments(
    mesh: SkeletalMeshBinary,
    fragments: list[CustomFragmentInput],
    keep_existing: bool = False,
) -> list[PackedFragmentRange]:
    """Pack `fragments` into `mesh` (`LOD0`, single `Section` `MaterialIndex=0`) and return Gestalt ranges."""
    ntc = max(1, mesh.num_tex_coords)
    if not keep_existing:
        gpu_verts: list[GpuSkinVertex] = []
        indices: list[int] = []
        bone_map: list[int] = [0]
    else:
        gpu_verts = list(mesh.gpu_vertices)
        indices = list(mesh.indices)
        bone_map = list(mesh.chunks[0].bone_map) if mesh.chunks else [0]
        if len(mesh.chunks) > 1:
            # Unify into 1 chunk if needed by remapping existing vertices' bone indices
            merged_bmap: list[int] = []
            bmap_lookup: dict[int, int] = {}
            new_existing: list[GpuSkinVertex] = []
            for vi, gv in enumerate(gpu_verts):
                ch = mesh.chunk_for_vertex(vi)
                remapped_b: list[int] = []
                for bi_local, wt in zip(gv.influence_bones, gv.influence_weights, strict=False):
                    skel_b = ch.bone_map[bi_local] if wt > 0 and bi_local < len(ch.bone_map) else 0
                    if skel_b not in bmap_lookup:
                        bmap_lookup[skel_b] = len(merged_bmap)
                        merged_bmap.append(skel_b)
                    remapped_b.append(bmap_lookup[skel_b] if wt > 0 else 0)
                new_existing.append(
                    GpuSkinVertex(
                        tangent_x=gv.tangent_x,
                        tangent_z=gv.tangent_z,
                        influence_bones=(remapped_b[0], remapped_b[1], remapped_b[2], remapped_b[3]),
                        influence_weights=gv.influence_weights,
                        position=gv.position,
                        uvs=gv.uvs,
                    )
                )
            gpu_verts = new_existing
            bone_map = merged_bmap or [0]

    def _bmap_slot(skel_bone_idx: int) -> int:
        try:
            return bone_map.index(skel_bone_idx)
        except ValueError:
            slot = len(bone_map)
            bone_map.append(skel_bone_idx)
            return slot

    ranges: list[PackedFragmentRange] = []
    for frag in fragments:
        if not frag.positions or not frag.triangles:
            raise ValueError(f"Fragment {frag.name!r} has empty geometry")
        bslot = _bmap_slot(frag.ref_bone_index)
        base_v = len(gpu_verts)
        start_i = len(indices)

        xs = [p[0] for p in frag.positions]
        ys = [p[1] for p in frag.positions]
        zs = [p[2] for p in frag.positions]
        bmin = (min(xs), min(ys), min(zs))
        bmax = (max(xs), max(ys), max(zs))

        for pos, nrm, tan, uv in zip(frag.positions, frag.normals, frag.tangents, frag.uvs, strict=False):
            tx = pack_normal(tan[0], tan[1], tan[2], 1.0)
            tz = pack_normal(nrm[0], nrm[1], nrm[2], tan[3])
            dup_uvs = tuple((uv[0], uv[1]) for _ in range(ntc))
            gpu_verts.append(
                GpuSkinVertex(
                    tangent_x=tx,
                    tangent_z=tz,
                    influence_bones=(bslot, 0, 0, 0),
                    influence_weights=(255, 0, 0, 0),
                    position=pos,
                    uvs=dup_uvs,
                )
            )

        for i0, i1, i2 in frag.triangles:
            indices.extend((base_v + i0, base_v + i1, base_v + i2))

        ranges.append(
            PackedFragmentRange(
                name=frag.name,
                start_index=start_i,
                num_triangles=len(frag.triangles),
                base_vertex=base_v,
                num_vertices=len(frag.positions),
                bounds_min=bmin,
                bounds_max=bmax,
            )
        )

    if len(gpu_verts) > 65535:
        raise ValueError(f"Total GPU vertices ({len(gpu_verts)}) exceeds 65535 limit")

    total_tris = len(indices) // 3
    mesh.gpu_vertices = gpu_verts
    mesh.num_vertices = len(gpu_verts)
    mesh.indices = indices
    mesh.sections = [
        SkelMeshSection(
            material_index=0,
            chunk_index=0,
            base_index=0,
            num_triangles=total_tris,
            triangle_sorting=0,
        )
    ]
    all_bones = list(range(len(mesh.ref_skeleton)))
    mesh.active_bone_indices = all_bones
    mesh.required_bones = [i for i in all_bones if i < 256]
    mesh.chunks = [
        SkelMeshChunk(
            base_vertex_index=0,
            rigid_vertices_raw=b"\x00\x00\x00\x00",
            soft_vertices_raw=b"\x00\x00\x00\x00",
            bone_map=bone_map,
            num_rigid_vertices=len(gpu_verts),
            num_soft_vertices=0,
            max_bone_influences=1,
        )
    ]

    # Expand overall bounds with generous padding so frustum culling never clips long blades
    all_x = [gv.position[0] for gv in gpu_verts]
    all_y = [gv.position[1] for gv in gpu_verts]
    all_z = [gv.position[2] for gv in gpu_verts]
    mn = (min(all_x) - 10.0, min(all_y) - 10.0, min(all_z) - 10.0)
    mx = (max(all_x) + 10.0, max(all_y) + 10.0, max(all_z) + 10.0)
    origin = ((mn[0] + mx[0]) * 0.5, (mn[1] + mx[1]) * 0.5, (mn[2] + mx[2]) * 0.5)
    extent = ((mx[0] - mn[0]) * 0.5, (mx[1] - mn[1]) * 0.5, (mx[2] - mn[2]) * 0.5)
    radius = math.sqrt(extent[0] ** 2 + extent[1] ** 2 + extent[2] ** 2)
    mesh.bounds_origin = origin
    mesh.bounds_extent = extent
    mesh.bounds_radius = radius

    return ranges


# Cooked BL2 `Texture2D` UPK Builder (`PF_DXT1` / `PF_DXT5` + `TC_Normalmap`)


@dataclass(frozen=True)
class CompiledMip:
    width: int
    height: int
    blocks_bytes: bytes


@dataclass(frozen=True)
class Texture2DSpec:
    name: str
    width: int
    height: int
    pixel_format: str  # "PF_DXT1" or "PF_DXT5"
    mips: list[CompiledMip]
    is_normal_map: bool = False
    lod_group: str = "TEXTUREGROUP_Weapon"


def encode_pil_to_dxt_mips(
    image: Any,
    name: str,
    dxt_format: str = "DXT1",
    is_normal_map: bool = False,
) -> Texture2DSpec:
    """Convert a `PIL.Image` (power-of-two dimensions) into a full `DXT1`/`DXT5` mip chain."""
    from PIL import Image

    w, h = image.size
    if w <= 0 or h <= 0 or (w & (w - 1)) != 0 or (h & (h - 1)) != 0:
        raise ValueError(f"Texture {name!r} dimensions {w}x{h} must be powers of two")

    rgba = image.convert("RGBA")
    pf_name = "PF_DXT5" if dxt_format.upper() == "DXT5" else "PF_DXT1"
    pil_fmt = "DXT5" if pf_name == "PF_DXT5" else "DXT1"
    block_bytes = 16 if pf_name == "PF_DXT5" else 8

    mips: list[CompiledMip] = []
    cw, ch = w, h
    cur_im = rgba
    while True:
        # Pillow DDS encoder expects >= 4x4 to emit a full DXT block cleanly
        enc_im = cur_im if (cw >= 4 and ch >= 4) else cur_im.resize((max(4, cw), max(4, ch)), Image.Resampling.BOX)
        buf = io.BytesIO()
        enc_im.save(buf, format="DDS", pixel_format=pil_fmt)
        dds_raw = buf.getvalue()
        expected_len = max(1, (max(4, cw) // 4)) * max(1, (max(4, ch) // 4)) * block_bytes
        payload = dds_raw[128 : 128 + expected_len]
        if len(payload) != expected_len:
            raise ValueError(f"DDS payload mismatch for {name} mip {cw}x{ch}: {len(payload)} != {expected_len}")
        mips.append(CompiledMip(width=max(4, cw), height=max(4, ch), blocks_bytes=payload))
        if cw == 1 and ch == 1:
            break
        nw = max(1, cw // 2)
        nh = max(1, ch // 2)
        cur_im = cur_im.resize((nw, nh), Image.Resampling.BOX)
        cw, ch = nw, nh

    return Texture2DSpec(
        name=name,
        width=w,
        height=h,
        pixel_format=pf_name,
        mips=mips,
        is_normal_map=is_normal_map,
    )


def make_flat_normal_spec(name: str = "T_Inkblade_Normal_Flat") -> Texture2DSpec:
    """Create the exact 4x4 3-mip `TC_Normalmap` flat tangent-space normal (`MaliwanBarrelTextures.Normal_Flat`)."""
    flat_block = bytes.fromhex("1f841f8400000000")
    mips = [
        CompiledMip(width=4, height=4, blocks_bytes=flat_block),
        CompiledMip(width=4, height=4, blocks_bytes=flat_block),
        CompiledMip(width=4, height=4, blocks_bytes=flat_block),
    ]
    return Texture2DSpec(
        name=name,
        width=4,
        height=4,
        pixel_format="PF_DXT1",
        mips=mips,
        is_normal_map=True,
    )


def build_texture_upk(specs: list[Texture2DSpec], package_guid_seed: str = "MeleeArsenalTextures") -> bytes:
    """Build a standalone cooked BL2 `.upk` containing one or more `Texture2D` exports."""
    pb = PackageBuilder(package_guid_seed=package_guid_seed)
    imp_engine = pb.add_import("Core", "Package", 0, "Engine")
    imp_tex_cls = pb.add_import("Core", "Class", imp_engine, "Texture2D")

    for spec in specs:
        mip_tail_idx = max(0, len(spec.mips) - 1)
        lighting_guid = _det_guid(f"LightingGuid:{spec.name}")
        tfc_guid = _det_guid(f"TFCGuid:{spec.name}")

        tprops: list[TaggedProp] = [
            TaggedProp("SizeX", 0, "IntProperty", 0, 0, raw_payload=struct.pack("<i", spec.width)),
            TaggedProp("SizeY", 0, "IntProperty", 0, 0, raw_payload=struct.pack("<i", spec.height)),
            TaggedProp("OriginalSizeX", 0, "IntProperty", 0, 0, raw_payload=struct.pack("<i", spec.width)),
            TaggedProp("OriginalSizeY", 0, "IntProperty", 0, 0, raw_payload=struct.pack("<i", spec.height)),
            TaggedProp(
                "Format",
                0,
                "ByteProperty",
                0,
                0,
                enum_name="EPixelFormat",
                byte_enum_val=(spec.pixel_format, 0),
            ),
            TaggedProp("MipTailBaseIdx", 0, "IntProperty", 0, 0, raw_payload=struct.pack("<i", mip_tail_idx)),
            TaggedProp("NeverStream", 0, "BoolProperty", 0, 0, bool_val=True),
        ]
        if spec.is_normal_map:
            tprops.extend(
                [
                    TaggedProp("SRGB", 0, "BoolProperty", 0, 0, bool_val=False),
                    TaggedProp(
                        "CompressionSettings",
                        0,
                        "ByteProperty",
                        0,
                        0,
                        enum_name="TextureCompressionSettings",
                        byte_enum_val=("TC_Normalmap", 0),
                    ),
                    TaggedProp("UnpackMin", 0, "FloatProperty", 0, 0, raw_payload=struct.pack("<f", -1.0)),
                    TaggedProp("UnpackMin", 0, "FloatProperty", 0, 1, raw_payload=struct.pack("<f", -1.0)),
                    TaggedProp("UnpackMin", 0, "FloatProperty", 0, 2, raw_payload=struct.pack("<f", -1.0)),
                ]
            )
        tprops.extend(
            [
                TaggedProp(
                    "LODGroup",
                    0,
                    "ByteProperty",
                    0,
                    0,
                    enum_name="TextureGroup",
                    byte_enum_val=(spec.lod_group, 0),
                ),
                TaggedProp(
                    "LightingGuid",
                    0,
                    "StructProperty",
                    0,
                    0,
                    struct_name="Guid",
                    raw_payload=lighting_guid,
                ),
            ]
        )

        # Pre-serialize tagged properties so all FNames are registered in `pb` prior to `build_bytes()`
        prop_bytes = serialize_tagged_props(tprops, pb.name_index)

        def _make_tex_fn(
            sp: Texture2DSpec = spec,
            pbytes: bytes = prop_bytes,
            tguid: bytes = tfc_guid,
        ) -> Callable[[int], bytes]:
            def _fn(serial_offset: int) -> bytes:
                buf = bytearray()
                buf.extend(struct.pack("<i", -1))  # NetIndex = -1
                buf.extend(pbytes)
                buf.extend(TEX_HASH_A)
                buf.extend(struct.pack("<i", len(sp.mips)))
                for mip in sp.mips:
                    data_len = len(mip.blocks_bytes)
                    bulk_abs_off = serial_offset + len(buf) + 16
                    buf.extend(struct.pack("<iiii", 0, data_len, data_len, bulk_abs_off))
                    buf.extend(mip.blocks_bytes)
                    buf.extend(struct.pack("<ii", mip.width, mip.height))
                buf.extend(TEX_HASH_B)
                buf.extend(tguid)
                buf.extend(struct.pack("<i", 0))  # CachedPVRTCMips = 0
                return bytes(buf)

            return _fn

        pb.add_export(
            class_index=imp_tex_cls,
            outer_index=0,
            object_name=spec.name,
            object_flags=OBJ_FLAGS_TOP_LEVEL,
            data_fn=_make_tex_fn(),
        )

    return pb.build_bytes()
