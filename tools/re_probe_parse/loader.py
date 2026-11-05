"""Read RE Probe (`re_probe.v1`) dumps: manifest + shard sections.

Every shard is a standalone JSON document:

    {"schema", "section", "shard", "items": [...]}

`manifest.json` (written last) is the authoritative index of which shards
closed cleanly; if it is missing (cancelled run) we fall back to globbing
`<section>-*.json`.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any


class DumpNotFoundError(FileNotFoundError):
    pass


def _load_json_file(path: str) -> Any:
    """Load one JSON file, or None if it is missing/unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"[re_probe_parse] skipping unreadable file {path}: {exc}")
        return None


class Dump:
    """A finished (or cancelled) RE Probe dump directory."""

    def __init__(self, dump_dir: str) -> None:
        self.dump_dir = os.path.abspath(dump_dir)
        if not os.path.isdir(self.dump_dir):
            raise DumpNotFoundError(f"dump directory not found: {self.dump_dir}")
        self.manifest: dict[str, Any] = _load_json_file(
            os.path.join(self.dump_dir, "manifest.json")
        ) or {}
        self.journal: dict[str, Any] = _load_json_file(
            os.path.join(self.dump_dir, "dump.json")
        ) or {}
        self.sections: dict[str, dict[str, Any]] = (
            self.manifest.get("sections") or {}
        )
        self.summary: dict[str, Any] = self.manifest.get("summary") or {}
        self.limits: dict[str, Any] = self.manifest.get("limits") or {}
        self.status: str | None = self.manifest.get("status") or self.journal.get(
            "status"
        )
        self.dump_id: str | None = self.manifest.get("dump_id") or os.path.basename(
            self.dump_dir
        )
        self.profile: str | None = self.manifest.get("profile")

    def resolve(self, path: str) -> str:
        """Resolve a manifest-relative path against the dump directory."""
        return path if os.path.isabs(path) else os.path.join(self.dump_dir, path)

    def shard_paths(self, section: str) -> list[str]:
        """Authoritative shard list for a section (manifest first, glob fallback)."""
        paths: list[str] = []
        sec = self.sections.get(section)
        if isinstance(sec, dict):
            for shard in sec.get("shards") or []:
                p = shard.get("path")
                if p:
                    paths.append(self.resolve(p))
        if not paths:
            paths = sorted(glob.glob(os.path.join(self.dump_dir, f"{section}-*.json")))
        return paths

    def section_items(self, section: str) -> list[dict[str, Any]]:
        """Every record in a section, in shard order."""
        items: list[dict[str, Any]] = []
        for path in self.shard_paths(section):
            doc = _load_json_file(path)
            if isinstance(doc, dict):
                items.extend(doc.get("items") or [])
        return items

    def index_by_path(self, section: str) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for item in self.section_items(section):
            path = item.get("path")
            if isinstance(path, str):
                index[path] = item
        return index

    def section_count(self, section: str) -> int:
        sec = self.sections.get(section) or {}
        declared = sec.get("count")
        if declared is not None:
            return int(declared)
        return len(self.section_items(section))

    def trace_files(self) -> list[str]:
        """Locate trace shards under `traces/` (manifest or glob)."""
        paths: list[str] = []
        sec = self.sections.get("traces")
        if isinstance(sec, dict):
            for shard in sec.get("shards") or []:
                p = shard.get("path")
                if p:
                    paths.append(self.resolve(p))
        if not paths:
            paths = sorted(glob.glob(os.path.join(self.dump_dir, "traces", "*.json")))
        return paths
