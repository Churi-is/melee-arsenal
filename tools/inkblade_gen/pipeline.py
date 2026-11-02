"""Stage runner with content-addressed caching.

A stage is skipped when its fingerprint (config + own source + upstream fingerprint + optional extra
key such as the decal directory) matches the one recorded on disk *and* its artifacts are still
present. Because fingerprints chain, editing the ink stage recomputes the ink stage and everything
after it while leaving the cached geometry / UV / flat / mottle stages untouched.

`--from <stage>` re-runs that stage and everything after it, but still loads the earlier stages from
the cache (re-running them only when their cached output is missing or stale).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Iterable, Sequence

from .config import RunConfig
from .stages import KitState, Stage, StageReport, default_stages


class PipelineError(RuntimeError):
    pass


@dataclass
class PipelineResult:
    state: KitState
    reports: list[StageReport]
    kit_dir: Path | None

    @property
    def ran(self) -> list[str]:
        return [report.name for report in self.reports if report.status == "ran"]

    @property
    def cached(self) -> list[str]:
        return [report.name for report in self.reports if report.status == "cached"]


class Pipeline:
    """Runs the stage stack, honouring `--only` / `--from` / `--force` and the work cache."""

    def __init__(self, cfg: RunConfig, stages: Sequence[Stage] | None = None) -> None:
        self.cfg = cfg
        self.stages = list(stages) if stages is not None else default_stages()
        self._names = [stage.name for stage in self.stages]
        self._by_name = {stage.name: stage for stage in self.stages}

    # -- selection ------------------------------------------------------
    def select(self, only: Iterable[str] | None = None) -> list[Stage]:
        """Stages to execute, always including the prerequisites of whatever was requested.

        The stack is linear, so selecting a stage selects the prefix that feeds it: the prerequisite
        stages come from cache when they are still valid and only re-run when they are not.
        """
        selection = list(self._names)
        if only:
            requested = [name.strip() for name in only if name.strip()]
            unknown = [name for name in requested if name not in self._names]
            if unknown:
                raise PipelineError(f"Unknown stage(s) {unknown}; available: {self._names}")
            last = max(self._names.index(name) for name in requested)
            selection = self._names[: last + 1]
        return [self._by_name[name] for name in selection]

    def _rerun_set(self, start: str | None) -> set[str]:
        """Stages that must not be served from cache: `--from` and everything downstream."""
        if not start:
            return set()
        return set(self._names[self._names.index(start) :])

    # -- execution ------------------------------------------------------
    def run(
        self,
        only: Iterable[str] | None = None,
        start: str | None = None,
        force: Iterable[str] | None = None,
    ) -> PipelineResult:
        forced = {name.strip() for name in (force or []) if name.strip()}
        unknown = forced - set(self._names)
        if unknown:
            raise PipelineError(f"Cannot force unknown stage(s) {sorted(unknown)}")
        if start and start not in self._names:
            raise PipelineError(f"Unknown stage {start!r}; available: {self._names}")
        invalidated = self._rerun_set(start) | forced

        state = KitState(cfg=self.cfg)
        upstream = ""
        reports: list[StageReport] = []
        for stage in self.select(only=only):
            extra = stage.extra_key(state) if stage.extra_key is not None else None
            fingerprint = stage.fingerprint(self.cfg, upstream, extra)
            cache_dir = Path(self.cfg.work_dir) / stage.name
            started = time.perf_counter()

            cached = (
                stage.cacheable
                and stage.load is not None
                and stage.name not in invalidated
                and self._cache_valid(cache_dir, fingerprint)
            )
            if cached and stage.load is not None:
                artifacts = stage.load(state, cache_dir)
                status = "cached"
            else:
                stage.run(state)
                artifacts = 0
                if stage.save is not None:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    artifacts = stage.save(state, cache_dir)
                    (cache_dir / "_fingerprint.json").write_text(
                        json.dumps({"stage": stage.name, "fingerprint": fingerprint, "artifacts": artifacts})
                    )
                status = "ran"
            reports.append(
                StageReport(stage.name, stage.title, status, fingerprint, time.perf_counter() - started, artifacts)
            )
            state.reports = reports  # export reads the report list for the manifest
            upstream = fingerprint

        state.reports = reports
        return PipelineResult(state=state, reports=reports, kit_dir=state.kit_dir)

    @staticmethod
    def _cache_valid(cache_dir: Path, fingerprint: str) -> bool:
        record = cache_dir / "_fingerprint.json"
        if not record.is_file():
            return False
        try:
            stored = json.loads(record.read_text())
        except (OSError, ValueError):
            return False
        if stored.get("fingerprint") != fingerprint:
            return False
        artifacts = int(stored.get("artifacts", 0))
        if artifacts <= 0:
            return False
        # Refuse a half-deleted cache: every recorded artifact must still be on disk.
        present = [path for path in cache_dir.iterdir() if path.is_file() and path.name != "_fingerprint.json"]
        return len(present) >= artifacts
