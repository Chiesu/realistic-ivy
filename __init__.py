# SPDX-License-Identifier: GPL-3.0-or-later
bl_info = {
    "name": "RG Realistic Ivy",
    "author": "Magnetic Works contributors",
    "version": (1, 4, 0),
    "blender": (5, 0, 0),
    "location": "3D View > Sidebar > Ivy",
    "description": "Generate realistic surface-following ivy from an existing vine object, with per-target state, a multi-target manager, multiple independent ivy systems, renderable root curves, explicit camera-facing orientation, transform-safe source anchoring and constrained randomization.",
    "category": "Object",
}

import bpy
import json
import math
import random
import time

from mathutils import Vector, Matrix, Quaternion
from mathutils.bvhtree import BVHTree
from bpy.props import (
    PointerProperty,
    IntProperty,
    FloatProperty,
    BoolProperty,
    EnumProperty,
    StringProperty,
    CollectionProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList
from bpy.app.handlers import persistent


WORLD_UP = Vector((0.0, 0.0, 1.0))

_AUTO_TIMER_PENDING = False
_AUTO_DIRTY = False
_AUTO_GENERATING = False
_AUTO_SUSPEND = False
_AUTO_LAST_CHANGE = 0.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def mix(a, b, t):
    return a + (b - a) * t


def smoothstep(v):
    v = clamp(v, 0.0, 1.0)
    return v * v * (3.0 - 2.0 * v)


def clean_name(name):
    return name.replace(" ", "_").replace("/", "_").replace("\\", "_")


def collection_name_from_string(name):
    return "RG_IVY_" + clean_name(name)


def guide_collection_name_from_string(name):
    return "RG_IVY_GUIDES_" + clean_name(name)


def collection_name(target):
    return collection_name_from_string(target.name)


def guide_collection_name(target):
    return guide_collection_name_from_string(target.name)


def delete_collection_and_objects(name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        return

    curve_data = []
    for obj in list(coll.objects):
        if obj.type == 'CURVE' and obj.data is not None:
            curve_data.append(obj.data)
        bpy.data.objects.remove(obj, do_unlink=True)

    bpy.data.collections.remove(coll)

    for data in curve_data:
        if data.users == 0:
            try:
                bpy.data.curves.remove(data)
            except Exception:
                pass


def create_collection(name):
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    return coll



STATE_VERSION = 1

PER_TARGET_PROPS = [
    "source",
    "growth_side",
    "seed",
    "main_runners",
    "start_height",
    "root_spread",
    "growth_length",
    "step_size",
    "upward_growth",
    "wander",
    "persistence",
    "top_crawl",
    "direction_jitter",
    "max_surface_jump",
    "surface_offset",
    "branch_count",
    "branch_length",
    "branch_angle",
    "source_scale",
    "scale_variation",
    "spacing",
    "spacing_variation",
    "coverage",
    "clusteriness",
    "tip_taper_start",
    "tip_scale",
    "source_anchor_mode",
    "forward_axis",
    "normal_axis",
    "flip_forward",
    "flip_normal",
    "face_camera",
    "face_camera_object",
    "camera_influence",
    "yaw_variation",
    "surface_tilt",
    "lateral_jitter",
    "normal_jitter",
    "show_guides",
    "root_material",
    "root_radius",
    "root_tip_scale",
    "root_smoothing",
    "root_curve_resolution",
    "root_bevel_resolution",
    "root_in_front",
    "hide_source",
]


def target_has_generated_system(target):
    if target is None:
        return False

    if bpy.data.collections.get(collection_name(target)) is not None:
        return True
    if bpy.data.collections.get(guide_collection_name(target)) is not None:
        return True

    try:
        return bool(target.get("RG_IVY_HAS_GENERATED", False))
    except Exception:
        return False


def target_has_saved_state(target):
    if target is None:
        return False
    try:
        return bool(target.get("RG_IVY_STATE_JSON", ""))
    except Exception:
        return False


def target_is_managed(target):
    return target_has_generated_system(target) or target_has_saved_state(target)


def saved_source_name(target):
    if target is None:
        return ""
    try:
        raw = target.get("RG_IVY_STATE_JSON", "")
        if not raw:
            return ""
        state = json.loads(raw)
        return state.get("source_name", "") or ""
    except Exception:
        return ""


def sync_manager_index_to_target(scene, target):
    if scene is None or not hasattr(scene, "rg_ivy_targets"):
        return
    if target is None:
        return
    for index, item in enumerate(scene.rg_ivy_targets):
        if item.target == target:
            scene.rg_ivy_target_index = index
            break


def rebuild_target_manager(scene, preferred_target=None):
    if scene is None or not hasattr(scene, "rg_ivy_targets"):
        return

    if preferred_target is None and hasattr(scene, "rg_ivy_settings"):
        preferred_target = scene.rg_ivy_settings.target

    scene.rg_ivy_targets.clear()

    targets = [
        obj for obj in scene.objects
        if obj.type == 'MESH' and target_is_managed(obj)
    ]
    targets.sort(key=lambda obj: obj.name.lower())

    selected_index = 0
    for index, target in enumerate(targets):
        item = scene.rg_ivy_targets.add()
        item.target = target
        item.has_generated = target_has_generated_system(target)
        item.has_saved_state = target_has_saved_state(target)
        item.source_name = saved_source_name(target)
        if preferred_target is not None and target == preferred_target:
            selected_index = index

    if len(scene.rg_ivy_targets) > 0:
        scene.rg_ivy_target_index = min(selected_index, len(scene.rg_ivy_targets) - 1)
    else:
        scene.rg_ivy_target_index = 0


def activate_target(scene, target, source_override=None):
    global _AUTO_SUSPEND

    if scene is None or target is None:
        return

    settings = scene.rg_ivy_settings
    previous = settings.target

    if previous is not None and previous != target:
        save_settings_to_target(settings, previous)

    _AUTO_SUSPEND = True
    try:
        settings.target = target
    finally:
        _AUTO_SUSPEND = False

    load_settings_from_target(scene, target)

    if source_override is not None:
        _AUTO_SUSPEND = True
        try:
            settings.source = source_override
        finally:
            _AUTO_SUSPEND = False
        save_settings_to_target(settings, target)

    sync_manager_index_to_target(scene, target)


def _serialize_target_state(settings):
    state = {"version": STATE_VERSION}

    for prop_name in PER_TARGET_PROPS:
        if prop_name == "source":
            obj = getattr(settings, "source", None)
            state["source_name"] = obj.name if obj is not None else ""
            continue

        if prop_name == "face_camera_object":
            obj = getattr(settings, "face_camera_object", None)
            state["face_camera_object_name"] = obj.name if obj is not None else ""
            continue

        if prop_name == "root_material":
            mat = getattr(settings, "root_material", None)
            state["root_material_name"] = mat.name if mat is not None else ""
            continue

        state[prop_name] = getattr(settings, prop_name)

    return state


def save_settings_to_target(settings, target):
    if target is None:
        return

    try:
        target["RG_IVY_STATE_JSON"] = json.dumps(_serialize_target_state(settings))
        target["RG_IVY_HAS_GENERATED"] = bool(settings.has_generated or target_has_generated_system(target))
    except Exception as exc:
        print("[RG Ivy] Failed saving target state:", repr(exc))


def load_settings_from_target(scene, target):
    global _AUTO_SUSPEND

    settings = scene.rg_ivy_settings
    state_raw = None
    try:
        state_raw = target.get("RG_IVY_STATE_JSON", "")
    except Exception:
        state_raw = ""

    state = {}
    if state_raw:
        try:
            state = json.loads(state_raw)
        except Exception:
            state = {}

    _AUTO_SUSPEND = True
    try:
        if state:
            source_name = state.get("source_name", "")
            if source_name:
                settings.source = bpy.data.objects.get(source_name)

            face_cam_name = state.get("face_camera_object_name", "")
            settings.face_camera_object = bpy.data.objects.get(face_cam_name) if face_cam_name else None

            root_mat_name = state.get("root_material_name", "")
            settings.root_material = bpy.data.materials.get(root_mat_name) if root_mat_name else None

            skip = {"version", "source_name", "face_camera_object_name", "root_material_name"}
            for key, value in state.items():
                if key in skip or not hasattr(settings, key):
                    continue
                try:
                    setattr(settings, key, value)
                except Exception:
                    pass

        settings.has_generated = target_has_generated_system(target)
        settings.active_target_name = target.name
    finally:
        _AUTO_SUSPEND = False


def save_current_target_state(scene):
    if scene is None or not hasattr(scene, "rg_ivy_settings"):
        return
    settings = scene.rg_ivy_settings
    target = settings.target
    if target is not None:
        save_settings_to_target(settings, target)


def target_property_update(self, context):
    global _AUTO_GENERATING, _AUTO_SUSPEND
    if _AUTO_GENERATING or _AUTO_SUSPEND or context is None:
        return

    settings = context.scene.rg_ivy_settings
    previous_name = settings.active_target_name
    new_target = settings.target

    if previous_name and (new_target is None or previous_name != new_target.name):
        previous_target = bpy.data.objects.get(previous_name)
        if previous_target is not None:
            save_settings_to_target(settings, previous_target)

    if new_target is None:
        settings.has_generated = False
        settings.active_target_name = ""
        return

    load_settings_from_target(context.scene, new_target)
    sync_manager_index_to_target(context.scene, new_target)


def request_auto_update(scene):
    global _AUTO_TIMER_PENDING, _AUTO_DIRTY, _AUTO_LAST_CHANGE

    if scene is None or not hasattr(scene, "rg_ivy_settings"):
        return

    settings = scene.rg_ivy_settings
    if not settings.auto_update or not settings.has_generated:
        return

    _AUTO_DIRTY = True
    _AUTO_LAST_CHANGE = time.monotonic()

    if not _AUTO_TIMER_PENDING:
        _AUTO_TIMER_PENDING = True
        delay = max(0.05, settings.update_delay)
        bpy.app.timers.register(ivy_auto_update_timer, first_interval=delay)


def ivy_property_update(self, context):
    global _AUTO_GENERATING, _AUTO_SUSPEND
    if _AUTO_GENERATING or _AUTO_SUSPEND or context is None:
        return
    save_current_target_state(context.scene)
    request_auto_update(context.scene)


def ivy_auto_update_timer():
    global _AUTO_TIMER_PENDING, _AUTO_DIRTY, _AUTO_LAST_CHANGE, _AUTO_GENERATING

    scene = bpy.context.scene
    if scene is None or not hasattr(scene, "rg_ivy_settings"):
        _AUTO_TIMER_PENDING = False
        return None

    settings = scene.rg_ivy_settings
    if not settings.auto_update or not settings.has_generated:
        _AUTO_DIRTY = False
        _AUTO_TIMER_PENDING = False
        return None

    delay = max(0.05, settings.update_delay)
    elapsed = time.monotonic() - _AUTO_LAST_CHANGE

    if elapsed < delay:
        return max(0.05, delay - elapsed)

    if not _AUTO_DIRTY:
        _AUTO_TIMER_PENDING = False
        return None

    if _AUTO_GENERATING:
        return 0.1

    _AUTO_DIRTY = False
    try:
        generate_ivy(bpy.context, silent=True)
    except Exception as exc:
        print("[RG Ivy Auto Update]", repr(exc))

    if _AUTO_DIRTY:
        return delay

    _AUTO_TIMER_PENDING = False
    return None


def random_tangent(normal, rng):
    v = Vector((
        rng.uniform(-1.0, 1.0),
        rng.uniform(-1.0, 1.0),
        rng.uniform(-1.0, 1.0),
    ))
    v -= normal * v.dot(normal)
    if v.length < 0.000001:
        v = normal.orthogonal()
    return v.normalized()


def project_to_tangent(v, normal):
    result = v - normal * v.dot(normal)
    if result.length < 0.000001:
        return None
    return result.normalized()


class SurfaceData:
    def __init__(self, context, obj):
        self.obj = obj
        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        world = eval_obj.matrix_world.copy()
        normal_matrix = world.to_3x3().inverted().transposed()

        vertices = [world @ vertex.co for vertex in mesh.vertices]
        polygons = [list(poly.vertices) for poly in mesh.polygons]

        self.bvh = BVHTree.FromPolygons(vertices, polygons, all_triangles=False)
        self.samples = []

        for poly in mesh.polygons:
            center = world @ poly.center
            normal = (normal_matrix @ poly.normal).normalized()
            self.samples.append({
                "location": center,
                "normal": normal,
                "area": max(poly.area, 0.000001),
            })

        eval_obj.to_mesh_clear()

        bbox = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        self.min = Vector((
            min(p.x for p in bbox),
            min(p.y for p in bbox),
            min(p.z for p in bbox),
        ))
        self.max = Vector((
            max(p.x for p in bbox),
            max(p.y for p in bbox),
            max(p.z for p in bbox),
        ))
        self.center = (self.min + self.max) * 0.5
        self.size = self.max - self.min
        self.height = max(self.size.z, 0.001)
        self.diagonal = max(self.size.length, 0.001)

    def nearest(self, point):
        result = self.bvh.find_nearest(point)
        if result is None:
            return None
        location, normal, face_index, distance = result
        if location is None:
            return None
        return location, normal.normalized(), distance


def camera_object_for_settings(context, settings=None):
    """Return the explicit ivy camera when set, otherwise the Scene Camera."""
    if settings is not None:
        camera = getattr(settings, "face_camera_object", None)
        if camera is not None and camera.type == 'CAMERA':
            return camera

    camera = context.scene.camera
    if camera is not None and camera.type == 'CAMERA':
        return camera

    return None


def camera_position(context, settings=None):
    camera = camera_object_for_settings(context, settings)
    if camera is None:
        return None
    return camera.matrix_world.translation.copy()


def poll_camera_object(self, obj):
    return obj is not None and obj.type == 'CAMERA'


def weighted_pick(items, weights, rng):
    total = sum(weights)
    if total <= 0.0:
        return rng.choice(items)

    value = rng.uniform(0.0, total)
    accumulated = 0.0
    for item, weight in zip(items, weights):
        accumulated += weight
        if value <= accumulated:
            return item
    return items[-1]


def choose_root(context, surface, settings, rng):
    candidates = []
    weights = []
    cam_pos = camera_position(context, settings)

    for sample in surface.samples:
        p = sample["location"]
        n = sample["normal"]
        z_norm = (p.z - surface.min.z) / surface.height

        if z_norm > settings.start_height:
            continue
        if n.dot(WORLD_UP) < -0.55:
            continue

        weight = sample["area"]
        low_weight = (1.0 - z_norm) ** 2
        weight *= 0.25 + low_weight

        if settings.growth_side == 'CAMERA' and cam_pos is not None:
            to_camera = cam_pos - p
            if to_camera.length > 0.00001:
                to_camera.normalize()
                facing = max(0.0, n.dot(to_camera))
                weight *= 0.08 + facing ** 3

        candidates.append(sample)
        weights.append(weight)

    if not candidates:
        candidates = surface.samples
        weights = [sample["area"] for sample in candidates]

    chosen = weighted_pick(candidates, weights, rng)
    return chosen["location"], chosen["normal"]


def grow_surface_path(
    surface,
    start_location,
    start_normal,
    settings,
    rng,
    step_count,
    initial_direction=None,
    upward_multiplier=1.0,
    wander_multiplier=1.0,
):
    step_size = surface.height * settings.step_size
    offset = settings.surface_offset
    current = start_location.copy()
    normal = start_normal.normalized()
    previous_direction = initial_direction
    output = [(current + normal * offset, normal.copy())]
    stagnation = 0

    for _ in range(step_count):
        up_tangent = project_to_tangent(WORLD_UP, normal)
        noise = random_tangent(normal, rng)

        persistence_direction = None
        if previous_direction is not None:
            persistence_direction = project_to_tangent(previous_direction, normal)

        desired = Vector((0.0, 0.0, 0.0))

        if persistence_direction is not None:
            desired += persistence_direction * settings.persistence

        if up_tangent is not None:
            desired += up_tangent * settings.upward_growth * upward_multiplier

        desired += noise * settings.wander * wander_multiplier

        top_factor = max(0.0, normal.dot(WORLD_UP))
        if top_factor > 0.55 and persistence_direction is not None:
            desired += persistence_direction * settings.top_crawl * top_factor

        desired = project_to_tangent(desired, normal)
        if desired is None:
            desired = noise

        angle = rng.uniform(-settings.direction_jitter, settings.direction_jitter)
        desired = (Quaternion(normal, angle) @ desired).normalized()

        proposed = current + desired * step_size
        nearest = surface.nearest(proposed)
        if nearest is None:
            break

        new_location, new_normal, distance = nearest
        max_jump = step_size * settings.max_surface_jump

        if distance > max_jump:
            proposed = current + desired * step_size * 0.45
            nearest = surface.nearest(proposed)
            if nearest is None:
                break
            new_location, new_normal, distance = nearest
            if distance > max_jump:
                break

        actual_move = new_location - current
        if actual_move.length < step_size * 0.08:
            stagnation += 1
        else:
            stagnation = 0

        if stagnation >= 4:
            break

        if actual_move.length > 0.000001:
            previous_direction = actual_move.normalized()

        current = new_location
        normal = new_normal
        output.append((current + normal * offset, normal.copy()))

    return output


def path_tangent(path, index):
    if len(path) < 2:
        return WORLD_UP.copy()

    if index <= 0:
        tangent = path[1][0] - path[0][0]
    elif index >= len(path) - 1:
        tangent = path[-1][0] - path[-2][0]
    else:
        tangent = path[index + 1][0] - path[index - 1][0]

    if tangent.length < 0.00001:
        return WORLD_UP.copy()
    return tangent.normalized()


def create_branches(surface, main_path, settings, rng):
    branches = []
    if len(main_path) < 8 or settings.branch_count <= 0:
        return branches

    low_index = max(2, int(len(main_path) * 0.18))
    high_index = max(low_index + 1, int(len(main_path) * 0.82))
    available = list(range(low_index, high_index))
    rng.shuffle(available)
    chosen = sorted(available[:settings.branch_count])

    for branch_number, index in enumerate(chosen):
        start_location = main_path[index][0] - main_path[index][1] * settings.surface_offset
        normal = main_path[index][1]
        tangent = path_tangent(main_path, index)
        sign = -1.0 if branch_number % 2 else 1.0
        angle = settings.branch_angle * sign * rng.uniform(0.65, 1.20)
        branch_direction = Quaternion(normal, angle) @ tangent

        branch_steps = max(
            4,
            int(len(main_path) * settings.branch_length * rng.uniform(0.65, 1.15)),
        )

        branch = grow_surface_path(
            surface=surface,
            start_location=start_location,
            start_normal=normal,
            settings=settings,
            rng=rng,
            step_count=branch_steps,
            initial_direction=branch_direction,
            upward_multiplier=0.55,
            wander_multiplier=1.20,
        )

        if len(branch) >= 3:
            branches.append(branch)

    return branches


AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}


def source_axis_info(source, settings):
    bbox = [Vector(corner) for corner in source.bound_box]
    bbox_min = Vector((
        min(v.x for v in bbox),
        min(v.y for v in bbox),
        min(v.z for v in bbox),
    ))
    bbox_max = Vector((
        max(v.x for v in bbox),
        max(v.y for v in bbox),
        max(v.z for v in bbox),
    ))

    local_extents = [bbox_max[i] - bbox_min[i] for i in range(3)]

    source_scale = source.matrix_world.to_scale()
    effective = [local_extents[i] * abs(source_scale[i]) for i in range(3)]

    if settings.forward_axis == 'AUTO':
        forward_index = max(range(3), key=lambda i: effective[i])
    else:
        forward_index = AXIS_INDEX[settings.forward_axis]

    if settings.normal_axis == 'AUTO':
        choices = [i for i in range(3) if i != forward_index]
        normal_index = min(choices, key=lambda i: effective[i])
    else:
        normal_index = AXIS_INDEX[settings.normal_axis]
        if normal_index == forward_index:
            choices = [i for i in range(3) if i != forward_index]
            normal_index = min(choices, key=lambda i: effective[i])

    side_index = [i for i in range(3) if i not in (forward_index, normal_index)][0]
    source_length = max(effective[forward_index], 0.001)

    return (
        forward_index,
        normal_index,
        side_index,
        source_length,
        source_scale,
        bbox_min,
        bbox_max,
    )


def source_anchor_local(source, settings, forward_index, bbox_min, bbox_max):
    """
    Return the local point that should be placed on the growth path.

    Blender's Apply Location can bake a world-space offset into mesh coordinates
    while resetting the object transform/origin to (0, 0, 0). Copying that object
    and replacing matrix_world then makes the geometry appear to shoot back toward
    the global origin. AUTO detects that condition and compensates for it.
    """
    mode = settings.source_anchor_mode

    if mode == 'ORIGIN':
        return Vector((0.0, 0.0, 0.0))

    center = (bbox_min + bbox_max) * 0.5
    extents = bbox_max - bbox_min
    diagonal = max(extents.length, 0.000001)

    def bbox_anchor(which):
        anchor = center.copy()
        if which == 'START':
            anchor[forward_index] = bbox_min[forward_index]
        elif which == 'END':
            anchor[forward_index] = bbox_max[forward_index]
        return anchor

    if mode == 'START':
        return bbox_anchor('START')

    if mode == 'END':
        return bbox_anchor('END')

    if mode == 'CENTER':
        return center

    # AUTO:
    # Measure how far the local object origin is from the actual geometry bounds.
    # If the origin is well outside the bounding box, Location was very likely
    # applied after positioning (or the origin is otherwise unusable for instancing).
    closest = Vector((
        clamp(0.0, bbox_min.x, bbox_max.x),
        clamp(0.0, bbox_min.y, bbox_max.y),
        clamp(0.0, bbox_min.z, bbox_max.z),
    ))
    origin_to_geometry = closest.length

    if origin_to_geometry > diagonal * 0.20:
        return bbox_anchor('START')

    # Preserve a deliberately authored source origin when it is close to geometry.
    return Vector((0.0, 0.0, 0.0))


def path_lengths(path):
    lengths = [0.0]
    total = 0.0
    for i in range(1, len(path)):
        total += (path[i][0] - path[i - 1][0]).length
        lengths.append(total)
    return lengths, total


def sample_path_at_distance(path, lengths, distance):
    if distance <= 0.0:
        return path[0][0], path[0][1], path_tangent(path, 0), 0.0

    total = lengths[-1]
    if distance >= total:
        return path[-1][0], path[-1][1], path_tangent(path, len(path) - 1), 1.0

    for i in range(1, len(lengths)):
        if lengths[i] >= distance:
            segment_length = lengths[i] - lengths[i - 1]
            t = 0.0 if segment_length <= 0.000001 else (distance - lengths[i - 1]) / segment_length

            p = path[i - 1][0].lerp(path[i][0], t)
            n = path[i - 1][1].lerp(path[i][1], t)
            if n.length < 0.000001:
                n = path[i - 1][1].copy()
            n.normalize()

            tangent = path[i][0] - path[i - 1][0]
            if tangent.length < 0.000001:
                tangent = path_tangent(path, i)
            tangent.normalize()

            return p, n, tangent, distance / total

    return None


def build_rotation(
    forward_index,
    normal_index,
    side_index,
    tangent,
    surface_normal,
    location,
    camera_location,
    settings,
    rng,
):
    forward = tangent.normalized()
    surface_outward = surface_normal.normalized()

    if settings.flip_forward:
        forward *= -1.0

    # Keep the source travelling along the growth path, while optionally
    # rotating its visible/surface axis toward an explicit camera. Projecting
    # the camera vector perpendicular to the growth tangent avoids corkscrew
    # flips when the path moves across walls, tops and corners.
    outward = surface_outward.copy()

    if settings.face_camera and camera_location is not None:
        to_camera = camera_location - location
        camera_facing = to_camera - forward * to_camera.dot(forward)

        if camera_facing.length > 0.000001:
            camera_facing.normalize()
            influence = clamp(settings.camera_influence, 0.0, 1.0)
            blended = (
                surface_outward * (1.0 - influence)
                + camera_facing * influence
            )
            if blended.length > 0.000001:
                outward = blended.normalized()

    if settings.flip_normal:
        outward *= -1.0

    yaw = rng.uniform(-settings.yaw_variation, settings.yaw_variation)
    forward = (Quaternion(outward, yaw) @ forward).normalized()

    tilt = rng.uniform(-settings.surface_tilt, settings.surface_tilt)
    outward = (Quaternion(forward, tilt) @ outward).normalized()

    side = forward.cross(outward)
    if side.length < 0.000001:
        side = forward.orthogonal()
    side.normalize()
    outward = side.cross(forward).normalized()

    columns = [None, None, None]
    columns[forward_index] = forward
    columns[normal_index] = outward
    columns[side_index] = side

    rotation = Matrix((columns[0], columns[1], columns[2])).transposed()
    if rotation.determinant() < 0.0:
        columns[side_index] *= -1.0
        rotation = Matrix((columns[0], columns[1], columns[2])).transposed()

    return rotation


def scatter_source_on_path(
    source,
    target,
    collection,
    path,
    settings,
    rng,
    branch=False,
    name_offset=0,
    camera_location=None,
):
    (
        forward_index,
        normal_index,
        side_index,
        source_length,
        source_scale,
        bbox_min,
        bbox_max,
    ) = source_axis_info(source, settings)

    source_anchor = source_anchor_local(
        source,
        settings,
        forward_index,
        bbox_min,
        bbox_max,
    )

    lengths, total = path_lengths(path)
    if total <= 0.0001:
        return 0

    base_spacing = source_length * settings.source_scale * settings.spacing
    base_spacing = max(base_spacing, total / 200.0, 0.001)

    distance = rng.uniform(0.0, base_spacing * 0.35)
    count = 0
    phase_a = rng.uniform(0.0, math.tau)
    phase_b = rng.uniform(0.0, math.tau)

    while distance <= total:
        sample = sample_path_at_distance(path, lengths, distance)
        if sample is None:
            break

        location, normal, tangent, t = sample

        cluster_a = math.sin(t * math.pi * 5.0 + phase_a) * 0.5 + 0.5
        cluster_b = math.sin(t * math.pi * 11.0 + phase_b) * 0.5 + 0.5
        organic_density = cluster_a * 0.70 + cluster_b * 0.30
        cluster_factor = 1.0 - settings.clusteriness + organic_density * settings.clusteriness

        probability = settings.coverage * cluster_factor
        if branch:
            probability *= 0.92
        probability = clamp(probability, 0.03, 1.0)

        if rng.random() <= probability:
            variation = settings.scale_variation
            scale_factor = settings.source_scale * rng.uniform(1.0 - variation, 1.0 + variation)

            tip_amount = smoothstep(
                (t - settings.tip_taper_start)
                / max(1.0 - settings.tip_taper_start, 0.001)
            )
            scale_factor *= 1.0 + (settings.tip_scale - 1.0) * tip_amount

            sideways = normal.cross(tangent)
            if sideways.length > 0.000001:
                sideways.normalize()
                location += sideways * rng.uniform(-settings.lateral_jitter, settings.lateral_jitter)

            location += normal * rng.uniform(-settings.normal_jitter, settings.normal_jitter)

            rotation = build_rotation(
                forward_index,
                normal_index,
                side_index,
                tangent,
                normal,
                location,
                camera_location,
                settings,
                rng,
            )

            duplicate = source.copy()
            if hasattr(source, "data"):
                duplicate.data = source.data

            duplicate.name = "RG_Ivy_" + str(name_offset + count).zfill(4)
            duplicate.hide_viewport = False
            duplicate.hide_render = False
            collection.objects.link(duplicate)

            sx = abs(source_scale.x) * scale_factor
            sy = abs(source_scale.y) * scale_factor
            sz = abs(source_scale.z) * scale_factor
            scale_matrix = Matrix.Diagonal((sx, sy, sz, 1.0))

            # Transform-safe source placement. The anchor correction is applied in
            # source-local space, so meshes whose Location was applied no longer
            # retain their baked offset toward global 0,0,0.
            anchor_matrix = Matrix.Translation(-source_anchor)
            world_matrix = (
                Matrix.Translation(location)
                @ rotation.to_4x4()
                @ scale_matrix
                @ anchor_matrix
            )

            duplicate.matrix_world = world_matrix
            duplicate.parent = target
            duplicate.matrix_parent_inverse = target.matrix_world.inverted()
            duplicate.matrix_world = world_matrix
            duplicate["RG_IVY_GENERATED"] = True
            count += 1

        jitter = settings.spacing_variation
        spacing_factor = rng.uniform(1.0 - jitter, 1.0 + jitter)
        distance += base_spacing * max(spacing_factor, 0.15)

    return count


def smooth_root_path(path, surface, settings):
    """
    Chaikin-style smoothing projected back onto the evaluated target surface.
    This keeps the visible root soft without letting it float through corners.
    """
    iterations = max(0, int(settings.root_smoothing))

    if iterations == 0 or len(path) < 3:
        return path

    result = list(path)

    for _ in range(iterations):
        smoothed = [result[0]]

        for i in range(len(result) - 1):
            p0, _n0 = result[i]
            p1, _n1 = result[i + 1]

            q = p0.lerp(p1, 0.25)
            r = p0.lerp(p1, 0.75)

            for candidate in (q, r):
                nearest = surface.nearest(candidate)

                if nearest is None:
                    continue

                location, normal, _distance = nearest
                smoothed.append((
                    location + normal * settings.surface_offset,
                    normal.copy(),
                ))

        smoothed.append(result[-1])
        result = smoothed

    # Optional final subdivision. Unlike Curve.resolution_u on a POLY spline,
    # this genuinely adds evaluated points and projects them back onto the surface.
    subdivisions = max(1, int(settings.root_curve_resolution))

    if subdivisions > 1 and len(result) >= 2:
        subdivided = [result[0]]

        for i in range(len(result) - 1):
            p0, _n0 = result[i]
            p1, _n1 = result[i + 1]

            for step in range(1, subdivisions + 1):
                t = step / subdivisions
                candidate = p0.lerp(p1, t)
                nearest = surface.nearest(candidate)

                if nearest is None:
                    continue

                location, normal, _distance = nearest
                subdivided.append((
                    location + normal * settings.surface_offset,
                    normal.copy(),
                ))

        result = subdivided

    return result


def create_guide_curve(collection, path, name, settings, surface, is_branch=False):
    if len(path) < 2:
        return None

    display_path = smooth_root_path(path, surface, settings)

    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions = '3D'
    curve.resolution_u = 1
    curve.render_resolution_u = 1
    curve.bevel_depth = settings.root_radius * (0.72 if is_branch else 1.0)
    curve.bevel_resolution = settings.root_bevel_resolution

    if settings.root_material is not None:
        curve.materials.append(settings.root_material)

    spline = curve.splines.new('POLY')
    spline.points.add(len(display_path) - 1)

    point_count = max(len(display_path) - 1, 1)

    for index, (point, sample) in enumerate(zip(spline.points, display_path)):
        p = sample[0]
        point.co = (p.x, p.y, p.z, 1.0)

        t = index / point_count
        taper = mix(1.0, settings.root_tip_scale, smoothstep(t))
        point.radius = taper

    obj = bpy.data.objects.new(name, curve)
    collection.objects.link(obj)
    obj.hide_render = False
    obj.show_in_front = settings.root_in_front
    obj["RG_IVY_ROOT"] = True
    return obj


def generate_ivy(context, silent=False):
    global _AUTO_GENERATING, _AUTO_DIRTY

    if _AUTO_GENERATING:
        return None

    scene = context.scene
    settings = scene.rg_ivy_settings
    target = settings.target
    source = settings.source

    if target is None:
        raise RuntimeError("No target selected.")
    if source is None:
        raise RuntimeError("No vine source selected.")
    if target == source:
        raise RuntimeError("Target and vine source cannot be the same object.")
    if target.type != 'MESH':
        raise RuntimeError("Target must be a mesh.")

    _AUTO_GENERATING = True
    _AUTO_DIRTY = False

    try:
        settings.active_target_name = target.name

        delete_collection_and_objects(collection_name(target))
        delete_collection_and_objects(guide_collection_name(target))

        output_collection = create_collection(collection_name(target))
        guide_collection = create_collection(guide_collection_name(target))

        surface = SurfaceData(context, target)
        rng = random.Random(settings.seed)
        base_location, base_normal = choose_root(context, surface, settings, rng)
        step_count = max(6, int(settings.growth_length / settings.step_size))
        all_paths = []

        for runner_index in range(settings.main_runners):
            if runner_index == 0:
                root_location = base_location.copy()
                root_normal = base_normal.copy()
            else:
                side = base_normal.cross(WORLD_UP)
                if side.length < 0.00001:
                    side = base_normal.orthogonal()
                side.normalize()

                spread = (
                    surface.height
                    * settings.root_spread
                    * runner_index
                    * rng.choice((-1.0, 1.0))
                )
                root_guess = base_location + side * spread
                nearest = surface.nearest(root_guess)

                if nearest:
                    root_location, root_normal, _distance = nearest
                else:
                    root_location = base_location.copy()
                    root_normal = base_normal.copy()

            initial_direction = random_tangent(root_normal, rng)
            main_path = grow_surface_path(
                surface=surface,
                start_location=root_location,
                start_normal=root_normal,
                settings=settings,
                rng=rng,
                step_count=step_count,
                initial_direction=initial_direction,
                upward_multiplier=1.0,
                wander_multiplier=1.0,
            )

            if len(main_path) < 3:
                continue

            all_paths.append((main_path, False))
            branches = create_branches(surface, main_path, settings, rng)
            for branch in branches:
                all_paths.append((branch, True))

        total_instances = 0
        orientation_camera_location = camera_position(context, settings)

        for path_index, (path, is_branch) in enumerate(all_paths):
            added = scatter_source_on_path(
                source=source,
                target=target,
                collection=output_collection,
                path=path,
                settings=settings,
                rng=rng,
                branch=is_branch,
                name_offset=total_instances,
                camera_location=orientation_camera_location,
            )
            total_instances += added

            if settings.show_guides:
                create_guide_curve(
                    collection=guide_collection,
                    path=path,
                    name="RG_Ivy_Root_" + str(path_index).zfill(2),
                    settings=settings,
                    surface=surface,
                    is_branch=is_branch,
                )

        # The former guide is now also the physical/root stem and is meant to render.
        guide_collection.hide_render = not settings.show_guides
        guide_collection.hide_viewport = not settings.show_guides

        try:
            source.hide_set(settings.hide_source)
        except Exception:
            pass
        source.hide_render = settings.hide_source

        settings.has_generated = True
        save_settings_to_target(settings, target)

        if not silent:
            print(
                f"[RG Ivy] {total_instances} linked vine instances, "
                f"{len(all_paths)} growth paths."
            )

        return total_instances, len(all_paths)

    finally:
        _AUTO_GENERATING = False


class RG_IvyTargetItem(PropertyGroup):
    target: PointerProperty(
        name="Target",
        type=bpy.types.Object,
    )

    has_generated: BoolProperty(default=False)
    has_saved_state: BoolProperty(default=False)
    source_name: StringProperty(default="")


class RG_IvySettings(PropertyGroup):
    has_generated: BoolProperty(default=False, options={'HIDDEN'})
    active_target_name: StringProperty(default="", options={'HIDDEN'})

    ui_manager: BoolProperty(default=True)
    ui_setup: BoolProperty(default=True)
    ui_growth: BoolProperty(default=True)
    ui_root: BoolProperty(default=True)
    ui_branches: BoolProperty(default=True)
    ui_scatter: BoolProperty(default=True)
    ui_orientation: BoolProperty(default=False)
    ui_randomizer: BoolProperty(default=True)
    ui_display: BoolProperty(default=False)

    target: PointerProperty(
        name="Target",
        description="Mesh surface the ivy grows across.",
        type=bpy.types.Object,
        update=target_property_update,
    )

    source: PointerProperty(
        name="Vine Source",
        description="Existing vine object used as linked instances. Keep this as a clean source object.",
        type=bpy.types.Object,
        update=ivy_property_update,
    )

    auto_update: BoolProperty(
        name="Auto Update",
        description="After the first generation, rebuild the ivy automatically when a control changes.",
        default=True,
        update=ivy_property_update,
    )

    update_delay: FloatProperty(
        name="Update Delay",
        description="Wait this long after you stop changing a slider before regenerating. Higher values are smoother on heavy scenes.",
        subtype='TIME',
        default=0.30,
        min=0.05,
        max=2.0,
    )

    growth_side: EnumProperty(
        name="Growth Region",
        description="Choose which part of the target is favored for root placement. This is separate from Face Camera instance orientation.",
        items=[
            ('CAMERA', "Camera Side", "Prefer surfaces facing the selected Ivy Camera, falling back to the Scene Camera."),
            ('ALL', "All Sides", "Allow roots around the entire target."),
        ],
        default='CAMERA',
        update=ivy_property_update,
    )

    seed: IntProperty(
        name="Seed",
        description="Changes the random growth decisions without changing the overall settings.",
        default=23,
        min=0,
        max=999999,
        update=ivy_property_update,
    )

    main_runners: IntProperty(
        name="Main Runners",
        description="Number of primary ivy growth lines. Usually 1–3 is enough for a hero object.",
        default=2,
        min=1,
        max=8,
        update=ivy_property_update,
    )

    start_height: FloatProperty(
        name="Root Height",
        description="Maximum normalized height where the main ivy can begin. Lower values keep roots close to the ground.",
        default=0.16,
        min=0.02,
        max=0.60,
        update=ivy_property_update,
    )

    root_spread: FloatProperty(
        name="Root Spread",
        description="Separates multiple main runners so they feel like a small colony rather than one duplicated stem.",
        default=0.025,
        min=0.0,
        max=0.25,
        update=ivy_property_update,
    )

    growth_length: FloatProperty(
        name="Growth Length",
        description="Approximate distance each main runner tries to travel, relative to target height.",
        default=1.35,
        min=0.20,
        max=5.0,
        update=ivy_property_update,
    )

    step_size: FloatProperty(
        name="Growth Resolution",
        description="Distance between surface-growth steps. Smaller values hug complex geometry better but regenerate more slowly.",
        default=0.035,
        min=0.008,
        max=0.15,
        update=ivy_property_update,
    )

    upward_growth: FloatProperty(
        name="Upward Bias",
        description="How strongly ivy prefers climbing upward instead of wandering sideways.",
        default=1.0,
        min=0.0,
        max=3.0,
        update=ivy_property_update,
    )

    wander: FloatProperty(
        name="Wander",
        description="Adds organic sideways exploration to the growth direction. Too much creates scribbly paths.",
        default=0.32,
        min=0.0,
        max=2.0,
        update=ivy_property_update,
    )

    persistence: FloatProperty(
        name="Direction Persistence",
        description="How strongly each step keeps following the previous direction. Higher values create smoother, more intentional vines.",
        default=0.82,
        min=0.0,
        max=2.0,
        update=ivy_property_update,
    )

    top_crawl: FloatProperty(
        name="Top Crawl",
        description="Keeps growth moving across horizontal top surfaces after the vine reaches an upper edge.",
        default=1.15,
        min=0.0,
        max=3.0,
        update=ivy_property_update,
    )

    direction_jitter: FloatProperty(
        name="Path Jitter",
        description="Small random angular changes along the growth path. Keep fairly low for believable mature ivy.",
        subtype='ANGLE',
        default=math.radians(9.0),
        min=0.0,
        max=math.radians(60.0),
        update=ivy_property_update,
    )

    max_surface_jump: FloatProperty(
        name="Corner Wrap",
        description="Tolerance for jumping to the nearest surface around edges and corners. Increase if paths stop before wrapping an edge.",
        default=2.5,
        min=1.0,
        max=8.0,
        update=ivy_property_update,
    )

    surface_offset: FloatProperty(
        name="Surface Offset",
        description="Distance between the generated growth path and the target surface. Raise slightly if stems intersect the mesh.",
        subtype='DISTANCE',
        default=0.006,
        min=0.0,
        soft_max=0.10,
        update=ivy_property_update,
    )

    branch_count: IntProperty(
        name="Branches / Runner",
        description="Number of secondary growth paths emerging from each main runner.",
        default=4,
        min=0,
        max=16,
        update=ivy_property_update,
    )

    branch_length: FloatProperty(
        name="Branch Length",
        description="Relative length of secondary branches compared with their parent runner.",
        default=0.30,
        min=0.05,
        max=0.90,
        update=ivy_property_update,
    )

    branch_angle: FloatProperty(
        name="Branch Angle",
        description="Typical angle at which secondary growth leaves the main runner.",
        subtype='ANGLE',
        default=math.radians(48.0),
        min=math.radians(5.0),
        max=math.radians(120.0),
        update=ivy_property_update,
    )

    source_scale: FloatProperty(
        name="Vine Scale",
        description="Global scale multiplier for the source vine model used along the generated paths.",
        default=1.0,
        min=0.02,
        max=10.0,
        update=ivy_property_update,
    )

    scale_variation: FloatProperty(
        name="Scale Variation",
        description="Random scale variation between source-vine instances. Moderate variation helps break repetition.",
        default=0.18,
        min=0.0,
        max=0.80,
        update=ivy_property_update,
    )

    spacing: FloatProperty(
        name="Spacing",
        description="Distance between source-vine instances relative to the source model's detected length. Lower values create more overlap.",
        default=0.58,
        min=0.10,
        max=2.0,
        update=ivy_property_update,
    )

    spacing_variation: FloatProperty(
        name="Spacing Variation",
        description="Randomizes spacing so repeated source pieces do not form an obvious rhythm.",
        default=0.28,
        min=0.0,
        max=0.90,
        update=ivy_property_update,
    )

    coverage: FloatProperty(
        name="Coverage",
        description="Probability of placing a source-vine instance at each candidate position. Lower values create more deliberate gaps.",
        default=0.90,
        min=0.05,
        max=1.0,
        update=ivy_property_update,
    )

    clusteriness: FloatProperty(
        name="Clustering",
        description="Creates alternating dense and sparse growth zones. This is useful for avoiding a uniform procedural ribbon.",
        default=0.38,
        min=0.0,
        max=1.0,
        update=ivy_property_update,
    )

    tip_taper_start: FloatProperty(
        name="Tip Taper Start",
        description="Normalized point where instances begin getting smaller toward branch tips.",
        default=0.72,
        min=0.20,
        max=0.95,
        update=ivy_property_update,
    )

    tip_scale: FloatProperty(
        name="Tip Scale",
        description="Final scale multiplier near branch tips. Smaller values suggest younger growth.",
        default=0.72,
        min=0.10,
        max=1.0,
        update=ivy_property_update,
    )

    source_anchor_mode: EnumProperty(
        name="Source Anchor",
        description="Controls which local point of the source vine is placed on the growth path. Auto preserves a good authored origin, but automatically compensates when Apply Location has baked the source geometry away from its origin.",
        items=[
            ('AUTO', "Auto", "Recommended. Use the object origin when it is close to the geometry; otherwise compensate for applied Location and anchor the geometric start of the vine."),
            ('ORIGIN', "Object Origin", "Use the source object's local origin exactly. Use only when the origin is intentionally positioned on the vine."),
            ('START', "Geometry Start", "Use the start end of the source bounding box along its forward axis. Robust for sources with applied Location."),
            ('CENTER', "Geometry Center", "Place the geometric center of each source vine on the growth path."),
            ('END', "Geometry End", "Use the opposite end of the source bounding box along its forward axis."),
        ],
        default='AUTO',
        update=ivy_property_update,
    )

    forward_axis: EnumProperty(
        name="Source Forward",
        description="Local axis that represents the length direction of the source vine. Auto uses the longest bounding-box axis.",
        items=[
            ('AUTO', "Auto", "Detect the longest local axis."),
            ('X', "X", "Use local X as vine direction."),
            ('Y', "Y", "Use local Y as vine direction."),
            ('Z', "Z", "Use local Z as vine direction."),
        ],
        default='AUTO',
        update=ivy_property_update,
    )

    normal_axis: EnumProperty(
        name="Source Surface Axis",
        description="Local axis that should point away from the target surface. Auto uses the thinnest remaining axis.",
        items=[
            ('AUTO', "Auto", "Detect the likely surface-normal axis."),
            ('X', "X", "Use local X as surface normal."),
            ('Y', "Y", "Use local Y as surface normal."),
            ('Z', "Z", "Use local Z as surface normal."),
        ],
        default='AUTO',
        update=ivy_property_update,
    )

    flip_forward: BoolProperty(
        name="Flip Direction",
        description="Reverse the source vine's forward axis if instances point backward along the path.",
        default=False,
        update=ivy_property_update,
    )

    flip_normal: BoolProperty(
        name="Flip Surface Side",
        description="Flip the source model away from or into the surface if the automatic normal direction is inverted.",
        default=False,
        update=ivy_property_update,
    )

    face_camera: BoolProperty(
        name="Face Camera",
        description="Orient the source vine's visible/surface axis toward a camera while keeping its forward axis aligned to the growth path.",
        default=False,
        update=ivy_property_update,
    )

    face_camera_object: PointerProperty(
        name="Camera",
        description="Camera used for Face Camera orientation and Camera Side root placement. If empty, the Scene Camera is used.",
        type=bpy.types.Object,
        poll=poll_camera_object,
        update=ivy_property_update,
    )

    camera_influence: FloatProperty(
        name="Camera Influence",
        description="Blend between the target surface normal and camera-facing orientation. Around 0.6-0.75 keeps the ivy attached while presenting foliage toward the shot; 1.0 behaves more like a billboard.",
        default=0.65,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        update=ivy_property_update,
    )

    yaw_variation: FloatProperty(
        name="Direction Variation",
        description="Rotates instances sideways around the target surface normal to reduce repeated alignment.",
        subtype='ANGLE',
        default=math.radians(12.0),
        min=0.0,
        max=math.radians(90.0),
        update=ivy_property_update,
    )

    surface_tilt: FloatProperty(
        name="Surface Tilt",
        description="Lets source pieces tilt slightly away from or toward the target surface.",
        subtype='ANGLE',
        default=math.radians(10.0),
        min=0.0,
        max=math.radians(60.0),
        update=ivy_property_update,
    )

    lateral_jitter: FloatProperty(
        name="Lateral Jitter",
        description="Offsets instances sideways across the surface to break a perfectly centered chain.",
        subtype='DISTANCE',
        default=0.008,
        min=0.0,
        soft_max=0.10,
        update=ivy_property_update,
    )

    normal_jitter: FloatProperty(
        name="Depth Jitter",
        description="Small random offset toward or away from the surface. Keep subtle to avoid floating vine pieces.",
        subtype='DISTANCE',
        default=0.003,
        min=0.0,
        soft_max=0.05,
        update=ivy_property_update,
    )

    randomize_strength: FloatProperty(
        name="Variation Strength",
        description="How far Randomize Look can move the current artistic settings toward a new realistic combination.",
        default=0.75,
        min=0.0,
        max=1.0,
    )

    show_guides: BoolProperty(
        name="Show Root / Guide",
        description="Create and display the generated growth curves as physical ivy roots/stems. These curves are visible in the viewport and render.",
        default=True,
        update=ivy_property_update,
    )

    root_material: PointerProperty(
        name="Root Material",
        description="Material assigned to the generated root/guide curves. Use a stem, bark or ivy-root material from your scene.",
        type=bpy.types.Material,
        update=ivy_property_update,
    )

    root_radius: FloatProperty(
        name="Root Radius",
        description="Physical radius of the generated root/stem curve. Secondary branches are automatically slightly thinner.",
        subtype='DISTANCE',
        default=0.003,
        min=0.0001,
        soft_max=0.05,
        update=ivy_property_update,
    )

    root_tip_scale: FloatProperty(
        name="Root Tip Scale",
        description="Tapers the visible root toward the end of each growth path. Lower values create finer young tips.",
        default=0.38,
        min=0.05,
        max=1.0,
        update=ivy_property_update,
    )

    root_smoothing: IntProperty(
        name="Root Smoothness",
        description="Number of surface-projected smoothing passes for the visible root path. Higher values add more points and soften angular growth while staying attached to the target.",
        default=1,
        min=0,
        max=4,
        update=ivy_property_update,
    )

    root_curve_resolution: IntProperty(
        name="Root Resolution",
        description="Surface-projected subdivisions added between generated root samples. Higher values make the visible root denser and smoother. Growth Resolution still controls the actual growth simulation accuracy.",
        default=2,
        min=1,
        max=8,
        update=ivy_property_update,
    )

    root_bevel_resolution: IntProperty(
        name="Roundness",
        description="Cross-section resolution of the root/stem. 2–4 is usually enough; higher values make the tube rounder but add geometry.",
        default=3,
        min=0,
        max=8,
        update=ivy_property_update,
    )

    root_in_front: BoolProperty(
        name="Guide In Front",
        description="Draw the generated root/guide in front of other geometry in the viewport for easier art direction. This does not affect rendering.",
        default=False,
        update=ivy_property_update,
    )

    hide_source: BoolProperty(
        name="Hide Source",
        description="Hide the original source vine after generation. Generated copies remain visible and renderable.",
        default=False,
        update=ivy_property_update,
    )


class RG_OT_IvyInfo(Operator):
    bl_idname = "rg.ivy_info"
    bl_label = "Parameter Info"
    bl_options = {'INTERNAL'}

    message: StringProperty(default="")

    @classmethod
    def description(cls, context, properties):
        return properties.message if properties.message else "Parameter information"

    def execute(self, context):
        self.report({'INFO'}, self.message)
        return {'FINISHED'}


class RG_UL_IvyTargets(UIList):
    bl_idname = "RG_UL_ivy_targets"

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index,
    ):
        target = item.target

        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            if target is None:
                row.label(text="Missing target", icon='ERROR')
                return

            row.label(text=target.name, icon='OUTLINER_OB_MESH')

            if item.has_generated:
                row.label(text="Ivy", icon='CHECKMARK')
            elif item.has_saved_state:
                row.label(text="Setup", icon='INFO')
            else:
                row.label(text="Legacy", icon='QUESTION')

        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon='OUTLINER_OB_MESH')


