# Inkblade staged kit generator

Pure-Python (Pillow only) replacement for the R3 → R4 → R5 chain of generators. Instead of three
scripts that each rewrote the previous revision's assets, the kit is produced by **one ordered stack
of independent stages**. Every revision tweak (thinner blade slots, the bifacial fittings, the Torgue
hex grip, bold ink linework) now lives *inside* the stage where it belongs, and a new art pass can be
inserted without editing anything that already exists.

```
./melee generate                      # full kit -> build/inkblade_kit (+ stage cache in build/inkblade_stages)
./melee generate --list-stages
./melee generate --only linework      # re-paint ink, everything before it comes from cache
./melee generate --from textures      # re-run from a stage onwards, keeping earlier cache
./melee generate --decals path/to/inkblade_decals --parts blade_jakobs,grip_hyperion
./melee generate --renders                 # + preview PNGs in build/inkblade_renders
./melee generate --quantise 64             # optional indexed palette (default is RGB)
./melee generate --linework-seed rogues     # re-roll hatch corners / stroke breaks / scratches
./melee generate --out build/dist/Inkblade_Modular_Kit_r6.zip
./melee build --kit build/inkblade_kit
```

`tools/generate_inkblade_kit.py` is the same entry point without the CLI wrapper.

## The stack

| # | Stage | What it owns | Artifacts (per part) |
| - | ----- | ------------ | -------------------- |
| 1 | `geometry` | Every mesh rule. R3 parametric shapes **and** the R4 revisions applied in one pass: halved blade-slot depth, per-manufacturer thickness, receiver shift, heavy-spine depth, centred cleave guard, Torgue hex shell + ribs, bifacial normalisation. Plus the R7 silhouette pass: slimmer/longer blades, the Jakobs sabre and the seated cleave-guard end caps. | `*.json` (faces + sockets) |
| 2 | `uv` | Newell-normal face projection at one shared texel density, padded shelf packing into a pow2 atlas, island template reuse for mirrored rear faces. | `*.json` (`inkblade.physical-uv/2.0`) |
| 3 | `flat` | **Flat textures saved as PNGs**: one solid palette colour per UV island (per-island brightness jitter, lightened `edge` role). | `<part>.png` (**the flat pass on disk**) |
| 4 | `mottle` | Paint wash (three-octave field), deposits, dry-brush streaks, gouges, chipped paint, tally marks, edge chips. | `<part>.png` |
| 5 | `linework` | The bold ink look: an overlay (translucent shadow planes, 3/5/7px weighted perimeters, broken pen strokes, corner hatch clusters, fractured accents, scratch groups, highlight nicks, all clipped to a 1px-eroded island mask) over corner hatch strokes and the `INK_PX` boundary. `--linework-seed TEXT` salts the RNG that picks hatch corners and stroke breaks; empty uses the default seed. | `<part>.png` |
| 6 | `decals` | Optional external decal art (extension point, no-op without art). | `<part>.png` |
| 7 | `textures` | Fill each island's own padded rectangle with its flat colour (never beyond it) and write the final atlases (`--quantise N` optionally reduces them to an indexed palette; off by default to avoid palette snapping). | `<part>_basecolor.png` |
| 8 | `export` | GLBs (embedded atlas, sockets, `FACE_FRONT`/`FACE_BACK`), assembled combinations, UV layout JSONs, `socket_manifest.json`, kit README. | `models/`, `textures/`, manifest |
| 9 | `render` | Preview PNGs via the built-in software rasteriser: 720×720 part and combination tiles plus labelled overview sheets. Optional (`--renders`). | `build/inkblade_renders/{parts,combinations,overview_*.png}` |

Ordering note: an atlas cannot be *painted* before the island layout exists, so the painting stages
follow the UV stage. What the split buys is that each painting pass is independent, inspectable and
re-runnable against the same layout — `--only linework` re-inks a kit in seconds.

## Previews without Blender

`--renders` (or `RUN` with `render_enabled=True`) writes previews next to the kit instead of into it:

```
build/inkblade_renders/
├── parts/<part_id>.png               # 720x720 each
├── combinations/<recipe>.png
├── overview_parts.png                # labelled contact sheets
└── overview_combinations.png
```

