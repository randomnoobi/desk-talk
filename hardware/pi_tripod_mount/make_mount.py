"""Tilted tripod mount for a Raspberry Pi 5 in an Argon NEO 5 (BRED) case.

Run with Blender (no window needed):
    /Applications/Blender.app/Contents/MacOS/Blender -b -P make_mount.py

It writes, next to this file:
    pi_tripod_mount_15deg.stl, _30deg.stl, _45deg.stl
                            the mount at three tilts, already lying the way
                            it should be printed (USB end down, no supports)
    fit_test.stl            a 12 mm slice of the walls, to check the case
                            slides in snugly before printing the whole thing
    pi_tripod_mount.blend   the 30 degree model, to open and tweak in Blender
    preview_side.png        side view as it sits on the tripod
    preview.png             the mount by itself

All sizes are in millimetres. Change the numbers below and run it again.

How it's used: tilt the tripod head to 90 degrees, so the tripod plate faces
forward. The flat foot of the mount screws onto it. The case slides into the
tray from the USB end, like a drawer, and lips on the two long walls hold it
in. With the GPIO side up and the HDMI side down, the case looks forward and
down by the TILT angle. The HDMI side's wall is only at the USB end, so the
power and HDMI plugs hang down freely.
"""

import math
import os

import bmesh
import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))

# --- The case (measure yours and change these) ---------------------------
CASE_L = 95.5  # long side, microSD end to USB end
CASE_W = 71.5  # short side, GPIO side to HDMI side
CASE_H = 30.0  # thickness, bottom to top; the lips sit just above this

# --- Tilt -----------------------------------------------------------------
TILTS = (15, 30, 45)  # one STL for each, in degrees down from straight ahead
BLEND_TILT = 30  # the one saved in the .blend and shown in the pictures

# --- Fit and strength -----------------------------------------------------
GAP = 0.3  # room between case and wall on each side (bigger = looser)
WALL = 3.0  # wall thickness
PLATE = 5.0  # thickness of the tray the case lies on
LIP_GAP = 0.4  # room between the case top and the lips
LIP_IN = 2.0  # how far the lips reach over the case's top edge
LIP_T = 2.0  # lip thickness

# --- Walls ------------------------------------------------------------------
HDMI_WALL_LEN = 30.0  # HDMI side wall, only at the USB end, clear of plugs
SD_WALL_H = 4.0  # low stop at the microSD end
SD_GAP = 20.0  # opening in it for the card
SD_CORNER_LEN = 5.0  # small ledge on the HDMI side at the microSD end

# --- Foot and tripod nut ----------------------------------------------------
FOOT_W = 44.0  # how wide the flat face against the tripod plate is
FOOT_MIN = 3.0  # thinnest part of the foot, at its low edge
NUT_FLATS = 11.11 + 0.3  # across the flats (1/4"-20 nut), plus press room
NUT_DEPTH = 5.56 + 0.2  # nut thickness, plus a little
UNDER_NUT = 3.0  # plastic between the nut and the tripod plate
SCREW_HOLE = 6.6  # 1/4" tripod screw passes through

# --- Camera slot (Arducam ABS case for Camera Module 3) ---------------------
# It sits on the top edge, past the GPIO wall, and looks the same way as the
# Pi. Its red base slides in from the outside edge, under two lips, until it
# stops against the GPIO wall. The ribbon goes out through a gap on that side.
CAMERA = True
CAM_L = 39.9  # red base, the edge the ribbon comes out of (faces the Pi)
CAM_W = 34.8  # red base, the other edge (the way it slides in)
CAM_BASE_H = 5.0  # red base thickness, under the clear cover (measure this)
CAM_H = 14.7  # whole camera case, only used for the pictures
CAM_GAP = 0.3  # room round the red base
CAM_LIP_GAP = 0.3  # room between the red base and the lips
CAM_LIP_IN = 2.0  # how far the lips reach over the red base
RIBBON_GAP = 20.0  # opening for the ribbon cable (it is 16 mm wide)

# --- Fit test ---------------------------------------------------------------
FIT_TEST_LEN = 12.0