class RG_OT_IvyRefreshTargets(Operator):
    bl_idname = "rg.ivy_refresh_targets"
    bl_label = "Refresh Ivy Targets"
    bl_description = "Rescan the scene for all objects that have an RG Ivy setup or generated ivy"

    def execute(self, context):
        rebuild_target_manager(context.scene)
        self.report({'INFO'}, f"Found {len(context.scene.rg_ivy_targets)} ivy targets")
        return {'FINISHED'}


class RG_OT_IvyLoadManagedTarget(Operator):
    bl_idname = "rg.ivy_load_managed_target"
    bl_label = "Load Selected Ivy Target"
    bl_description = "Load the selected target's own saved ivy settings and make it the active Blender object"

    def execute(self, context):
        scene = context.scene
        index = scene.rg_ivy_target_index

        if index < 0 or index >= len(scene.rg_ivy_targets):
            self.report({'ERROR'}, "No ivy target selected")
            return {'CANCELLED'}

        target = scene.rg_ivy_targets[index].target
        if target is None:
            rebuild_target_manager(scene)
            self.report({'ERROR'}, "The selected target no longer exists")
            return {'CANCELLED'}

        activate_target(scene, target)

        try:
            bpy.ops.object.select_all(action='DESELECT')
            target.hide_set(False)
            target.select_set(True)
            context.view_layer.objects.active = target
        except Exception:
            pass

        rebuild_target_manager(scene, preferred_target=target)
        return {'FINISHED'}


