# RG Realistic Ivy

Surface-following ivy generator for Blender built for controlled environmental art and architectural objects.

> Independent experimental add-on by Magnetic Works. Blender 5.0+ is declared by the source package; other versions have not been verified.

## Capabilities

- Grow runners and branches across the selected mesh target using an existing vine/leaf source object.
- Edit density, coverage, wandering, branch distribution, spacing, taper, orientation and per-target settings.
- Generate multiple ivy systems, keep per-target state, and manage systems through the Ivy sidebar.
- Display guides and configurable renderable root curves.
- Control leaf orientation, including explicit camera-facing behavior.
- Work with applied or unapplied target transforms.

## Install

1. Download `rg_realistic_ivy-1.4.0.zip` from [Releases](https://github.com/Chiesu/magnetic-ivy/releases/latest).
2. In Blender 5.0+, choose **Edit > Preferences > Get Extensions > Install from Disk** (menu wording depends on Blender version).
3. Select the ZIP and enable **RG Realistic Ivy**.
4. Find **Ivy** in the 3D Viewport sidebar (`N`).

## Basic workflow

1. Prepare a mesh as the **target** (for example a wall or tomb).
2. Prepare a separate mesh object to serve as the **ivy source** (leaf/branch geometry).
3. In the **Ivy** sidebar, specify target and source, then generate the system.
4. Tune coverage, main runners, source scale, jitter and orientation.
5. Use the built-in manager to switch between target systems. Test changes on a duplicate object first.

The source mesh is **not** included with this repository. Any downloaded botanical assets must be obtained from their original provider and under their own license.

## Compatibility and caution

The tool creates and removes Blender collections/objects as part of regeneration. Back up complex scenes before mass editing. No compatibility guarantee with earlier custom systems is implied.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