# Derived sizes. In the flat tray the case's middle is at x = 0, y = 0; +x is
# the USB end, +y the GPIO side, and the tray's underside is z = 0.
IN_X = CASE_L / 2 + GAP
IN_Y = CASE_W / 2 + GAP
OUT_X = IN_X + WALL
OUT_Y = IN_Y + WALL
LIP_Z = PLATE + CASE_H + LIP_GAP
WALL_TOP = LIP_Z + LIP_T
# Camera slot, at the USB end so it prints standing on the bed.
CAM_SEAT = WALL_TOP  # the camera's red base sits at the top of the GPIO wall
CAM_X1 = OUT_X - WALL
CAM_X0 = CAM_X1 - CAM_L - 2 * CAM_GAP
CAM_Y0 = OUT_Y
CAM_Y1 = CAM_Y0 + CAM_W + 2 * CAM_GAP
CAM_LIP_Z = CAM_SEAT + CAM_BASE_H + CAM_LIP_GAP
CAM_TOP = CAM_LIP_Z + LIP_T


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001
    scene.unit_settings.length_unit = "MILLIMETERS"


def new_object(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def box(name, x0, x1, y0, y1, z0, z1):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((x0 if v.co.x < 0 else x1, y0 if v.co.y < 0 else y1,
                       z0 if v.co.z < 0 else z1))
    return new_object(name, bm)


def prism(name, radius, sides, z0, z1, x=0.0, y=0.0):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=sides, radius1=radius,
                          radius2=radius, depth=z1 - z0)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(x, y, (z0 + z1) / 2))
    return new_object(name, bm)


def hull(name, points):
    bm = bmesh.new()
    for p in points:
        bm.verts.new(p)
    bmesh.ops.convex_hull(bm, input=bm.verts)
    return new_object(name, bm)


def combine(target, tool, op):
    mod = target.modifiers.new("bool", "BOOLEAN")
    mod.operation = op
    mod.solver = "EXACT"
    mod.object = tool
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(tool, do_unlink=True)


def build_tray(name):
    """The flat tray the case slides into, before it's tilted."""
    tray = box(name, -OUT_X, OUT_X, -OUT_Y, OUT_Y, 0, PLATE)
    e = 0.01  # small overlap so the pieces join cleanly
    pieces = [
        # GPIO side: full-length wall with a lip over the case's top edge
        box("gpio_wall", -OUT_X, OUT_X, IN_Y, OUT_Y, PLATE - e, WALL_TOP),
        box("gpio_lip", -OUT_X, OUT_X, IN_Y - LIP_IN, IN_Y + e, LIP_Z,
            WALL_TOP),
        # HDMI side: wall and lip only at the USB end
        box("hdmi_wall", OUT_X - HDMI_WALL_LEN, OUT_X, -OUT_Y, -IN_Y,
            PLATE - e, WALL_TOP),
        box("hdmi_lip", OUT_X - HDMI_WALL_LEN, OUT_X, -IN_Y - e,
            -IN_Y + LIP_IN, LIP_Z, WALL_TOP),
        # microSD end: low stop with a gap for the card
        box("sd_wall_a", -OUT_X, -IN_X, SD_GAP / 2, OUT_Y, PLATE - e,
            PLATE + SD_WALL_H),
        box("sd_wall_b", -OUT_X, -IN_X, -OUT_Y, -SD_GAP / 2, PLATE - e,
            PLATE + SD_WALL_H),
        box("sd_corner", -OUT_X, -IN_X + SD_CORNER_LEN, -OUT_Y, -IN_Y,
            PLATE - e, PLATE + SD_WALL_H),
    ]
    for p in pieces:
        combine(tray, p, "UNION")
    return tray