class RG_OT_IvyRegenerateManagedTarget(Operator):
    bl_idname = "rg.ivy_regenerate_managed_target"
    bl_label = "Regenerate Selected Ivy"
    bl_description = "Load the selected managed target and regenerate only its ivy system"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        index = scene.rg_ivy_target_index

        if index < 0 or index >= len(scene.rg_ivy_targets):
            self.report({'ERROR'}, "No ivy target selected")
            return {'CANCELLED'}

        target = scene.rg_ivy_targets[index].target
        if target is None:
            self.report({'ERROR'}, "The selected target no longer exists")
            return {'CANCELLED'}

        activate_target(scene, target)

        try:
            result = generate_ivy(context, silent=False)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        rebuild_target_manager(scene, preferred_target=target)

        if result:
            count, paths = result
            self.report({'INFO'}, f"{count} vine instances, {paths} growth paths")

        return {'FINISHED'}


class RG_OT_IvyUseSelection(Operator):
    bl_idname = "rg.ivy_use_selection"
    bl_label = "Use Selected Pair"
    bl_description = "Select the vine source first, then Shift-select the target mesh last so it is active."

    def execute(self, context):
        global _AUTO_SUSPEND

        selected = list(context.selected_objects)
        target = context.active_object

        if target is None or target not in selected or len(selected) != 2:
            self.report({'ERROR'}, "Select exactly two objects. Target must be active last.")
            return {'CANCELLED'}

        source = selected[0] if selected[1] == target else selected[1]

        if target.type != 'MESH':
            self.report({'ERROR'}, "Target must be a mesh.")
            return {'CANCELLED'}

        activate_target(
            context.scene,
            target,
            source_override=source,
        )
        rebuild_target_manager(
            context.scene,
            preferred_target=target,
        )
        request_auto_update(context.scene)
        return {'FINISHED'}


