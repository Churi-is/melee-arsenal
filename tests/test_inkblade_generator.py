"""Tests for the staged Inkblade kit generator (`tools/inkblade_gen`)."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image, ImageDraw

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
    canvas = textures.paint_linework(part, layout, canvas)
    colours = textures.role_colours(part, layout, cfg.seed)
    final = textures.finalise_atlas(canvas, layout, colours, cfg.quantize_colors).convert("RGB")
    return final.tobytes()


class LineworkPortTests(unittest.TestCase):
    """The ink pass must be the bold kit's overlay, not an approximation of it.

    `reink_bold.py` draws its overlay with numpy `float64` polygons and cv2 masks. This test runs that
    exact algorithm (transcribed below with numpy kept, only the contour source swapped for the UV
    island polygon) and compares it to the pure-Python port layer by layer.
    """

    @staticmethod
    def _reference_layers(layout, part_id):
        """Verbatim R5 overlay loop, from `assets/inkblade_bold_kit/tools/reink_bold.py`.

        One RNG stream for the whole atlas and islands in layout order, exactly like the original.
        """
        import hashlib
        import math
        import random

        import numpy as np
        from PIL import Image, ImageDraw, ImageFilter

        width, height = layout.atlas_size
        shade = Image.new("RGBA", (width, height))
        ink = Image.new("RGBA", (width, height))
        sd = ImageDraw.Draw(shade)
        draw = ImageDraw.Draw(ink)
        rng = random.Random(int(hashlib.sha256(("bold5" + part_id).encode()).hexdigest()[:12], 16))
        # `cv2.fillConvexPoly(mask, ...)`: the union of the island's triangles, as one filled polygon.
        mask_image = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(mask_image)
        for island in layout.islands:
            if island.owner and len(island.polygon) >= 3:
                mask_draw.polygon([tuple(v) for v in island.polygon], fill=255)
        mask = np.array(mask_image)
        for island in layout.islands:
          if not island.owner or len(island.polygon) < 3:
            continue
          poly = list(island.polygon)
          x, y, ww, hh = island.rect
          area = 0.5 * abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))))
          if area < 85 or min(ww, hh) < 6:
            continue
          poly = np.array(poly, dtype=float)
          centroid = poly.mean(axis=0)
          short = min(ww, hh)
          if area > 1100:
              candidates = sorted(
                  range(len(poly)),
                  key=lambda i: np.linalg.norm(poly[(i + 1) % len(poly)] - poly[i]),
                  reverse=True,
              )
              for ii in candidates[: 1 if area < 7000 else 2]:
                  p_ = poly[ii]
                  q = poly[(ii + 1) % len(poly)]
                  center = (p_ + q) / 2
                  t = centroid - center
                  t = t / (np.linalg.norm(t) + 1e-9)
                  inset = min(20, max(5, short * 0.13))
                  shallow = inset * 0.25
                  sd.polygon(
                      [tuple(p_ + t * shallow), tuple(q + t * shallow), tuple(q + t * inset), tuple(p_ + t * inset)],
                      fill=(24, 29, 34, rng.randint(28, 52)),
                  )
          perimeter = [tuple(v) for v in poly] + [tuple(poly[0])]
          weight = 3 if short < 22 else 5 if short < 65 else 7
          draw.line(perimeter, fill=(13, 15, 20, 230), width=weight, joint="curve")
          for i, p_ in enumerate(poly):
              q = poly[(i + 1) % len(poly)]
              length = float(np.linalg.norm(q - p_))
              if length < 25 or rng.random() > 0.82:
                  continue
              inward = centroid - (p_ + q) * 0.5
              inward /= np.linalg.norm(inward) + 1e-9
              a = p_ + (q - p_) * rng.uniform(0.06, 0.2) + inward * rng.uniform(5, 9)
              z = p_ + (q - p_) * rng.uniform(0.68, 0.94) + inward * rng.uniform(5, 9)
              mid = (a + z) * 0.5 + inward * rng.uniform(-1.2, 1.2)
              draw.line([tuple(a), tuple(mid), tuple(z)], fill=(17, 17, 21, rng.randint(150, 220)), width=rng.choice([2, 2, 3]))
          if area > 550:
              eligible = []
              for i, corner in enumerate(poly):
                  la = np.linalg.norm(poly[i - 1] - corner)
                  lb = np.linalg.norm(poly[(i + 1) % len(poly)] - corner)
                  if min(la, lb) >= 12:
                      eligible.append(i)
              rng.shuffle(eligible)
              count = min(len(eligible), 2 if area < 3500 else 3 if area < 14000 else 5)
              for i in eligible[:count]:
                  corner = poly[i]
                  va = poly[i - 1] - corner
                  vb = poly[(i + 1) % len(poly)] - corner
                  la = np.linalg.norm(va)
                  lb = np.linalg.norm(vb)
                  va /= la
                  vb /= lb
                  span = min(105, max(16, math.sqrt(area) * 0.42), la * 0.5, lb * 0.5)
                  sd.polygon(
                      [tuple(corner), tuple(corner + va * span * 0.9), tuple(corner + vb * span * 0.9)],
                      fill=(22, 25, 29, rng.randint(35, 66)),
                  )
                  steps = max(4, min(13, int(span / 6)))
                  for j in range(1, steps + 1):
                      f = (j + 0.4) / float(steps + 2)
                      jitter = rng.uniform(-1.0, 1.0)
                      u = span * f
                      start = corner + va * u + vb * 2
                      end = corner + vb * (u * rng.uniform(0.75, 1.05)) + va * 2
                      mid = (start + end) * 0.5 + np.array([jitter, -jitter])
                      draw.line([tuple(start), tuple(mid), tuple(end)], fill=(15, 16, 20, rng.randint(180, 240)), width=rng.choice([2, 2, 3]))
                  if span > 30 and rng.random() < 0.55:
                      p_ = corner + va * span * 0.66 + vb * span * 0.1
                      q = p_ + vb * min(14, span * 0.22)
                      draw.line([tuple(p_), tuple(q)], fill=(17, 17, 22, 185), width=2)
          if area > 2500:
              for _ in range(min(22, int(area / 5000) + 4)):
                  px = rng.randint(x, x + ww - 1)
                  py = rng.randint(y, y + hh - 1)
                  if px < 0 or py < 0 or px >= width or py >= height or not mask[py, px]:
                      continue
                  angle = rng.uniform(-0.35, 0.35) if ww >= hh else rng.uniform(1.18, 1.94)
                  length = rng.uniform(5, min(45, max(7, max(ww, hh) * 0.1)))
                  dx = math.cos(angle) * length
                  dy = math.sin(angle) * length
                  draw.line(
                      [(px, py), (px + dx * 0.55, py + dy * 0.55), (px + dx, py + dy)],
                      fill=(20, 19, 23, rng.randint(125, 205)),
                      width=rng.choice([1, 2, 2]),
                  )
                  if rng.random() < 0.35:
                      draw.line([(px + 1, py + 3), (px + dx * 0.6, py + dy * 0.6 + 3)], fill=(255, 240, 210, 130), width=1)
          # `cv2.erode(mask, np.ones((3,3),np.uint8), iterations=1)`; Pillow's MinFilter(3) is the same
          # 3x3 erosion, substituted in both the port and here so this test compares the drawing code.
        clip = np.array(mask_image.filter(ImageFilter.MinFilter(3)))
        for layer in (shade, ink):
            alpha = np.minimum(np.asarray(layer.getchannel("A")), clip)
            layer.putalpha(Image.fromarray(alpha.astype("uint8")))
        return shade, ink

    def test_port_matches_the_reference_overlay(self) -> None:
        try:
            import numpy  # noqa: F401
        except ImportError:  # pragma: no cover - numpy is only needed for this cross-check
            self.skipTest("numpy unavailable")

        from PIL import ImageChops

        from tools.inkblade_gen import textures

        cfg = RunConfig(
            density=768.0, parts=("blade_jakobs", "grip_torgue", "accessory_serrated_edge")
        )
        parts = build_parts(cfg.parts)
        layouts = unwrap_parts(parts, cfg)
        for part_id, layout in layouts.items():
            mine_shade, mine_ink = textures.linework_layers(part_id, layout)
            ref_shade, ref_ink = self._reference_layers(layout, part_id)
            for name, mine, ref in (("shade", mine_shade, ref_shade), ("ink", mine_ink, ref_ink)):
                difference = ImageChops.difference(mine, ref)
                mismatched = sum(1 for value in difference.tobytes() if value)
                self.assertEqual(mismatched, 0, f"{part_id}: {name} layer differs from the R5 overlay")


class FaceInkPortTests(unittest.TestCase):
    """The `texture_face` hatch/perimeter block must match the reference line for line."""

    def _reference_canvas(self, layout, part_id: str, ink_px: int):
        """Verbatim transcription of `source/r3/tools/physical_uv.py::texture_face`'s ink block."""
        rng = random.Random(int.from_bytes(hashlib.sha256(part_id.encode()).digest()[:8], "little"))
        canvas = Image.new("RGB", layout.atlas_size, (13, 17, 23))
        for island in layout.islands:
            if not island.owner:
                continue
            x, y, w, h = island.rect
            area = w * h
            polygon = [(float(px - x), float(py - y)) for px, py in island.polygon]
            canvas.paste(Image.new("RGB", (w, h), (200, 200, 200)), (x, y))
            hatch = Image.new("RGBA", (w, h))
            hd = ImageDraw.Draw(hatch)
            if min(w, h) > 30:
                for vx, vy in polygon[: 1 if area < 16000 else 2]:
                    towardx = 1 if vx < w / 2 else -1
                    towardy = 1 if vy < h / 2 else -1
                    for j in range(6):
                        hx = vx + towardx * (7 + j * 5)
                        hy = vy + towardy * 7
                        hd.line(
                            (hx, hy, hx - towardx * 4, hy + towardy * (11 + rng.randrange(5))),
                            fill=(34, 39, 43, 235),
                            width=2,
                        )
            face = canvas.crop((x, y, x + w, y + h)).convert("RGBA")
            face = Image.alpha_composite(face, hatch).convert("RGB")
            ImageDraw.Draw(face).line(polygon + [polygon[0]], fill=(11, 15, 21), width=int(ink_px), joint="curve")
            canvas.paste(face, (x, y))
        return canvas

    def test_face_ink_matches_the_reference(self) -> None:
        from PIL import ImageChops

        from tools.inkblade_gen import textures

        cfg = RunConfig(density=768.0, parts=("grip_jakobs", "blade_jakobs", "accessory_serrated_edge"))
        parts = build_parts(cfg.parts)
        layouts = unwrap_parts(parts, cfg)
        for part_id, layout in layouts.items():
            mine = Image.new("RGB", layout.atlas_size, (13, 17, 23))
            for island in layout.islands:
                if island.owner:
                    mine.paste(Image.new("RGB", island.rect[2:], (200, 200, 200)), island.rect[:2])
            textures._paint_face_ink(part_id, layout, mine, int(cfg.ink_px))
            ref = self._reference_canvas(layout, part_id, int(cfg.ink_px))
            difference = ImageChops.difference(mine, ref)
            mismatched = sum(1 for value in difference.tobytes() if value)
            self.assertEqual(mismatched, 0, f"{part_id}: face ink differs from the reference")


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
        self.assertEqual(RunConfig().linework_seed, "", "the shipped strokes stay the default")

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
        for part_id, key, expected in (
            ("accessory_reinforced_heavy_spine", "accessory_face_gap_m", 0.0401667),
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