def build_camera_holder(name, with_brace=True):
    """The camera slot on top of the GPIO wall, in the flat tray's frame."""
    e = 0.01
    s = CAM_SEAT
    holder = box(name, CAM_X0 - WALL, OUT_X, IN_Y, CAM_Y1 + 1, s - PLATE, s)
    mid = (CAM_X0 + CAM_X1) / 2
    pieces = [
        # side rails with lips over the red base
        box("cam_rail_a", CAM_X1, OUT_X, IN_Y, CAM_Y1 + 1, s - e, CAM_TOP),
        box("cam_lip_a", CAM_X1 - CAM_LIP_IN, CAM_X1 + e, IN_Y, CAM_Y1 + 1,
            CAM_LIP_Z, CAM_TOP),
        box("cam_rail_b", CAM_X0 - WALL, CAM_X0, IN_Y, CAM_Y1 + 1, s - e,
            CAM_TOP),
        box("cam_lip_b", CAM_X0 - e, CAM_X0 + CAM_LIP_IN, IN_Y, CAM_Y1 + 1,
            CAM_LIP_Z, CAM_TOP),
        # stop on the Pi side, with a gap for the ribbon
        box("cam_stop_a", CAM_X0 - e, mid - RIBBON_GAP / 2, IN_Y, CAM_Y0,
            s - e, CAM_SEAT + CAM_BASE_H),
        box("cam_stop_b", mid + RIBBON_GAP / 2, CAM_X1 + e, IN_Y, CAM_Y0,
            s - e, CAM_SEAT + CAM_BASE_H),
    ]
    if with_brace:
        # a solid brace from the tray down under the camera seat
        pts = []
        for x in (CAM_X0 - WALL, OUT_X):
            pts += [Vector((x, OUT_Y - 0.5, 0)), Vector((x, OUT_Y - 0.5, s)),
                    Vector((x, CAM_Y1 + 1, s - PLATE + 0.5)),
                    Vector((x, CAM_Y1 + 1, s))]
        pieces.append(hull("cam_brace", pts))
    for p in pieces:
        combine(holder, p, "UNION")
    return holder


def build_camera_fit_test():
    """Just the camera slot, lying flat, with 2 mm of seat under it."""
    test = build_camera_holder("CameraFitTest", with_brace=False)
    keep = box("keep", CAM_X0 - WALL - 1, OUT_X + 1, IN_Y - 1, CAM_Y1 + 2,
               CAM_SEAT - 2, CAM_TOP + 1)
    combine(test, keep, "INTERSECT")
    for v in test.data.vertices:
        v.co.z -= CAM_SEAT - 2
    return test


def tilt_matrix(tilt):
    """Tilt the tray so the GPIO side rises, and lift its low edge clear."""
    a = math.radians(tilt)
    lift = FOOT_MIN + OUT_Y * math.sin(a)
    return Matrix.Translation((0, 0, lift)) @ Matrix.Rotation(a, 4, "X")


def build_mount(tilt):
    a = math.radians(tilt)
    tilt_m = tilt_matrix(tilt)
    mount = build_tray(f"PiTripodMount_{tilt}deg")
    if CAMERA:
        combine(mount, build_camera_holder("camera_holder"), "UNION")
    mount.data.transform(tilt_m)

    # Foot: fills from the tripod plate (z = 0) up to the tray's underside
    # along the tray's low edge.
    pts = []
    for x in (-OUT_X, OUT_X):
        for y in (-OUT_Y, -OUT_Y + FOOT_W):
            p = tilt_m @ Vector((x, y, 0.5))
            pts += [p, Vector((p.x, p.y, 0))]
    combine(mount, hull("foot", pts), "UNION")

    # Nut pocket from above, screw hole from below, at the foot's middle.
    sy = (-OUT_Y + FOOT_W / 2) * math.cos(a)
    floor = (tilt_m @ Vector((0, -OUT_Y + FOOT_W / 2, PLATE))).z
    nut_r = NUT_FLATS / 2 / math.cos(math.radians(30))
    combine(mount, prism("nut", nut_r, 6, UNDER_NUT, floor + 8, y=sy),
            "DIFFERENCE")
    combine(mount, prism("hole", SCREW_HOLE / 2, 48, -1, UNDER_NUT + 1, y=sy),
            "DIFFERENCE")
    return mount, sy


def build_fit_test():
    test = build_tray("FitTest")
    keep = box("keep", OUT_X - FIT_TEST_LEN, OUT_X + 1, -OUT_Y - 1, OUT_Y + 1,
               PLATE - 2, WALL_TOP + 1)
    combine(test, keep, "INTERSECT")
    return test


def print_matrix(obj):
    """Stand it on its USB end: every wall and lip then grows straight up."""
    rot = Matrix.Rotation(math.radians(90), 4, "Y")  # +x (USB end) goes down
    pts = [rot @ v.co for v in obj.data.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts),
                 min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), 0))
    shift = Vector((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))
    return Matrix.Translation(shift) @ rot