class RG_OT_IvyUseSceneCamera(Operator):
    bl_idname = "rg.ivy_use_scene_camera"
    bl_label = "Use Scene Camera"
    bl_description = "Assign the current Scene Camera to the ivy Camera field."

    def execute(self, context):
        global _AUTO_SUSPEND

        camera = context.scene.camera
        if camera is None or camera.type != 'CAMERA':
            self.report({'ERROR'}, "No Scene Camera is assigned.")
            return {'CANCELLED'}

        settings = context.scene.rg_ivy_settings
        _AUTO_SUSPEND = True
        try:
            settings.face_camera_object = camera
        finally:
            _AUTO_SUSPEND = False

        save_current_target_state(context.scene)
        request_auto_update(context.scene)
        return {'FINISHED'}


class RG_OT_IvyGenerate(Operator):
    bl_idname = "rg.ivy_generate"
    bl_label = "Generate / Refresh Ivy"
    bl_description = "Build or rebuild the ivy using the current settings. After the first build, Auto Update can handle later changes."
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            result = generate_ivy(context, silent=False)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        rebuild_target_manager(
            context.scene,
            preferred_target=context.scene.rg_ivy_settings.target,
        )

        if result:
            count, paths = result
            self.report({'INFO'}, f"{count} vine instances, {paths} growth paths")

        return {'FINISHED'}


