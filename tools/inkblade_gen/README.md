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
./melee generate --decals assets/inkblade_decals --parts blade_jakobs,grip_hyperion
./melee generate --out dist/Inkblade_Modular_Kit_r6.zip
./melee build --kit build/inkblade_kit
```

`tools/generate_inkblade_kit.py` is the same entry point without the CLI wrapper.

## The stack

| # | Stage | What it owns | Artifacts (per part) |
| - | ----- | ------------ | -------------------- |
| 1 | `geometry` | Every mesh rule. R3 parametric shapes **and** the R4 revisions applied in one pass: halved blade-slot depth, per-manufacturer thickness, receiver shift, heavy-spine depth, centred cleave guard, Torgue hex shell + ribs, bifacial normalisation. | `*.json` (faces + sockets) |
| 2 | `uv` | Newell-normal face projection at one shared texel density, padded shelf packing into a pow2 atlas, island template reuse for mirrored rear faces. | `*.json` (`inkblade.physical-uv/2.0`) |
| 3 | `flat` | **Flat textures saved as PNGs**: one solid palette colour per UV island (per-island brightness jitter, lightened `edge` role). | `<part>.png` (**the flat pass on disk**) |
| 4 | `mottle` | Paint wash (three-octave field), deposits, dry-brush streaks, gouges, chipped paint, tally marks, edge chips. | `<part>.png` |
| 5 | `linework` | The bold ink look: weighted contours, angular shadow planes, broken pen strokes, corner hatch clusters, scratches and highlight nicks. | `<part>.png` |
| 6 | `decals` | Optional external decal art (extension point, no-op without art). | `<part>.png` |
| 7 | `textures` | Seal island colour into the UV padding, quantise to 64 colours, write the shipped atlases. | `<part>_basecolor.png` |
| 8 | `export` | GLBs (embedded atlas, sockets, `FACE_FRONT`/`FACE_BACK`), assembled combinations, UV layout JSONs, `socket_manifest.json`, kit README. | `models/`, `textures/`, manifest |

Ordering note: an atlas cannot be *painted* before the island layout exists, so the painting stages
follow the UV stage. What the split buys is that each painting pass is independent, inspectable and
re-runnable against the same layout — `--only linework` re-inks a kit in seconds.

## Caching

Each stage has a fingerprint of `config + its own source + the upstream fingerprint + an optional
extra key` (the decals stage hashes the decal directory listing). A stage is skipped when its
fingerprint matches `build/inkblade_stages/<stage>/_fingerprint.json` and its artifacts are still on
disk. Because fingerprints chain, editing the ink stage re-runs ink and everything after it while the
geometry / UV / flat / mottle PNGs stay untouched.

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
├── stages.py     # Stage definitions, KitState, manifest builder, stage catalogue
├── pipeline.py   # ordering, --only/--from/--force, fingerprint cache
└── cli.py        # argument parsing, kit zipping, console report
```

No Blender, NumPy or OpenCV is required: geometry is plain Python, and the R3 noise fields, contour
work and hatching were rebuilt with Pillow primitives (`ImageChops`, `MaxFilter`, polygon masks).

## Verification

- `tests/test_inkblade_generator.py` covers the stage contract (ordering, insertion, cache chaining,
  decal invalidation), the kit contract consumed by `tools/inkblade_upk`, geometry parity against the
  shipped kit (bounds + sockets), and paint determinism.
- Generated kits compile unchanged with `python tools/compile_inkblade_upk.py --kit <dir>`.
