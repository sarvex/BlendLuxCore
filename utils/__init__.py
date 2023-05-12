import bpy
import mathutils
import math
import re
import hashlib
import os
from os.path import basename, dirname
from ..bin import pyluxcore
from . import view_layer

MESH_OBJECTS = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
EXPORTABLE_OBJECTS = MESH_OBJECTS | {"LIGHT"}
NON_DEFORMING_MODIFIERS = {"COLLISION", "PARTICLE_INSTANCE", "PARTICLE_SYSTEM", "SMOKE"}


def sanitize_luxcore_name(string):
    """
    Do NOT use this function to create a luxcore name for an object/material/etc.!
    Use the function get_luxcore_name() instead.
    This is just a regex that removes non-allowed characters.
    """
    return re.sub("[^_0-9a-zA-Z]+", "__", string)


def make_key(datablock):
    # We use the memory address as key, e.g. to track materials or objects even when they are
    # renamed during viewport render.
    # Note that the memory address changes on undo/redo, but in this case the viewport render
    # is stopped and re-started anyway, so it should not be a problem.
    assert isinstance(datablock, bpy.types.ID)
    return str(datablock.original.as_pointer())


def make_key_from_bpy_struct(bpy_struct):
    return str(bpy_struct.as_pointer())


def make_key_from_instance(dg_obj_instance):
    if dg_obj_instance.is_instance:
        key = make_key(dg_obj_instance.object)
        key += f"_{make_key(dg_obj_instance.parent)}"
        key += persistent_id_to_str(dg_obj_instance.persistent_id)
    else:
        key = make_key(dg_obj_instance.object.original)
    return key


def make_name_from_instance(dg_obj_instance):
    return sanitize_luxcore_name(make_key_from_instance(dg_obj_instance))


def get_pretty_name(datablock):
    name = datablock.name

    if hasattr(datablock, "type"):
        name = f"{datablock.type.title()}_{name}"

    return name


def get_luxcore_name(datablock, is_viewport_render=True):
    """
    This is the function you should use to get a unique luxcore name
    for a datablock (object, lamp, material etc.).
    If is_viewport_render is True, the name is persistent even if
    the user renames the datablock.

    Note that we can't use pretty names in viewport render.
    If we would do that, renaming a datablock during the render
    would change all references to it.
    """
    key = make_key(datablock)

    if not is_viewport_render:
        # Final render - we can use pretty names
        key = get_pretty_name(datablock) + key

    return sanitize_luxcore_name(key)


def obj_from_key(key, objects):
    return next((obj for obj in objects if key == make_key(obj)), None)


def persistent_id_to_str(persistent_id):
    # Apparently we need all entries in persistent_id, otherwise
    # there are collisions when instances are nested
    return "_".join([str(pid) for pid in persistent_id])


def make_object_id(dg_obj_instance):
    chosen_id = dg_obj_instance.object.original.luxcore.id
    if chosen_id != -1:
        return chosen_id

    if dg_obj_instance.is_instance:
        # random_id seems to be a 4-Byte integer in range -0xffffffff to 0xffffffff.
        return dg_obj_instance.random_id & 0xfffffffe

    key = dg_obj_instance.object.original.name

    # We do this similar to Cycles: hash the object's name to get an ID that's stable over
    # frames and between re-renders (as long as the object is not renamed).
    digest = hashlib.md5(key.encode("utf-8")).digest()
    as_int = int.from_bytes(digest, byteorder="little")
    # Truncate to 4 bytes because LuxCore uses unsigned int for the object ID.
    # Make sure it's not exactly 0xffffffff because that's LuxCore's Null index for object IDs.
    return min(as_int & 0xffffffff, 0xffffffff - 1)


def create_props(prefix, definitions):
    """
    :param prefix: string, will be prepended to each key part of the definitions.
                   Example: "scene.camera." (note the trailing dot)
    :param definitions: dictionary of definition pairs. Example: {"fieldofview", 45}
    :return: pyluxcore.Properties() object, initialized with the given definitions.
    """
    props = pyluxcore.Properties()

    for k, v in definitions.items():
        props.Set(pyluxcore.Property(prefix + k, v))

    return props


