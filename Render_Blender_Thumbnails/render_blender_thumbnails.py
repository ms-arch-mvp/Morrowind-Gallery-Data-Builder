import bpy
import os
import sys
import mathutils
import math
import pathlib
import numpy as np

# Terminal color codes for logging
OKGREEN = '\033[92m'
OKCYAN = '\033[96m'
OKBLUE = '\033[94m'
WARNING = '\033[93m'
FAIL = '\033[91m'
ENDC = '\033[0m'

# ==============================================================================
# 1. Parse Arguments
# ==============================================================================
argv = sys.argv
if '--' in argv:
    args = argv[argv.index('--') + 1:]
else:
    args = argv[1:]

positional_args = [a for a in args if not a.startswith('--')]
if len(positional_args) < 2:
    print(f"{FAIL}Usage: blender --background --python run_single_nif.py -- <nif_path> <output_path> [--cull-backfaces] [--format PNG|TGA]{ENDC}")
    sys.exit(1)

nif_path_str = positional_args[0]
output_path = positional_args[1]
cull_backfaces = "--cull-backfaces" in args

# A .blend source is opened directly rather than imported through the NIF addon.
is_blend = nif_path_str.lower().endswith('.blend')

# Output format: PNG (default) or TGA
output_format = 'PNG'
if "--format" in args:
    try:
        fmt_idx = args.index("--format")
        output_format = args[fmt_idx + 1].upper()
        if output_format not in ('PNG', 'TGA'):
            print(f"{WARNING}[Worker] Unknown format '{output_format}', falling back to PNG.{ENDC}")
            output_format = 'PNG'
    except (ValueError, IndexError):
        pass

auto_set_emissive = "--auto-set-emissive-color" in args

# Collect multiple emissive-exception values (supports repeated flags)
emissive_exceptions = []
i = 0
while i < len(args):
    if args[i] == "--emissive-exception":
        try:
            emissive_exceptions.append(args[i + 1].lower())
            i += 2
            continue
        except Exception:
            pass
    i += 1

resolution = 1024
if "--resolution" in args:
    try:
        res_idx = args.index("--resolution")
        resolution = int(args[res_idx + 1])
    except (ValueError, IndexError):
        pass

is_creature = "--is-creature" in args
is_npc = "--is-npc" in args
is_body_part = "--is-body-part" in args
render_vertex_normals = "--vertex-normals" in args

# ------------------------------------------------------------------------------
# NIF import settings (forwarded from the PowerShell control panel).
# Only consumed for .nif imports; ignored for .blend sources.
# Order mirrors the importer's own attribute list.
# ------------------------------------------------------------------------------
def _arg_value(flag, default=None):
    """Return the token following `flag`, or `default` if absent."""
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return args[idx + 1]
    return default

def _arg_bool(flag, default):
    """Parse a boolean setting passed as `--flag True|False`."""
    val = _arg_value(flag, None)
    if val is None:
        return default
    return str(val).strip().lower() in ('1', 'true', 'yes', 'on')

nif_use_existing_materials      = _arg_bool('--nif-use-existing-materials', True)
nif_ignore_collision_nodes      = _arg_bool('--nif-ignore-collision-nodes', True)
nif_ignore_animations           = _arg_bool('--nif-ignore-animations', False)
nif_ignore_armatures            = _arg_bool('--nif-ignore-armatures', False)
nif_ignore_billboard_nodes      = _arg_bool('--nif-ignore-billboard-nodes', True)
nif_ignore_emissive_color       = _arg_bool('--nif-ignore-emissive-color', False)
nif_ignore_tri_shadow           = _arg_bool('--nif-ignore-tri-shadow', True)
nif_ignore_nodes                = _arg_value('--nif-ignore-nodes', 'Lightning')
nif_ignore_nodes_under_switches = _arg_value('--nif-ignore-nodes-under-switches', 'OFF, HARVESTED, Closed')
nif_filter_best_lod             = _arg_bool('--nif-filter-best-lod', True)

rotation_angle = 0
if "--rotation" in args:
    try:
        rot_idx = args.index("--rotation")
        rotation_angle = float(args[rot_idx + 1])
    except (ValueError, IndexError):
        pass

