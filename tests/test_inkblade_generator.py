"""Tests for the staged Inkblade kit generator (`tools/inkblade_gen`)."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image

from tools.inkblade_gen import Pipeline, RunConfig
from tools.inkblade_gen.config import ELEMENTS
from tools.inkblade_gen.geometry import build_parts
from tools.inkblade_gen.glb import _to_gltf
from tools.inkblade_gen.stages import KitState, Stage, _run_linework, build_manifest, default_stages
from tools.inkblade_gen.uv import unwrap_parts

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_KIT = REPO_ROOT / "assets" / "inkblade_bold_kit"


def _read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    assert data[:4] == b"glTF", "not a GLB"
    json_length = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + json_length])
    offset = 20 + json_length
    bin_length = struct.unpack_from("<I", data, offset)[0]
    return doc, data[offset + 8 : offset + 8 + bin_length]


class StageStackTests(unittest.TestCase):
    """The stack shape: ordered, linear, and extensible without touching existing stages."""

    def test_default_stack_is_ordered(self) -> None:
        names = [stage.name for stage in default_stages()]
        self.assertEqual(
            names,
            ["geometry", "uv", "flat", "mottle", "linework", "decals", "textures", "export"],
        )

    def test_flat_texture_stage_writes_pngs_before_models(self) -> None:
        """The flat colour pass is a real PNG-writing stage, not an inline step of the exporter."""
        flat = next(stage for stage in default_stages() if stage.name == "flat")
        self.assertIsNotNone(flat.save)
        self.assertIsNotNone(flat.load)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = RunConfig(
                out_dir=Path(tmp) / "kit",
                work_dir=Path(tmp) / "work",
                density=320.0,
                ink_px=3,
                padding=5,
                parts=("element_fire",),
            )
            result = Pipeline(cfg).run(only=["flat"])
            # `--only flat` runs the prerequisites and stops before the export stage.
            self.assertEqual([r.name for r in result.reports], ["geometry", "uv", "flat"])
            self.assertIsNone(result.kit_dir)
            png = Path(tmp) / "work" / "flat" / "element_fire.png"
            self.assertTrue(png.is_file())
            with Image.open(png) as image:
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.size, tuple(result.state.layouts["element_fire"].atlas_size))

    def test_new_stage_can_be_inserted_without_editing_existing_ones(self) -> None:
        """A future art pass (decals, stencils, grime...) is one extra `Stage` in the list."""
        calls: list[str] = []

        def run_extra(state) -> None:  # noqa: ANN001 - matches the Stage.run signature
            calls.append("extra")

        stages = list(default_stages())
        index = next(i for i, stage in enumerate(stages) if stage.name == "linework")
        stages.insert(index + 1, Stage("extra", "Test art pass", run_extra, cacheable=False))

        with tempfile.TemporaryDirectory() as tmp:
            cfg = RunConfig(
                out_dir=Path(tmp) / "kit",
                work_dir=Path(tmp) / "work",
                density=320.0,
                ink_px=3,
                padding=5,
                parts=("element_fire",),
            )
            result = Pipeline(cfg, stages).run()
            self.assertEqual(calls, ["extra"])
            self.assertIn("extra", [report.name for report in result.reports])
            order = [report.name for report in result.reports]
            self.assertLess(order.index("linework"), order.index("extra"))
            self.assertLess(order.index("extra"), order.index("textures"))


class PipelineCacheTests(unittest.TestCase):
    """Stage fingerprints: unchanged stages are restored, changed ones invalidate downstream work."""

    def _cfg(self, tmp: str) -> RunConfig:
        return RunConfig(
            out_dir=Path(tmp) / "kit",
            work_dir=Path(tmp) / "work",
            density=320.0,
            ink_px=3,
            padding=5,
            parts=("blade_jakobs", "grip_jakobs"),
        )

    def test_second_run_is_served_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Pipeline(self._cfg(tmp)).run()
            second = Pipeline(self._cfg(tmp)).run()
            self.assertEqual(first.ran[-1], "export")
            self.assertEqual(second.cached, ["geometry", "uv", "flat", "mottle", "linework", "decals", "textures"])
            self.assertEqual(second.ran, ["export"])

    def test_forcing_a_stage_reruns_it_even_with_a_warm_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Pipeline(self._cfg(tmp)).run()
            result = Pipeline(self._cfg(tmp)).run(force=["linework"])
            self.assertIn("linework", result.ran)
            self.assertEqual(result.cached[:4], ["geometry", "uv", "flat", "mottle"])

    def test_editing_a_stage_invalidates_it_and_everything_downstream(self) -> None:
        """The fingerprint chains, so a changed ink stage re-runs every later painting stage."""

        def edited_linework(state: KitState) -> None:
            _run_linework(state)

        with tempfile.TemporaryDirectory() as tmp:
            Pipeline(self._cfg(tmp)).run()
            stages = default_stages()
            for index, stage in enumerate(stages):
                if stage.name == "linework":
                    stages[index] = Stage("linework", stage.title, edited_linework, stage.save, stage.load)
            result = Pipeline(self._cfg(tmp), stages).run()
            self.assertEqual(result.cached, ["geometry", "uv", "flat", "mottle"])
            self.assertEqual(result.ran, ["linework", "decals", "textures", "export"])

    def test_restarting_from_a_stage_reuses_upstream_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Pipeline(self._cfg(tmp)).run()
            result = Pipeline(self._cfg(tmp)).run(start="textures")
            self.assertIn("geometry", result.cached)
            self.assertEqual(result.ran[-2:], ["textures", "export"])

    def test_decal_art_change_invalidates_the_decal_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            decals = Path(tmp) / "decals" / "blade_jakobs"
            decals.mkdir(parents=True)
            Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(decals / "badge.png")
            cfg = self._cfg(tmp)
            cfg.decal_dir = Path(tmp) / "decals"
            first = Pipeline(cfg).run()
            self.assertEqual(first.state.decals_applied.get("blade_jakobs"), 1)

            Image.new("RGBA", (32, 32), (0, 0, 255, 255)).save(decals / "stripe.png")
            second = Pipeline(cfg).run()
            self.assertEqual(second.cached[:4], ["geometry", "uv", "flat", "mottle"])
            self.assertIn("decals", second.ran)
            self.assertEqual(second.state.decals_applied.get("blade_jakobs"), 2)

    def test_unknown_stage_names_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline(self._cfg(tmp))
            with self.assertRaises(Exception):
                pipeline.run(only=["nope"])
            with self.assertRaises(Exception):
                pipeline.run(start="nope")


class KitOutputTests(unittest.TestCase):
    """Exported kit must satisfy the UE3 compiler's `socket_manifest.json` contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cfg = RunConfig(
            out_dir=Path(cls._tmp.name) / "kit",
            work_dir=Path(cls._tmp.name) / "work",
            density=320.0,
            ink_px=4,
            padding=6,
            parts=("blade_jakobs", "grip_jakobs", "accessory_serrated_edge", "element_fire"),
        )
        cls.result = Pipeline(cls.cfg).run()
        cls.kit = Path(cls.cfg.out_dir)
        cls.manifest = json.loads((cls.kit / "socket_manifest.json").read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_manifest_schema_and_parts(self) -> None:
        self.assertEqual(self.manifest["schema"], "inkblade.socket-kit/1.0")
        self.assertEqual(self.manifest["revision"], self.cfg.revision)
        self.assertEqual(set(self.manifest["parts"]), {"blade_jakobs", "grip_jakobs", "accessory_serrated_edge", "element_fire"})
        self.assertEqual(self.manifest["elements"], dict(ELEMENTS))
        notes = self.manifest["revision_notes"]
        for name in ("blade_core_depth_multiplier", "heavy_spine_depth_multiplier", "torgue_grip", "stages"):
            self.assertIn(name, notes)
        self.assertEqual(
            notes["stages"], ["geometry", "uv", "flat", "mottle", "linework", "decals", "textures"]
        )

    def test_part_entries_carry_compiler_fields(self) -> None:
        for part_id, entry in self.manifest["parts"].items():
            for key in ("file", "slot", "manufacturer", "texture", "uv_layout", "sockets_gltf_m", "bifacial_fit"):
                self.assertIn(key, entry, f"{part_id} missing {key}")
            self.assertTrue((self.kit / entry["file"]).is_file())
            self.assertTrue((self.kit / entry["texture"]).is_file())
            self.assertTrue((self.kit / entry["uv_layout"]).is_file())
            self.assertIn("MOUNT", entry["sockets_gltf_m"])

    def test_blade_sockets_match_the_shipped_kit(self) -> None:
        shipped = json.loads((SHIPPED_KIT / "socket_manifest.json").read_text())
        for part_id in ("blade_jakobs", "grip_jakobs", "element_fire"):
            self.assertEqual(
                self.manifest["parts"][part_id]["sockets_gltf_m"],
                shipped["parts"][part_id]["sockets_gltf_m"],
                f"{part_id} sockets drifted from the shipped kit",
            )

    def test_textures_are_png_atlases_with_layout_json(self) -> None:
        for part_id, entry in self.manifest["parts"].items():
            layout = json.loads((self.kit / entry["uv_layout"]).read_text())
            self.assertEqual(layout["schema"], "inkblade.physical-uv/2.0")
            self.assertEqual(layout["atlas_px"], entry["atlas_px"])
            with Image.open(self.kit / entry["texture"]) as atlas:
                self.assertEqual(list(atlas.size), layout["atlas_px"])
                self.assertIn(atlas.mode, {"P", "RGB"})
            islands = layout["islands"]
            self.assertTrue(islands)
            owners = [island for island in islands if island.get("owner", True)]
            self.assertTrue(owners)
            for island in owners:
                x, y, w, h = island["rect_px"]
                self.assertGreaterEqual(w, 1)
                self.assertGreaterEqual(h, 1)
                self.assertLessEqual(x + w, layout["atlas_px"][0])
                self.assertLessEqual(y + h, layout["atlas_px"][1])

    def test_glb_nodes_carry_sockets_and_mounts(self) -> None:
        doc, blob = _read_glb(self.kit / "models" / "parts" / "blade_jakobs.glb")
        names = {node.get("name") for node in doc["nodes"]}
        self.assertTrue({"blade_jakobs__ROOT", "MOUNT", "SOCKET_ACCESSORY", "SOCKET_ELEMENT", "SOCKET_GRIP"} <= names)
        self.assertTrue(doc["images"], "atlas must be embedded in the GLB")
        self.assertGreater(len(blob), 0)
        primitives = [primitive for mesh in doc["meshes"] for primitive in mesh["primitives"]]
        self.assertTrue(primitives)
        for primitive in primitives:
            self.assertEqual(primitive["mode"], 4)
            self.assertIn("TEXCOORD_0", primitive["attributes"])

    def test_bifacial_parts_export_front_and_back_faces(self) -> None:
        doc, _ = _read_glb(self.kit / "models" / "parts" / "element_fire.glb")
        names = {node.get("name") for node in doc["nodes"]}
        self.assertTrue({"FACE_FRONT", "FACE_BACK"} <= names)

    def test_generated_kit_compiles_with_the_upk_compiler(self) -> None:
        try:
            from tools.inkblade_upk.kit import InkbladeModularKit
        except ImportError:  # pragma: no cover - the compiler is always in-repo
            self.skipTest("inkblade_upk unavailable")
        kit = InkbladeModularKit(self.kit)
        self.assertEqual(kit.revision, self.cfg.revision)
        self.assertEqual(set(kit.parts), set(self.manifest["parts"]))
        for part_id, part in kit.parts.items():
            self.assertTrue(part.triangles, f"{part_id} exported no triangles")
            self.assertTrue(part.sockets_gltf_m)


class BehaviourParityTests(unittest.TestCase):
    """The geometry stage reproduces the shipped geometry; the texture stack stays deterministic."""

    def test_final_geometry_matches_shipped_bounds(self) -> None:
        parts = build_parts()
        for part_id in ("blade_jakobs", "grip_torgue", "accessory_serrated_edge", "element_fire"):
            mine = parts[part_id]
            gltf = [_to_gltf(v) for face in mine.faces for v in face.verts]
            lo = tuple(round(min(v[i] for v in gltf), 4) for i in range(3))
            hi = tuple(round(max(v[i] for v in gltf), 4) for i in range(3))
            doc, blob = _read_glb(SHIPPED_KIT / "models" / "parts" / f"{part_id}.glb")
            positions: list[tuple[float, float, float]] = []
            for mesh in doc["meshes"]:
                for primitive in mesh["primitives"]:
                    accessor = doc["accessors"][primitive["attributes"]["POSITION"]]
                    view = doc["bufferViews"][accessor["bufferView"]]
                    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
                    stride = view.get("byteStride", 12)
                    for index in range(accessor["count"]):
                        positions.append(struct.unpack_from("<3f", blob, start + index * stride))
            ref_lo = tuple(round(min(p[i] for p in positions), 4) for i in range(3))
            ref_hi = tuple(round(max(p[i] for p in positions), 4) for i in range(3))
            self.assertEqual((lo, hi), (ref_lo, ref_hi), f"{part_id} bounds drifted")

    def test_torgue_grip_keeps_its_hex_shell(self) -> None:
        parts = build_parts()
        grip = parts["grip_torgue"]
        self.assertEqual(grip.notes.get("shell_sides"), 6)
        self.assertEqual(grip.notes.get("rib_count"), 4)
        self.assertEqual(grip.notes.get("shell_radius_m"), 0.094)
        # Box-primitive soup is fully replaced by the hex shell + ribs (plus the cyl primitives).
        shell_and_ribs = 2 + 6 + 4 * 6 * 4
        self.assertGreaterEqual(len(grip.faces), shell_and_ribs)

    def test_texture_stack_is_deterministic(self) -> None:
        cfg = RunConfig(density=256.0, ink_px=3, padding=4, parts=("element_fire",))
        parts = build_parts(cfg.parts)
        layouts = unwrap_parts(parts, cfg)
        first = _paint_atlas_bytes(cfg, parts, layouts)
        second = _paint_atlas_bytes(cfg, parts, layouts)
        self.assertEqual(first, second)


def _paint_atlas_bytes(cfg: RunConfig, parts, layouts) -> bytes:
    """Run the paint stack for one part and return the resulting atlas bytes."""
    from tools.inkblade_gen import textures

    part = parts["element_fire"]
    layout = layouts["element_fire"]
    canvas = textures.new_canvas(layout)
    canvas = textures.paint_flat_colours(part, layout, canvas, cfg.seed)
    canvas = textures.paint_mottle(part, layout, canvas)
    canvas = textures.paint_linework(part, layout, canvas, ink_px=cfg.ink_px)
    colours = textures.role_colours(part, layout, cfg.seed)
    final = textures.finalise_atlas(canvas, layout, colours, cfg.quantize_colors).convert("RGB")
    return final.tobytes()


class ManifestBuilderTests(unittest.TestCase):
    def test_manifest_is_derived_from_finished_state(self) -> None:
        cfg = RunConfig(density=256.0, ink_px=3, padding=4, parts=("element_fire",))
        parts = build_parts(cfg.parts)
        layouts = unwrap_parts(parts, cfg)

        state = KitState(cfg=cfg)
        state.parts = parts
        state.layouts = layouts
        manifest = build_manifest(state)
        self.assertEqual(list(manifest["parts"]), ["element_fire"])
        self.assertEqual(manifest["parts"]["element_fire"]["slot"], "ElementalPartDefinition")
        self.assertTrue(manifest["parts"]["element_fire"]["bifacial_fit"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