def matrix_to_list(matrix, invert=False):
    """
    Flatten a 4x4 matrix into a list
    Returns list[16]
    """
    # Copy required for BlenderMatrix4x4ToList(), not sure why, but if we don't
    # make a copy, we only get an identity matrix in C++
    matrix = matrix.copy()

    if invert:
        matrix.invert_safe()

    return pyluxcore.BlenderMatrix4x4ToList(matrix)


def list_to_matrix(lst):
    return mathutils.Matrix([lst[:4], lst[4:8], lst[8:12], lst[12:16]])


def calc_filmsize_raw(scene, context=None):
    if context:
        # Viewport render
        width = context.region.width
        height = context.region.height
    else:
        # Final render
        scale = scene.render.resolution_percentage / 100
        width = int(scene.render.resolution_x * scale)
        height = int(scene.render.resolution_y * scale)

    return width, height


def calc_filmsize(scene, context=None):
    render = scene.render
    border_min_x, border_max_x, border_min_y, border_max_y = calc_blender_border(scene, context)
    width_raw, height_raw = calc_filmsize_raw(scene, context)
    
    if context:
        # Viewport render        
        width = width_raw
        height = height_raw
        if context.region_data.view_perspective in ("ORTHO", "PERSP"):            
            width = int(width_raw * border_max_x) - int(width_raw * border_min_x)
            height = int(height_raw * border_max_y) - int(height_raw * border_min_y)
        else:
            # Camera viewport
            zoom = 0.25 * ((math.sqrt(2) + context.region_data.view_camera_zoom / 50) ** 2)
            aspectratio, aspect_x, aspect_y = calc_aspect(render.resolution_x * render.pixel_aspect_x,
                                                          render.resolution_y * render.pixel_aspect_y,
                                                          scene.camera.data.sensor_fit)

            if render.use_border:
                base = zoom
                if scene.camera.data.sensor_fit == "AUTO":
                    base *= max(width, height)
                elif scene.camera.data.sensor_fit == "HORIZONTAL":
                    base *= width
                elif scene.camera.data.sensor_fit == "VERTICAL":
                    base *= height

                width = int(base * aspect_x * border_max_x) - int(base * aspect_x * border_min_x)
                height = int(base * aspect_y * border_max_y) - int(base * aspect_y * border_min_y)

        pixel_size = int(scene.luxcore.viewport.pixel_size)
        width //= pixel_size
        height //= pixel_size
    else:
        # Final render
        width = int(width_raw * border_max_x) - int(width_raw * border_min_x)
        height = int(height_raw * border_max_y) - int(height_raw * border_min_y)

    # Make sure width and height are never zero
    # (can e.g. happen if you have a small border in camera viewport and zoom out a lot)
    width = max(2, width)
    height = max(2, height)

    return width, height


def calc_blender_border(scene, context=None):
    if context and context.region_data.view_perspective in ("ORTHO", "PERSP"):
        # Viewport camera
        border_max_x = context.space_data.render_border_max_x
        border_max_y = context.space_data.render_border_max_y
        border_min_x = context.space_data.render_border_min_x
        border_min_y = context.space_data.render_border_min_y
        use_border = context.space_data.use_render_border
    else:
        render = scene.render

        # Final camera
        border_max_x = render.border_max_x
        border_max_y = render.border_max_y
        border_min_x = render.border_min_x
        border_min_y = render.border_min_y

        use_border = render.use_border

    if use_border:
        blender_border = [border_min_x, border_max_x, border_min_y, border_max_y]
        # Round all values to avoid running into problems later
        # when a value is for example 0.699999988079071
        return [round(value, 6) for value in blender_border]
    else:
        return [0, 1, 0, 1]


