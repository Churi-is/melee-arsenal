# Asset Provenance

## Generated Assets (Primary)

All InkBlade weapon visuals are **procedurally generated in pure Python + Pillow** — no extracted Borderlands 2 meshes or commercial textures are redistributed:

- **22 modular parts** (5 blades, 5 grips, 7 accessories, 5 emitters) built by `tools/inkblade_gen/geometry.py` — parametric silhouettes (R3 base + R4/R7/R8 revisions: halved slot depth, bifacial normalisation, Torgue hex shell, slimmed spines, 1.5× blade length).
- **UV atlases → PNG textures** via the 10-stage pipeline (`flat → mottle → detail → decals → linework → textures`) — palette fills, paint washes, brushed metal, stamped markings, and bold ink contours. Decal art (`tools/inkblade_gen/decals.py`) is procedural except for an optional external `--decals` directory.
- **GLB → UE3 UPK** via `tools/inkblade_upk/` (pure-Python LZO1X / DXT1 / SkeletalMesh codec). Cooked packages are `build/WillowGame/CookedPCConsole/MeleeArsenalMeshes.upk` and `MeleeArsenalTextures.upk`.

Reproducibility: `tools/compile_inkblade_upk.py --fingerprint-only` hashes the kit's texture/UV/mesh content; `melee install` auto-recompiles stale packages. Pillow version changes can affect pixel hashes; builds are pinned to `Pillow==13.0.0` in `requirements.txt` and `pyproject.toml`.

## Donor / Reference Assets

The UPK compiler needs a valid UE3 header template — a **stripped, header-only extract** of a stock `Weap_Pistol` donor (`tools/inkblade_upk/donors/weap_pistol_donor.upk`) captured via `sdk_mods/melee_arsenal_probe`. Only the package summary and skeletal-mesh header skeleton are retained; no pistol geometry, textures, or sounds ship. This is the standard approach for BL2 SDK mods that add new Gestalt/mesh content.

## Audio & Effects (Runtime)

No custom sound files are bundled. Combat audio uses verified live BL2 `AkEvent` / `CameraAnim` / `ParticleSystem` paths (e.g. `Ake_Wep_Melee`, `Anim_CameraAnimations.Melee`, `FX_WEP_Explosions`), resolved at runtime via `unrealsdk.find_object` / `find_class`. Fallback heuristic scoring (`_score_explosion_ps_path` / `_score_explosion_ak_path`) scans already-loaded objects if exact paths drift between builds.

## Palette & Lore

Manufacturer palettes (`tools/inkblade_gen/config.py:PALETTE`) and element colours are original, chosen for readability against the charcoal ink linework. Weapon names, flavor text, and balance values derive from `sdk_mods/melee_arsenal/core/parts/` (one module per part type) and `core/stats.py`, not from datamined BL2 strings.

## Third-Party

- **Pillow** (HPND License) — image generation.
- **Willow2 Mod Manager** (GPLv3) — hosts the mod (`unrealsdk`, `mods_base`, `pyunrealsdk`).
- **Python** (PSF License) — offline tooling.

This file, plus `LICENSE`, satisfies the "license/provenance" ask: testing and distribution are reproducible, and every non-procedural artifact's origin is declared.