def export_stl(obj, path):
    copy = obj.copy()
    copy.data = obj.data.copy()
    bpy.context.collection.objects.link(copy)
    copy.data.transform(print_matrix(copy))
    copy.location = (0, 0, 0)
    bpy.ops.object.select_all(action="DESELECT")
    copy.select_set(True)
    bpy.context.view_layer.objects.active = copy
    bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True,
                          apply_modifiers=True)
    bpy.data.objects.remove(copy, do_unlink=True)


def material(name, rgba):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = rgba
    return mat


def add_helpers(tilt, sy):
    """See-through case and a stand-in tripod plate, for the pictures."""
    case = box("NEO5_case_preview", -CASE_L / 2, CASE_L / 2, -CASE_W / 2,
               CASE_W / 2, PLATE, PLATE + CASE_H)
    case.data.transform(tilt_matrix(tilt))
    case.data.materials.append(material("case", (0.05, 0.05, 0.06, 1)))
    plate = box("tripod_plate_preview", -22, 22, sy - 22, sy + 22, -10, 0)
    plate.data.materials.append(material("tripod", (0.35, 0.35, 0.38, 1)))
    helpers = [case, plate]
    if CAMERA:
        cam = box("camera_preview", CAM_X0 + CAM_GAP, CAM_X1 - CAM_GAP,
                  CAM_Y0 + CAM_GAP, CAM_Y1 - CAM_GAP, CAM_SEAT,
                  CAM_SEAT + CAM_BASE_H)
        cover = box("camera_cover_preview", CAM_X0 + 5, CAM_X1 - 5,
                    CAM_Y0 + 4, CAM_Y1 - 4, CAM_SEAT + CAM_BASE_H,
                    CAM_SEAT + CAM_H)
        lens = prism("camera_lens_preview", 4, 32, CAM_SEAT + CAM_H - 0.5,
                     CAM_SEAT + CAM_H + 1.5, x=(CAM_X0 + CAM_X1) / 2,
                     y=(CAM_Y0 + CAM_Y1) / 2)
        cam.data.materials.append(material("cam", (0.85, 0.12, 0.12, 1)))
        cover.data.materials.append(material("cover", (0.8, 0.85, 0.9, 1)))
        lens.data.materials.append(material("lens", (0.02, 0.02, 0.02, 1)))
        for o in (cam, cover, lens):
            o.data.transform(tilt_matrix(tilt))
        helpers += [cam, cover, lens]
    return helpers


def as_used(objs):
    """Turn everything the way it sits with the tripod head at 90 degrees."""
    for o in objs:
        o.rotation_euler = (math.radians(90), 0, 0)


def render(path, cam_loc, target, ortho=None):
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = cam_loc
    cam.rotation_euler = (Vector(target) - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.clip_end = 5000
    if ortho:
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = ortho
    else:
        cam_data.lens = 50
    scene.camera = cam
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.show_shadows = False
    scene.display.shading.show_cavity = True
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 900
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)


def main():
    clear_scene()
    for tilt in TILTS:
        mount, _ = build_mount(tilt)
        export_stl(mount, os.path.join(HERE, f"pi_tripod_mount_{tilt}deg.stl"))
        bpy.data.objects.remove(mount, do_unlink=True)

    fit = build_fit_test()
    export_stl(fit, os.path.join(HERE, "fit_test.stl"))
    bpy.data.objects.remove(fit, do_unlink=True)
    if CAMERA:
        cam_fit = build_camera_fit_test()
        bpy.ops.object.select_all(action="DESELECT")
        cam_fit.select_set(True)
        bpy.ops.wm.stl_export(
            filepath=os.path.join(HERE, "camera_fit_test.stl"),
            export_selected_objects=True)
        bpy.data.objects.remove(cam_fit, do_unlink=True)

    mount, sy = build_mount(BLEND_TILT)
    mount.data.materials.append(material("mount", (0.95, 0.55, 0.15, 1)))
    helpers = add_helpers(BLEND_TILT, sy)
    case = helpers[0]
    as_used([mount] + helpers)

    case.hide_render = True
    render(os.path.join(HERE, "preview.png"), (200, -280, -40), (0, -20, 0))
    case.hide_render = False
    render(os.path.join(HERE, "preview_side.png"), (400, -30, 0), (0, -30, 0),
           ortho=190)
    bpy.ops.wm.save_as_mainfile(
        filepath=os.path.join(HERE, "pi_tripod_mount.blend"))


main()