def calc_screenwindow(zoom, shift_x, shift_y, scene, context=None):
    # shift is in range -2..2
    # offset is in range -1..1
    render = scene.render

    width_raw, height_raw = calc_filmsize_raw(scene, context)
    border_min_x, border_max_x, border_min_y, border_max_y = calc_blender_border(scene, context)

    # Following: Black Magic
    scale = 1
    offset_x = 0
    offset_y = 0

    if context:
        # Viewport rendering
        if context.region_data.view_perspective == "CAMERA":
            # Camera view
            offset_x, offset_y = context.region_data.view_camera_offset

            if scene.camera and scene.camera.data.type == "ORTHO":                    
                scale = 0.5 * scene.camera.data.ortho_scale

            if render.use_border:
                offset_x = 0
                offset_y = 0
                aspectratio, xaspect, yaspect = calc_aspect(render.resolution_x * render.pixel_aspect_x,
                                                            render.resolution_y * render.pixel_aspect_y,
                                                            scene.camera.data.sensor_fit)

                zoom = scale if scene.camera and scene.camera.data.type == "ORTHO" else 1
            else:
                # No border
                aspectratio, xaspect, yaspect = calc_aspect(width_raw, height_raw, scene.camera.data.sensor_fit)

        else:
            # Normal viewport
            aspectratio, xaspect, yaspect = calc_aspect(width_raw, height_raw)
    else:
        # Final rendering
        aspectratio, xaspect, yaspect = calc_aspect(render.resolution_x * render.pixel_aspect_x,
                                                    render.resolution_y * render.pixel_aspect_y,
                                                    scene.camera.data.sensor_fit)

        if scene.camera and scene.camera.data.type == "ORTHO":                    
            scale = 0.5 * scene.camera.data.ortho_scale                

    dx = scale * 2 * (shift_x + 2 * xaspect * offset_x)
    dy = scale * 2 * (shift_y + 2 * yaspect * offset_y)

    screenwindow = [
        -xaspect*zoom + dx,
         xaspect*zoom + dx,
        -yaspect*zoom + dy,
         yaspect*zoom + dy
    ]

    screenwindow = [
        screenwindow[0] * (1 - border_min_x) + screenwindow[1] * border_min_x,
        screenwindow[0] * (1 - border_max_x) + screenwindow[1] * border_max_x,
        screenwindow[2] * (1 - border_min_y) + screenwindow[3] * border_min_y,
        screenwindow[2] * (1 - border_max_y) + screenwindow[3] * border_max_y
    ]

    return screenwindow


def calc_aspect(width, height, fit="AUTO"):
    horizontal_fit = False
    if fit == "AUTO":
        horizontal_fit = (width > height)
    elif fit == "HORIZONTAL":
        horizontal_fit = True
    
    if horizontal_fit:
        aspect = height / width
        xaspect = 1
        yaspect = aspect
    else:
        aspect = width / height
        xaspect = aspect
        yaspect = 1
    
    return aspect, xaspect, yaspect


def find_active_uv(uv_layers):
    return next((uv for uv in uv_layers if uv.active_render), None)


def find_active_vertex_color_layer(vertex_colors):
    return next((layer for layer in vertex_colors if layer.active_render), None)


def is_instance_visible(dg_obj_instance, obj, context):
    if not (dg_obj_instance.show_self or dg_obj_instance.show_particles):
        return False
    
    if context:    
        viewport_vis_obj = dg_obj_instance.parent if dg_obj_instance.parent else obj
        if not viewport_vis_obj.visible_in_viewport_get(context.space_data):
            return False
        
    return is_obj_visible(obj)


def is_obj_visible(obj):
    if obj.luxcore.exclude_from_render or obj.type not in EXPORTABLE_OBJECTS:
        return False

    # Do not export the object if it's made completely invisible through Cycles settings
    # (some addons like HardOps do this to hide objects)
    return is_obj_visible_in_cycles(obj)


def is_obj_visible_in_cycles(obj):
    if bpy.app.version[:2] >= (3, 0):
        return any((obj.visible_camera, obj.visible_diffuse, obj.visible_glossy, obj.visible_transmission, obj.visible_volume_scatter, obj.visible_shadow))
    c_vis = obj.cycles_visibility
    return any((c_vis.camera, c_vis.diffuse, c_vis.glossy, c_vis.transmission, c_vis.scatter, c_vis.shadow))



def visible_to_camera(dg_obj_instance, is_viewport_render, view_layer=None):
    obj = dg_obj_instance.parent if dg_obj_instance.is_instance else dg_obj_instance.object
    if not obj.luxcore.visible_to_camera:
        return False
    if is_viewport_render:
        obj = obj.original
    return not obj.indirect_only_get(view_layer=view_layer)


def get_theme(context):
    current_theme_name = context.preferences.themes.items()[0][0]
    return context.preferences.themes[current_theme_name]


