"""Self-contained pure-stdlib UE3 compressed `.upk` (`Startup.upk`) reader & `USkeletalMesh` donor extractor.

Runs inside Borderlands 2's embedded 32-bit CPython 3.14 with zero third-party dependencies.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import struct
from typing import Any, BinaryIO, Callable
import zlib

PACKAGE_FILE_TAG = 0x9E2A83C1
AR_VER = 832
LIC_VER = 46
ENGINE_VER = 1712575
COOKER_VER = 134
PACKAGE_FLAGS_COOKED = 0x80880009
NAME_FLAGS_DEFAULT = 0x0007001000000000
OBJ_FLAGS_TOP_LEVEL = 0x0007000400000000
OBJ_FLAGS_SUB_OBJECT = 0x0007000000000000

COMPRESS_ZLIB = 1
COMPRESS_LZO = 2
COMPRESS_FIND = 0xFF

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
STRUCT_ARRAY_PROPS = frozenset({"LODInfo", "TriangleSortSettings"})


def lzo1x_decompress(src: bytes | memoryview, expected_len: int | None = None) -> bytes:
    mv = src if isinstance(src, memoryview) else memoryview(src)
    n = len(mv)
    if n == 0:
        return b""

    ip = 0
    out = bytearray(expected_len if expected_len else max(256, n * 3))
    op = 0

    def ensure(extra: int) -> None:
        if op + extra > len(out):
            out.extend(b"\x00" * max(extra, len(out)))

    def copy_match(m_pos: int, m_len: int) -> None:
        nonlocal op
        if m_pos < 0:
            raise ValueError(f"LZO1X lookbehind underrun: m_pos={m_pos}, op={op}")
        ensure(m_len)
        dist = op - m_pos
        if dist >= m_len:
            out[op : op + m_len] = out[m_pos : m_pos + m_len]
            op += m_len
        elif dist == 1:
            out[op : op + m_len] = bytes([out[m_pos]]) * m_len
            op += m_len
        else:
            for _ in range(m_len):
                out[op] = out[m_pos]
                op += 1
                m_pos += 1

    state = "top"
    t = 0
    if mv[ip] > 17:
        t = mv[ip] - 17
        ip += 1
        if t < 4:
            state = "match_next"
        else:
            ensure(t)
            out[op : op + t] = mv[ip : ip + t]
            op += t
            ip += t
            state = "first_literal_run"

    while True:
        if state == "top":
            t = mv[ip]
            ip += 1
            if t >= 16:
                state = "match"
                continue
            if t == 0:
                while mv[ip] == 0:
                    t += 255
                    ip += 1
                t += 15 + mv[ip]
                ip += 1
            lit_len = t + 3
            ensure(lit_len)
            out[op : op + lit_len] = mv[ip : ip + lit_len]
            op += lit_len
            ip += lit_len
            state = "first_literal_run"
            continue

        if state == "first_literal_run":
            t = mv[ip]
            ip += 1
            if t >= 16:
                state = "match"
                continue
            m_pos = op - (1 + 0x0800) - (t >> 2) - (mv[ip] << 2)
            ip += 1
            copy_match(m_pos, 3)
            state = "match_done"
            continue

        if state == "match":
            if t >= 64:
                m_pos = op - 1 - ((t >> 2) & 7) - (mv[ip] << 3)
                ip += 1
                m_len = (t >> 5) + 1
                copy_match(m_pos, m_len)
            elif t >= 32:
                m_len = t & 31
                if m_len == 0:
                    while mv[ip] == 0:
                        m_len += 255
                        ip += 1
                    m_len += 31 + mv[ip]
                    ip += 1
                m_len += 2
                v = mv[ip] | (mv[ip + 1] << 8)
                ip += 2
                m_pos = op - 1 - (v >> 2)
                t = v
                copy_match(m_pos, m_len)
            elif t >= 16:
                m_pos = op - ((t & 8) << 11)
                m_len = t & 7
                if m_len == 0:
                    while mv[ip] == 0:
                        m_len += 255
                        ip += 1
                    m_len += 7 + mv[ip]
                    ip += 1
                v = mv[ip] | (mv[ip + 1] << 8)
                ip += 2
                m_pos -= v >> 2
                if m_pos == op:
                    break
                m_pos -= 0x4000
                m_len += 2
                t = v
                copy_match(m_pos, m_len)
            else:
                m_pos = op - 1 - (t >> 2) - (mv[ip] << 2)
                ip += 1
                copy_match(m_pos, 2)
            state = "match_done"
            continue

        if state == "match_done":
            t = t & 3
            if t == 0:
                state = "top"
                continue
            state = "match_next"
            continue

        if state == "match_next":
            ensure(t)
            out[op : op + t] = mv[ip : ip + t]
            op += t
            ip += t
            t = mv[ip]
            ip += 1
            state = "match"
            continue

    result = bytes(out[:op])
    if expected_len is not None and len(result) != expected_len:
        raise ValueError(f"LZO1X length mismatch: {len(result)} != {expected_len}")
    return result


def decompress_ue3_block(
    block: bytes | memoryview,
    uncompressed_size: int,
    flags: int = COMPRESS_FIND,
) -> bytes:
    is_zlib = len(block) >= 2 and block[0] == 0x78 and block[1] in (0x01, 0x5E, 0x9C, 0xDA)
    method = (COMPRESS_ZLIB if is_zlib else COMPRESS_LZO) if flags == COMPRESS_FIND else flags
    if method == COMPRESS_ZLIB:
        try:
            res = zlib.decompress(block)
            if len(res) == uncompressed_size:
                return res
        except Exception:
            if len(block) == uncompressed_size:
                return bytes(block)
            raise
    try:
        return lzo1x_decompress(block, uncompressed_size)
    except Exception:
        if len(block) == uncompressed_size:
            return bytes(block)
        raise


def _read_fstring(buf: bytes | memoryview, pos: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("<i", buf, pos)
    pos += 4
    if length == 0:
        return "", pos
    if length > 0:
        raw = bytes(buf[pos : pos + length])
        pos += length
        if raw.endswith(b"\x00"):
            raw = raw[:-1]
        return raw.decode("latin1"), pos
    count = -length
    raw_u16 = bytes(buf[pos : pos + count * 2])
    pos += count * 2
    text = raw_u16.decode("utf-16-le", errors="replace")
    if text.endswith("\x00"):
        text = text[:-1]
    return text, pos


def _pack_fstring(text: str) -> bytes:
    raw = text.encode("latin1") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def quat_rotate_vec(
    q: tuple[float, float, float, float],
    v: tuple[float, float, float],
) -> tuple[float, float, float]:
    qx, qy, qz, qw = q
    vx, vy, vz = v
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def pack_normal(nx: float, ny: float, nz: float, w_sign: float = 1.0) -> int:
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    if ln > 1e-8:
        nx, ny, nz = nx / ln, ny / ln, nz / ln
    else:
        nx, ny, nz = 0.0, 0.0, 1.0
    bx = max(0, min(255, int(round((nx + 1.0) * 127.5))))
    by = max(0, min(255, int(round((ny + 1.0) * 127.5))))
    bz = max(0, min(255, int(round((nz + 1.0) * 127.5))))
    bw = 255 if w_sign >= 0.0 else 0
    return bx | (by << 8) | (bz << 16) | (bw << 24)


@dataclass(frozen=True)
class SubBlockSpan:
    uncomp_offset: int
    uncomp_size: int
    comp_file_offset: int
    comp_size: int
    is_raw: bool = False


@dataclass(frozen=True)
class ExportEntry:
    index: int
    class_index: int
    super_index: int
    outer_index: int
    object_name: str
    object_name_num: int
    archetype_index: int
    object_flags: int
    serial_size: int
    serial_offset: int
    export_flags: int


def _parse_chunk_header(
    fh: BinaryIO, file_offset: int, uncomp_base: int
) -> tuple[list[SubBlockSpan], int, int]:
    fh.seek(file_offset)
    hdr = fh.read(16)
    if len(hdr) < 16:
        raise ValueError(f"Truncated FCompressedChunkHeader at 0x{file_offset:X}")
    tag, _bsize, sum_comp, sum_uncomp = struct.unpack("<IIII", hdr)
    if tag != PACKAGE_FILE_TAG:
        raise ValueError(f"Bad chunk header tag 0x{tag:08X}")
    blocks: list[tuple[int, int]] = []
    acc_c = 0
    acc_u = 0
    while acc_c < sum_comp and acc_u < sum_uncomp:
        b_c, b_u = struct.unpack("<II", fh.read(8))
        blocks.append((b_c, b_u))
        acc_c += b_c
        acc_u += b_u
    data_pos = fh.tell()
    cur_u = uncomp_base
    spans: list[SubBlockSpan] = []
    for b_c, b_u in blocks:
        spans.append(SubBlockSpan(cur_u, b_u, data_pos, b_c, False))
        data_pos += b_c
        cur_u += b_u
    return spans, data_pos, cur_u


class UE3PackageArchive:
    """Random-access UE3 `.upk` reader supporting fully-compressed (`Startup.upk`), chunked, and raw `.upk`s."""

    def __init__(self, source: str | Path | bytes, max_cached_blocks: int = 32) -> None:
        if isinstance(source, (bytes, bytearray, memoryview)):
            import io

            self._fh: BinaryIO = io.BytesIO(bytes(source))
            self._file_size = len(source)
        else:
            p = Path(source)
            self._fh = p.open("rb")
            self._file_size = p.stat().st_size

        self._max_cached_blocks = max_cached_blocks
        self._block_cache: OrderedDict[int, bytes] = OrderedDict()
        self.is_fully_compressed = False
        self.is_chunk_compressed = False
        self.compression_flags = COMPRESS_FIND
        self._spans: list[SubBlockSpan] = []
        self.blocks_decompressed = 0

        self._fh.seek(0)
        dw0, dw1, dw2, dw3 = struct.unpack("<IIII", self._fh.read(16))
        if dw0 != PACKAGE_FILE_TAG:
            raise ValueError(f"Bad UPK magic 0x{dw0:08X}")

        is_pow2_block = dw1 >= 512 and (dw1 & (dw1 - 1)) == 0 and 0 < dw2 <= self._file_size and dw3 > 0
        if dw1 == PACKAGE_FILE_TAG or is_pow2_block:
            self.is_fully_compressed = True
            fpos = 0
            upos = 0
            while fpos + 16 <= self._file_size:
                self._fh.seek(fpos)
                pk = self._fh.read(4)
                if len(pk) < 4 or struct.unpack("<I", pk)[0] != PACKAGE_FILE_TAG:
                    break
                spans, fpos, upos = _parse_chunk_header(self._fh, fpos, upos)
                self._spans.extend(spans)
            self.uncompressed_size = upos
            self._parse_summary_and_tables()
        else:
            self._fh.seek(0)
            raw_head = self._fh.read(min(65536, self._file_size))
            summary = self._parse_summary_buf(raw_head)
            chunks = summary["compressed_chunks"]
            if chunks:
                self.is_chunk_compressed = True
                if summary["compression_flags"] in (1, 2):
                    self.compression_flags = summary["compression_flags"]
                if chunks[0][0] > 0:
                    self._spans.append(SubBlockSpan(0, chunks[0][0], 0, chunks[0][0], True))
                for u_off, u_sz, c_off, c_sz in chunks:
                    if c_sz == u_sz:
                        self._spans.append(SubBlockSpan(u_off, u_sz, c_off, c_sz, True))
                    else:
                        spans, _, _ = _parse_chunk_header(self._fh, c_off, u_off)
                        self._spans.extend(spans)
                self.uncompressed_size = chunks[-1][0] + chunks[-1][1]
            else:
                self.uncompressed_size = self._file_size
                self._spans.append(SubBlockSpan(0, self._file_size, 0, self._file_size, True))
            self._parse_summary_and_tables()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> UE3PackageArchive:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _parse_summary_buf(buf: bytes) -> dict[str, Any]:
        pos = 0
        tag, ver, headers_size = struct.unpack_from("<III", buf, pos)
        pos += 12
        pkg_group, pos = _read_fstring(buf, pos)
        (
            pkg_flags,
            name_cnt,
            name_off,
            exp_cnt,
            exp_off,
            imp_cnt,
            imp_off,
            dep_off,
            _f38,
            _f3c,
            _f40,
            _unk38,
        ) = struct.unpack_from("<IIIIIIIIIIII", buf, pos)
        pos += 48 + 16
        (gen_cnt,) = struct.unpack_from("<i", buf, pos)
        pos += 4 + 12 * gen_cnt
        eng_ver, cook_ver, comp_flags, chunk_cnt = struct.unpack_from("<iiii", buf, pos)
        pos += 16
        chunks = [struct.unpack_from("<iiii", buf, pos + 16 * k) for k in range(chunk_cnt)]
        return {
            "tag": tag,
            "file_version": ver & 0xFFFF,
            "licensee_version": (ver >> 16) & 0xFFFF,
            "headers_size": headers_size,
            "package_group": pkg_group,
            "package_flags": pkg_flags,
            "name_count": name_cnt,
            "name_offset": name_off,
            "export_count": exp_cnt,
            "export_offset": exp_off,
            "import_count": imp_cnt,
            "import_offset": imp_off,
            "depends_offset": dep_off,
            "engine_version": eng_ver,
            "cooker_version": cook_ver,
            "compression_flags": comp_flags,
            "compressed_chunks": chunks,
        }

    def read_range(self, offset: int, size: int) -> bytes:
        if size <= 0:
            return b""
        if len(self._spans) == 1 and self._spans[0].is_raw:
            self._fh.seek(self._spans[0].comp_file_offset + offset)
            return self._fh.read(size)

        lo, hi = 0, len(self._spans) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            sp = self._spans[mid]
            if sp.uncomp_offset + sp.uncomp_size <= offset:
                lo = mid + 1
            else:
                hi = mid

        out = bytearray()
        cur = offset
        rem = size
        idx = lo
        while rem > 0 and idx < len(self._spans):
            sp = self._spans[idx]
            loff = cur - sp.uncomp_offset
            take = min(rem, sp.uncomp_size - loff)
            if sp.is_raw:
                self._fh.seek(sp.comp_file_offset + loff)
                out.extend(self._fh.read(take))
            else:
                blk = self._block_cache.get(idx)
                if blk is not None:
                    self._block_cache.move_to_end(idx)
                else:
                    self._fh.seek(sp.comp_file_offset)
                    comp = self._fh.read(sp.comp_size)
                    blk = decompress_ue3_block(comp, sp.uncomp_size, self.compression_flags)
                    self.blocks_decompressed += 1
                    self._block_cache[idx] = blk
                    if len(self._block_cache) > self._max_cached_blocks:
                        self._block_cache.popitem(last=False)
                out.extend(blk[loff : loff + take])
            cur += take
            rem -= take
            idx += 1
        return bytes(out)

    def _parse_summary_and_tables(self) -> None:
        head = self.read_range(0, min(65536, self.uncompressed_size))
        self.summary = self._parse_summary_buf(head)
        s = self.summary
        tend = min(
            self.uncompressed_size,
            max(
                s["headers_size"],
                s["export_offset"] + s["export_count"] * 96,
                s["import_offset"] + s["import_count"] * 28,
            ),
        )
        tbuf = memoryview(self.read_range(0, tend))

        p = s["name_offset"]
        self.names: list[str] = []
        for _ in range(s["name_count"]):
            nm, p = _read_fstring(tbuf, p)
            p += 8
            self.names.append(nm)

        p = s["import_offset"]
        self.imports: list[tuple[str, str, int, str, int]] = []
        for _ in range(s["import_count"]):
            cp_i, _cp_n, cn_i, _cn_n, out_i, on_i, on_n = struct.unpack_from("<iiiiiii", tbuf, p)
            p += 28
            self.imports.append((self.names[cp_i], self.names[cn_i], out_i, self.names[on_i], on_n))

        p = s["export_offset"]
        self.exports: list[ExportEntry] = []
        for i in range(s["export_count"]):
            cls_i, sup_i, out_i, nm_i, nm_n, arch_i = struct.unpack_from("<iiiiii", tbuf, p)
            p += 24
            (oflags,) = struct.unpack_from("<Q", tbuf, p)
            p += 8
            ssz, soff, eflags, ncnt = struct.unpack_from("<iiii", tbuf, p)
            p += 16 + 4 * ncnt + 16 + 4
            self.exports.append(
                ExportEntry(
                    index=i,
                    class_index=cls_i,
                    super_index=sup_i,
                    outer_index=out_i,
                    object_name=self.names[nm_i],
                    object_name_num=nm_n,
                    archetype_index=arch_i,
                    object_flags=oflags,
                    serial_size=ssz,
                    serial_offset=soff,
                    export_flags=eflags,
                )
            )

    @staticmethod
    def _fmt(base: str, num: int) -> str:
        return base if num == 0 else f"{base}_{num - 1}"

    def resolve_ref_short(self, ref: int) -> str:
        if ref == 0:
            return "None"
        if ref < 0:
            imp = self.imports[-ref - 1]
            return self._fmt(imp[3], imp[4])
        exp = self.exports[ref - 1]
        return self._fmt(exp.object_name, exp.object_name_num)

    def resolve_full_path(self, ref: int) -> str:
        if ref == 0:
            return "None"
        parts: list[str] = []
        cur = ref
        seen: set[int] = set()
        while cur != 0 and cur not in seen:
            seen.add(cur)
            if cur < 0:
                imp = self.imports[-cur - 1]
                parts.append(self._fmt(imp[3], imp[4]))
                cur = imp[2]
            else:
                exp = self.exports[cur - 1]
                parts.append(self._fmt(exp.object_name, exp.object_name_num))
                cur = exp.outer_index
        return ".".join(reversed(parts))

    def export_class(self, exp: ExportEntry) -> str:
        return "Class" if exp.class_index == 0 else self.resolve_ref_short(exp.class_index)


# ---------------------------------------------------------------------------
# Tagged Properties + `USkeletalMesh` Donor Stripper & Fragment Bounds
# ---------------------------------------------------------------------------


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
    buf: bytes | memoryview, pos: int, name_lookup: Callable[[int], str]
) -> tuple[list[TaggedProp], int]:
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
            props.append(TaggedProp(pname, nn, ptype, tn, arr_idx, bool_val=bval))
        elif ptype == "ByteProperty":
            ei, en = struct.unpack_from("<ii", buf, pos)
            pos += 8
            ename = name_lookup(ei)
            if ename == "None":
                braw = int(buf[pos])
                pos += 1
                props.append(TaggedProp(pname, nn, ptype, tn, arr_idx, enum_name="None", enum_num=en, byte_raw=braw))
            else:
                vi, vn = struct.unpack_from("<ii", buf, pos)
                pos += 8
                props.append(
                    TaggedProp(pname, nn, ptype, tn, arr_idx, enum_name=ename, enum_num=en, byte_enum_val=(name_lookup(vi), vn))
                )
        elif ptype == "NameProperty":
            vi, vn = struct.unpack_from("<ii", buf, pos)
            pos += 8
            props.append(TaggedProp(pname, nn, ptype, tn, arr_idx, name_val=(name_lookup(vi), vn)))
        elif ptype == "StructProperty":
            si, sn = struct.unpack_from("<ii", buf, pos)
            pos += 8
            sname = name_lookup(si)
            raw = bytes(buf[pos : pos + psize])
            pos += psize
            nested = None
            if sname not in ATOMIC_STRUCTS:
                try:
                    sub_p, sub_end = parse_tagged_props(raw, 0, name_lookup)
                    if sub_end == len(raw):
                        nested = sub_p
                except Exception:
                    nested = None
            props.append(
                TaggedProp(pname, nn, ptype, tn, arr_idx, struct_name=sname, struct_num=sn, raw_payload=raw, nested_struct_props=nested)
            )
        elif ptype == "ArrayProperty":
            raw = bytes(buf[pos : pos + psize])
            pos += psize
            items = None
            if pname in STRUCT_ARRAY_PROPS and len(raw) >= 4:
                (cnt,) = struct.unpack_from("<i", raw, 0)
                spos = 4
                parsed: list[list[TaggedProp]] = []
                try:
                    for _ in range(cnt):
                        ep, spos = parse_tagged_props(raw, spos, name_lookup)
                        parsed.append(ep)
                    if spos == len(raw):
                        items = parsed
                except Exception:
                    items = None
            props.append(TaggedProp(pname, nn, ptype, tn, arr_idx, raw_payload=raw, struct_array_items=items))
        else:
            raw = bytes(buf[pos : pos + psize])
            pos += psize
            props.append(TaggedProp(pname, nn, ptype, tn, arr_idx, raw_payload=raw))
    return props, pos


def serialize_tagged_props(props: list[TaggedProp], name_index_of: Callable[[str], int]) -> bytes:
    out = bytearray()
    for p in props:
        ni = name_index_of(p.name)
        ti = name_index_of(p.prop_type)
        if p.prop_type == "BoolProperty":
            out.extend(struct.pack("<iiiiiiB", ni, p.name_num, ti, p.prop_type_num, 0, p.array_index, int(p.bool_val)))
        elif p.prop_type == "ByteProperty":
            ei = name_index_of(p.enum_name)
            if p.enum_name == "None" or p.byte_enum_val is None:
                out.extend(struct.pack("<iiiiiiiiB", ni, p.name_num, ti, p.prop_type_num, 1, p.array_index, ei, p.enum_num, p.byte_raw))
            else:
                vi = name_index_of(p.byte_enum_val[0])
                out.extend(struct.pack("<iiiiiiiiii", ni, p.name_num, ti, p.prop_type_num, 8, p.array_index, ei, p.enum_num, vi, p.byte_enum_val[1]))
        elif p.prop_type == "NameProperty":
            assert p.name_val is not None
            vi = name_index_of(p.name_val[0])
            out.extend(struct.pack("<iiiiiiii", ni, p.name_num, ti, p.prop_type_num, 8, p.array_index, vi, p.name_val[1]))
        elif p.prop_type == "StructProperty":
            si = name_index_of(p.struct_name)
            payload = serialize_tagged_props(p.nested_struct_props, name_index_of) if p.nested_struct_props is not None else p.raw_payload
            out.extend(struct.pack("<iiiiiiii", ni, p.name_num, ti, p.prop_type_num, len(payload), p.array_index, si, p.struct_num))
            out.extend(payload)
        elif p.prop_type == "ArrayProperty":
            if p.struct_array_items is not None:
                buf = bytearray(struct.pack("<i", len(p.struct_array_items)))
                for elem in p.struct_array_items:
                    buf.extend(serialize_tagged_props(elem, name_index_of))
                payload = bytes(buf)
            else:
                payload = p.raw_payload
            out.extend(struct.pack("<iiiiii", ni, p.name_num, ti, p.prop_type_num, len(payload), p.array_index))
            out.extend(payload)
        else:
            out.extend(struct.pack("<iiiiii", ni, p.name_num, ti, p.prop_type_num, len(p.raw_payload), p.array_index))
            out.extend(p.raw_payload)
    out.extend(struct.pack("<ii", name_index_of("None"), 0))
    return bytes(out)


def extract_pistol_donor_and_bounds(
    ar: UE3PackageArchive,
    mesh_exp: ExportEntry,
    fragment_ranges: list[tuple[str, int, int]],
) -> dict[str, Any]:
    """Parse `GestaltDef_Pistol_GestaltSkeletalMesh`, compute all fragment 3D bounds, and build stripped donor `.upk`."""
    eb = memoryview(ar.read_range(mesh_exp.serial_offset, mesh_exp.serial_size))
    def name_of(i):
        return ar.names[i]
    pos = 4
    props, pos = parse_tagged_props(eb, pos, name_of)

    # Extract socket sub-objects if present in same archive
    sockets: list[tuple[str, int, list[TaggedProp]]] = []
    for pr in props:
        if pr.name == "Sockets" and pr.prop_type == "ArrayProperty" and len(pr.raw_payload) >= 4:
            (scnt,) = struct.unpack_from("<i", pr.raw_payload, 0)
            srefs = struct.unpack_from(f"<{scnt}i", pr.raw_payload, 4)
            for sref in srefs:
                if sref > 0 and (sref - 1) < len(ar.exports):
                    sexp = ar.exports[sref - 1]
                    seb = memoryview(ar.read_range(sexp.serial_offset, sexp.serial_size))
                    sprops, _ = parse_tagged_props(seb, 4, name_of)
                    sockets.append((sexp.object_name, sexp.object_name_num, sprops))

    clean_props: list[TaggedProp] = []
    for pr in props:
        if pr.name == "bHasVertexColors":
            continue
        if pr.name == "LODInfo" and pr.struct_array_items and len(pr.struct_array_items) > 1:
            pr.struct_array_items = [pr.struct_array_items[0]]
        clean_props.append(pr)

    bx, by, bz, ex, ey, ez, brad = struct.unpack_from("<7f", eb, pos)
    pos += 28
    (mat_cnt,) = struct.unpack_from("<i", eb, pos)
    pos += 4 + 4 * mat_cnt
    mesh_origin = struct.unpack_from("<3f", eb, pos)
    pos += 12
    rot_origin = struct.unpack_from("<3i", eb, pos)
    pos += 12

    (bone_cnt,) = struct.unpack_from("<i", eb, pos)
    pos += 4
    bones: list[dict[str, Any]] = []
    root_transforms: list[tuple[tuple[float, float, float], tuple[float, float, float, float]]] = []
    for bi in range(bone_cnt):
        b_ni, b_nn, b_fl, qx, qy, qz, qw, px, py, pz, nchild, par_idx, bcol = struct.unpack_from(
            "<iiI4f3fiiI", eb, pos
        )
        pos += 52
        bname = name_of(b_ni)
        lq = (qx, qy, qz, -qw) if bi > 0 else (qx, qy, qz, qw)
        qlen = math.sqrt(sum(c * c for c in lq)) or 1.0
        lq = (lq[0] / qlen, lq[1] / qlen, lq[2] / qlen, lq[3] / qlen)
        if bi == 0 or par_idx < 0 or par_idx >= bi:
            wpos, wquat = (px, py, pz), lq
        else:
            ppos, pquat = root_transforms[par_idx]
            rp = quat_rotate_vec(pquat, (px, py, pz))
            wpos = (ppos[0] + rp[0], ppos[1] + rp[1], ppos[2] + rp[2])
            ax, ay, az, aw = pquat
            bxq, byq, bzq, bwq = lq
            wquat = (
                aw * bxq + ax * bwq + ay * bzq - az * byq,
                aw * byq - ax * bzq + ay * bwq + az * bxq,
                aw * bzq + ax * byq - ay * bxq + az * bwq,
                aw * bwq - ax * bxq - ay * byq - az * bzq,
            )
        root_transforms.append((wpos, wquat))
        bones.append(
            {
                "index": bi,
                "name": bname,
                "name_num": b_nn,
                "flags": b_fl,
                "orientation": [round(qx, 6), round(qy, 6), round(qz, 6), round(qw, 6)],
                "position": [round(px, 4), round(py, 4), round(pz, 4)],
                "root_space_pos": [round(wpos[0], 4), round(wpos[1], 4), round(wpos[2], 4)],
                "num_children": nchild,
                "parent_index": par_idx,
                "bone_color": bcol,
                "_raw_orient": (qx, qy, qz, qw),
                "_raw_pos": (px, py, pz),
            }
        )

    (skel_depth,) = struct.unpack_from("<i", eb, pos)
    pos += 4
    (lod_cnt,) = struct.unpack_from("<i", eb, pos)
    pos += 4
    (sec_cnt,) = struct.unpack_from("<i", eb, pos)
    pos += 4
    sections = []
    for _ in range(sec_cnt):
        mi, ci, bi_s, ntri, tsort = struct.unpack_from("<hhiiB", eb, pos)
        pos += 13
        sections.append({"material_index": mi, "chunk_index": ci, "base_index": bi_s, "num_triangles": ntri})

    _f0, idx_dts, _esz, idx_cnt = struct.unpack_from("<iBii", eb, pos)
    pos += 13
    if idx_dts == 2:
        indices = list(struct.unpack_from(f"<{idx_cnt}H", eb, pos))
        pos += 2 * idx_cnt
    else:
        indices = list(struct.unpack_from(f"<{idx_cnt}I", eb, pos))
        pos += 4 * idx_cnt

    (act_cnt,) = struct.unpack_from("<i", eb, pos)
    pos += 4 + 2 * act_cnt
    (chunk_cnt,) = struct.unpack_from("<i", eb, pos)
    pos += 4
    chunks: list[tuple[int, list[int]]] = []
    for _ in range(chunk_cnt):
        bvi, rcnt = struct.unpack_from("<ii", eb, pos)
        pos += 8 + rcnt * 48
        (scnt_v,) = struct.unpack_from("<i", eb, pos)
        pos += 4 + scnt_v * 60
        (bm_cnt,) = struct.unpack_from("<i", eb, pos)
        pos += 4
        bmap = list(struct.unpack_from(f"<{bm_cnt}H", eb, pos))
        pos += 2 * bm_cnt + 12
        chunks.append((bvi, bmap))

    _lsz, num_verts, req_cnt = struct.unpack_from("<iii", eb, pos)
    pos += 12 + req_cnt
    rpf, _rpc, rpd, _rpo = struct.unpack_from("<iiii", eb, pos)
    pos += 16
    if not (rpf & 0x20) and rpd > 0:
        pos += rpd

    (ntc,) = struct.unpack_from("<i", eb, pos)
    pos += 4
    vb_ntc, fp_uv, pack_pos = struct.unpack_from("<iii", eb, pos)
    pos += 12
    m_ext = struct.unpack_from("<3f", eb, pos)
    pos += 12
    m_orig = struct.unpack_from("<3f", eb, pos)
    pos += 12
    v_stride, v_cnt = struct.unpack_from("<ii", eb, pos)
    pos += 8

    # Extract vertex positions & dominant bone indices quickly
    uv_bytes = (8 if fp_uv else 4) * vb_ntc
    is_actually_packed = (v_stride == 20 + uv_bytes)
    vert_pos: list[tuple[float, float, float]] = []
    vert_b0: list[int] = []
    for vi in range(v_cnt):
        base_o = pos + vi * v_stride
        b0 = eb[base_o + 8]
        if is_actually_packed:
            (p32,) = struct.unpack_from("<I", eb, base_o + 16)
            # Signed 11-11-10 fixed point in [-1, 1] scaled by MeshExtension + MeshOrigin
            ix = p32 & 0x7FF
            if ix & 0x400:
                ix -= 0x800
            iy = (p32 >> 11) & 0x7FF
            if iy & 0x400:
                iy -= 0x800
            iz = (p32 >> 22) & 0x3FF
            if iz & 0x200:
                iz -= 0x400
            vx = (ix / 1023.0) * m_ext[0] + m_orig[0]
            vy = (iy / 1023.0) * m_ext[1] + m_orig[1]
            vz = (iz / 511.0) * m_ext[2] + m_orig[2]
        else:
            vx, vy, vz = struct.unpack_from("<3f", eb, base_o + 16)
        vert_pos.append((vx, vy, vz))
        vert_b0.append(b0)

    def _chunk_bmap(vi: int) -> list[int]:
        cur_bm = chunks[0][1]
        for bvi, bm in chunks:
            if vi >= bvi:
                cur_bm = bm
            else:
                break
        return cur_bm

    # Compute 3D bounding boxes for every requested Gestalt fragment!
    frag_bounds_out: dict[str, Any] = {}
    for fname, first_idx, num_prims in fragment_ranges:
        islice = indices[first_idx : first_idx + num_prims * 3]
        if not islice:
            continue
        uverts = sorted(set(islice))
        raw_pts: list[tuple[float, float, float]] = []
        root_pts: list[tuple[float, float, float]] = []
        used_bones: set[str] = set()
        for vi in uverts:
            vp = vert_pos[vi]
            raw_pts.append(vp)
            bm = _chunk_bmap(vi)
            b0 = vert_b0[vi]
            sk_bi = bm[b0] if b0 < len(bm) else 0
            if 0 <= sk_bi < len(bones):
                used_bones.add(bones[sk_bi]["name"])
                bp, bq = root_transforms[sk_bi]
                rp = quat_rotate_vec(bq, vp)
                root_pts.append((bp[0] + rp[0], bp[1] + rp[1], bp[2] + rp[2]))
            else:
                root_pts.append(vp)

        def _fmt_box(pts: list[tuple[float, float, float]]) -> dict[str, list[float]]:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            zs = [p[2] for p in pts]
            mn = [round(min(xs), 3), round(min(ys), 3), round(min(zs), 3)]
            mx = [round(max(xs), 3), round(max(ys), 3), round(max(zs), 3)]
            ct = [round((mn[k] + mx[k]) * 0.5, 3) for k in range(3)]
            sz = [round(mx[k] - mn[k], 3) for k in range(3)]
            return {"min": mn, "max": mx, "center": ct, "size": sz}

        frag_bounds_out[fname] = {
            "first_index": first_idx,
            "num_primitives": num_prims,
            "vertex_count": len(uverts),
            "bones_used": sorted(used_bones),
            "raw_bounds": _fmt_box(raw_pts),
            "root_bounds": _fmt_box(root_pts),
        }

    # Build stripped 1-triangle donor `.upk` preserving `RefSkeleton`, `Sockets`, `LODInfo`, and `NameIndexMap`!
    donor_bytes = _build_stripped_donor_upk(
        mesh_name="Weap_Pistol_DonorMesh",
        props=clean_props,
        sockets=sockets,
        bounds=(bx, by, bz, ex, ey, ez, brad),
        mesh_origin=mesh_origin,
        rot_origin=rot_origin,
        bones=bones,
        skel_depth=skel_depth,
        ntc=max(1, ntc),
        m_ext=m_ext,
        m_orig=m_orig,
    )

    clean_bones = [
        {k: v for k, v in b.items() if not k.startswith("_")}
        for b in bones
    ]
    return {
        "mesh_full_path": ar.resolve_full_path(mesh_exp.index + 1),
        "serial_size": mesh_exp.serial_size,
        "lod_count": lod_cnt,
        "section_count": sec_cnt,
        "sections": sections,
        "num_vertices": num_verts,
        "num_triangles": idx_cnt // 3,
        "num_tex_coords": ntc,
        "use_full_precision_uvs": fp_uv,
        "use_packed_position": pack_pos,
        "mesh_origin": [round(x, 4) for x in mesh_origin],
        "rot_origin": list(rot_origin),
        "bounds": {
            "origin": [round(bx, 3), round(by, 3), round(bz, 3)],
            "extent": [round(ex, 3), round(ey, 3), round(ez, 3)],
            "radius": round(brad, 3),
        },
        "skeletal_depth": skel_depth,
        "socket_subobject_count": len(sockets),
        "ref_skeleton": clean_bones,
        "fragment_vertex_bounds": frag_bounds_out,
        "donor_upk_bytes": donor_bytes,
    }


def _build_stripped_donor_upk(
    mesh_name: str,
    props: list[TaggedProp],
    sockets: list[tuple[str, int, list[TaggedProp]]],
    bounds: tuple[float, float, float, float, float, float, float],
    mesh_origin: tuple[float, float, float],
    rot_origin: tuple[int, int, int],
    bones: list[dict[str, Any]],
    skel_depth: int,
    ntc: int,
    m_ext: tuple[float, float, float],
    m_orig: tuple[float, float, float],
) -> bytes:
    names: list[str] = []
    nmap: dict[str, int] = {}

    def nidx(nm: str) -> int:
        if nm in nmap:
            return nmap[nm]
        i = len(names)
        names.append(nm)
        nmap[nm] = i
        return i

    nidx("None")
    imports: list[tuple[str, str, int, str, int]] = []

    def add_imp(cp: str, cn: str, outer: int, on: str) -> int:
        nidx(cp)
        nidx(cn)
        nidx(on)
        imports.append((cp, cn, outer, on, 0))
        return -len(imports)

    imp_eng = add_imp("Core", "Package", 0, "Engine")
    imp_sk = add_imp("Core", "Class", imp_eng, "SkeletalMesh")
    imp_sock = add_imp("Core", "Class", imp_eng, "SkeletalMeshSocket") if sockets else 0

    sock_exp_ids = list(range(2, 2 + len(sockets)))
    patched_props: list[TaggedProp] = []
    for pr in props:
        if pr.name == "Sockets":
            raw_s = struct.pack(f"<i{len(sock_exp_ids)}i", len(sock_exp_ids), *sock_exp_ids)
            patched_props.append(TaggedProp("Sockets", pr.name_num, "ArrayProperty", pr.prop_type_num, 0, raw_payload=raw_s))
        else:
            patched_props.append(pr)

    prop_bytes = serialize_tagged_props(patched_props, nidx)
    for b in bones:
        nidx(b["name"])
    nidx(mesh_name)
    sock_blobs: list[tuple[str, int, bytes]] = []
    for sname, snum, sprops in sockets:
        nidx(sname)
        sock_blobs.append((sname, snum, struct.pack("<i", -1) + serialize_tagged_props(sprops, nidx)))

    # Assemble NameTable & ImportTable
    name_buf = bytearray()
    for nm in names:
        name_buf.extend(_pack_fstring(nm))
        name_buf.extend(struct.pack("<Q", NAME_FLAGS_DEFAULT))
    imp_buf = bytearray()
    for cp, cn, outer, on, on_n in imports:
        imp_buf.extend(struct.pack("<iiiiiii", nidx(cp), 0, nidx(cn), 0, outer, nidx(on), on_n))

    exp_count = 1 + len(sock_blobs)
    guid = hashlib.md5(b"Weap_Pistol_Donor_Template").digest()

    def _pack_sum(hsize: int, noff: int, eoff: int, ioff: int, doff: int) -> bytes:
        ver = (LIC_VER << 16) | AR_VER
        b = bytearray(struct.pack("<III", PACKAGE_FILE_TAG, ver, hsize))
        b.extend(_pack_fstring("None"))
        b.extend(
            struct.pack(
                "<IIIIIIIIIIII",
                PACKAGE_FLAGS_COOKED,
                len(names),
                noff,
                exp_count,
                eoff,
                len(imports),
                ioff,
                doff,
                hsize,
                0,
                0,
                0,
            )
        )
        b.extend(guid)
        b.extend(struct.pack("<iiii", 1, exp_count, len(names), 0))
        pkg_source = struct.unpack_from("<I", guid, 0)[0]
        b.extend(struct.pack("<iiiiIii", ENGINE_VER, COOKER_VER, 0, 0, pkg_source, 0, 0))
        return bytes(b)

    noff = len(_pack_sum(0, 0, 0, 0, 0))
    ioff = noff + len(name_buf)
    eoff = ioff + len(imp_buf)
    doff = eoff + exp_count * 68
    hsize = doff + exp_count * 4

    return _finalize_donor_package_bytes(
        _pack_sum(hsize, noff, eoff, ioff, doff),
        name_buf,
        imp_buf,
        hsize,
        imp_sk,
        imp_sock,
        nidx,
        mesh_name,
        prop_bytes,
        bounds,
        mesh_origin,
        rot_origin,
        bones,
        skel_depth,
        ntc,
        m_ext,
        m_orig,
        sock_blobs,
    )


def _finalize_donor_package_bytes(
    summary_bytes: bytes,
    name_buf: bytearray,
    imp_buf: bytearray,
    hsize: int,
    imp_sk: int,
    imp_sock: int,
    nidx: Callable[[str], int],
    mesh_name: str,
    prop_bytes: bytes,
    bounds: tuple[float, float, float, float, float, float, float],
    mesh_origin: tuple[float, float, float],
    rot_origin: tuple[int, int, int],
    bones: list[dict[str, Any]],
    skel_depth: int,
    ntc: int,
    m_ext: tuple[float, float, float],
    m_orig: tuple[float, float, float],
    sock_blobs: list[tuple[str, int, bytes]],
) -> bytes:
    sk_buf = bytearray(struct.pack("<i", -1))
    sk_buf.extend(prop_bytes)
    sk_buf.extend(struct.pack("<7f", *bounds))
    sk_buf.extend(struct.pack("<ii", 1, 0))
    sk_buf.extend(struct.pack("<3f", *mesh_origin))
    sk_buf.extend(struct.pack("<3i", *rot_origin))
    sk_buf.extend(struct.pack("<i", len(bones)))
    for b in bones:
        sk_buf.extend(
            struct.pack(
                "<iiI4f3fiiI",
                nidx(b["name"]),
                b["name_num"],
                b["flags"],
                *b["_raw_orient"],
                *b["_raw_pos"],
                b["num_children"],
                b["parent_index"],
                b["bone_color"],
            )
        )
    sk_buf.extend(struct.pack("<i", skel_depth))
    sk_buf.extend(struct.pack("<ii", 1, 1))
    sk_buf.extend(struct.pack("<hhiiB", 0, 0, 0, 1, 0))
    sk_buf.extend(struct.pack("<iBii3H", 1, 2, 2, 3, 0, 1, 2))
    all_b = list(range(len(bones)))
    sk_buf.extend(struct.pack(f"<i{len(all_b)}h", len(all_b), *all_b))
    # 1 Chunk: base_vertex_index=0, rigid_cnt=0, soft_cnt=0, bmap_cnt=1, bmap=[0], num_rigid=3, num_soft=0, max_inf=1
    sk_buf.extend(struct.pack("<iiiiiHiii", 1, 0, 0, 0, 1, 0, 3, 0, 1))
    req_b = [i for i in all_b if i < 256]
    sk_buf.extend(struct.pack("<iii", 0, 3, len(req_b)))
    sk_buf.extend(bytes(req_b))
    rp_abs = hsize + len(sk_buf) + 16
    sk_buf.extend(struct.pack("<iiii", 0, 0, 0, rp_abs))
    v_stride = 28 + 4 * ntc
    sk_buf.extend(struct.pack("<iiii3f3fii", ntc, ntc, 0, 0, *m_ext, *m_orig, v_stride, 3))
    tx = pack_normal(1.0, 0.0, 0.0, 1.0)
    tz = pack_normal(0.0, 0.0, 1.0, 1.0)
    v_one = struct.pack("<II4B4B3f", tx, tz, 0, 0, 0, 0, 255, 0, 0, 0, 0.0, 0.0, 0.0) + (b"\x00\x00\x00\x00" * ntc)
    sk_buf.extend(v_one * 3)
    sk_buf.extend(struct.pack("<i", 0))
    sk_buf.extend(struct.pack("<i", len(bones)))
    for b in bones:
        sk_buf.extend(struct.pack("<iii", nidx(b["name"]), b["name_num"], b["index"]))
    sk_buf.extend(struct.pack("<4i", 0, 0, 0, 0))
    sk_buf.extend(struct.pack("<i4f", 4, 1.0, 1.0, 0.0, 0.0))

    sk_bytes = bytes(sk_buf)
    exp_buf = bytearray()
    cur_off = hsize
    exp_entries = [(imp_sk, 0, mesh_name, 0, OBJ_FLAGS_TOP_LEVEL, sk_bytes)]
    for sname, snum, sbytes in sock_blobs:
        exp_entries.append((imp_sock, 1, sname, snum, OBJ_FLAGS_SUB_OBJECT, sbytes))

    for cls_i, out_i, oname, onum, oflags, payload in exp_entries:
        exp_buf.extend(
            struct.pack(
                "<iiiiiiQiiii",
                cls_i,
                0,
                out_i,
                nidx(oname),
                onum,
                0,
                oflags,
                len(payload),
                cur_off,
                0,
                0,
            )
        )
        exp_buf.extend(b"\x00" * 20)
        cur_off += len(payload)

    out = bytearray(summary_bytes)
    out.extend(name_buf)
    out.extend(imp_buf)
    out.extend(exp_buf)
    out.extend(b"\x00" * (4 * len(exp_entries)))
    assert len(out) == hsize
    for *_, payload in exp_entries:
        out.extend(payload)
    return bytes(out)