class RG_OT_IvyClear(Operator):
    bl_idname = "rg.ivy_clear"
    bl_label = "Clear Ivy"
    bl_description = "Delete generated ivy and guide curves for the current target."

    def execute(self, context):
        global _AUTO_DIRTY

        settings = context.scene.rg_ivy_settings
        target = settings.target

        if target is not None:
            delete_collection_and_objects(collection_name(target))
            delete_collection_and_objects(guide_collection_name(target))
            settings.has_generated = False
            try:
                target["RG_IVY_HAS_GENERATED"] = False
            except Exception:
                pass
            save_settings_to_target(settings, target)

        settings.has_generated = False
        _AUTO_DIRTY = False
        rebuild_target_manager(
            context.scene,
            preferred_target=target,
        )
        return {'FINISHED'}


class RG_OT_IvyRandomizeSeed(Operator):
    bl_idname = "rg.ivy_randomize_seed"
    bl_label = "New Growth"
    bl_description = "Change only the random seed. Use this when the current parameters are good but you want another growth composition."

    def execute(self, context):
        global _AUTO_SUSPEND

        settings = context.scene.rg_ivy_settings
        _AUTO_SUSPEND = True
        try:
            settings.seed = random.randint(0, 999999)
        finally:
            _AUTO_SUSPEND = False

        if settings.has_generated:
            try:
                generate_ivy(context, silent=True)
            except Exception as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}

        return {'FINISHED'}