def get_abspath(path, library=None, must_exist=False, must_be_existing_file=False, must_be_existing_dir=False):
    """ library: The library this path is from. """
    assert not (must_be_existing_file and must_be_existing_dir)

    abspath = bpy.path.abspath(path, library=library)

    if must_be_existing_file and not os.path.isfile(abspath):
        raise OSError(f'Not an existing file: "{abspath}"')

    if must_be_existing_dir and not os.path.isdir(abspath):
        raise OSError(f'Not an existing directory: "{abspath}"')

    if must_exist and not os.path.exists(abspath):
        raise OSError(f'Path does not exist: "{abspath}"')

    return abspath


def absorption_at_depth_scaled(abs_col, depth, scale=1):
    assert depth > 0
    abs_col = list(abs_col)
    assert len(abs_col) == 3

    scaled = [0, 0, 0]
    for i in range(len(abs_col)):
        v = float(abs_col[i])
        scaled[i] = (-math.log(max([v, 1e-30])) / depth) * scale * (v == 1.0 and -1 or 1)

    return scaled


def all_elems_equal(_list):
    # https://stackoverflow.com/a/10285205
    # The list must not be empty!
    first = _list[0]
    return all(x == first for x in _list)


def use_obj_motion_blur(obj, scene):
    """ Check if this particular object will be exported with motion blur """
    cam = scene.camera

    if cam is None:
        return False

    motion_blur = cam.data.luxcore.motion_blur
    object_blur = motion_blur.enable and motion_blur.object_blur

    return object_blur and obj.luxcore.enable_motion_blur


def has_deforming_modifiers(obj):
    return any(mod.type not in NON_DEFORMING_MODIFIERS for mod in obj.modifiers)


def can_share_mesh(obj):
    if not obj.data or obj.data.users < 2:
        return False
    return not has_deforming_modifiers(obj)


def use_instancing(obj, scene, is_viewport_render):
    if is_viewport_render:
        # Always instance in viewport so we can move the object/light around
        return True

    return True if use_obj_motion_blur(obj, scene) else bool(can_share_mesh(obj))


def find_smoke_domain_modifier(obj):
    for mod in obj.modifiers:
        if bpy.app.version[:2] < (2, 82):
            if mod.type == "SMOKE" and mod.smoke_type == "DOMAIN":
                return mod
        elif mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
            return mod

    return None


def get_name_with_lib(datablock):
    """
    Format the name for display similar to Blender,
    with an "L" as prefix if from a library
    """
    text = datablock.name
    if datablock.library:
        # text += ' (Lib: "%s")' % datablock.library.name
        text = f"L {text}"
    return text


def clamp(value, _min=0, _max=1):
    return max(_min, min(_max, value))


def using_filesaver(is_viewport_render, scene):
    return not is_viewport_render and scene.luxcore.config.use_filesaver


def using_bidir_in_viewport(scene):
    return scene.luxcore.config.engine == "BIDIR" and scene.luxcore.viewport.use_bidir


def using_hybridbackforward(scene):
    config = scene.luxcore.config
    return (config.engine == "PATH" and not config.use_tiles
            and config.path.hybridbackforward_enable)


def using_hybridbackforward_in_viewport(scene):
    return using_hybridbackforward(scene) and scene.luxcore.viewport.add_light_tracing


def using_photongi_debug_mode(is_viewport_render, scene):
    if is_viewport_render:
        return False
    config = scene.luxcore.config
    if config.engine != "PATH":
        return False
    return config.photongi.enabled and config.photongi.debug != "off"


def is_pixel_filtering_forced_disabled(scene, denoiser_enabled):
    if denoiser_enabled:
        config = scene.luxcore.config

        # Bidir renders are not properly denoised with pixel filtering
        if config.engine == "BIDIR":
            return True
        # Light traced caustics are not properly denoised with pixel filtering
        if config.engine == "PATH" and config.path.hybridbackforward_enable:
            return True
    return False


def get_halt_conditions(scene):
    render_layer = view_layer.get_current_view_layer(scene)

    if render_layer and render_layer.luxcore.halt.enable:
        # Global halt conditions are overridden by this render layer
        return render_layer.luxcore.halt
    else:
        # Use global halt conditions
        return scene.luxcore.halt


def use_two_tiled_passes(scene):
    # When combining the BCD denoiser with tilepath in singlepass mode, we have to render
    # two passes (twice as many samples) because the first pass is needed as denoiser
    # warmup, and only during the second pass can the denoiser collect sample information.
    config = scene.luxcore.config
    denoiser = scene.luxcore.denoiser
    using_tilepath = config.engine == "PATH" and config.use_tiles
    return denoiser.enabled and denoiser.type == "BCD" and using_tilepath and not config.tile.multipass_enable


