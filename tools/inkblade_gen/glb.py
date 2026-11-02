"""GLB assembly: non-indexed flat-shaded primitive export plus a portable part merger.

The export mirrors what the shipped kits contain: one `<part>.glb` per part with a `__ROOT` node,
`MOUNT` / `SOCKET_*` empties, `FACE_FRONT` / `FACE_BACK` instances for bifacial parts, an embedded
base-colour atlas, and two materials (`SURFACE` and `ELEMENT_ACCENT`) that drive the runtime's
element recolouring.

Pure standard library + the already-encoded PNG bytes.
"""
from __future__ import annotations

import copy
import json
import struct
from typing import Any, Iterable, Sequence

from .geometry import Face, Part
from .uv import PartLayout, Vec2, Vec3, newell_normal

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

SURFACE_CHANNEL = "SURFACE"
ACCENT_CHANNEL = "ELEMENT_ACCENT"


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------
def read_glb(payload: bytes) -> tuple[dict[str, Any], bytearray]:
    magic, version, total = struct.unpack_from("<III", payload)
    if magic != GLB_MAGIC or version != 2 or total != len(payload):
        raise ValueError("Not a valid glTF 2.0 binary")
    offset = 12
    doc: dict[str, Any] | None = None
    binary = bytearray()
    while offset < len(payload):
        chunk_length, chunk_type = struct.unpack_from("<II", payload, offset)
        chunk = payload[offset + 8 : offset + 8 + chunk_length]
        offset += 8 + chunk_length
        if chunk_type == CHUNK_JSON:
            doc = json.loads(chunk)
        elif chunk_type == CHUNK_BIN:
            binary = bytearray(chunk)
    if doc is None:
        raise ValueError("GLB has no JSON chunk")
    return doc, binary


def write_glb(doc: dict[str, Any], binary: bytes | bytearray) -> bytes:
    data = bytearray(binary)
    doc = copy.deepcopy(doc)
    doc["buffers"] = [{"byteLength": len(data)}]
    body = json.dumps(doc, separators=(",", ":")).encode()
    body += b" " * ((-len(body)) % 4)
    data += b"\0" * ((-len(data)) % 4)
    header = struct.pack("<III", GLB_MAGIC, 2, 28 + len(body) + len(data))
    return (
        header
        + struct.pack("<II", len(body), CHUNK_JSON)
        + body
        + struct.pack("<II", len(data), CHUNK_BIN)
        + bytes(data)
    )


# ---------------------------------------------------------------------------
# Triangulation (ear clipping — concave cleaver silhouettes and the swept guard need it)
# ---------------------------------------------------------------------------
def triangulate(uv: Sequence[Vec2]) -> list[tuple[int, int, int]]:
    count = len(uv)
    ids = list(range(count))
    out: list[tuple[int, int, int]] = []
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(uv, uv[1:] + uv[:1]))
    sign = 1 if area >= 0 else -1

    def cross(a: Vec2, b: Vec2, c: Vec2) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def inside(p: Vec2, a: Vec2, b: Vec2, c: Vec2) -> bool:
        return all(sign * value >= -1e-12 for value in (cross(a, b, p), cross(b, c, p), cross(c, a, p)))

    while len(ids) > 3:
        found = False
        for j, b in enumerate(ids):
            a = ids[j - 1]
            c = ids[(j + 1) % len(ids)]
            if sign * cross(uv[a], uv[b], uv[c]) <= 1e-12:
                continue
            if any(inside(uv[k], uv[a], uv[b], uv[c]) for k in ids if k not in (a, b, c)):
                continue
            out.append((a, b, c))
            ids.pop(j)
            found = True
            break
        if not found:
            # Collinear bevel corners can safely be dropped without adding a diagonal seam.
            j = min(
                range(len(ids)),
                key=lambda i: abs(cross(uv[ids[i - 1]], uv[ids[i]], uv[ids[(i + 1) % len(ids)]])),
            )
            if abs(cross(uv[ids[j - 1]], uv[ids[j]], uv[ids[(j + 1) % len(ids)]])) < 1e-10:
                ids.pop(j)
            else:
                raise ValueError("Could not triangulate painted face")
    if len(ids) == 3:
        out.append((ids[0], ids[1], ids[2]))
    return out


def _to_gltf(vertex: Vec3) -> Vec3:
    """Blender working space `(x, y, z)` -> glTF `(x, z, -y)` (Y-up, right-handed)."""
    return (vertex[0], vertex[2], -vertex[1])