class RG_OT_IvyRandomizeLook(Operator):
    bl_idname = "rg.ivy_randomize_look"
    bl_label = "Randomize Look"
    bl_description = "Randomize growth, branching, spacing and variation within constrained realistic ranges."

    def execute(self, context):
        global _AUTO_SUSPEND

        settings = context.scene.rg_ivy_settings
        strength = settings.randomize_strength
        rng = random.Random(time.time_ns())

        def blend_prop(current, target):
            return mix(current, target, strength)

        def blend_int(current, target):
            return int(round(blend_prop(current, target)))

        _AUTO_SUSPEND = True
        try:
            settings.seed = rng.randint(0, 999999)

            settings.main_runners = clamp(blend_int(settings.main_runners, rng.randint(1, 3)), 1, 8)
            settings.start_height = blend_prop(settings.start_height, rng.uniform(0.08, 0.20))
            settings.root_spread = blend_prop(settings.root_spread, rng.uniform(0.015, 0.045))
            settings.growth_length = blend_prop(settings.growth_length, rng.uniform(1.05, 1.65))
            settings.upward_growth = blend_prop(settings.upward_growth, rng.uniform(0.82, 1.30))
            settings.wander = blend_prop(settings.wander, rng.uniform(0.20, 0.48))
            settings.persistence = blend_prop(settings.persistence, rng.uniform(0.72, 1.12))
            settings.top_crawl = blend_prop(settings.top_crawl, rng.uniform(0.90, 1.55))
            settings.direction_jitter = blend_prop(
                settings.direction_jitter,
                math.radians(rng.uniform(5.0, 15.0)),
            )
            settings.max_surface_jump = blend_prop(settings.max_surface_jump, rng.uniform(2.1, 3.8))

            settings.branch_count = clamp(blend_int(settings.branch_count, rng.randint(2, 6)), 0, 16)
            settings.branch_length = blend_prop(settings.branch_length, rng.uniform(0.20, 0.42))
            settings.branch_angle = blend_prop(
                settings.branch_angle,
                math.radians(rng.uniform(32.0, 68.0)),
            )

            settings.scale_variation = blend_prop(settings.scale_variation, rng.uniform(0.10, 0.25))
            settings.spacing = blend_prop(settings.spacing, rng.uniform(0.45, 0.75))
            settings.spacing_variation = blend_prop(settings.spacing_variation, rng.uniform(0.16, 0.38))
            settings.coverage = blend_prop(settings.coverage, rng.uniform(0.74, 0.97))
            settings.clusteriness = blend_prop(settings.clusteriness, rng.uniform(0.22, 0.56))
            settings.tip_taper_start = blend_prop(settings.tip_taper_start, rng.uniform(0.65, 0.80))
            settings.tip_scale = blend_prop(settings.tip_scale, rng.uniform(0.60, 0.82))

            settings.yaw_variation = blend_prop(
                settings.yaw_variation,
                math.radians(rng.uniform(7.0, 18.0)),
            )
            settings.surface_tilt = blend_prop(
                settings.surface_tilt,
                math.radians(rng.uniform(4.0, 14.0)),
            )
        finally:
            _AUTO_SUSPEND = False

        if settings.has_generated:
            try:
                generate_ivy(context, silent=True)
            except Exception as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}

        return {'FINISHED'}