def pluralize(format_str, amount):
    formatted = format_str % amount
    if amount != 1:
        formatted += "s"
    return formatted


def is_opencl_build():
    return pyluxcore.GetPlatformDesc().Get("compile.LUXRAYS_ENABLE_OPENCL").GetBool()
    
    
def is_cuda_build():
    return pyluxcore.GetPlatformDesc().Get("compile.LUXRAYS_ENABLE_CUDA").GetBool()


def image_sequence_resolve_all(image):
    """
    From https://blender.stackexchange.com/a/21093/29401
    Returns a list of tuples: (index, filepath)
    index is the frame number, parsed from the filepath
    """
    filepath = get_abspath(image.filepath, image.library)
    basedir, filename = os.path.split(filepath)
    filename_noext, ext = os.path.splitext(filename)

    from string import digits
    if isinstance(filepath, bytes):
        digits = digits.encode()
    filename_nodigits = filename_noext.rstrip(digits)

    if len(filename_nodigits) == len(filename_noext):
        # Input isn't from a sequence
        return []

    indexed_filepaths = []
    for f in os.scandir(basedir):
        index_str = f.name[len(filename_nodigits):-len(ext) if ext else -1]

        if (f.is_file()
                and f.name.startswith(filename_nodigits)
                and f.name.endswith(ext)
                and index_str.isdigit()):
            elem = (int(index_str), f.path)
            indexed_filepaths.append(elem)

    return sorted(indexed_filepaths, key=lambda elem: elem[0])

def openVDB_sequence_resolve_all(file):
    filepath = get_abspath(file)
    basedir, filename = os.path.split(filepath)
    filename_noext, ext = os.path.splitext(filename)

    # A file sequence has a running number at the end of the filename, e.g. name001.ext
    # in case of the Blender cache files the structure is name_frame_index.ext

    # Test if the filename structure matches the Blender nomenclature
    matchstr = r'(.*)_([0-9]{6})_([0-9]{2})'
    matchObj = re.match(matchstr, filename_noext)

    if not matchObj:
        matchstr = r'(\D*)([0-9]+)'
        # Test if the filename structure matches a general sequence structure
        matchObj = re.match(matchstr, filename_noext)

    if matchObj:
        name = matchObj[1]
    else:
        # Input isn't from a sequence
        return []

    indexed_filepaths = []
    for f in os.scandir(basedir):
        filename_noext2, ext2 = os.path.splitext(f.name)
        if ext == ext2:
            matchObj = re.match(matchstr, filename_noext2)
            if matchObj and name == matchObj[1]:
                elem = int(matchObj[2]), f.path
                indexed_filepaths.append(elem)

    return sorted(indexed_filepaths, key=lambda elem: elem[0])


def is_valid_camera(obj):
    return obj and hasattr(obj, "type") and obj.type == "CAMERA"


def get_blendfile_name():
    basename = bpy.path.basename(bpy.data.filepath)
    return os.path.splitext(basename)[0]  # remove ".blend"


def get_persistent_cache_file_path(file_path, save_or_overwrite, is_viewport_render, scene):
    file_path_abs = get_abspath(file_path, library=scene.library)

    if not os.path.isfile(file_path_abs) and not save_or_overwrite:
        # Do not save the cache file
        return ""
    if using_filesaver(is_viewport_render, scene) and file_path.startswith("//"):
        # It is a relative path and we are using filesaver - don't make it
        # an absolute path, just strip the leading "//"
        return file_path[2:]
    if os.path.isfile(file_path) and save_or_overwrite:
        # To overwrite the file, we first have to delete it, otherwise
        # LuxCore loads the cache from this file
        os.remove(file_path)
    return file_path_abs


def in_material_shading_mode(context):
    return context and context.space_data.shading.type == "MATERIAL"


def get_addon_preferences(context):
    addon_name = basename(dirname(dirname(__file__)))
    return context.preferences.addons[addon_name].preferences


def count_index(func):
    """
    A decorator that increments an index each time the decorated function is called.
    It also passes the index as a keyword argument to the function.
    """
    def wrapper(*args, **kwargs):
        kwargs["index"] = wrapper.index
        wrapper.index += 1
        return func(*args, **kwargs)
    wrapper.index = 0
    return wrapper
