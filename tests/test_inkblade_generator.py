"""Tests for the staged Inkblade kit generator (`tools/inkblade_gen`)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from PIL import Image

from tools.inkblade_gen import Pipeline, RunConfig
from tools.inkblade_gen.config import ELEMENTS
from tools.inkblade_gen import geometry
from tools.inkblade_gen.geometry import build_parts
from tools.inkblade_gen.stages import KitState, Stage, _run_linework, build_manifest, default_stages
from tools.inkblade_gen.uv import unwrap_parts



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
            ["geometry", "uv", "flat", "mottle", "linework", "decals", "textures", "export", "render"],
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
            self.assertIn("export", first.ran)
            self.assertIn("render", first.ran)
            self.assertEqual(
                second.cached, ["geometry", "uv", "flat", "mottle", "linework", "decals", "textures"]
            )
            self.assertEqual(second.ran, ["export", "render"])

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
            self.assertEqual(result.ran, ["linework", "decals", "textures", "export", "render"])

    def test_restarting_from_a_stage_reuses_upstream_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Pipeline(self._cfg(tmp)).run()
            result = Pipeline(self._cfg(tmp)).run(start="textures")
            self.assertIn("geometry", result.cached)
            self.assertEqual(result.ran[-3:], ["textures", "export", "render"])

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


class GeneratorBehaviourTests(unittest.TestCase):
    """Geometry invariants and deterministic texture generation."""

    def test_r6_stage_regression_hashes(self) -> None:
        # Representative element, default seeds, no decals. Hash pixels rather than PNG
        # encoding; intentional art changes require reviewing and updating these baselines.
        expected = {
            "geometry": "f9c5fedb561f2669de19eaba1ed03d95c3c1f82e821abcae40ea700b10f14124",
            "uv": "607d808523c4a09cf3022f01cb3102a5d3554cee5741a49b577ee130232345a8",
            "flat": "3e245e2713f388e1b863660beac6078904e64e64cf053d01fd03066fa0e3f45a",
            "mottle": "716ccc2cb9f473d279e3d4d37403a0c9c0a1097fb8354bd357e8ddcc1ecaffe9",
            "linework": "2e3ac8c2d2d63ac569c95ace26188ccbdfc7fe473da67f88fece291331e0b16d",
            "decals": "2e3ac8c2d2d63ac569c95ace26188ccbdfc7fe473da67f88fece291331e0b16d",
            "textures": "cc0155c8db7738dd6506005990ef50cf9cd8b9d461c4558119bd10dfd060b123",
        }
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            cfg = RunConfig(out_dir=Path(tmp) / "kit", work_dir=work, density=256.0,
                            ink_px=3, padding=4, parts=("element_fire",), bake_combinations=False)
            Pipeline(cfg).run()
            for stage, digest in expected.items():
                name = ("element_fire.json" if stage in ("geometry", "uv") else
                        "element_fire_basecolor.png" if stage == "textures" else "element_fire.png")
                path = work / stage / name
                if path.suffix == ".png":
                    with Image.open(path) as image:
                        data = image.convert("RGBA").tobytes()
                else:
                    data = path.read_bytes()
                with self.subTest(stage=stage):
                    self.assertEqual(hashlib.sha256(data).hexdigest(), digest)

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
    canvas = textures.paint_linework(part, layout, canvas)
    colours = textures.role_colours(part, layout, cfg.seed)
    final = textures.finalise_atlas(canvas, layout, colours, cfg.quantize_colors).convert("RGB")
    return final.tobytes()


class InkSeedTests(unittest.TestCase):
    """The ink passes are seedable, and padding never paints outside an island's rectangle."""

    def _layout(self, part_id: str):
        cfg = RunConfig(density=512.0, parts=(part_id,))
        parts = build_parts(cfg.parts)
        return parts[part_id], unwrap_parts(parts, cfg)[part_id], cfg

    def test_linework_seed_rerolls_the_ink_and_stays_deterministic(self) -> None:
        from tools.inkblade_gen import textures

        part, layout, _ = self._layout("grip_jakobs")
        reference = textures.linework_layers(part.part_id, layout)
        again = textures.linework_layers(part.part_id, layout)
        self.assertEqual(
            b"".join(layer.tobytes() for layer in reference),
            b"".join(layer.tobytes() for layer in again),
            "the same salt must reproduce the same ink",
        )
        for salt in ("seed-a", "seed-b"):
            shifted = textures.linework_layers(part.part_id, layout, salt)
            self.assertNotEqual(
                reference[1].tobytes(), shifted[1].tobytes(), f"salt {salt} left the ink unchanged"
            )
        self.assertEqual(
            textures.paint_linework(part, layout, textures.new_canvas(layout), seed_salt="seed-a").tobytes(),
            textures.paint_linework(part, layout, textures.new_canvas(layout), seed_salt="seed-a").tobytes(),
        )

    def test_cli_exposes_the_ink_seed(self) -> None:
        from tools.inkblade_gen.cli import build_parser, config_from_args

        args = build_parser().parse_args(["--linework-seed", "salt"])
        self.assertEqual(config_from_args(args).linework_seed, "salt")
        self.assertEqual(RunConfig().linework_seed, "", "the default ink seed is empty")

    def test_padding_stays_inside_each_island_rectangle(self) -> None:
        from PIL import ImageChops, ImageDraw

        from tools.inkblade_gen import textures

        part, layout, cfg = self._layout("grip_jakobs")
        canvas = textures.new_canvas(layout)
        canvas = textures.paint_flat_colours(part, layout, canvas, cfg.seed)
        canvas = textures.paint_mottle(part, layout, canvas)
        colours = textures.role_colours(part, layout, cfg.seed)
        sealed = textures.seal_padding(canvas.copy(), layout, colours)

        width, height = layout.atlas_size
        rects = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(rects)
        for island in layout.islands:
            if island.owner:
                x, y, w, h = island.rect
                draw.rectangle((x, y, x + w - 1, y + h - 1), fill=255)
        changed = ImageChops.difference(canvas, sealed).convert("L").point(lambda v: 255 if v else 0)
        outside = ImageChops.subtract(changed, rects)
        self.assertEqual(
            sum(1 for value in outside.tobytes() if value),
            0,
            "padding painted over texels that belong to no island",
        )
        self.assertGreater(sum(1 for value in changed.tobytes() if value), 0)