def section_header(layout, settings, property_name, label):
    opened = getattr(settings, property_name)
    box = layout.box()
    row = box.row(align=True)
    row.prop(
        settings,
        property_name,
        text=label,
        icon='TRIA_DOWN' if opened else 'TRIA_RIGHT',
        emboss=False,
    )
    return box, opened


def draw_info_prop(layout, settings, prop_name, label=None, info=None, slider=False):
    row = layout.row(align=True)
    row.prop(settings, prop_name, text=label if label is not None else "", slider=slider)

    if info is None:
        prop = settings.bl_rna.properties.get(prop_name)
        info = prop.description if prop else ""

    op = row.operator(
        "rg.ivy_info",
        text="",
        icon='INFO',
        emboss=False,
    )
    op.message = info or "No additional information."


class RG_PT_IvyPanel(Panel):
    bl_label = "Realistic Ivy"
    bl_idname = "RG_PT_realistic_ivy"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Ivy"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.rg_ivy_settings

        box, opened = section_header(layout, settings, "ui_manager", "Ivy Systems")
        if opened:
            row = box.row()
            row.template_list(
                "RG_UL_ivy_targets",
                "",
                scene,
                "rg_ivy_targets",
                scene,
                "rg_ivy_target_index",
                rows=4,
            )

            controls = row.column(align=True)
            controls.operator("rg.ivy_refresh_targets", text="", icon='FILE_REFRESH')
            controls.operator("rg.ivy_load_managed_target", text="", icon='RESTRICT_SELECT_OFF')
            controls.operator("rg.ivy_regenerate_managed_target", text="", icon='OUTLINER_OB_CURVE')

            if len(scene.rg_ivy_targets) == 0:
                box.label(text="No ivy systems found in this scene", icon='INFO')
            else:
                index = min(max(scene.rg_ivy_target_index, 0), len(scene.rg_ivy_targets) - 1)
                item = scene.rg_ivy_targets[index]
                if item.target is not None:
                    info_row = box.row(align=True)
                    info_row.label(text=f"Selected: {item.target.name}")
                    if item.source_name:
                        box.label(text=f"Source: {item.source_name}", icon='LINKED')

                actions = box.row(align=True)
                actions.operator("rg.ivy_load_managed_target", text="Load / Jump", icon='RESTRICT_SELECT_OFF')
                actions.operator("rg.ivy_regenerate_managed_target", text="Regenerate", icon='FILE_REFRESH')

        box, opened = section_header(layout, settings, "ui_setup", "Setup")
        if opened:
            col = box.column(align=True)
            draw_info_prop(col, settings, "target", "Target")
            draw_info_prop(col, settings, "source", "Vine Source")
            col.separator()
            col.operator("rg.ivy_use_selection", icon='EYEDROPPER')
            if settings.target is not None:
                if target_has_generated_system(settings.target):
                    col.label(text=f"Independent ivy found for: {settings.target.name}", icon='LINKED')
                else:
                    col.label(text="This target has no saved ivy system yet", icon='INFO')
            col.separator()
            draw_info_prop(col, settings, "growth_side", "Growth Region")
            col.separator()

            row = col.row(align=True)
            row.prop(settings, "auto_update")
            info = row.operator("rg.ivy_info", text="", icon='INFO', emboss=False)
            info.message = settings.bl_rna.properties["auto_update"].description
            if settings.auto_update:
                row.prop(settings, "update_delay", text="Delay")

            if settings.has_generated:
                col.label(
                    text="Live" if settings.auto_update else "Manual update",
                    icon='PLAY' if settings.auto_update else 'PAUSE',
                )

        box, opened = section_header(layout, settings, "ui_growth", "Growth")
        if opened:
            col = box.column(align=True)
            draw_info_prop(col, settings, "main_runners", "Main Runners")
            draw_info_prop(col, settings, "start_height", "Root Height")
            draw_info_prop(col, settings, "root_spread", "Root Spread")
            draw_info_prop(col, settings, "growth_length", "Growth Length")
            draw_info_prop(col, settings, "surface_offset", "Surface Offset")
            col.separator()
            draw_info_prop(col, settings, "upward_growth", "Upward Bias")
            draw_info_prop(col, settings, "wander", "Wander")
            draw_info_prop(col, settings, "persistence", "Direction Persistence")
            draw_info_prop(col, settings, "top_crawl", "Top Crawl")
            draw_info_prop(col, settings, "direction_jitter", "Path Jitter")
            draw_info_prop(col, settings, "max_surface_jump", "Corner Wrap")
            col.separator()
            draw_info_prop(col, settings, "step_size", "Growth Resolution")

        box, opened = section_header(layout, settings, "ui_root", "Root / Guide")
        if opened:
            col = box.column(align=True)
            draw_info_prop(col, settings, "show_guides", "Show Root / Guide")
            draw_info_prop(col, settings, "root_material", "Root Material")
            col.separator()
            draw_info_prop(col, settings, "root_radius", "Root Radius")
            draw_info_prop(col, settings, "root_tip_scale", "Root Tip Scale")
            col.separator()
            draw_info_prop(col, settings, "root_smoothing", "Root Smoothness")
            draw_info_prop(col, settings, "root_curve_resolution", "Root Resolution")
            draw_info_prop(col, settings, "root_bevel_resolution", "Roundness")
            col.separator()
            draw_info_prop(col, settings, "root_in_front", "Guide In Front")

        box, opened = section_header(layout, settings, "ui_branches", "Branching")
        if opened:
            col = box.column(align=True)
            draw_info_prop(col, settings, "branch_count", "Branches / Runner")
            draw_info_prop(col, settings, "branch_length", "Branch Length")
            draw_info_prop(col, settings, "branch_angle", "Branch Angle")

        box, opened = section_header(layout, settings, "ui_scatter", "Vine Scatter")
        if opened:
            col = box.column(align=True)
            draw_info_prop(col, settings, "source_scale", "Vine Scale")
            draw_info_prop(col, settings, "scale_variation", "Scale Variation")
            col.separator()
            draw_info_prop(col, settings, "spacing", "Spacing")
            draw_info_prop(col, settings, "spacing_variation", "Spacing Variation")
            draw_info_prop(col, settings, "coverage", "Coverage")
            draw_info_prop(col, settings, "clusteriness", "Clustering")
            col.separator()
            draw_info_prop(col, settings, "tip_taper_start", "Tip Taper Start")
            draw_info_prop(col, settings, "tip_scale", "Tip Scale")

        box, opened = section_header(layout, settings, "ui_orientation", "Orientation")
        if opened:
            col = box.column(align=True)
            draw_info_prop(col, settings, "source_anchor_mode", "Source Anchor")
            col.separator()
            draw_info_prop(col, settings, "forward_axis", "Source Forward")
            draw_info_prop(col, settings, "normal_axis", "Surface Axis")
            draw_info_prop(col, settings, "flip_forward", "Flip Direction")
            draw_info_prop(col, settings, "flip_normal", "Flip Surface Side")
            col.separator()
            draw_info_prop(col, settings, "face_camera", "Face Camera")
            if settings.face_camera:
                draw_info_prop(col, settings, "face_camera_object", "Camera")
                row = col.row(align=True)
                row.operator("rg.ivy_use_scene_camera", text="Use Scene Camera", icon='CAMERA_DATA')

                resolved_camera = camera_object_for_settings(context, settings)
                if resolved_camera is None:
                    warning = col.row()
                    warning.alert = True
                    warning.label(text="No camera available", icon='ERROR')
                elif settings.face_camera_object is None:
                    col.label(text=f"Using Scene Camera: {resolved_camera.name}", icon='CAMERA_DATA')

                draw_info_prop(col, settings, "camera_influence", "Camera Influence", slider=True)

            col.separator()
            draw_info_prop(col, settings, "yaw_variation", "Direction Variation")
            draw_info_prop(col, settings, "surface_tilt", "Surface Tilt")
            draw_info_prop(col, settings, "lateral_jitter", "Lateral Jitter")
            draw_info_prop(col, settings, "normal_jitter", "Depth Jitter")

        box, opened = section_header(layout, settings, "ui_randomizer", "Randomizer")
        if opened:
            col = box.column(align=True)
            draw_info_prop(col, settings, "seed", "Seed")
            row = col.row()
            row.scale_y = 1.15
            row.operator("rg.ivy_randomize_seed", text="New Growth", icon='FILE_REFRESH')
            col.separator()
            draw_info_prop(col, settings, "randomize_strength", "Variation Strength")
            row = col.row()
            row.scale_y = 1.25
            row.operator("rg.ivy_randomize_look", text="Randomize Look", icon='FILE_REFRESH')
            col.label(text="Keeps scale, axes and surface offset.")

        box, opened = section_header(layout, settings, "ui_display", "Display")
        if opened:
            col = box.column(align=True)
            draw_info_prop(col, settings, "hide_source", "Hide Source")

        layout.separator()
        row = layout.row()
        row.scale_y = 1.6
        row.operator("rg.ivy_generate", icon='OUTLINER_OB_CURVE')

        row = layout.row()
        row.operator("rg.ivy_clear", icon='TRASH')


