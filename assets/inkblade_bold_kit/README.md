# Inkblade — bold ink modular weapon kit

A visual revision of the supplied Inkblade R4 kit, inspired by graphic-novel / looter-shooter ink work. The **existing low-poly meshes, UVs, mounting nodes, scale, part compatibility metadata and seven example assemblies have been retained**, not redesigned. The UV atlases have been painted over with significantly heavier weighted contours, layered angular shadows, expanded corner hatching, broken secondary lines and directional scratches; the underlying colors remain. The linework is deterministic procedural illustration, not an AI-generated texture or manually painted art. The effect is intentionally bold and graphic, while element colors remain visible.

## Contents

- `models/parts/`: 22 standalone self-contained GLB files (5 blades, 5 grips, 7 accessories and 5 element pieces).
- `models/combinations/`: 7 assembled self-contained GLBs.
- `textures/`: updated base-color PNG atlases and original UV layout JSON files. Textures are also embedded in the GLBs. Some assembly images are colorized copies, so they may differ from the standalone PNGs.
- `renders/parts/` and `renders/combinations/`: individual PNG previews, lit from the front at a slight angle.
- `renders/overview_*.png`: labeled overview sheets.
- `socket_manifest.json`: the supplied R4 socket coordinates, fit adjustments, policy, example recipes and design metadata. Gameplay statistics, rolls and titles are metadata only; no game integration is included.
- `tools/tint_accent.py`: optional colorization utility for ELEMENT_ACCENT material channels. Requires Python 3 and Pillow.
- `tools/render_previews.py`: Blender Python render script used for the images.
- `tools/reink_bold.py`: the texture-generation script, for reference. **It is not idempotent**; run it only on a fresh copy of the original R4 kit, not on these already-painted assets. Requires Pillow, NumPy and OpenCV.

## Regenerating this kit

These binaries are the shipped `r5` paint-over of the R4 kit. The maintainable generator now lives in
`tools/inkblade_gen/` (see its README) and rebuilds the whole kit — geometry, UVs, flat colour PNGs,
mottle, bold ink linework, optional decals and the export — as an ordered stack of stages, using only
Pillow:

```
./melee generate                                   # -> build/inkblade_kit
./melee generate --renders                         # + previews in build/inkblade_renders
./melee generate --only linework                   # re-ink only, reusing cached UV/flat/mottle work
./melee generate --parts blade_jakobs --decals art # one part, with external decal art
./melee build --kit build/inkblade_kit             # compile the generated kit to UE3 packages
```

The generated kit satisfies the same `socket_manifest.json` contract as this directory (schema
`inkblade.socket-kit/1.0`), reproduces the shipped part bounds and sockets exactly, and compiles
unchanged with `tools/compile_inkblade_upk.py`. It is not byte-identical art: the ink is re-painted by
the Pillow stage stack rather than by `tools/reink_bold.py` (which needs OpenCV and is kept for
reference only). Re-run `tools/render_previews.py` in Blender if you replace these images.

## Assemble

Import the GLBs with a glTF 2.0 importer. glTF coordinates are right-handed, Y-up, in meters. Each part has a root, named `MOUNT` and/or `SOCKET_*` transform nodes, and UV-textured materials. Align a child part's `MOUNT` to its parent's target socket: `world_child = world_socket × inverse(child_mount)` (the supplied mounts are identity transforms). The grip attaches to `SOCKET_GRIP`; most accessories use `SOCKET_ACCESSORY`, while the balanced pommel uses the grip's `SOCKET_POMMEL`; elemental modules use `SOCKET_ELEMENT`.

The bifacial blade accessories and element parts have `FACE_FRONT` and `FACE_BACK` instances. Once mounted, move these nodes outward in opposite local glTF Z directions by the blade's `accessory_face_gap_m` or `element_face_gap_m`, respectively, as recorded in the manifest. Preserve their existing translations and negative back-face scale. The assembled examples already have the gap adjustments applied. Consult the manifest for targeting, accessory, and element restrictions; the files themselves do not enforce them.

## Elements and materials

`ELEMENT_ACCENT` material names/extras identify recolorable surfaces where present. Some blades have no such surface: the separate element module provides the visible element color. The assembled examples already have their chosen colors baked. The original material setup is retained; these are textured PBR materials, not a full custom toon shader or live outline shader. Linework is painted into the UVs, so very small details can vary with resolution and viewing angle.

To produce a recolored copy (this does not check element policy):

```
python tools/tint_accent.py models/parts/blade_hyperion.glb shock_blade.glb '#40baff'
```

The utility duplicates accent textures before recoloring so non-accent materials sharing the source atlas are unchanged. It preserves ink by mapping the texture's maximum-channel brightness to the new color. Choose a permitted element from the blade's policy in the manifest; color the compatible accent-bearing parts and attach the matching element module. Unlike the original R4 palette-based recoloring code, the new atlases are RGB PNGs.

## Rendering

Individual PNGs show the supplied assemblies/parts from one angle; they do not prove every possible mix-and-match fit. To re-render with Blender 4+/5+, run `blender --background --python tools/render_previews.py`. The renders use soft studio lights and the actual textured GLBs. No gameplay, animation, real-time engine or Borderlands integration is included.