# ==============================================================================
# 2. Setup Scene
# ==============================================================================
def clear_scene():
    """Surgically clear the scene without resetting user preferences."""
    # Delete all objects
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    
    # Clear other data-blocks to prevent memory bloat
    for mesh in bpy.data.meshes:
        bpy.data.meshes.remove(mesh)
    for mat in bpy.data.materials:
        bpy.data.materials.remove(mat)
    for img in bpy.data.images:
        bpy.data.images.remove(img)
    for cam in bpy.data.cameras:
        bpy.data.cameras.remove(cam)
    for light in bpy.data.lights:
        bpy.data.lights.remove(light)

# ==============================================================================
# 3. Load the model (import NIF, or open a .blend directly)
# ==============================================================================
if is_blend:
    # Opening a .blend replaces the entire scene, so no manual clear is needed.
    print(f"{OKBLUE}[Worker] Opening BLEND: {nif_path_str}{ENDC}")
    try:
        bpy.ops.wm.open_mainfile(filepath=nif_path_str)
    except Exception as e:
        print(f"{FAIL}[Worker] Error opening BLEND: {e}{ENDC}")
        sys.exit(1)
else:
    clear_scene()

    # Ensure the NIF importer addon is enabled in background mode
    addon_name = 'io_scene_mw'
    if addon_name not in bpy.context.preferences.addons:
        print(f"{OKCYAN}[Worker] Enabling addon: {addon_name}{ENDC}")
        try:
            bpy.ops.preferences.addon_enable(module=addon_name)
        except Exception as e:
            print(f"{FAIL}[Worker] Error enabling addon {addon_name}: {e}{ENDC}")

    # Check and print current texture paths for debugging
    prefs = bpy.context.preferences.addons[addon_name].preferences
    if hasattr(prefs, "texture_paths"):
        paths = [p.name for p in prefs.texture_paths]
        print(f"{OKCYAN}[Worker] Texture paths found in preferences: {paths}{ENDC}")
    else:
        print(f"{WARNING}[Worker] No texture_paths property found in addon preferences.{ENDC}")

    print(f"{OKBLUE}[Worker] Importing NIF: {nif_path_str}{ENDC}")
    try:
        bpy.ops.import_scene.mw(
            filepath=nif_path_str,
            use_existing_materials=nif_use_existing_materials,
            ignore_collision_nodes=nif_ignore_collision_nodes,
            ignore_animations=nif_ignore_animations,
            ignore_armatures=nif_ignore_armatures,
            ignore_billboard_nodes=nif_ignore_billboard_nodes,
            ignore_emissive_color=nif_ignore_emissive_color,
            ignore_tri_shadow=nif_ignore_tri_shadow,
            ignore_nodes=nif_ignore_nodes,
            ignore_nodes_under_switches=nif_ignore_nodes_under_switches,
            filter_best_lod=nif_filter_best_lod,
        )
    except Exception as e:
        print(f"{FAIL}[Worker] Error importing NIF: {e}{ENDC}")
        sys.exit(1)


# ==============================================================================
# 4. Camera
# ==============================================================================
def get_scene_all_points():
    """Get all world-space points for all geometry in the scene."""
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    if not meshes:
        return None
    
    depsgraph = bpy.context.evaluated_depsgraph_get()
    all_points = []
    for obj in meshes:
        eval_obj = obj.evaluated_get(depsgraph)
        try:
            # For Blender 3.0+, we need to request the evaluated mesh
            eval_mesh = eval_obj.to_mesh()
            if eval_mesh.vertices and len(eval_mesh.vertices) > 0:
                for v in eval_mesh.vertices:
                    all_points.append(eval_obj.matrix_world @ v.co)
            else:
                for corner in eval_obj.bound_box:
                    all_points.append(eval_obj.matrix_world @ mathutils.Vector(corner))
            eval_obj.to_mesh_clear()
        except Exception:
            # Fallback to base object bounding box
            for corner in obj.bound_box:
                all_points.append(obj.matrix_world @ mathutils.Vector(corner))
    return all_points

all_world_points = get_scene_all_points()

if all_world_points is None:
    print(f"{WARNING}[Worker] No mesh objects found in file.{ENDC}")
    sys.exit(0)

# Calculate world-space center of the bounding box
min_world = mathutils.Vector((min(p.x for p in all_world_points), min(p.y for p in all_world_points), min(p.z for p in all_world_points)))
max_world = mathutils.Vector((max(p.x for p in all_world_points), max(p.y for p in all_world_points), max(p.z for p in all_world_points)))
world_center = (min_world + max_world) / 2
world_size = max_world - min_world
max_dim = max(world_size)