`render.py` is a small orthographic software rasteriser: three-light flat shading, z-buffered
(supersampled 2× by default) triangles sampled straight from the painted atlas, so the ink linework
shows in every preview. Bifacial parts render both `FACE_FRONT`/`FACE_BACK` instances, and element
recipes get their accent islands recoloured exactly like the exported combinations. A full 22-part
+ 7-combination set takes ~30 s. `--render-size` and `--render-supersample 1` trade quality for speed.

## Caching

Each stage has a fingerprint of `config + its own source + the upstream fingerprint + an optional
extra key` (the decals stage hashes the decal directory listing). "Its own source" means the `run`
wrapper **and** the module files declared in `Stage.sources` — e.g. `geometry.py` for the geometry
stage, `textures.py` for the painting stages. The wrapper is usually a two-line call into a module,
so hashing only the wrapper would serve stale geometry after a mesh edit.

A stage is skipped when its
fingerprint matches `build/inkblade_stages/<stage>/_fingerprint.json` and its artifacts are still on
disk. Because fingerprints chain, editing the ink stage re-runs ink and everything after it while the
geometry / UV / flat / mottle PNGs stay untouched. Editing `geometry.py` re-runs the whole stack,
as it should.

## R7 silhouette pass

`geometry.py` owns a small, shared blade pass on top of the R3 silhouettes:

- `BLADE_SPINE_SCALE` shrinks every blade's **spine width** (the Y / edge-on thickness). It scales
  the whole blade part, so the front rail, receiver and face overlays stay attached; the X face
  width is untouched. `accessory_face_gap_m` follows it so socketed fittings stay on the skin.
- `BLADE_LENGTH_SCALE` stretches the blade above `BLADE_LENGTH_PIVOT_Z`. Guards, sockets and the
  element receiver keep their positions, and `BLADES[*].base_reach_m` stays equal to the exported
  tip height.
- Jakobs was rebuilt as a frontier sabre: straight and full-width out of the hilt, a very subtle
  curve back near the end and a small clipped point only in the last stretch (the cutting edge
  sweeps back to meet the spine line). Earlier passes tapered from the ricasso and curved too soon;
  the original shipped mesh was a recurved hook. Its swept quillons are unchanged. Blade length is
  unchanged by this revision.
- `accessory_flared_cleave_guard`'s yellow end caps are seated into the plate: the R4
  `CLEAVE_GUARD_PLATE_SHIFT` moved the plate back but left the caps at their old depth, so they
  floated clear of the wing skin.

## Adding a stage

```python
from tools.inkblade_gen.stages import Stage, default_stages

stages = default_stages()
stages.insert(-1, Stage("stencils", "Stamp stencil motifs", run_stencils, save_canvases, load_canvases))
```

Any stage that receives the shared `KitState` can paint into `state.canvases` (RGB atlases keyed by
part id), read `state.layouts` for island geometry, and persist whatever it needs. Stages that do not
implement `save`/`load` simply always run. `--list-stages` prints whatever the stack contains.

## Layout

```
tools/inkblade_gen/
├── config.py     # palettes, ELEMENTS, ink/shadow/hatch colours, RunConfig
├── geometry.py   # final geometry: R3 shapes + all revisions in one pass
├── uv.py         # projection, packing, template reuse -> PartLayout + layout JSON
├── textures.py   # the painting layers (flat / mottle / linework / decals / seal / quantise)
├── glb.py        # GLB read/write, ear-clipping triangulation, per-part export, merge
├── render.py     # Blender-free software rasteriser + contact sheets
├── stages.py     # Stage definitions, KitState, manifest builder, stage catalogue
├── pipeline.py   # ordering, --only/--from/--force, fingerprint cache
└── cli.py        # argument parsing, kit zipping, console report
```

No Blender, NumPy or OpenCV is required: geometry is plain Python, and the R3 noise fields, contour
work and hatching were rebuilt with Pillow primitives (`ImageChops`, `MaxFilter`, polygon masks).

## Verification

- `tests/test_inkblade_generator.py` covers the stage contract (ordering, insertion, cache chaining,
  decal invalidation), the kit contract consumed by `tools/inkblade_upk`, geometry and texture regression hashes, paint determinism, and the render stage (coverage, determinism,
  bifacial pairing, output layout).
- Stage hashes cover a representative default-seed element; deliberate art changes require reviewing and updating the baselines. Pillow version changes can also affect pixel hashes.
- Generated kits compile unchanged with `python tools/compile_inkblade_upk.py --kit <dir>`.
