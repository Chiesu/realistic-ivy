# RG Realistic Ivy

Surface-following ivy generator for Blender built for controlled environmental art and architectural objects.

> Independent experimental Blender add-on. Blender 5.0+ is declared by the source package; other versions have not been verified.

<img width="1920" height="1080" alt="hero" src="https://github.com/user-attachments/assets/dc899aa5-5b6b-47a9-8bc8-b43388dd4ff9" />

## Capabilities

<img width="1920" height="1080" alt="growth-controls" src="https://github.com/user-attachments/assets/50162a20-9483-4807-81f4-bb3e5cd003aa" />

- Grow runners and branches across the selected mesh target using an existing vine/leaf source object.
- Edit density, coverage, wandering, branch distribution, spacing, taper, orientation and per-target settings.
- Generate multiple ivy systems, keep per-target state, and manage systems through the Ivy sidebar.
- Display guides and configurable renderable root curves.
- Control leaf orientation, including explicit camera-facing behavior.
- Work with applied or unapplied target transforms.

## Install

1. Download `rg_realistic_ivy-1.4.0.zip` from [Releases](https://github.com/Chiesu/realistic-ivy/releases/latest).
2. In Blender 5.0+, choose **Edit > Preferences > Get Extensions > Install from Disk** (menu wording depends on Blender version).
3. Select the ZIP and enable **RG Realistic Ivy**.
4. Find **Ivy** in the 3D Viewport sidebar (`N`).

## Basic workflow

<img width="1920" height="1080" alt="target-setup" src="https://github.com/user-attachments/assets/7b40fe7c-8b45-47fd-8d0b-1e1c578fecd4" />

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