class ElementTintTests(unittest.TestCase):
    """`recolor_accent` must tint the accent islands, not blow them out to white."""

    def test_tint_keeps_luminance_and_ink(self) -> None:
        from tools.inkblade_gen import textures
        from tools.inkblade_gen.config import ELEMENTS

        cfg = RunConfig(density=192.0, padding=4, parts=("blade_maliwan",))
        parts = build_parts(cfg.parts)
        layouts = unwrap_parts(parts, cfg)
        layout = layouts["blade_maliwan"]
        canvas = textures.new_canvas(layout)
        canvas = textures.paint_flat_colours(parts["blade_maliwan"], layout, canvas, cfg.seed)
        tint = textures.hex_to_rgb(ELEMENTS["Corrosive"])
        tinted = textures.recolor_accent(canvas, layout, ELEMENTS["Corrosive"])

        island = next(i for i in layout.islands if i.role == "accent" and i.owner)
        x, y, w, h = island.rect
        centre = (x + w // 2, y + h // 2)
        before = canvas.getpixel(centre)
        after = tinted.getpixel(centre)
        expected = tuple(int(round(channel * max(before) / 255.0)) for channel in tint)
        self.assertEqual(after, expected)
        self.assertLess(max(after), 255 - 1, "the tint must not clip to white")
        self.assertNotEqual(after, before)

        # A dark pixel inside an accent island stays dark (ink is never recoloured).
        dark = None
        for px in range(x, x + w):
            for py in range(y, y + h):
                if max(canvas.getpixel((px, py))) < 40:
                    dark = (px, py)
                    break
            if dark:
                break
        if dark:
            self.assertEqual(tinted.getpixel(dark), canvas.getpixel(dark))


class CombinationAssemblyTests(unittest.TestCase):
    """Socketed pieces must land on the blade skin, with their own atlas."""

    def test_bifacial_pieces_get_the_blade_skin_gap(self) -> None:
        from tools.inkblade_gen.glb import build_part_glb, read_glb, shift_bifacial_faces

        cfg = RunConfig(
            density=192.0,
            padding=4,
            parts=("blade_torgue", "accessory_reinforced_heavy_spine", "element_explosive"),
        )
        parts = build_parts(cfg.parts)
        layouts = unwrap_parts(parts, cfg)
        blade = parts["blade_torgue"]
        # The accessory gap follows the r7 slimmer spine (BLADE_SPINE_SCALE).
        accessory_gap = (
            geometry.BLADE_THICKNESS_M["Torgue"] * geometry.BLADE_SPINE_SCALE / 4
            - geometry.ACCESSORY_FACE_CLIP_M
        )
        for part_id, key, expected in (
            ("accessory_reinforced_heavy_spine", "accessory_face_gap_m",
             parts["accessory_reinforced_heavy_spine"].fit_inner_shift_m + accessory_gap),
            ("element_explosive", "element_face_gap_m", 0.04675),
        ):
            part = parts[part_id]
            doc, _ = read_glb(build_part_glb(part, layouts[part_id], b"", revision=cfg.revision))
            bare = {
                node["name"]: node["translation"][2]
                for node in doc["nodes"]
                if node.get("name") in ("FACE_FRONT", "FACE_BACK")
            }
            self.assertAlmostEqual(bare["FACE_FRONT"], part.fit_inner_shift_m, places=6)
            shift_bifacial_faces(doc, float(blade.notes[key]))
            fitted = {
                node["name"]: node["translation"][2]
                for node in doc["nodes"]
                if node.get("name") in ("FACE_FRONT", "FACE_BACK")
            }
            self.assertAlmostEqual(fitted["FACE_FRONT"], expected, places=6)
            self.assertAlmostEqual(fitted["FACE_BACK"], -expected, places=6)

    def test_combination_pieces_keep_their_own_atlas(self) -> None:
        from tools.inkblade_gen.render import assembly_scene

        cfg = RunConfig(
            density=192.0,
            padding=4,
            parts=("blade_torgue", "grip_torgue", "accessory_reinforced_heavy_spine"),
        )
        parts = build_parts(cfg.parts)
        layouts = unwrap_parts(parts, cfg)
        state = _painted_state(cfg, parts, layouts)
        triangles, name, atlases = assembly_scene(
            state, ("05_Detonator", "Torgue", "Torgue", "reinforced_heavy_spine", None)
        )
        self.assertEqual(name, "05_Detonator")
        self.assertEqual(len(atlases), 3, "blade, grip and accessory each need their own atlas")
        materials = {triangle.material for triangle in triangles}
        self.assertEqual(materials, {0, 1, 2})
        self.assertIs(atlases[0], state.finals["blade_torgue"])
        self.assertIs(atlases[2], state.finals["accessory_reinforced_heavy_spine"])

        # The accessory's decorative plate must end up in front of the blade, not buried in it.
        blade_front = max(
            max(triangle.a[2], triangle.b[2], triangle.c[2])
            for triangle in triangles
            if triangle.material == 0
        )
        accessory_front = max(
            max(triangle.a[2], triangle.b[2], triangle.c[2])
            for triangle in triangles
            if triangle.material == 2
        )
        self.assertGreater(
            accessory_front,
            blade_front,
            "the socketed accessory should sit proud of the blade skin",
        )


class RenderStageTests(unittest.TestCase):
    """Preview renders are a stage too: optional, ordered last, and Blender-free."""

    def test_render_stage_is_last_and_off_by_default(self) -> None:
        names = [stage.name for stage in default_stages()]
        self.assertEqual(names[-1], "render")
        self.assertFalse(RunConfig().render_enabled)

    def test_rasteriser_draws_the_part_deterministically(self) -> None:
        from tools.inkblade_gen.render import BACKGROUND, part_scene, rasterise

        cfg = RunConfig(density=320.0, ink_px=3, padding=5, parts=("grip_jakobs",), quantize_colors=None)
        parts = build_parts(cfg.parts)
        layouts = unwrap_parts(parts, cfg)
        state = _painted_state(cfg, parts, layouts)
        triangles = part_scene(state.parts["grip_jakobs"], state.layouts["grip_jakobs"])
        self.assertTrue(triangles)
        first = rasterise(triangles, state.finals["grip_jakobs"], size=(200, 200), supersample=1)
        second = rasterise(triangles, state.finals["grip_jakobs"], size=(200, 200), supersample=1)
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertEqual(first.size, (200, 200))
        pixels = first.tobytes()
        covered = sum(1 for index in range(0, len(pixels), 3) if tuple(pixels[index : index + 3]) != BACKGROUND)
        self.assertGreater(covered / (first.width * first.height), 0.05)
        self.assertLess(covered / (first.width * first.height), 0.95)

    def test_bifacial_parts_render_both_instances(self) -> None:
        from tools.inkblade_gen.render import part_scene, part_triangles

        cfg = RunConfig(density=320.0, ink_px=3, padding=5, parts=("element_fire",), quantize_colors=None)
        parts = build_parts(cfg.parts)
        layouts = unwrap_parts(parts, cfg)
        single = part_triangles(parts["element_fire"], layouts["element_fire"])
        both = part_scene(parts["element_fire"], layouts["element_fire"])
        self.assertAlmostEqual(len(both), len(single) * 2, delta=2)

    def test_render_stage_writes_previews_and_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = RunConfig(
                out_dir=Path(tmp) / "kit",
                work_dir=Path(tmp) / "work",
                density=256.0,
                ink_px=3,
                padding=4,
                parts=("blade_jakobs", "grip_jakobs"),
                render_enabled=True,
                render_dir=Path(tmp) / "renders",
                render_size=160,
                render_supersample=1,
            )
            result = Pipeline(cfg).run()
            renders = Path(cfg.render_dir)
            self.assertEqual(result.state.renders["parts"], 2)
            # `blade_jakobs` + `grip_jakobs` is the recipe for the 03_Cavalier assembly.
            self.assertEqual(result.state.renders["combinations"], 1)
            self.assertTrue((renders / "combinations" / "03_Cavalier.png").is_file())
            self.assertTrue((renders / "overview_combinations.png").is_file())
            self.assertTrue((renders / "parts" / "blade_jakobs.png").is_file())
            self.assertTrue((renders / "parts" / "grip_jakobs.png").is_file())
            self.assertTrue((renders / "overview_parts.png").is_file())
            self.assertTrue((renders / "README.md").is_file())
            with Image.open(renders / "parts" / "blade_jakobs.png") as tile:
                self.assertEqual(tile.size, (160, 160))
            with Image.open(renders / "overview_parts.png") as sheet:
                self.assertGreater(sheet.width, 160)
                self.assertGreater(sheet.height, 160)

    def test_kit_run_without_renders_leaves_no_preview_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = RunConfig(
                out_dir=Path(tmp) / "kit",
                work_dir=Path(tmp) / "work",
                density=256.0,
                ink_px=3,
                padding=4,
                parts=("element_fire",),
                render_dir=Path(tmp) / "renders",
            )
            Pipeline(cfg).run()
            self.assertFalse(Path(cfg.render_dir).exists())


def _painted_state(cfg: RunConfig, parts, layouts):
    """Run the paint stages for a small part set without touching the filesystem."""
    from tools.inkblade_gen import textures
    from tools.inkblade_gen.stages import KitState

    state = KitState(cfg=cfg)
    state.parts = parts
    state.layouts = layouts
    for part_id, layout in layouts.items():
        canvas = textures.new_canvas(layout)
        canvas = textures.paint_flat_colours(parts[part_id], layout, canvas, cfg.seed)
        canvas = textures.paint_mottle(parts[part_id], layout, canvas)
        canvas = textures.paint_linework(parts[part_id], layout, canvas)
        colours = textures.role_colours(parts[part_id], layout, cfg.seed)
        state.colours[part_id] = colours
        state.finals[part_id] = textures.finalise_atlas(canvas, layout, colours, cfg.quantize_colors)
    return state


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


class R7BladeSilhouetteTests(unittest.TestCase):
    """The r7 model pass: longer, slimmer blades and a real Jakobs sabre."""

    R3_TIP_Z = {"Hyperion": 1.45, "Bandit": 1.25, "Jakobs": 1.39, "Maliwan": 1.38, "Torgue": 1.19}

    @staticmethod
    def _bbox(part, axis: int) -> tuple[float, float]:
        values = [vertex[axis] for face in part.faces for vertex in face.verts]
        return min(values), max(values)

    def test_every_blade_is_longer_and_the_manifest_reach_matches_the_mesh(self) -> None:
        parts = build_parts()
        for maker, r3_tip in self.R3_TIP_Z.items():
            with self.subTest(maker=maker):
                part = parts[f"blade_{maker.lower()}"]
                tip = max(vertex[2] for face in part.faces for vertex in face.verts)
                self.assertGreater(tip, r3_tip * 1.05, "blade did not get longer")
                self.assertAlmostEqual(tip, part.meta["base_reach_m"], places=4)

    def test_spine_got_thinner_without_changing_the_face_width(self) -> None:
        """Only the Y (edge-on spine) shrinks; the X silhouette must be untouched."""
        parts = build_parts()
        with mock.patch.object(geometry, "BLADE_SPINE_SCALE", 1.0):
            baseline = build_parts()
        for maker in geometry.BLADES:
            with self.subTest(maker=maker):
                part_id = f"blade_{maker.lower()}"
                thin = max(abs(value) for value in self._bbox(parts[part_id], 1))
                thick = max(abs(value) for value in self._bbox(baseline[part_id], 1))
                self.assertAlmostEqual(thin, thick * geometry.BLADE_SPINE_SCALE, places=6)
                self.assertEqual(self._bbox(parts[part_id], 0), self._bbox(baseline[part_id], 0))
                # Socket gaps follow the thinner skin so accessories stay attached.
                core = parts[part_id].notes["core_thickness_m"]
                self.assertAlmostEqual(core, geometry.BLADE_THICKNESS_M[maker] * geometry.BLADE_SPINE_SCALE / 2)
                self.assertAlmostEqual(
                    parts[part_id].notes["accessory_face_gap_m"],
                    core / 2 - geometry.ACCESSORY_FACE_CLIP_M,
                )

    @staticmethod
    def _span_at(pts, z: float) -> tuple[float, float]:
        """Left/right silhouette x at a given (stretched) z."""
        crossings: list[float] = []
        for (x1, z1), (x2, z2) in zip(pts, pts[1:] + pts[:1]):
            if (z1 <= z <= z2) or (z2 <= z <= z1):
                if z1 != z2:
                    crossings.append(x1 + (z - z1) / (z2 - z1) * (x2 - x1))
        return min(crossings), max(crossings)

    def test_jakobs_stays_straight_and_full_width_until_the_very_end(self) -> None:
        """Sabre silhouette: straight out of the hilt, then a subtle curve + small tip taper."""
        geo = geometry._blade("Jakobs")
        main_profile = next(component for component in geo.components if component.kind == "profile")
        pts = [tuple(point) for point in main_profile.args[0]]
        tip = max(pts, key=lambda point: point[1])
        self.assertLessEqual(max(x for x, _ in pts), 0.25, "blade still hooks out sideways")
        self.assertTrue(0.05 < tip[0] < 0.20, "only a subtle late lean, not a recurved hook")
        self.assertGreater(tip[1], 1.5, "keep the longer blade length")

        widths, centres = [], []
        for pre_z in (0.30, 0.55, 0.80, 0.92, 1.10, 1.16):
            left, right = self._span_at(pts, geometry._blade_z(pre_z))
            widths.append(right - left)
            centres.append((right + left) / 2)
        self.assertLess(max(widths) / min(widths), 1.08, "blade tapers too much / too early")
        self.assertLess(max(centres) - min(centres), 0.02, "blade starts curving too soon")

        late_left, late_right = self._span_at(pts, geometry._blade_z(1.16))
        self.assertGreaterEqual(late_right - late_left, 0.90 * widths[0], "taper must start at the very end")
        tip_left, tip_right = self._span_at(pts, geometry._blade_z(1.38))
        self.assertLess(tip_right - tip_left, 0.50 * widths[0], "small clipped point at the tip")


class FlaredCleaveGuardTests(unittest.TestCase):
    """The yellow end caps must be seated in the plate, not floating in front of it."""

    @staticmethod
    def _inside(polygon, x: float, z: float) -> bool:
        inside = False
        for index, (x1, z1) in enumerate(polygon):
            x2, z2 = polygon[(index + 1) % len(polygon)]
            if (z1 > z) != (z2 > z) and x < x1 + (z - z1) * (x2 - x1) / (z2 - z1):
                inside = not inside
        return inside

    def test_end_caps_intersect_the_plate(self) -> None:
        geo = geometry._accessory("flared_cleave_guard")
        plate = next(component for component in geo.components if component.kind == "profile")
        plate_pts = plate.args[0]
        plate_front_y = plate.args[2] - plate.args[1] / 2 + geometry.CLEAVE_GUARD_PLATE_SHIFT
        caps = [
            component for component in geo.components
            if component.kind == "box" and abs(abs(component.args[0][0]) - 0.27) < 1e-9
        ]
        self.assertEqual(len(caps), 2)
        for cap in caps:
            with self.subTest(cap=cap.args[0]):
                (cx, cy, cz), (_, sy, _) = cap.args[0], cap.args[1]
                self.assertLess(cy - sy / 2, plate_front_y)
                self.assertGreater(cy + sy / 2, plate_front_y, "cap floats clear of the plate")
                self.assertTrue(self._inside(plate_pts, cx, cz), "cap is not over the wing")


class StageSourceFingerprintTests(unittest.TestCase):
    """A stage's real source files belong in its cache fingerprint."""

    def test_geometry_stage_declares_its_module(self) -> None:
        stage = next(stage for stage in default_stages() if stage.name == "geometry")
        self.assertIn("geometry.py", [str(source) for source in stage.sources])

    def test_editing_a_source_file_invalidates_the_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "geometry.py"
            source.write_text("RULE = 1\n")
            stage = Stage("probe", "Probe", lambda state: None, sources=(source,))
            cfg = RunConfig()
            first = stage.fingerprint(cfg, "")
            source.write_text("RULE = 2\n")
            self.assertNotEqual(first, stage.fingerprint(cfg, ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