# ---------------------------------------------------------------------------
# Part export
# ---------------------------------------------------------------------------
def build_part_doc(
    part: Part,
    layout: PartLayout,
    png_bytes: bytes,
    *,
    generator: str = "Inkblade staged generator",
    revision: str = "final",
) -> tuple[dict[str, Any], bytearray]:
    """Build the glTF document + binary chunk for one part (no clipping, no merging)."""
    channels = {SURFACE_CHANNEL: [], ACCENT_CHANNEL: []}
    for face, uvs in zip(part.faces, layout.face_uvs):
        channel = ACCENT_CHANNEL if face.tag == "accent" else SURFACE_CHANNEL
        channels[channel].append((face, uvs))

    doc: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": generator},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {
                "name": f"{part.part_id}__ROOT",
                "children": [],
                "extras": {
                    "part_id": part.part_id,
                    "slot": part.slot,
                    "manufacturer": part.manufacturer,
                    "revision": revision,
                    "bifacial_fit": part.bifacial,
                },
            }
        ],
        "meshes": [],
        "materials": [],
        "textures": [{"sampler": 0, "source": 0}],
        "images": [{"name": f"{part.part_id}_basecolor", "mimeType": "image/png", "bufferView": 0}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "accessors": [],
        "bufferViews": [],
    }
    binary = bytearray()

    def append_view(blob: bytes, target: int | None = None) -> int:
        binary.extend(b"\0" * ((-len(binary)) % 4))
        view: dict[str, Any] = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(blob)}
        if target is not None:
            view["target"] = target
        doc["bufferViews"].append(view)
        binary.extend(blob)
        return len(doc["bufferViews"]) - 1

    append_view(png_bytes)  # bufferView 0 == the atlas

    def attribute(rows: list[tuple[float, ...]], kind: str, components: int, bounds: bool = False) -> int:
        flat = [value for row in rows for value in row]
        view = append_view(struct.pack("<" + "f" * len(flat), *flat), target=34962)
        accessor: dict[str, Any] = {
            "bufferView": view,
            "componentType": 5126,
            "count": len(rows),
            "type": kind,
        }
        if bounds:
            accessor["min"] = [min(row[i] for row in rows) for i in range(components)]
            accessor["max"] = [max(row[i] for row in rows) for i in range(components)]
        doc["accessors"].append(accessor)
        return len(doc["accessors"]) - 1

    primitives: list[dict[str, Any]] = []
    for channel in (SURFACE_CHANNEL, ACCENT_CHANNEL):
        positions: list[Vec3] = []
        normals: list[Vec3] = []
        texcoords: list[Vec2] = []
        for face, uvs in channels[channel]:
            xyz = [_to_gltf(v) for v in face.verts]
            normal = newell_normal(xyz)
            for tri in triangulate(uvs):
                positions.extend(xyz[i] for i in tri)
                normals.extend([normal] * 3)
                texcoords.extend(uvs[i] for i in tri)
        if not positions:
            continue
        materials_index = 0 if channel == SURFACE_CHANNEL else 1
        primitives.append(
            {
                "attributes": {
                    "POSITION": attribute(positions, "VEC3", 3, True),
                    "NORMAL": attribute(normals, "VEC3", 3),
                    "TEXCOORD_0": attribute(texcoords, "VEC2", 2),
                },
                "material": materials_index,
                "mode": 4,
            }
        )

    doc["meshes"] = [{"name": part.part_id, "primitives": primitives}]

    base_material: dict[str, Any] = {
        "name": f"{part.part_id}__SURFACE",
        "pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0},
            "metallicFactor": 0.0,
            "roughnessFactor": 0.86,
        },
        "doubleSided": False,
        "extras": {"channel": SURFACE_CHANNEL},
    }
    accent_material: dict[str, Any] = {
        "name": f"{part.part_id}__ELEMENT_ACCENT",
        "pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0},
            "metallicFactor": 0.0,
            "roughnessFactor": 0.86,
        },
        "emissiveFactor": [0.08, 0.08, 0.08],
        "extras": {"channel": ACCENT_CHANNEL},
    }
    doc["materials"] = [base_material, accent_material]

    root = doc["nodes"][0]
    if part.bifacial:
        for name, scale, shift in (
            ("FACE_FRONT", [1, 1, 1], part.fit_inner_shift_m),
            ("FACE_BACK", [1, 1, -1], -part.fit_inner_shift_m),
        ):
            root["children"].append(len(doc["nodes"]))
            doc["nodes"].append(
                {"name": name, "mesh": 0, "scale": scale, "translation": [0.0, 0.0, round(float(shift), 7)]}
            )
    else:
        root["children"].append(len(doc["nodes"]))
        doc["nodes"].append({"name": part.part_id, "mesh": 0})

    for socket_name, position in part.sockets_blender.items():
        x, y, z = _to_gltf(position)
        root["children"].append(len(doc["nodes"]))
        doc["nodes"].append(
            {
                "name": socket_name,
                "translation": [round(x, 7), round(y, 7), round(z, 7)],
                "extras": {"socket": True, "socket_name": socket_name},
            }
        )
    return doc, binary