@persistent
def rg_ivy_load_post(_dummy):
    for scene in bpy.data.scenes:
        if hasattr(scene, "rg_ivy_settings") and hasattr(scene, "rg_ivy_targets"):
            try:
                rebuild_target_manager(scene)
            except Exception as exc:
                print("[RG Ivy] Target manager refresh failed after load:", repr(exc))


classes = (
    RG_IvyTargetItem,
    RG_IvySettings,
    RG_UL_IvyTargets,
    RG_OT_IvyInfo,
    RG_OT_IvyRefreshTargets,
    RG_OT_IvyLoadManagedTarget,
    RG_OT_IvyRegenerateManagedTarget,
    RG_OT_IvyUseSelection,
    RG_OT_IvyUseSceneCamera,
    RG_OT_IvyGenerate,
    RG_OT_IvyClear,
    RG_OT_IvyRandomizeSeed,
    RG_OT_IvyRandomizeLook,
    RG_PT_IvyPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.rg_ivy_settings = PointerProperty(type=RG_IvySettings)
    bpy.types.Scene.rg_ivy_targets = CollectionProperty(type=RG_IvyTargetItem)
    bpy.types.Scene.rg_ivy_target_index = IntProperty(default=0, min=0)

    if rg_ivy_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(rg_ivy_load_post)

    for scene in bpy.data.scenes:
        try:
            rebuild_target_manager(scene)
        except Exception:
            pass


def unregister():
    global _AUTO_TIMER_PENDING, _AUTO_DIRTY

    _AUTO_TIMER_PENDING = False
    _AUTO_DIRTY = False

    if rg_ivy_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(rg_ivy_load_post)

    if hasattr(bpy.types.Scene, "rg_ivy_target_index"):
        del bpy.types.Scene.rg_ivy_target_index

    if hasattr(bpy.types.Scene, "rg_ivy_targets"):
        del bpy.types.Scene.rg_ivy_targets

    if hasattr(bpy.types.Scene, "rg_ivy_settings"):
        del bpy.types.Scene.rg_ivy_settings

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


if __name__ == "__main__":
    register()
