"""Streaming JSON shard writer.

Every shard file is a complete, standalone JSON document:

    {"schema":"re_probe.v1.classes","section":"classes","shard":0,"items":[...]}

Shards are written incrementally so a multi-hundred-megabyte dump never has to
exist in memory, and a crash can only damage the shard currently being written.
The manifest is written last and only lists shards which were closed cleanly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from . import api


class ShardWriter:
    """Writes one section as a sequence of numbered JSON shard files."""

    def __init__(
        self,
        directory: Path,
        section: str,
        *,
        shard_items: int = 1000,
        schema_prefix: str = "re_probe.v1",
    ) -> None:
        self.directory = directory
        self.section = section
        self.schema = f"{schema_prefix}.{section}"
        self.shard_items = max(1, int(shard_items))
        self.directory.mkdir(parents=True, exist_ok=True)

        self.total_items = 0
        self._shard_index = 0
        self._shard_count = 0
        self._file = None
        self._shards: list[dict[str, Any]] = []

    # -- lifecycle ----------------------------------------------------------

    def _open_shard(self) -> None:
        path = self.directory / f"{self.section}-{self._shard_index:04d}.json"
        self._file = path.open("w", encoding="utf-8", newline="\n")
        self._file.write(
            '{"schema":'
            + api.dumps(self.schema)
            + ',"section":'
            + api.dumps(self.section)
            + ',"shard":'
            + str(self._shard_index)
            + ',"items":['
        )
        self._shard_count = 0

    def _close_shard(self) -> None:
        if self._file is None:
            return
        self._file.write("\n]}\n")
        self._file.flush()
        self._file.close()
        path = self.directory / f"{self.section}-{self._shard_index:04d}.json"
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if self._shard_count > 0:
            self._shards.append(
                {
                    "path": path.name,
                    "shard": self._shard_index,
                    "items": self._shard_count,
                    "bytes": size,
                }
            )
        else:
            # Do not leave empty shards behind.
            try:
                path.unlink()
            except OSError:
                pass
        self._file = None
        self._shard_index += 1

    def close(self) -> None:
        self._close_shard()

    # -- writing ------------------------------------------------------------

    def add(self, item: Any) -> None:
        if self._file is None:
            self._open_shard()
        if self._shard_count > 0:
            self._file.write(",\n")
        try:
            rendered = api.dumps(item)
        except Exception as exc:  # pragma: no cover - last resort
            rendered = api.dumps({"$error": f"could not serialize item: {exc!r}"})
        self._file.write(rendered)
        self._shard_count += 1
        self.total_items += 1

        if self._shard_count >= self.shard_items:
            self._close_shard()

    def extend(self, items: Iterable[Any]) -> None:
        for item in items:
            self.add(item)

    def flush(self) -> None:
        if self._file is not None:
            self._file.flush()

    # -- stats --------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        shards = list(self._shards)
        # Account for the shard currently open, if any.
        if self._file is not None and self._shard_count > 0:
            shards.append(
                {
                    "path": f"{self.section}-{self._shard_index:04d}.json",
                    "shard": self._shard_index,
                    "items": self._shard_count,
                    "bytes": None,
                    "open": True,
                }
            )
        return {
            "schema": self.schema,
            "count": self.total_items,
            "shards": shards,
        }
