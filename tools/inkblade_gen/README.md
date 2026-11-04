# InkBlade generator

The generator builds the mesh and texture kit with Python and Pillow. Run it from the repository root:

```bash
./melee generate
./melee generate --list-stages
./melee generate --only linework
./melee generate --from textures
./melee generate --decals path/to/decals --parts blade_jakobs,grip_hyperion
./melee generate --renders
./melee build --kit build/inkblade_kit
```

`--only` runs the requested stage and its prerequisites. `--from` reruns that stage and all later stages. Valid cache entries supply earlier outputs. `--force` bypasses the selected stages' cache.

Other options include `--no-decals-art`, `--linework-seed TEXT`, `--quantise COLORS`, and `--out PATH`. Run `./melee generate --help` for the full list.

## Stages

| Stage | Output |
| --- | --- |
| `geometry` | Part meshes, metadata, and sockets |
| `uv` | Packed UV layouts |
| `flat` | Palette-filled atlases |
| `mottle` | Paint wear, deposits, and scratches |
| `detail` | Metal, bevel, and surface detail |
| `decals` | Manufacturer markings |
| `linework` | Ink contours and shading |
| `textures` | Final padded PNG atlases |
| `export` | GLBs, combinations, and `socket_manifest.json` |
| `render` | Optional preview images |

Decals are painted before linework. The final PNG atlases, GLBs, manifest, and intermediate stage cache are written under `build/` by default. Use `--renders` to write previews under `build/inkblade_renders`.

## Modules

- `geometry.py` builds meshes and sockets.
- `uv.py` projects faces and packs texture islands.
- `textures.py` and `decals.py` paint the atlases.
- `stages.py` defines stage inputs, outputs, and cache serialization.
- `pipeline.py` runs the stages and validates cached outputs.
- `glb.py` writes the exported mesh files.
- `render.py` renders previews without Blender.

Run `./melee generate` and `./melee build` to regenerate and compile the kit. Build-time dependencies are in `requirements.txt`.