# Ensure we have a camera and set it to Ortho
if bpy.context.scene.camera is None:
    cam_data = bpy.data.cameras.new(name="Camera")
    cam_obj = bpy.data.objects.new("Camera", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

cam = bpy.context.scene.camera
cam.data.type = 'ORTHO'

# Position the camera
if is_npc:
    # Front-on view for NPCs
    diagonal = mathutils.Vector((0, 1, 0)).normalized()
elif is_body_part:
    # Front-on view for body parts (rotated 180 from NPC)
    diagonal = mathutils.Vector((0, -1, 0)).normalized()
elif is_creature:
    # Opposite way for creatures
    diagonal = mathutils.Vector((-1, 1, 1)).normalized()
else:
    # Default
    diagonal = mathutils.Vector((1, -1, 1)).normalized()

if rotation_angle != 0:
    # Rotate the diagonal vector around the Z axis (Counter-clockwise) to rotate the object clockwise in the image
    rot_quat = mathutils.Quaternion((0, 0, 1), math.radians(rotation_angle))
    diagonal = rot_quat @ diagonal
# For ortho, the exact distance doesn't affect magnification, but we need 
# enough distance to avoid clipping the model's volume.
distance = max_dim * 5 
cam.location = world_center + diagonal * distance

# Look-at rotation (point precisely at world center)
forward = (world_center - cam.location).normalized()
up_vec = mathutils.Vector((0, 0, 1))
right_vec = forward.cross(up_vec).normalized()
up_final = right_vec.cross(forward).normalized()
rot_matrix = mathutils.Matrix((right_vec, up_final, -forward)).transposed()
cam.rotation_euler = rot_matrix.to_euler()

# Update scene so the camera's matrix_world is correct
bpy.context.view_layer.depsgraph.update()

# Perform projection-based fitting for the perfect ortho_scale
cam_inv = cam.matrix_world.inverted()
local_points = [cam_inv @ p for p in all_world_points]
min_local = mathutils.Vector((min(p.x for p in local_points), min(p.y for p in local_points), min(p.z for p in local_points)))
max_local = mathutils.Vector((max(p.x for p in local_points), max(p.y for p in local_points), max(p.z for p in local_points)))

# The scale needed depends on the local width and height of the bounding volume
width_needed = max_local.x - min_local.x
height_needed = max_local.y - min_local.y
cam.data.ortho_scale = max(width_needed, height_needed)

# Center the camera precisely on the projected model center
local_center = (min_local + max_local) / 2
cam.location = cam.matrix_world @ mathutils.Vector((local_center.x, local_center.y, 0))

# ==============================================================================
# 5. Rendering
# ==============================================================================

def apply_vertex_normal_material():
    """Replace all materials with a vertex normal visualisation shader (world space)."""
    mat_name = "__VertexNormalVis__"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(name=mat_name)
    # Blender <5.0 may require explicit node enabling; 5.0+ always has nodes
    if mat.node_tree is None:
        mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    node_out   = nodes.new('ShaderNodeOutputMaterial')
    node_emit  = nodes.new('ShaderNodeEmission')
    node_geo   = nodes.new('ShaderNodeNewGeometry')
    node_xform = nodes.new('ShaderNodeVectorTransform')
    node_xform.vector_type = 'NORMAL'
    node_xform.convert_from = 'WORLD'
    node_xform.convert_to   = 'CAMERA'
    node_vmul  = nodes.new('ShaderNodeVectorMath')
    node_vmul.operation = 'MULTIPLY_ADD'
    node_vmul.inputs[1].default_value = (0.5, 0.5, -0.5)
    node_vmul.inputs[2].default_value = (0.5, 0.5,  0.5)

    links.new(node_geo.outputs['Normal'],    node_xform.inputs['Vector'])
    links.new(node_xform.outputs['Vector'],  node_vmul.inputs[0])
    links.new(node_vmul.outputs['Vector'],   node_emit.inputs['Color'])
    links.new(node_emit.outputs['Emission'], node_out.inputs['Surface'])

    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH':
            continue
        obj.data.materials.clear()
        obj.data.materials.append(mat)


scene = bpy.context.scene

if output_format == 'TGA':
    scene.render.image_settings.file_format = 'TARGA'
    scene.render.image_settings.color_mode = 'RGBA'
else:
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
scene.render.film_transparent = True
# Strip extension — Blender appends the correct one based on file_format.
# Passing a path WITH an extension causes Blender to write two files.
scene.render.filepath = str(pathlib.Path(output_path).with_suffix(''))
scene.render.resolution_x = resolution
scene.render.resolution_y = resolution
scene.render.resolution_percentage = 100

# To achieve the most accurate look (textures + vertex colors), we use the EEVEE engine
scene.render.engine = 'BLENDER_EEVEE'

if render_vertex_normals:
    scene.view_settings.view_transform = 'Raw'
    scene.view_settings.look = 'None'

# Set up a flat, white background for unlit result
if scene.world is None:
    scene.world = bpy.data.worlds.new("World")
world_nodes = scene.world.node_tree.nodes
world_nodes.clear()
world_node_out = world_nodes.new('ShaderNodeOutputWorld')
world_node_back = world_nodes.new('ShaderNodeBackground')
world_node_back.inputs['Color'].default_value = (1, 1, 1, 1)
world_node_back.inputs['Strength'].default_value = 1.0
scene.world.node_tree.links.new(world_node_back.outputs[0], world_node_out.inputs[0])

# Backface culling
for mat in bpy.data.materials:
    if cull_backfaces:
        mat.use_backface_culling = True
        mat.show_transparent_back = False
    else:
        mat.use_backface_culling = False
        mat.show_transparent_back = True


def _is_non_black_color(col):
    try:
        # col can be a sequence (r,g,b,...) or a float
        if hasattr(col, '__len__'):
            return (col[0] + col[1] + col[2]) > 0.01
        else:
            return float(col) > 0.01
    except Exception:
        return False


def adjust_emissive_materials():
    """Set emissive inputs to black except for materials matching the exception string which become mid-grey.

    Tries several heuristics to find emissive inputs in node trees and node groups
    used by the Morrowind shader addon.
    """
    mid_grey = (0.5, 0.5, 0.5, 1.0)
    black = (0.0, 0.0, 0.0, 1.0)

    for mat in bpy.data.materials:
        name = (mat.name or "").lower()
        if getattr(mat, 'node_tree', None) is None:
            continue

        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        def set_color_on_socket(socket, color_tuple):
            try:
                socket.default_value = color_tuple
            except Exception:
                try:
                    # some sockets expect 3-float values
                    socket.default_value = color_tuple[:3]
                except Exception:
                    pass

        # First pass: emission nodes
        for node in nodes:
            try:
                if node.type == 'EMISSION':
                    col_socket = node.inputs.get('Color')
                    if col_socket is None:
                        continue
                    # If color is linked, find the source node
                    if col_socket.is_linked:
                        from_node = col_socket.links[0].from_node
                        # If source is an RGB node or group, try to set its value
                        if hasattr(from_node, 'outputs'):
                            for out in from_node.outputs:
                                # attempt to set any color default on the source node
                                try:
                                    if hasattr(from_node, 'inputs') and from_node.inputs:
                                        for inp in from_node.inputs:
                                            if 'color' in inp.name.lower() or 'emiss' in inp.name.lower():
                                                try:
                                                    if _is_non_black_color(inp.default_value):
                                                        if any(exc and exc in name for exc in emissive_exceptions):
                                                            set_color_on_socket(inp, mid_grey)
                                                        else:
                                                            set_color_on_socket(inp, black)
                                                except Exception:
                                                    pass
                                except Exception:
                                    pass
                    else:
                        # default value present on emission node
                        cur = col_socket.default_value
                        if _is_non_black_color(cur):
                            if any(exc and exc in name for exc in emissive_exceptions):
                                set_color_on_socket(col_socket, mid_grey)
                            else:
                                set_color_on_socket(col_socket, black)

                # Handle node groups
                if node.type == 'GROUP' and node.node_tree is not None:
                    # Iterate inputs that look like emissive/glow/emission
                    for inp in node.inputs:
                        iname = (inp.name or "").lower()
                        if 'emiss' in iname or 'glow' in iname or 'emit' in iname:
                            # If linked, follow the link
                            if inp.is_linked:
                                from_node = inp.links[0].from_node
                                # try to set color on connected node inputs
                                try:
                                    for fin in from_node.inputs:
                                        if 'color' in fin.name.lower() or 'emiss' in fin.name.lower():
                                                                if _is_non_black_color(fin.default_value):
                                                                    if any(exc and exc in name for exc in emissive_exceptions):
                                                                        set_color_on_socket(fin, mid_grey)
                                                                    else:
                                                                        set_color_on_socket(fin, black)
                                except Exception:
                                    pass
                            else:
                                # Direct default on group input
                                if _is_non_black_color(inp.default_value):
                                    if any(exc and exc in name for exc in emissive_exceptions):
                                        set_color_on_socket(inp, mid_grey)
                                    else:
                                        set_color_on_socket(inp, black)
            except Exception:
                continue

        # Additionally, look for any nodes with attribute 'inputs' whose input names suggest emissive
        for node in nodes:
            try:
                for inp in getattr(node, 'inputs', []) or []:
                    iname = (inp.name or "").lower()
                    if 'emiss' in iname or 'glow' in iname or 'emit' in iname:
                        if inp.is_linked:
                            from_node = inp.links[0].from_node
                            for fin in getattr(from_node, 'inputs', []) or []:
                                if 'color' in (fin.name or '').lower() or 'emiss' in (fin.name or '').lower():
                                    if _is_non_black_color(fin.default_value):
                                        if any(exc and exc in name for exc in emissive_exceptions):
                                            set_color_on_socket(fin, mid_grey)
                                        else:
                                            set_color_on_socket(fin, black)
                        else:
                            if _is_non_black_color(inp.default_value):
                                if any(exc and exc in name for exc in emissive_exceptions):
                                    set_color_on_socket(inp, mid_grey)
                                else:
                                    set_color_on_socket(inp, black)
            except Exception:
                pass


if auto_set_emissive:
    try:
        print(f"{OKCYAN}[Worker] Adjusting emissive colors per auto-set flag{ENDC}")
        adjust_emissive_materials()
    except Exception as e:
        print(f"{FAIL}[Worker] Failed adjusting emissive colors: {e}{ENDC}")

if render_vertex_normals:
    try:
        print(f"{OKCYAN}[Worker] Applying vertex normal visualisation material{ENDC}")
        apply_vertex_normal_material()
    except Exception as e:
        print(f"{FAIL}[Worker] Failed applying vertex normal material: {e}{ENDC}")

# Hide all lights and non-mesh objects to ensure they don't affect the unlit look
for o in bpy.context.scene.objects:
    if o.type != 'MESH':
        o.hide_render = True


def resolve_saved_output_path():
    ext_map = {'PNG': '.png', 'TARGA': '.tga', 'TARGA_RAW': '.tga'}
    fmt = scene.render.image_settings.file_format
    expected_ext = ext_map.get(fmt, pathlib.Path(output_path).suffix)
    return str(pathlib.Path(output_path).with_suffix(expected_ext))


def image_has_visible_pixels(image_path):
    img = bpy.data.images.load(image_path)
    try:
        pixels = np.array(img.pixels)
        return np.any(pixels[3::4] > 0.01)
    finally:
        bpy.data.images.remove(img)


# Perform the render
print(f"{OKCYAN}[Worker] Rendering to: {output_path}{ENDC}")
try:
    bpy.ops.render.render(write_still=True)
except Exception as e:
    print(f"{FAIL}[Worker] Render failed: {e}{ENDC}")
    sys.exit(1)

if cull_backfaces:
    try:
        # Blender may correct the extension on save (e.g. adds .tga when format is TARGA).
        # Resolve the actual saved path so we load the right file.
        resolved_path = resolve_saved_output_path()
        
        # Alpha channel is every 4th element starting at index 3
        # If there are no pixels with alpha > 0.01, the image is transparent
        if not image_has_visible_pixels(resolved_path):
            print(f"{WARNING}[Worker] Image is completely transparent. Re-rendering without backface culling.{ENDC}")
            for mat in bpy.data.materials:
                mat.use_backface_culling = False
                mat.show_transparent_back = True
            
            # Re-render
            bpy.ops.render.render(write_still=True)
            print(f"{OKCYAN}[Worker] Secondary render completed.{ENDC}")
    except Exception as e:
        print(f"{FAIL}[Worker] Failed to verify transparency: {e}{ENDC}")

try:
    final_output_path = resolve_saved_output_path()
    if not os.path.exists(final_output_path):
        print(f"{FAIL}[Worker] Expected output file was not created: {final_output_path}{ENDC}")
        sys.exit(1)

    if not image_has_visible_pixels(final_output_path):
        print(f"{WARNING}[Worker] Final render is empty/fully transparent.{ENDC}")
        sys.exit(0)
except SystemExit:
    raise
except Exception as e:
    print(f"{FAIL}[Worker] Failed to validate final output: {e}{ENDC}")
    sys.exit(1)

print(f"{OKGREEN}[Worker] Finished.{ENDC}")
