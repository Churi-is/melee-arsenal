"""Random-access UE3 `.upk` reader supporting uncompressed, chunk-compressed, and fully-compressed (`Startup.upk`) packages."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import struct
from typing import BinaryIO

from .lzo1x import COMPRESS_FIND, decompress_ue3_block

PACKAGE_FILE_TAG = 0x9E2A83C1


@dataclass(frozen=True)
class SubBlockSpan:
    uncomp_offset: int
    uncomp_size: int
    comp_file_offset: int
    comp_size: int
    is_raw: bool = False


@dataclass(frozen=True)
class PackageSummary:
    tag: int
    file_version: int
    licensee_version: int
    headers_size: int
    package_group: str
    package_flags: int
    name_count: int
    name_offset: int
    export_count: int
    export_offset: int
    import_count: int
    import_offset: int
    depends_offset: int
    f38: int
    f3c: int
    f40: int
    unk38: int
    guid: bytes
    generations: tuple[tuple[int, int, int], ...]
    engine_version: int
    cooker_version: int
    compression_flags: int
    compressed_chunks: tuple[tuple[int, int, int, int], ...]
    u3unk60: int


@dataclass(frozen=True)
class NameEntry:
    name: str
    flags: int


@dataclass(frozen=True)
class ImportEntry:
    class_package: str
    class_package_num: int
    class_name: str
    class_name_num: int
    outer_index: int
    object_name: str
    object_name_num: int


@dataclass(frozen=True)
class ExportEntry:
    index: int  # 0-based index into ExportTable
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
    net_objects: tuple[int, ...]
    guid: bytes
    u3unk6c: int


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


def _parse_chunk_header_from_stream(
    fh: BinaryIO,
    file_offset: int,
    uncomp_base: int,
) -> tuple[list[SubBlockSpan], int, int]:
    """Parse one `FCompressedChunkHeader` at `file_offset`.

    Returns `(spans, next_file_offset, next_uncomp_offset)`.
    """
    fh.seek(file_offset)
    hdr = fh.read(16)
    if len(hdr) < 16:
        raise ValueError(f"Truncated FCompressedChunkHeader at 0x{file_offset:X}")
    tag, _block_size, sum_comp, sum_uncomp = struct.unpack("<IIII", hdr)
    if tag != PACKAGE_FILE_TAG:
        raise ValueError(
            f"Expected FCompressedChunkHeader tag 0x9E2A83C1 at 0x{file_offset:X}, got 0x{tag:08X}"
        )

    blocks: list[tuple[int, int]] = []
    acc_comp = 0
    acc_uncomp = 0
    while acc_comp < sum_comp and acc_uncomp < sum_uncomp:
        pair = fh.read(8)
        if len(pair) < 8:
            raise ValueError("Truncated FCompressedChunkBlock table")
        b_comp, b_uncomp = struct.unpack("<II", pair)
        if b_comp <= 0 or b_uncomp <= 0:
            raise ValueError(f"Invalid FCompressedChunkBlock ({b_comp}, {b_uncomp})")
        blocks.append((b_comp, b_uncomp))
        acc_comp += b_comp
        acc_uncomp += b_uncomp

    if acc_uncomp != sum_uncomp:
        raise ValueError(f"Chunk uncompressed sum mismatch: {acc_uncomp} != {sum_uncomp}")

    data_pos = fh.tell()
    cur_uncomp = uncomp_base
    spans: list[SubBlockSpan] = []
    for b_comp, b_uncomp in blocks:
        spans.append(
            SubBlockSpan(
                uncomp_offset=cur_uncomp,
                uncomp_size=b_uncomp,
                comp_file_offset=data_pos,
                comp_size=b_comp,
                is_raw=False,
            )
        )
        data_pos += b_comp
        cur_uncomp += b_uncomp

    return spans, data_pos, cur_uncomp


class UE3PackageArchive:
    """Random-access UE3 `.upk` reader with on-demand sub-block decompression."""

    def __init__(self, source: str | Path | bytes, max_cached_blocks: int = 32) -> None:
        if isinstance(source, (bytes, bytearray, memoryview)):
            import io

            self._fh: BinaryIO = io.BytesIO(bytes(source))
            self._owns_fh = True
            self._file_size = len(source)
        else:
            p = Path(source)
            self._fh = p.open("rb")
            self._owns_fh = True
            self._file_size = p.stat().st_size

        self._max_cached_blocks = max_cached_blocks
        self._block_cache: OrderedDict[int, bytes] = OrderedDict()
        self.is_fully_compressed = False
        self.is_chunk_compressed = False
        self.compression_flags = COMPRESS_FIND
        self._spans: list[SubBlockSpan] = []

        self._fh.seek(0)
        head16 = self._fh.read(16)
        if len(head16) < 16:
            raise ValueError("File too small to be a UE3 package")
        dw0, dw1, dw2, dw3 = struct.unpack("<IIII", head16)
        if dw0 != PACKAGE_FILE_TAG:
            raise ValueError(f"Bad UPK magic 0x{dw0:08X} (expected 0x9E2A83C1)")

        # In an uncompressed BL2/UE3 header, `dw1` is `(LicVer << 16) | FileVer` (`FileVer = 832`, `LicVer = 46`).
        # In a fully compressed package (`Startup.upk`), `dw1` is `PACKAGE_FILE_TAG` or a power-of-two `BlockSize`
        # (typically `0x20000`), and `dw2 = Sum.CompressedSize <= file_size`, `dw3 = Sum.UncompressedSize > 0`.
        is_pow2_block = dw1 >= 512 and (dw1 & (dw1 - 1)) == 0 and 0 < dw2 <= self._file_size and dw3 > 0
        if dw1 == PACKAGE_FILE_TAG or is_pow2_block:
            # Fully compressed package (`Startup.upk`)
            self.is_fully_compressed = True
            file_pos = 0
            uncomp_pos = 0
            while file_pos + 16 <= self._file_size:
                self._fh.seek(file_pos)
                peek = self._fh.read(4)
                if len(peek) < 4 or struct.unpack("<I", peek)[0] != PACKAGE_FILE_TAG:
                    break
                spans, file_pos, uncomp_pos = _parse_chunk_header_from_stream(
                    self._fh, file_pos, uncomp_pos
                )
                self._spans.extend(spans)
            self.uncompressed_size = uncomp_pos
            header_probe = self.read_range(0, min(4096, self.uncompressed_size))
            self.summary, _ = self._parse_summary(header_probe)
        else:
            # Uncompressed summary at start of file; may still have `Summary.CompressedChunks`
            self._fh.seek(0)
            raw_head = self._fh.read(min(65536, self._file_size))
            self.summary, summary_end = self._parse_summary(raw_head)
            if self.summary.compressed_chunks:
                self.is_chunk_compressed = True
                if self.summary.compression_flags in (1, 2):
                    self.compression_flags = self.summary.compression_flags
                first_uncomp_off = self.summary.compressed_chunks[0][0]
                if first_uncomp_off > 0:
                    self._spans.append(
                        SubBlockSpan(
                            uncomp_offset=0,
                            uncomp_size=first_uncomp_off,
                            comp_file_offset=0,
                            comp_size=first_uncomp_off,
                            is_raw=True,
                        )
                    )
                for u_off, u_size, c_off, c_size in self.summary.compressed_chunks:
                    if c_size == u_size:
                        self._spans.append(
                            SubBlockSpan(
                                uncomp_offset=u_off,
                                uncomp_size=u_size,
                                comp_file_offset=c_off,
                                comp_size=c_size,
                                is_raw=True,
                            )
                        )
                    else:
                        spans, _, _ = _parse_chunk_header_from_stream(self._fh, c_off, u_off)
                        self._spans.extend(spans)
                last_u_off, last_u_size, _, _ = self.summary.compressed_chunks[-1]
                self.uncompressed_size = last_u_off + last_u_size
            else:
                self.uncompressed_size = self._file_size
                self._spans.append(
                    SubBlockSpan(
                        uncomp_offset=0,
                        uncomp_size=self._file_size,
                        comp_file_offset=0,
                        comp_size=self._file_size,
                        is_raw=True,
                    )
                )

        self.names: list[NameEntry] = []
        self.imports: list[ImportEntry] = []
        self.exports: list[ExportEntry] = []
        self._load_tables()

    def close(self) -> None:
        if self._owns_fh and self._fh is not None:
            self._fh.close()

    def __enter__(self) -> UE3PackageArchive:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _parse_summary(buf: bytes) -> tuple[PackageSummary, int]:
        pos = 0
        tag, version, headers_size = struct.unpack_from("<III", buf, pos)
        pos += 12
        file_ver = version & 0xFFFF
        lic_ver = (version >> 16) & 0xFFFF
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
            f38,
            f3c,
            f40,
            unk38,
        ) = struct.unpack_from("<IIIIIIIIIIII", buf, pos)
        pos += 48
        guid = bytes(buf[pos : pos + 16])
        pos += 16
        (gen_cnt,) = struct.unpack_from("<i", buf, pos)
        pos += 4
        gens: list[tuple[int, int, int]] = []
        for _ in range(gen_cnt):
            gens.append(struct.unpack_from("<iii", buf, pos))
            pos += 12
        eng_ver, cook_ver, comp_flags, chunk_cnt = struct.unpack_from("<iiii", buf, pos)
        pos += 16
        chunks: list[tuple[int, int, int, int]] = []
        for _ in range(chunk_cnt):
            chunks.append(struct.unpack_from("<iiii", buf, pos))
            pos += 16
        (u3unk60,) = struct.unpack_from("<I", buf, pos)
        pos += 4
        return (
            PackageSummary(
                tag=tag,
                file_version=file_ver,
                licensee_version=lic_ver,
                headers_size=headers_size,
                package_group=pkg_group,
                package_flags=pkg_flags,
                name_count=name_cnt,
                name_offset=name_off,
                export_count=exp_cnt,
                export_offset=exp_off,
                import_count=imp_cnt,
                import_offset=imp_off,
                depends_offset=dep_off,
                f38=f38,
                f3c=f3c,
                f40=f40,
                unk38=unk38,
                guid=guid,
                generations=tuple(gens),
                engine_version=eng_ver,
                cooker_version=cook_ver,
                compression_flags=comp_flags,
                compressed_chunks=tuple(chunks),
                u3unk60=u3unk60,
            ),
            pos,
        )

    def _get_span_bytes(self, span_idx: int) -> bytes:
        span = self._spans[span_idx]
        if span.is_raw:
            # Raw uncompressed file backing; caller slices directly via `_fh`
            raise RuntimeError("Direct raw span should not use block cache")
        cached = self._block_cache.get(span_idx)
        if cached is not None:
            self._block_cache.move_to_end(span_idx)
            return cached
        self._fh.seek(span.comp_file_offset)
        comp = self._fh.read(span.comp_size)
        if len(comp) != span.comp_size:
            raise ValueError(f"Short read in span {span_idx}")
        decomp = decompress_ue3_block(comp, span.uncomp_size, self.compression_flags)
        self._block_cache[span_idx] = decomp
        if len(self._block_cache) > self._max_cached_blocks:
            self._block_cache.popitem(last=False)
        return decomp

    def read_range(self, offset: int, size: int) -> bytes:
        """Read `size` uncompressed virtual bytes starting at `offset`."""
        if size <= 0:
            return b""
        if offset < 0 or offset + size > self.uncompressed_size:
            raise ValueError(
                f"Out-of-bounds UPK read: [{offset}, {offset + size}) > {self.uncompressed_size}"
            )
        if len(self._spans) == 1 and self._spans[0].is_raw:
            self._fh.seek(self._spans[0].comp_file_offset + offset)
            return self._fh.read(size)

        # Binary-search first span whose end > offset
        lo = 0
        hi = len(self._spans) - 1
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
            local_off = cur - sp.uncomp_offset
            avail = sp.uncomp_size - local_off
            take = rem if rem < avail else avail
            if sp.is_raw:
                self._fh.seek(sp.comp_file_offset + local_off)
                out.extend(self._fh.read(take))
            else:
                blk = self._get_span_bytes(idx)
                out.extend(blk[local_off : local_off + take])
            cur += take
            rem -= take
            idx += 1

        if rem != 0:
            raise ValueError(f"Incomplete UPK read: {rem} bytes left")
        return bytes(out)

    def _load_tables(self) -> None:
        s = self.summary
        table_end = max(
            s.headers_size,
            s.export_offset + s.export_count * 96,
            s.import_offset + s.import_count * 28,
            s.name_offset + s.name_count * 64,
        )
        table_end = min(table_end, self.uncompressed_size)
        tbuf = memoryview(self.read_range(0, table_end))

        # NameTable
        p = s.name_offset
        names: list[NameEntry] = []
        for _ in range(s.name_count):
            nm, p = _read_fstring(tbuf, p)
            (flags,) = struct.unpack_from("<Q", tbuf, p)
            p += 8
            names.append(NameEntry(name=nm, flags=flags))
        self.names = names

        def fname_str(idx: int) -> str:
            return names[idx].name if 0 <= idx < len(names) else f"<bad_name_{idx}>"

        # ImportTable
        p = s.import_offset
        imports: list[ImportEntry] = []
        for _ in range(s.import_count):
            cp_i, cp_n, cn_i, cn_n, outer_i, on_i, on_n = struct.unpack_from("<iiiiiii", tbuf, p)
            p += 28
            imports.append(
                ImportEntry(
                    class_package=fname_str(cp_i),
                    class_package_num=cp_n,
                    class_name=fname_str(cn_i),
                    class_name_num=cn_n,
                    outer_index=outer_i,
                    object_name=fname_str(on_i),
                    object_name_num=on_n,
                )
            )
        self.imports = imports

        # ExportTable
        p = s.export_offset
        exports: list[ExportEntry] = []
        for i in range(s.export_count):
            cls_i, sup_i, outer_i, name_i, name_n, arch_i = struct.unpack_from(
                "<iiiiii", tbuf, p
            )
            p += 24
            (obj_flags,) = struct.unpack_from("<Q", tbuf, p)
            p += 8
            ser_size, ser_off, exp_flags, net_cnt = struct.unpack_from("<iiii", tbuf, p)
            p += 16
            net_objs = struct.unpack_from(f"<{net_cnt}i", tbuf, p) if net_cnt > 0 else ()
            p += 4 * net_cnt
            guid = bytes(tbuf[p : p + 16])
            p += 16
            (u3unk6c,) = struct.unpack_from("<i", tbuf, p)
            p += 4
            exports.append(
                ExportEntry(
                    index=i,
                    class_index=cls_i,
                    super_index=sup_i,
                    outer_index=outer_i,
                    object_name=fname_str(name_i),
                    object_name_num=name_n,
                    archetype_index=arch_i,
                    object_flags=obj_flags,
                    serial_size=ser_size,
                    serial_offset=ser_off,
                    export_flags=exp_flags,
                    net_objects=net_objs,
                    guid=guid,
                    u3unk6c=u3unk6c,
                )
            )
        self.exports = exports

    @staticmethod
    def _fmt_fname(base: str, num: int) -> str:
        return base if num == 0 else f"{base}_{num - 1}"

    def resolve_obj_ref_name(self, obj_index: int) -> str:
        if obj_index == 0:
            return "None"
        if obj_index < 0:
            imp = self.imports[-obj_index - 1]
            return self._fmt_fname(imp.object_name, imp.object_name_num)
        exp = self.exports[obj_index - 1]
        return self._fmt_fname(exp.object_name, exp.object_name_num)

    def resolve_full_path(self, obj_index: int) -> str:
        if obj_index == 0:
            return "None"
        parts: list[str] = []
        cur = obj_index
        seen: set[int] = set()
        while cur != 0 and cur not in seen:
            seen.add(cur)
            if cur < 0:
                imp = self.imports[-cur - 1]
                parts.append(self._fmt_fname(imp.object_name, imp.object_name_num))
                cur = imp.outer_index
            else:
                exp = self.exports[cur - 1]
                parts.append(self._fmt_fname(exp.object_name, exp.object_name_num))
                cur = exp.outer_index
        return ".".join(reversed(parts))

    def export_class_name(self, exp: ExportEntry) -> str:
        if exp.class_index == 0:
            return "Class"
        return self.resolve_obj_ref_name(exp.class_index)

    def find_export(
        self,
        full_or_short_name: str,
        class_name: str | None = None,
    ) -> ExportEntry | None:
        target = full_or_short_name.lower()
        for exp in self.exports:
            if class_name and self.export_class_name(exp).lower() != class_name.lower():
                continue
            short = self._fmt_fname(exp.object_name, exp.object_name_num).lower()
            if short == target:
                return exp
            full = self.resolve_full_path(exp.index + 1).lower()
            if full == target or full.endswith("." + target):
                return exp
        return None

    def read_export_bytes(self, exp: ExportEntry) -> bytes:
        return self.read_range(exp.serial_offset, exp.serial_size)