def build_part_glb(
    part: Part,
    layout: PartLayout,
    png_bytes: bytes,
    *,
    generator: str = "Inkblade staged generator",
    revision: str = "final",
) -> bytes:
    doc, binary = build_part_doc(part, layout, png_bytes, generator=generator, revision=revision)
    return write_glb(doc, binary)


# ---------------------------------------------------------------------------
# Combination assembly (port of the R3/R4 portable merger)
# ---------------------------------------------------------------------------
MERGED_KEYS = (
    "nodes",
    "meshes",
    "materials",
    "textures",
    "images",
    "samplers",
    "accessors",
    "bufferViews",
)


def merge_glbs(pieces: Iterable[tuple[dict[str, Any], bytes | bytearray, Sequence[float], str]]) -> bytes:
    """Merge `(doc, binary, root_translation, label)` pieces into one self-contained GLB."""
    doc: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "Inkblade staged assembler"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
    }
    for key in MERGED_KEYS:
        doc[key] = []
    # Node 0 is the assembly root; every piece's own root becomes its direct child.
    doc["nodes"].append({"name": "INKBLADE_ASSEMBLY", "children": []})
    data = bytearray()

    for source, blob, offset, label in pieces:
        source = copy.deepcopy(source)
        base = {key: len(doc[key]) for key in MERGED_KEYS}
        data.extend(b"\0" * ((-len(data)) % 4))
        byte_offset = len(data)
        for view in source.get("bufferViews", []):
            view["buffer"] = 0
            view["byteOffset"] = view.get("byteOffset", 0) + byte_offset
        for accessor in source.get("accessors", []):
            if "bufferView" in accessor:
                accessor["bufferView"] += base["bufferViews"]
        for image in source.get("images", []):
            if "bufferView" in image:
                image["bufferView"] += base["bufferViews"]
        for texture in source.get("textures", []):
            if "source" in texture:
                texture["source"] += base["images"]
            if "sampler" in texture:
                texture["sampler"] += base["samplers"]
        for material in source.get("materials", []):
            for block in (material, material.get("pbrMetallicRoughness", {})):
                for key, value in block.items():
                    if key.endswith("Texture") and isinstance(value, dict) and "index" in value:
                        value["index"] += base["textures"]
        for mesh in source.get("meshes", []):
            for primitive in mesh["primitives"]:
                if "material" in primitive:
                    primitive["material"] += base["materials"]
                if "indices" in primitive:
                    primitive["indices"] += base["accessors"]
                primitive["attributes"] = {
                    key: value + base["accessors"] for key, value in primitive["attributes"].items()
                }
        for node in source.get("nodes", []):
            if "mesh" in node:
                node["mesh"] += base["meshes"]
            if "children" in node:
                node["children"] = [i + base["nodes"] for i in node["children"]]
        roots = source["scenes"][source.get("scene", 0)]["nodes"]
        for root_index in roots:
            node = source["nodes"][root_index]
            node["translation"] = [
                a + b for a, b in zip(node.get("translation", [0, 0, 0]), offset)
            ]
            node.setdefault("extras", {})["assembly_part"] = label
            doc["nodes"][0]["children"].append(root_index + base["nodes"])
        for key in MERGED_KEYS:
            doc[key].extend(source.get(key, []))
        data.extend(blob)

    return write_glb(doc, data)


def part_root_children(doc: dict[str, Any]) -> list[int]:
    """Root node children indices (used by tests and the assembler)."""
    roots = doc["scenes"][doc.get("scene", 0)]["nodes"]
    return list(doc["nodes"][roots[0]].get("children", []))


def node_name(doc: dict[str, Any], index: int) -> str:
    return str(doc["nodes"][index].get("name", ""))
